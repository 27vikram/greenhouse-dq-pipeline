#!/usr/bin/env python3
"""
Generate one month (September 2026) of synthetic greenhouse data from three
"source systems", with data quality issues planted on purpose.

Outputs
  data/reference/            master data (crops, zones, SKUs)
  data/raw/climate/          climate sensor CSVs, one file per day
  data/raw/erp/              ERP harvest export (one monthly CSV)
  data/raw/shipments/        WMS shipment JSON files, one per day
  data/planted_issues.json   answer key: every planted issue and how many rows it touches

Deterministic: the same seed always produces the same files.
Standard library only.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
START = date(2026, 9, 1)
DAYS = 30

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
REF = DATA / "reference"

# ------------------------------------------------------------------ master data
CROPS = {
    # crop: (family, kg per m2 per day, harvest weekdays where Mon=0)
    "Bell Pepper": ("Pepper", 0.085, {0, 3}),
    "Mini Cucumber": ("Cucumber", 0.120, {0, 1, 2, 3, 4, 5}),
    "Tomato on the Vine": ("Tomato", 0.100, {0, 2, 4}),
    "Mini Sweet Pepper": ("Pepper", 0.060, {1, 4}),
}
ZONES = [  # (zone_id, greenhouse, crop, area_m2)
    ("Z01", "GH-A", "Bell Pepper", 10400),
    ("Z02", "GH-A", "Bell Pepper", 9800),
    ("Z03", "GH-A", "Bell Pepper", 10100),
    ("Z04", "GH-A", "Bell Pepper", 9600),
    ("Z05", "GH-A", "Mini Cucumber", 8800),
    ("Z06", "GH-A", "Mini Cucumber", 9200),
    ("Z07", "GH-B", "Mini Cucumber", 9000),
    ("Z08", "GH-B", "Tomato on the Vine", 11200),
    ("Z09", "GH-B", "Tomato on the Vine", 10800),
    ("Z10", "GH-B", "Tomato on the Vine", 11000),
    ("Z11", "GH-B", "Mini Sweet Pepper", 7600),
    ("Z12", "GH-B", "Mini Sweet Pepper", 7900),
]
SKUS = [  # (sku, description, crop, case_kg)
    ("BPR-5KG", "Red Bell Pepper 5 kg case", "Bell Pepper", 5.0),
    ("BPY-5KG", "Yellow Bell Pepper 5 kg case", "Bell Pepper", 5.0),
    ("MCU-4KG", "Mini Cucumber 4 kg case", "Mini Cucumber", 4.0),
    ("TOV-5KG", "Tomato on the Vine 5 kg case", "Tomato on the Vine", 5.0),
    ("MSP-2KG", "Mini Sweet Pepper 2 kg case", "Mini Sweet Pepper", 2.0),
]
# A new SKU the warehouse starts shipping on Sep 18 that nobody added to master data.
UNMASTERED_SKU = ("BPO-5KG", "Bell Pepper", 5.0)
UNMASTERED_FROM = date(2026, 9, 18)

CUSTOMERS = [
    "Northline Grocers", "Lakeshore Foods", "Maple Fresh Markets",
    "Great Lakes Wholesale", "Prairie Co-op", "Harbourview Markets",
]

ISSUES: list[dict] = []


def plant(issue_id, source, dimension, description, affected, rule_hint, checks):
    """checks: [(rule_id, expected_failures)] - what dq.result should show for this issue."""
    ISSUES.append({
        "issue_id": issue_id,
        "source": source,
        "dq_dimension": dimension,
        "description": description,
        "affected": affected,
        "how_to_catch": rule_hint,
        "checks": [{"rule_id": r, "expected_failures": n} for r, n in checks],
    })


def days():
    for i in range(DAYS):
        yield START + timedelta(days=i)


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def reset_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    for f in path.iterdir():
        if f.is_file():
            f.unlink()


# ------------------------------------------------------------------ reference
def write_reference():
    write_csv(REF / "crops.csv", ["crop_name", "crop_family"],
              [(c, v[0]) for c, v in CROPS.items()])
    write_csv(REF / "zones.csv", ["zone_id", "greenhouse", "crop_name", "area_m2"], ZONES)
    write_csv(REF / "skus.csv", ["sku", "description", "crop_name", "case_kg"], SKUS)


# ------------------------------------------------------------------ climate
def climate_reading(rng, ts):
    h = ts.hour
    daylight = max(0.0, math.sin(math.pi * (h - 6) / 14)) if 6 <= h <= 20 else 0.0
    temp = 18.5 + 7.0 * daylight + rng.gauss(0, 0.4)
    hum = 84 - 16 * daylight + rng.gauss(0, 1.8)
    co2 = 720 + 230 * daylight + rng.gauss(0, 22)
    light = max(0.0, 1150 * daylight + rng.gauss(0, 25)) if daylight > 0 else 0.0
    return {
        "temp_c": round(temp, 1),
        "humidity_pct": round(hum, 1),
        "co2_ppm": int(round(co2)),
        "light_par": int(round(light)),
    }


def gen_climate(rng):
    out = RAW / "climate"
    reset_dir(out)
    header = ["zone_id", "sensor_id", "reading_ts", "temp_c", "humidity_pct", "co2_ppm", "light_par"]

    missing_file_day = date(2026, 9, 22)
    gap_zone, gap_day, gap_hours = "Z07", date(2026, 9, 9), range(2, 8)
    f_zone, f_days = "Z03", {date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)}
    frozen_zone, frozen_from, frozen_to, frozen_value = "Z09", datetime(2026, 9, 25, 8), datetime(2026, 9, 26, 2), 812
    temp_spikes = {("Z05", datetime(2026, 9, 6, 14)), ("Z11", datetime(2026, 9, 19, 3))}
    hum_sentinels = {
        ("Z02", datetime(2026, 9, 3, 11)), ("Z06", datetime(2026, 9, 8, 22)),
        ("Z08", datetime(2026, 9, 13, 5)), ("Z10", datetime(2026, 9, 24, 16)),
        ("Z12", datetime(2026, 9, 28, 9)),
    }
    dup_zone, dup_day = "Z02", date(2026, 9, 11)

    n = defaultdict(int)
    for d in days():
        rows = []
        for zone_id, *_ in ZONES:
            for h in range(24):
                ts = datetime(d.year, d.month, d.day, h)
                r = climate_reading(rng, ts)  # always draw, so the random stream stays stable
                if zone_id == gap_zone and d == gap_day and h in gap_hours:
                    n["gap"] += 1
                    continue
                if zone_id == f_zone and d in f_days:
                    r["temp_c"] = round(r["temp_c"] * 9 / 5 + 32, 1)
                    n["fahrenheit"] += 1
                if zone_id == frozen_zone and frozen_from <= ts < frozen_to:
                    r["co2_ppm"] = frozen_value
                    n["frozen"] += 1
                if (zone_id, ts) in temp_spikes:
                    r["temp_c"] = 85.0
                    n["spike"] += 1
                if (zone_id, ts) in hum_sentinels:
                    r["humidity_pct"] = -999
                    n["sentinel"] += 1
                rows.append([zone_id, f"SNS-{zone_id}-01", ts.strftime("%Y-%m-%d %H:%M:%S"),
                             r["temp_c"], r["humidity_pct"], r["co2_ppm"], r["light_par"]])
        if d == dup_day:
            extra = [r for r in rows if r[0] == dup_zone]
            rows.extend(extra)
            n["dup"] = len(extra)
        if d == missing_file_day:
            n["missing_file_rows"] = len(rows)
            continue
        write_csv(out / f"climate_{d:%Y%m%d}.csv", header, rows)

    plant("C01", "climate", "Timeliness",
          f"No climate file delivered for {missing_file_day} ({n['missing_file_rows']} hourly readings never arrive).",
          {"files": 1, "rows": n["missing_file_rows"]},
          "Expect one file per calendar day; flag missing days.",
          [("CLM-101", 1)])
    plant("C02", "climate", "Completeness",
          f"Sensor in {gap_zone} offline {gap_day} 02:00-07:00; {n['gap']} hourly readings missing.",
          {"rows": n["gap"]},
          "Expect 24 readings per zone per day; flag zone-days with fewer.",
          [("CLM-102", 1)])
    plant("C03", "climate", "Uniqueness",
          f"climate_{dup_day:%Y%m%d}.csv contains {dup_zone} twice (file re-sent and appended).",
          {"rows": n["dup"]},
          "zone_id + reading_ts must be unique.",
          [("CLM-003", n["dup"])])
    plant("C04", "climate", "Validity",
          f"{f_zone} temperatures reported in Fahrenheit Sep 15-17 after a sensor firmware update; header still says temp_c.",
          {"rows": n["fahrenheit"]},
          "Range check on temp_c (ref.sensor_valid_range). RCA candidate: all failures share one zone and date window.",
          [("CLM-004", n["fahrenheit"])])
    plant("C05", "climate", "Validity", "Two temperature spikes of 85.0 C.",
          {"rows": n["spike"]}, "Range check on temp_c.", [("CLM-004", n["spike"])])
    plant("C06", "climate", "Validity", "Five humidity readings of -999 (vendor 'no reading' sentinel).",
          {"rows": n["sentinel"]}, "Range check on humidity_pct.", [("CLM-005", n["sentinel"])])
    plant("C07", "climate", "Accuracy",
          f"{frozen_zone} CO2 sensor stuck at {frozen_value} ppm for {n['frozen']} consecutive hours. Values are in range, so range checks pass.",
          {"rows": n["frozen"]},
          "Flatline check: same value for N+ consecutive hours (window functions).",
          [("CLM-103", n["frozen"])])


# ------------------------------------------------------------------ harvest (ERP)
def erp_crop_name(zone_id, crop, d):
    if zone_id == "Z02":
        return "BELL PEPPER "          # differs only by case + trailing space
    if zone_id == "Z03":
        return "Bell Peppers"          # plural, typed by a different clerk
    if zone_id == "Z04" and d >= date(2026, 9, 15):
        return "B. Pepper"             # abbreviation starts mid-month
    return crop


def gen_harvest(rng):
    out = RAW / "erp"
    reset_dir(out)
    rows = []
    harvest_id = 500000
    pending = {z[0]: 0.0 for z in ZONES}
    sellable = defaultdict(lambda: defaultdict(float))  # day -> crop -> true A+B kg

    for d in days():
        for zone_id, gh, crop, area in ZONES:
            _, kg_m2, harvest_days = CROPS[crop]
            pending[zone_id] += kg_m2 * area * rng.uniform(0.85, 1.15)
            if d.weekday() not in harvest_days:
                continue
            total, pending[zone_id] = pending[zone_id], 0.0
            for grade, share in (("A", 0.80), ("B", 0.15), ("Cull", 0.05)):
                harvest_id += 1
                kg = round(total * share * rng.uniform(0.96, 1.04), 1)
                if grade != "Cull":
                    sellable[d][crop] += kg
                rows.append({
                    "harvest_id": harvest_id,
                    "zone_id": zone_id,
                    "crop_name": erp_crop_name(zone_id, crop, d),
                    "harvest_date": d.isoformat(),
                    "grade": grade,
                    "kg_harvested": kg,
                    "recorded_by": f"{gh.lower()}.clerk{1 if int(zone_id[1:]) % 2 else 2}",
                })

    # pick rows to corrupt from a shuffled pool so no row is corrupted twice
    pool_idx = [i for i in range(len(rows)) if rows[i]["harvest_date"] != "2026-09-30"]
    rng.shuffle(pool_idx)
    pool = iter(pool_idx)
    neg = [next(pool) for _ in range(2)]
    for i in neg:
        rows[i]["kg_harvested"] = -rows[i]["kg_harvested"]
    null_grade = [next(pool) for _ in range(5)]
    for i in null_grade:
        rows[i]["grade"] = ""
    dup_src = [next(pool) for _ in range(4)]
    future = next(i for i, r in enumerate(rows) if r["harvest_date"] == "2026-09-30")
    rows[future]["harvest_date"] = "2026-10-30"

    for k, day in enumerate(("2026-09-21", "2026-09-22", "2026-09-23")):
        harvest_id += 1
        rows.append({"harvest_id": harvest_id, "zone_id": "Z13", "crop_name": "Mini Cucumber",
                     "harvest_date": day, "grade": "A", "kg_harvested": round(rng.uniform(850, 950), 1),
                     "recorded_by": "gh-b.clerk2"})
    for i in dup_src:
        copy = dict(rows[i])
        copy["kg_harvested"] = round(copy["kg_harvested"] * 1.08, 1)
        rows.append(copy)

    rows.sort(key=lambda r: r["harvest_id"])  # stable: duplicates land next to their originals
    # counted on the final file, so re-exported duplicates are included
    n_plural = sum(r["crop_name"] == "Bell Peppers" for r in rows)
    n_abbrev = sum(r["crop_name"] == "B. Pepper" for r in rows)
    n_case = sum(r["crop_name"] == "BELL PEPPER " for r in rows)
    header = ["harvest_id", "zone_id", "crop_name", "harvest_date", "grade", "kg_harvested", "recorded_by"]
    write_csv(out / "harvest_export_20260930.csv", header, [[r[c] for c in header] for r in rows])

    plant("H01", "erp_harvest", "Consistency",
          "Bell Pepper spelled 'Bell Peppers' (all Z03 rows) and 'B. Pepper' (Z04 from Sep 15). These rows drop out of any join to the crop master.",
          {"rows_bell_peppers": n_plural, "rows_b_pepper": n_abbrev},
          "crop_name must exist in ref.crop. RCA candidate: Bell Pepper shipped kg > harvested kg.",
          [("HRV-004", n_plural + n_abbrev), ("REC-001", 1)])
    plant("H02", "erp_harvest", "Consistency (control case, NOT an issue)",
          "Z02 rows say 'BELL PEPPER ' (upper case + trailing space). Under SQL Server's default case-insensitive collation, = ignores case and trailing spaces, so these still match.",
          {"rows": n_case},
          "Should PASS the crop_name check. Good talking point about collation.",
          [("HRV-004", 0)])
    plant("H03", "erp_harvest", "Validity", "Negative kg_harvested.", {"rows": 2}, "kg_harvested >= 0.", [("HRV-007", 2)])
    plant("H04", "erp_harvest", "Completeness", "Blank grade.", {"rows": 5}, "grade NOT NULL and IN ('A','B','Cull').", [("HRV-006", 5)])
    plant("H05", "erp_harvest", "Validity", "harvest_date typed as 2026-10-30 instead of 2026-09-30 (future date).",
          {"rows": 1}, "harvest_date not later than the extract date.", [("HRV-005", 1)])
    plant("H06", "erp_harvest", "Consistency", "Zone Z13 does not exist in the zone master.",
          {"rows": 3}, "zone_id must exist in ref.zone.", [("HRV-003", 3)])
    plant("H07", "erp_harvest", "Uniqueness",
          "Four harvest_ids appear twice with different kg (ERP correction re-exported without removing the original).",
          {"harvest_ids": 4, "rows": 8}, "harvest_id must be unique; flag conflicting duplicates.",
          [("HRV-002", 8)])
    return sellable


# ------------------------------------------------------------------ shipments (WMS)
def gen_shipments(rng, sellable):
    out = RAW / "shipments"
    reset_dir(out)

    skus_by_crop = defaultdict(list)
    for sku, _, crop, case_kg in SKUS:
        skus_by_crop[crop].append((sku, case_kg))

    # opening stock: about two days of sellable output per crop
    inventory = defaultdict(float)
    for _, _, crop, area in ZONES:
        inventory[crop] += 2 * CROPS[crop][1] * area * 0.95

    by_day = {}
    for d in days():
        lines = []
        for crop in CROPS:
            ship_kg = inventory[crop] * rng.uniform(0.88, 0.95)
            inventory[crop] -= ship_kg
            options = list(skus_by_crop[crop])
            if crop == UNMASTERED_SKU[1] and d >= UNMASTERED_FROM:
                options.append((UNMASTERED_SKU[0], UNMASTERED_SKU[2]))
            weights = [rng.uniform(0.6, 1.4) for _ in options]
            total_w = sum(weights)
            for (sku, case_kg), w in zip(options, weights):
                cases = int(ship_kg * w / total_w / case_kg)
                while cases > 0:
                    chunk = min(cases, rng.randint(120, 420))
                    lines.append((rng.choice(CUSTOMERS), sku, chunk))
                    cases -= chunk
            inventory[crop] += sellable[d][crop]  # today's harvest ships from tomorrow

        by_customer = defaultdict(list)
        for cust, sku, cases in lines:
            by_customer[cust].append((sku, cases))
        by_day[d] = [
            {
                "shipment_id": f"SHP-{d:%Y%m%d}-{n:03d}",
                "customer": cust,
                "ship_date": d.isoformat(),
                "lines": [{"line_no": i, "sku": s, "cases": c} for i, (s, c) in enumerate(by_customer[cust], start=1)],
            }
            for n, cust in enumerate(sorted(by_customer), start=1)
        ]

    n_unknown = sum(l["sku"] == UNMASTERED_SKU[0] for ships in by_day.values() for s in ships for l in s["lines"])

    # S03: zero / negative cases
    skip = {date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 20), date(2026, 9, 26)}
    candidates = [(d, si, li) for d, ships in by_day.items() if d not in skip
                  for si, s in enumerate(ships) for li in range(len(s["lines"]))]
    for k, (d, si, li) in enumerate(rng.sample(candidates, 4)):
        by_day[d][si]["lines"][li]["cases"] = -40 if k == 0 else 0

    # S04: missing customer
    by_day[date(2026, 9, 26)][0]["customer"] = None
    n_no_customer_lines = len(by_day[date(2026, 9, 26)][0]["lines"])

    # S05: two Sep 10 shipments re-sent inside the Sep 11 file
    resent = json.loads(json.dumps(by_day[date(2026, 9, 10)][:2]))
    by_day[date(2026, 9, 11)].extend(resent)
    n_resent_lines = sum(len(s["lines"]) for s in resent)

    # S01: no file on Sep 20; those shipments arrive late inside the Sep 21 file
    late = by_day.pop(date(2026, 9, 20))
    by_day[date(2026, 9, 21)] = late + by_day[date(2026, 9, 21)]

    for d in days():
        if d not in by_day:
            continue
        ships = by_day[d]
        doc = {"source_system": "WMS", "extract_date": d.isoformat(), "record_count": len(ships), "shipments": ships}
        (out / f"shipments_{d:%Y%m%d}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    plant("S01", "wms_shipments", "Timeliness",
          "No shipments file for 2026-09-20. Its shipments arrive a day late inside the 2026-09-21 file (ship_date still 09-20).",
          {"files_missing": 1, "late_shipments": len(late), "late_lines": sum(len(s["lines"]) for s in late)},
          "Expect one file per day; flag ship_date earlier than extract_date.",
          [("SHP-101", 1), ("SHP-102", sum(len(s["lines"]) for s in late))])
    plant("S02", "wms_shipments", "Consistency",
          f"SKU {UNMASTERED_SKU[0]} (orange bell pepper) shipped from {UNMASTERED_FROM} but never added to the SKU master.",
          {"lines": n_unknown},
          "sku must exist in ref.sku. Second RCA candidate: shipped kg drops when these lines are quarantined.",
          [("SHP-004", n_unknown)])
    plant("S03", "wms_shipments", "Validity", "Lines with cases = 0 (3) or -40 (1).",
          {"lines": 4}, "cases > 0.", [("SHP-005", 4)])
    plant("S04", "wms_shipments", "Completeness", "A 2026-09-26 shipment has no customer.",
          {"shipments": 1, "lines": n_no_customer_lines}, "customer NOT NULL.", [("SHP-006", n_no_customer_lines)])
    plant("S05", "wms_shipments", "Uniqueness", "Two 2026-09-10 shipments re-sent in the 2026-09-11 file.",
          {"shipments": 2, "lines": n_resent_lines}, "shipment_id + line_no must be unique across files. The re-sent lines are also late (ship_date before extract_date).",
          [("SHP-003", n_resent_lines), ("SHP-102", n_resent_lines)])


def main():
    write_reference()
    gen_climate(random.Random(SEED + 1))
    sellable = gen_harvest(random.Random(SEED + 2))
    gen_shipments(random.Random(SEED + 3), sellable)

    (DATA / "planted_issues.json").write_text(json.dumps({"seed": SEED, "issues": ISSUES}, indent=2), encoding="utf-8")

    print(f"Wrote data to {DATA}")
    for kind in ("climate", "erp", "shipments"):
        print(f"  raw/{kind}: {len(list((RAW / kind).iterdir()))} files")
    print(f"  planted_issues.json: {len(ISSUES)} entries")


if __name__ == "__main__":
    main()
