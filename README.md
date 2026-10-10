# Greenhouse Data Quality & Pipeline Health Monitor

A SQL Server data pipeline for greenhouse operations data. It loads three source systems
(climate sensors, ERP harvest records, warehouse shipments), runs data quality controls
from source to target, quarantines bad rows for triage, and reports data quality and
pipeline health in Power BI.

The source data is synthetic and contains **19 deliberately planted data quality issues**
(see `data/planted_issues.json` after generating). The answer key lets you prove the
controls catch every one of them.

## Architecture

```
 Source files                 SQL Server: GreenhouseDW
 ─────────────                ───────────────────────────────────────────────────────────
 climate/*.csv     ─┐         ops.file_load      file audit: hash, row counts, status
 erp/*.csv         ─┼─ load ─▶ stg.*             raw landing, all text
 shipments/*.json  ─┘            │
                                 ▼ DQ rules (dq.rule)  ──▶ dq.result     metrics per run
                                 │                     ──▶ err.quarantine bad rows as JSON
                                 ▼
                              dw.dim_* / dw.fact_*  star schema  ──▶ Power BI
```

| Schema | Purpose |
|---|---|
| `ref` | Master data and config: crops, zones, SKUs, valid sensor ranges, crop aliases |
| `stg` | Raw landing. Every column is text so a bad value never breaks a load |
| `dq`  | Rule catalogue and per-run results (rows checked, rows failed, failure rate) |
| `err` | Quarantined rows with the rule that caught them |
| `dw`  | Typed star schema for reporting |
| `ops` | Pipeline runs and file-level audit |

## Controls at each hop

**File → staging** (`pipeline/load_staging.py`)
- Idempotent: a file whose SHA-256 was already loaded is skipped, so reruns are safe
- Schema check: a file missing an expected column is rejected whole
- Row-count reconciliation: rows in file must equal rows landed

**Staging → warehouse**: DQ rule catalogue *(next step)*

## Running it

The only prerequisite is **Docker Desktop**. SQL Server and the Python pipeline
(with the Microsoft ODBC driver) both run in containers, so it behaves the same on
Mac, Windows and Linux.

```bash
cp .env.example .env     # then change the password
make build               # build the Python image (first time only)
make all                 # generate data -> build database -> load staging
```

| Command | What it does |
|---|---|
| `make up` | Start SQL Server and wait until it is healthy |
| `make generate` | Create the synthetic source files in `data/` |
| `make setup` | Rebuild the database (drops and recreates tables) |
| `make load` | Land source files in `stg.*` |
| `make sql` | Open a SQL prompt on GreenhouseDW |
| `make down` / `make reset` | Stop SQL Server / delete it and its data |

Run `make load` a second time: every file shows as `skipped`. That is the idempotency control working.

Apple Silicon Macs: in Docker Desktop, turn on "Use Rosetta for x86_64/amd64 emulation" so the SQL Server image runs.

To browse the database from a GUI, connect VS Code (SQL Server extension) or any SQL client to
`localhost,1433`, user `sa`, with "Trust server certificate" enabled.

## Source data

| Source | Format | Volume | Notes |
|---|---|---|---|
| Climate sensors | CSV, one file per day | ~8,400 hourly readings, 12 zones | temp, humidity, CO₂, light |
| ERP harvest export | CSV, one monthly file | ~500 records | zone, crop, grade, kg |
| WMS shipments | Nested JSON, one file per day | ~360 lines | flattened to one row per line |

Shipments start from about two days of opening stock, so shipped kg can run slightly above
harvested kg for a crop. A gap of 40%+ is not normal. That is the RCA.

## Project structure

```
Dockerfile                   Python 3.12 + ODBC Driver 18 image for the pipeline
docker-compose.yml           SQL Server + pipeline containers
Makefile                     one-word commands for every step
generator/generate_data.py   synthetic data + planted issues
pipeline/db.py               connection + SQL file runner
pipeline/setup_db.py         build the database
pipeline/load_staging.py     file → staging loader with controls
sql/01_schemas.sql           schemas
sql/02_tables.sql            all tables
sql/03_seed_config.sql       sensor ranges + date dimension
```

## Progress

- [x] Data generator with planted issues
- [x] Database schema (ref / stg / dq / err / dw / ops)
- [x] Staging loader with file-level controls
- [ ] DQ rule catalogue + quarantine
- [ ] Staging → warehouse transforms
- [ ] Root cause analysis write-up
- [ ] Indexing and tuning (before/after)
- [ ] Backup and restore test
- [ ] Source-to-Target Mapping, data dictionary (AI-assisted), runbook
- [ ] Power BI dashboard
