#!/usr/bin/env python3
"""Run the full pipeline as one tracked run:

  1. files -> staging          (file-level controls, see load_staging.py)
  2. staging -> DQ -> warehouse (row rules, quarantine, dimension + fact loads)
  3. set-level checks           (missing files, incomplete days, flatlines, reconciliation)

The run, every file and every rule result are logged under one run_id.
Safe to rerun: files already processed are skipped.

Usage:  python pipeline/run_pipeline.py
"""
from __future__ import annotations

import time

from db import connect
from load_staging import file_problems, finish_run, load_all, print_load_summary, start_run


def process_pending(conn, run_id: int) -> None:
    cur = conn.cursor()
    cur.execute("EXEC ops.usp_process_pending @run_id = ?", run_id)
    while cur.nextset():   # surface any error raised after the first statement
        pass
    conn.commit()


def print_dq_summary(conn, run_id: int) -> int:
    cur = conn.cursor()
    cur.execute("""
        SELECT rule_id, severity, on_fail, rows_checked, rows_failed, status, rule_name, details
        FROM dq.vw_rule_results
        WHERE run_id = ?
        ORDER BY rule_id""", run_id)
    rows = cur.fetchall()
    failed = [r for r in rows if r.status == "Fail"]
    print(f"\nData quality: {len(rows)} rules checked, {len(rows) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print(f"{'rule':<9}{'severity':<10}{'action':<12}{'checked':>8}{'failed':>8}  rule")
        for r in failed:
            print(f"{r.rule_id:<9}{r.severity:<10}{r.on_fail:<12}{r.rows_checked:>8}{r.rows_failed:>8}  {r.rule_name}")
            if r.details:
                print(f"{'':<47}↳ {r.details[:110]}{'…' if len(r.details) > 110 else ''}")

    cur.execute("""
        SELECT (SELECT COUNT(*) FROM dw.fact_climate_hourly),
               (SELECT COUNT(*) FROM dw.fact_harvest),
               (SELECT COUNT(*) FROM dw.fact_shipment_line),
               (SELECT COUNT(*) FROM err.quarantine WHERE run_id = ?)""", run_id)
    climate, harvest, shipments, quarantined = cur.fetchone()
    print(f"\nWarehouse now holds {climate:,} climate readings, {harvest:,} harvest records, "
          f"{shipments:,} shipment lines. Quarantined this run: {quarantined:,} row-rule failures.")
    return len(failed)


def main() -> None:
    conn = connect()
    run_id = start_run(conn, "run_pipeline")
    started = time.perf_counter()
    try:
        summary = load_all(conn, run_id)
        process_pending(conn, run_id)
    except Exception as e:
        conn.rollback()
        finish_run(conn, run_id, "failed", str(e)[:4000])
        raise

    print(f"\nRun {run_id}: files -> staging")
    print_load_summary(summary)
    failed_rules = print_dq_summary(conn, run_id)

    status = "succeeded_with_warnings" if failed_rules or file_problems(summary) else "succeeded"
    finish_run(conn, run_id, status, f"{failed_rules} rule(s) failed")
    print(f"\nRun {run_id} {status} in {time.perf_counter() - started:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
