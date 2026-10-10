# RCA-001: Bell Pepper shipments exceed harvest by 42%

| | |
|---|---|
| **Status** | Resolved |
| **Severity** | High: harvest volume understated in reporting |
| **Detected by** | `REC-001` Shipped kg does not exceed sellable harvest (reconciliation control) |
| **Detected** | Run 1, September 2026 data |
| **Affected data** | 36 ERP harvest records (zones Z03, Z04), 33,469 kg of grade A+B Bell Pepper |
| **Root cause** | Crop name entered with non-standard spellings in the ERP; rows failed the crop master lookup |
| **Fix** | Spelling variants mapped in master data (`config/crop_aliases.csv` → `ref.crop_alias`) |

## 1. Symptom

The cross-system reconciliation control `REC-001` failed on the first full run. For September,
the warehouse showed Bell Pepper shipping **81,195 kg** against only **57,096 kg** of sellable
(grade A+B) harvest, a ratio of **1.42**. The other three crops reconciled at 1.02–1.04,
which is explained by opening stock.

Shipping 42% more than was harvested is physically impossible, so one side of the
reconciliation was wrong.

```sql
SELECT * FROM dq.vw_crop_reconciliation;
```

| crop | sellable kg | shipped kg | ratio |
|---|---:|---:|---:|
| **Bell Pepper** | **57,095.9** | **81,195.0** | **1.422** |
| Mini Cucumber | 88,681.4 | 92,188.0 | 1.040 |
| Mini Sweet Pepper | 25,335.2 | 26,206.0 | 1.034 |
| Tomato on the Vine | 91,001.7 | 92,885.0 | 1.021 |

## 2. Investigation

**Which side is wrong?** Bell Pepper is grown in four zones of similar size (Z01–Z04), so they
should harvest similar volumes. Breaking warehouse harvest down by zone showed:

| zone | sellable kg in warehouse |
|---|---:|
| Z01 | 23,546 |
| Z02 | 22,770 |
| Z03 | **0**, missing entirely |
| Z04 | **10,780**, about half of its peers |

Harvest was understated, not shipments overstated.

**Where did the rows go?** No rows are silently dropped by the pipeline: every rejected row lands in
`err.quarantine` with the rule that rejected it. Grouping the quarantined harvest rows by
rule, zone and crop name showed 36 rows held by `HRV-004` *Crop name matches crop master*.

```sql
SELECT rule_id,
       JSON_VALUE(row_data, '$.zone_id')   AS zone_id,
       JSON_VALUE(row_data, '$.crop_name') AS crop_name,
       COUNT(*) AS n
FROM err.quarantine
WHERE source_table = 'stg.harvest'
GROUP BY rule_id, JSON_VALUE(row_data, '$.zone_id'), JSON_VALUE(row_data, '$.crop_name');
```

**Profiling the source.** Profiling `stg.harvest` (the untouched copy of the ERP export) for the
four Bell Pepper zones:

| zone | crop_name as entered | entered by | first seen | rows |
|---|---|---|---|---:|
| Z01 | `Bell Pepper` | gh-a.clerk1 | 2026-09-03 | 24 |
| Z02 | `BELL PEPPER ` (upper case, trailing space) | gh-a.clerk2 | 2026-09-03 | 24 |
| Z03 | `Bell Peppers` | gh-a.clerk1 | 2026-09-03 | 24 |
| Z04 | `Bell Pepper` | gh-a.clerk2 | 2026-09-03 | 12 |
| Z04 | `B. Pepper` | gh-a.clerk2 | 2026-09-17 | 12 |

Two findings:

1. **Z03** has used the plural `Bell Peppers` all month. **Z04** switched to the abbreviation
   `B. Pepper` mid-month (first harvest after 2026-09-15). That suggests a change in how the
   clerk enters data, or in an ERP pick-list, worth raising with the GH-A team.
2. **Z02's** `BELL PEPPER ` differs in case and has a trailing space, yet it was *not* quarantined.
   SQL Server's default collation (`SQL_Latin1_General_CP1_CI_AS`) is case-insensitive, and the
   `=` operator ignores trailing spaces. The database tolerated this variant without any rule
   needing to. This is worth knowing: under a case-sensitive collation, or in a tool like
   Python/pandas, Z02 would have failed too.

## 3. Root cause

The ERP harvest screen accepts free-text crop names. Two clerks entered Bell Pepper with
non-standard spellings, which cannot match the crop master. The pipeline correctly refused to
guess, quarantined the rows under `HRV-004`, and kept them out of reporting.

The reconciliation control then exposed the business impact: **33,469 kg of Bell Pepper harvest
(about 37% of the true total) was missing from reporting.**

## 4. Fix

Spelling variants are mapped in master data, not hardcoded in SQL, so the business can maintain
them and every change is version-controlled:

```csv
# config/crop_aliases.csv  →  ref.crop_alias
alias,crop_name,reason
Bell Peppers,Bell Pepper,Z03 clerk enters plural form - RCA 2026-10
B. Pepper,Bell Pepper,Z04 abbreviation from 2026-09-15 - RCA 2026-10
```

The harvest load resolves a crop by exact master name first, then by alias
(`COALESCE(ref.crop.crop_name, ref.crop_alias.crop_name)`).

## 5. Verification

After the fix (`make setup run`):

| check | before | after |
|---|---:|---:|
| `HRV-004` rows quarantined | 36 | **0** |
| `REC-001` crops failing | 1 | **0** |
| Harvest records in warehouse | 453 | **489** |
| Bell Pepper sellable kg | 57,096 | **90,565** |
| Bell Pepper shipped ÷ harvested | 1.422 | **0.897** |

All four zones now report similar volumes (Z03: 22,699 kg, Z04: 21,550 kg).

## 6. Follow-up finding: unknown SKU

After the fix, Bell Pepper reconciles at 0.90, slightly *below* its peers (1.02–1.04).
The gap comes from rule `SHP-004`: 20 shipment lines for SKU `BPO-5KG` (orange bell pepper, shipped
from 2026-09-18) are quarantined because the SKU was never added to the SKU master.

Those lines carry **15,200 kg**. Including them, Bell Pepper would reconcile at **1.06**, in line
with the other crops, which confirms the explanation.

**Action:** the product team adds `BPO-5KG` to the SKU master (`ref.sku`), then the quarantined
lines are reprocessed. This is a master data request, not a pipeline change, so it is tracked
separately.

## 7. Prevention

| Action | Owner | Type |
|---|---|---|
| `HRV-004` stays active: any new unknown crop name is quarantined and reported, never silently lost | Data Engineering | Detective (in place) |
| `REC-001` runs every load: catches volume gaps regardless of cause | Data Engineering | Detective (in place) |
| Replace free-text crop entry in the ERP harvest screen with a pick-list from the crop master | ERP / GH-A operations | Preventive |
| Add a "new SKU" step to the product launch checklist so the SKU master is updated before first shipment | Product / Warehouse | Preventive |
| Review `err.vw_quarantine_summary` in the weekly data triage meeting | Data Engineering + Business | Process |

## Lessons

- **Quarantine beats silent drops.** An inner join would have dropped these 36 rows with no trace.
  Quarantine kept the evidence, which made the RCA a 15-minute investigation.
- **Row-level rules alone were not enough.** `HRV-004` flagged the rows, but only the cross-system
  reconciliation (`REC-001`) showed the business impact: a 37% understatement of harvest.
- **Know your collation.** Case and trailing-space tolerance is a database setting, not a law of
  nature. Rules that depend on it should be documented.
