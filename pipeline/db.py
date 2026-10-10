"""Shared database helpers: connection settings come from .env."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pyodbc
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SQL_DIR = ROOT / "sql"

load_dotenv(ROOT / ".env")

DATABASE = os.getenv("SQL_DATABASE", "GreenhouseDW")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", DATABASE):
    sys.exit(f"SQL_DATABASE '{DATABASE}' is not a plain identifier.")


def connect(database: str | None = None, autocommit: bool = False) -> pyodbc.Connection:
    password = os.getenv("MSSQL_SA_PASSWORD")
    if not password:
        sys.exit("MSSQL_SA_PASSWORD is not set. Copy .env.example to .env and fill it in.")
    conn_str = (
        f"DRIVER={{{os.getenv('ODBC_DRIVER', 'ODBC Driver 18 for SQL Server')}}};"
        f"SERVER={os.getenv('SQL_SERVER', 'localhost,1433')};"
        f"DATABASE={database or DATABASE};"
        f"UID={os.getenv('SQL_USER', 'sa')};PWD={password};"
        "Encrypt=yes;TrustServerCertificate=yes;"   # local dev container uses a self-signed cert
    )
    return pyodbc.connect(conn_str, autocommit=autocommit)


def run_sql_file(conn: pyodbc.Connection, path: Path) -> None:
    """Run a .sql file, splitting batches on GO lines like sqlcmd/SSMS do."""
    text = path.read_text(encoding="utf-8")
    batches = re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    cur = conn.cursor()
    for batch in batches:
        if batch.strip():
            cur.execute(batch)
            while cur.nextset():   # drain any extra result sets / row counts
                pass
    cur.close()
