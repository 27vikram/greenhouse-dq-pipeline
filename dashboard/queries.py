"""Read-only queries behind the dashboard. Each returns a pandas DataFrame."""
from __future__ import annotations

import decimal
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from db import connect  # noqa: E402


def query(sql: str, *params) -> pd.DataFrame:
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, *params)
        columns = [c[0] for c in cur.description]
        df = pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=columns)
    finally:
        conn.close()
    # SQL Server DECIMAL arrives as Python Decimal; make those columns plain floats
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, decimal.Decimal)).any():
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def runs() -> pd.DataFrame:
    return query("""
        SELECT TOP 50 run_id, started_at, ended_at, status, message,
               DATEDIFF(SECOND, started_at, ended_at) AS duration_s
        FROM ops.pipeline_run
        ORDER BY run_id DESC""")


def latest_rules() -> pd.DataFrame:
    return query("""
        SELECT rule_id, rule_name, source_system, check_level, dq_dimension, severity, on_fail,
               rows_checked, rows_failed, failure_rate, status, details, run_id
        FROM dq.vw_latest_rule_status
        ORDER BY rule_id""")


def rule_history() -> pd.DataFrame:
    return query("""
        SELECT run_id,
               COUNT(*) AS rules_checked,
               SUM(CASE WHEN status = 'Fail' THEN 1 ELSE 0 END) AS rules_failed,
               SUM(rows_failed) AS failures
        FROM dq.vw_rule_results
        GROUP BY run_id
        ORDER BY run_id""")


def files() -> pd.DataFrame:
    return query("""
        SELECT source_system, file_name, ops.fn_file_date(file_name) AS file_date,
               rows_in_file, rows_loaded, status, processed_run_id, loaded_at
        FROM ops.file_load
        ORDER BY source_system, file_name""")


def reconciliation() -> pd.DataFrame:
    return query("""
        SELECT crop_name, sellable_kg, shipped_kg, shipped_to_harvest_ratio
        FROM dq.vw_crop_reconciliation
        ORDER BY crop_name""")


def quarantine_summary() -> pd.DataFrame:
    return query("""
        SELECT rule_id, rule_name, source_table, file_name, rows_quarantined, rows_open
        FROM err.vw_quarantine_summary
        ORDER BY rule_id, file_name""")


def quarantine_rows(rule_id: str) -> pd.DataFrame:
    return query("""
        SELECT TOP 500 quarantine_id, run_id, row_data, quarantined_at
        FROM err.quarantine
        WHERE rule_id = ?
        ORDER BY quarantine_id""", rule_id)


def warehouse_counts() -> pd.DataFrame:
    return query("""
        SELECT 'Climate readings' AS item, COUNT(*) AS n FROM dw.fact_climate_hourly
        UNION ALL SELECT 'Harvest records', COUNT(*) FROM dw.fact_harvest
        UNION ALL SELECT 'Shipment lines', COUNT(*) FROM dw.fact_shipment_line""")


def zone_yield() -> pd.DataFrame:
    return query("""
        SELECT z.zone_id, z.greenhouse, c.crop_name, z.area_m2,
               SUM(CASE WHEN h.grade IN ('A', 'B') THEN h.kg_harvested ELSE 0 END) AS sellable_kg,
               COUNT(h.harvest_id) AS records
        FROM dw.dim_zone z
        JOIN dw.dim_crop c          ON c.crop_key = z.crop_key
        LEFT JOIN dw.fact_harvest h ON h.zone_key = z.zone_key
        GROUP BY z.zone_id, z.greenhouse, c.crop_name, z.area_m2
        ORDER BY z.zone_id""")
