#!/usr/bin/env python3
"""(Re)build GreenhouseDW: database, schemas, tables, config and reference data.

WARNING: drops and recreates every table. Dev use only.
Usage:  python pipeline/setup_db.py
"""
from __future__ import annotations

import csv

from db import DATA_DIR, DATABASE, ROOT, SQL_DIR, connect, run_sql_file

ALIAS_FILE = ROOT / "config" / "crop_aliases.csv"   # hand-maintained crop spelling map (the RCA fix)

SQL_FILES = ["01_schemas.sql", "02_tables.sql", "03_seed_config.sql", "04_dq_rules.sql", "05_dq_engine.sql"]


def create_database() -> None:
    conn = connect(database="master", autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT DB_ID(?)", DATABASE)
    if cur.fetchone()[0] is None:
        cur.execute(f"CREATE DATABASE [{DATABASE}]")
        print(f"Created database {DATABASE}")
    conn.close()


def read_ref(name: str) -> list[dict]:
    path = DATA_DIR / "reference" / name
    if not path.exists():
        raise SystemExit(f"{path} not found. Run generator/generate_data.py first.")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_reference(conn) -> None:
    cur = conn.cursor()
    crops = read_ref("crops.csv")
    zones = read_ref("zones.csv")
    skus = read_ref("skus.csv")
    cur.executemany("INSERT INTO ref.crop (crop_name, crop_family) VALUES (?, ?)",
                    [(r["crop_name"], r["crop_family"]) for r in crops])
    cur.executemany("INSERT INTO ref.zone (zone_id, greenhouse, crop_name, area_m2) VALUES (?, ?, ?, ?)",
                    [(r["zone_id"], r["greenhouse"], r["crop_name"], int(r["area_m2"])) for r in zones])
    cur.executemany("INSERT INTO ref.sku (sku, description, crop_name, case_kg) VALUES (?, ?, ?, ?)",
                    [(r["sku"], r["description"], r["crop_name"], float(r["case_kg"])) for r in skus])
    aliases = []
    if ALIAS_FILE.exists():
        with ALIAS_FILE.open(newline="", encoding="utf-8") as f:
            aliases = [r for r in csv.DictReader(f) if (r.get("alias") or "").strip()]
    if aliases:
        cur.executemany("INSERT INTO ref.crop_alias (alias, crop_name, reason) VALUES (?, ?, ?)",
                        [(r["alias"], r["crop_name"].strip(), r.get("reason")) for r in aliases])
    print(f"Loaded reference data: {len(crops)} crops, {len(zones)} zones, {len(skus)} SKUs, {len(aliases)} crop aliases")


def main() -> None:
    create_database()
    conn = connect(autocommit=True)
    for name in SQL_FILES:
        run_sql_file(conn, SQL_DIR / name)
        print(f"Ran {name}")
    load_reference(conn)
    conn.close()
    print("Setup complete.")


if __name__ == "__main__":
    main()
