#!/usr/bin/env python3
"""Land raw source files into the stg schema, with file-level controls.

Controls at this hop (file -> staging):
  * Idempotency: a file whose SHA-256 was already loaded successfully is skipped,
    so the loader can be rerun safely after a failure.
  * Schema check: a file missing an expected column is rejected whole, never half-loaded.
  * Row-count reconciliation: rows read from the file must equal rows landed for that load_id.
Every file gets a row in ops.file_load, so any staging row can be traced to its file and row.

Usage:  python pipeline/load_staging.py
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pyodbc

from db import DATA_DIR, connect

RAW = DATA_DIR / "raw"

CLIMATE_COLS = ["zone_id", "sensor_id", "reading_ts", "temp_c", "humidity_pct", "co2_ppm", "light_par"]
HARVEST_COLS = ["harvest_id", "zone_id", "crop_name", "harvest_date", "grade", "kg_harvested", "recorded_by"]
SHIPMENT_COLS = ["extract_date", "shipment_id", "customer_name", "ship_date", "line_no", "sku", "cases"]


class FileRejected(Exception):
    """The file is unusable as delivered (e.g. schema drift). Logged, not raised further."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(value) -> str | None:
    """Empty -> NULL. Everything else is kept exactly as delivered (no trimming) so DQ sees the raw value."""
    if value is None:
        return None
    value = str(value)
    return value if value.strip() else None


def read_csv_rows(path: Path, expected: list[str]) -> list[list]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in expected if c not in header]
        if missing:
            raise FileRejected(f"missing expected column(s): {', '.join(missing)}; header was {header}")
        reader.fieldnames = header
        return [[clean(row.get(c)) for c in expected] for row in reader]


def read_shipments(path: Path) -> list[list]:
    """Flatten the nested WMS JSON: one staging row per shipment line."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FileRejected(f"invalid JSON: {e}") from e
    if not isinstance(doc.get("shipments"), list):
        raise FileRejected("no 'shipments' array in file")
    rows = []
    for s in doc["shipments"]:
        for line in s.get("lines") or []:
            rows.append([clean(doc.get("extract_date")), clean(s.get("shipment_id")), clean(s.get("customer")),
                         clean(s.get("ship_date")), clean(line.get("line_no")), clean(line.get("sku")),
                         clean(line.get("cases"))])
    return rows


SOURCES = [
    # (source_system, folder, pattern, staging table, columns, reader)
    ("climate", "climate", "*.csv", "stg.climate_reading", CLIMATE_COLS, lambda p: read_csv_rows(p, CLIMATE_COLS)),
    ("erp_harvest", "erp", "*.csv", "stg.harvest", HARVEST_COLS, lambda p: read_csv_rows(p, HARVEST_COLS)),
    ("wms_shipments", "shipments", "*.json", "stg.shipment_line", SHIPMENT_COLS, read_shipments),
]


def start_run(conn, message: str) -> int:
    cur = conn.cursor()
    cur.execute("INSERT INTO ops.pipeline_run (status, message) OUTPUT INSERTED.run_id VALUES ('running', ?)", message)
    run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, message: str | None = None) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE ops.pipeline_run SET ended_at = SYSUTCDATETIME(), status = ?, "
                "message = COALESCE(?, message) WHERE run_id = ?", status, message, run_id)
    conn.commit()


def load_file(conn, run_id: int, system: str, path: Path, table: str, cols: list[str], reader) -> tuple[str, int]:
    cur = conn.cursor()
    digest = sha256(path)
    cur.execute("SELECT load_id FROM ops.file_load WHERE file_hash = ? AND status = 'loaded'", digest)
    if cur.fetchone():
        return "skipped", 0

    cur.execute("INSERT INTO ops.file_load (run_id, source_system, file_name, file_hash) "
                "OUTPUT INSERTED.load_id VALUES (?, ?, ?, ?)", run_id, system, path.name, digest)
    load_id = cur.fetchone()[0]
    conn.commit()

    try:
        rows = reader(path)
        if rows:
            ins = conn.cursor()            # separate cursor: setinputsizes would leak into later queries
            ins.fast_executemany = True
            ins.setinputsizes([(pyodbc.SQL_INTEGER, 0, 0), (pyodbc.SQL_WVARCHAR, 260, 0), (pyodbc.SQL_INTEGER, 0, 0)]
                              + [(pyodbc.SQL_WVARCHAR, 100, 0)] * len(cols))
            placeholders = ", ".join("?" * (len(cols) + 3))
            ins.executemany(
                f"INSERT INTO {table} (load_id, source_file, source_row, {', '.join(cols)}) VALUES ({placeholders})",
                [[load_id, path.name, i, *r] for i, r in enumerate(rows, start=1)],
            )
            ins.close()
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE load_id = ?", load_id)
        landed = cur.fetchone()[0]
        status = "loaded" if landed == len(rows) else "row_mismatch"
        cur.execute("UPDATE ops.file_load SET rows_in_file = ?, rows_loaded = ?, status = ? WHERE load_id = ?",
                    len(rows), landed, status, load_id)
        conn.commit()
        return status, landed
    except FileRejected as e:
        conn.rollback()
        cur.execute("UPDATE ops.file_load SET status = 'rejected', message = ? WHERE load_id = ?", str(e)[:4000], load_id)
        conn.commit()
        return "rejected", 0
    except Exception as e:
        conn.rollback()
        cur.execute("UPDATE ops.file_load SET status = 'failed', message = ? WHERE load_id = ?", str(e)[:4000], load_id)
        conn.commit()
        raise


def load_all(conn, run_id: int) -> dict:
    summary = {}
    for system, folder, pattern, table, cols, reader in SOURCES:
        counts = {"loaded": 0, "skipped": 0, "rejected": 0, "row_mismatch": 0, "rows": 0}
        for path in sorted((RAW / folder).glob(pattern)):
            status, rows = load_file(conn, run_id, system, path, table, cols, reader)
            counts[status] += 1
            counts["rows"] += rows
            if status in ("rejected", "row_mismatch"):
                print(f"  ! {path.name}: {status}")
        summary[system] = counts
    return summary


def main() -> None:
    conn = connect()
    run_id = start_run(conn, "load_staging")
    try:
        summary = load_all(conn, run_id)
    except Exception as e:
        finish_run(conn, run_id, "failed", str(e)[:4000])
        raise
    problems = any(c["rejected"] or c["row_mismatch"] for c in summary.values())
    finish_run(conn, run_id, "succeeded_with_warnings" if problems else "succeeded")

    print(f"\nRun {run_id}")
    print(f"{'source':<15}{'loaded':>8}{'skipped':>9}{'rejected':>10}{'mismatch':>10}{'rows':>9}")
    for system, c in summary.items():
        print(f"{system:<15}{c['loaded']:>8}{c['skipped']:>9}{c['rejected']:>10}{c['row_mismatch']:>10}{c['rows']:>9}")
    conn.close()


if __name__ == "__main__":
    main()
