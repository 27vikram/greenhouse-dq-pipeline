#!/usr/bin/env python3
"""Smoke test: render every tab of the dashboard with sample data, no database needed.

Fails if the app raises, or if any chart spec is invalid (Altair validates on render).
Usage:  python tests/smoke_dashboard.py
"""
from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.modules.setdefault("pyodbc", types.ModuleType("pyodbc"))   # no driver needed for the test

import queries  # noqa: E402

RULES = [  # rule_id, source, severity, on_fail, checked, failed, details
    ("CLM-001", "climate", "Critical", "Quarantine", 8370, 0, None),
    ("CLM-003", "climate", "High", "Quarantine", 8370, 24, None),
    ("CLM-004", "climate", "High", "Quarantine", 8370, 74, None),
    ("CLM-101", "climate", "High", "Warn", 30, 1, "missing 2026-09-22"),
    ("CLM-103", "climate", "Medium", "Warn", 8267, 18, "Z09 stuck at 812 ppm for 18h from 2026-09-25 08:00"),
    ("HRV-002", "erp_harvest", "Critical", "Quarantine", 508, 8, None),
    ("HRV-004", "erp_harvest", "High", "Quarantine", 508, 36, None),
    ("SHP-004", "wms_shipments", "High", "Quarantine", 394, 20, None),
    ("SHP-101", "wms_shipments", "High", "Warn", 30, 1, "missing 2026-09-20"),
    ("REC-001", "cross_system", "High", "Warn", 4, 1, "Bell Pepper: shipped 81,195 kg vs 57,096 kg harvested (1.422x)"),
]


def fake_runs():
    t = datetime(2026, 10, 10, 18, 0)
    return pd.DataFrame([
        {"run_id": 2, "started_at": t + timedelta(minutes=9), "ended_at": t + timedelta(minutes=9, seconds=1),
         "status": "succeeded_with_warnings", "message": "16 rule(s) failed", "duration_s": 1},
        {"run_id": 1, "started_at": t, "ended_at": t + timedelta(seconds=1),
         "status": "succeeded_with_warnings", "message": "17 rule(s) failed", "duration_s": 1},
    ])


def fake_rules():
    return pd.DataFrame([{
        "rule_id": r, "rule_name": f"Rule {r}", "source_system": s, "check_level": "Row", "dq_dimension": "Validity",
        "severity": sev, "on_fail": act, "rows_checked": c, "rows_failed": f,
        "failure_rate": f / c, "status": "Fail" if f else "Pass", "details": d, "run_id": 2,
    } for r, s, sev, act, c, f, d in RULES])


def fake_history():
    return pd.DataFrame({"run_id": [1, 2], "rules_checked": [27, 27], "rules_failed": [17, 15], "failures": [205, 169]})


def fake_files():
    rows = []
    for src, prefix, ext, missing in (("climate", "climate", "csv", date(2026, 9, 22)),
                                      ("wms_shipments", "shipments", "json", date(2026, 9, 20))):
        for i in range(30):
            d = date(2026, 9, 1) + timedelta(days=i)
            if d != missing:
                rows.append({"source_system": src, "file_name": f"{prefix}_{d:%Y%m%d}.{ext}", "file_date": d,
                             "rows_in_file": 288, "rows_loaded": 288, "status": "loaded", "processed_run_id": 1,
                             "loaded_at": datetime(2026, 10, 10, 18, 0)})
    rows.append({"source_system": "erp_harvest", "file_name": "harvest_export_20260930.csv",
                 "file_date": date(2026, 9, 30), "rows_in_file": 508, "rows_loaded": 508, "status": "loaded",
                 "processed_run_id": 1, "loaded_at": datetime(2026, 10, 10, 18, 0)})
    return pd.DataFrame(rows)


def fake_rec():
    return pd.DataFrame({
        "crop_name": ["Bell Pepper", "Mini Cucumber", "Mini Sweet Pepper", "Tomato on the Vine"],
        "sellable_kg": [57095.9, 88681.4, 25335.2, 91001.7],
        "shipped_kg": [81195.0, 92188.0, 26206.0, 92885.0],
        "shipped_to_harvest_ratio": [1.422, 1.040, 1.034, 1.021],
    })


def fake_qsum():
    return pd.DataFrame({"rule_id": ["HRV-004", "SHP-004"], "rule_name": ["Crop name", "SKU exists"],
                         "source_table": ["stg.harvest", "stg.shipment_line"],
                         "file_name": ["harvest_export_20260930.csv", "shipments_20260918.json"],
                         "rows_quarantined": [36, 20], "rows_open": [36, 20]})


def fake_qrows(rule_id):
    return pd.DataFrame({"quarantine_id": [1, 2], "run_id": [1, 1], "quarantined_at": [datetime.now()] * 2,
                         "row_data": [json.dumps({"zone_id": "Z03", "crop_name": "Bell Peppers", "kg_harvested": "1500.2"}),
                                      json.dumps({"zone_id": "Z04", "crop_name": "B. Pepper", "kg_harvested": None})]})


def fake_counts():
    return pd.DataFrame({"item": ["Climate readings", "Harvest records", "Shipment lines"], "n": [8267, 453, 360]})


def fake_zone_yield():
    zones = [("Z01", "Bell Pepper", 10400, 23546), ("Z02", "Bell Pepper", 9800, 22770), ("Z03", "Bell Pepper", 10100, 0),
             ("Z04", "Bell Pepper", 9600, 10780), ("Z05", "Mini Cucumber", 8800, 28000),
             ("Z08", "Tomato on the Vine", 11200, 31000), ("Z11", "Mini Sweet Pepper", 7600, 12500)]
    return pd.DataFrame([{"zone_id": z, "greenhouse": "GH-A", "crop_name": c, "area_m2": a,
                          "sellable_kg": kg, "records": 24} for z, c, a, kg in zones])


queries.runs, queries.latest_rules, queries.rule_history = fake_runs, fake_rules, fake_history
queries.files, queries.reconciliation, queries.quarantine_summary = fake_files, fake_rec, fake_qsum
queries.quarantine_rows, queries.warehouse_counts, queries.zone_yield = fake_qrows, fake_counts, fake_zone_yield

from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
if at.exception:
    for e in at.exception:
        print("EXCEPTION:", e.message, "\n", "\n".join(e.stack_trace[-6:]))
    sys.exit(1)

print(f"Rendered OK: {len(at.tabs)} tabs, {len(at.metric)} metrics, {len(at.dataframe)} tables, "
      f"{len(at.get('arrow_vega_lite_chart'))} charts")
