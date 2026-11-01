#!/usr/bin/env python3
"""Create the items table from item.csv.

The table is dropped and recreated in a single transaction, so the script can
safely be run again.

Connection settings are read from the standard libpq environment variables
(PGUSER, PGPASSWORD, PGHOST, PGPORT, PGDATABASE). PGHOST defaults to
localhost, PGDATABASE to piscineds, and the password is prompted for when
PGPASSWORD is not set.
"""

import argparse
import csv
import getpass
import os
import sys
from pathlib import Path

import psycopg

DEFAULT_CSV = (Path(__file__).resolve().parent.parent
               / "subject" / "item" / "item.csv")
CHUNK_SIZE = 1 << 20

COLUMNS = ["product_id", "category_id", "category_code", "brand"]


CREATE_TABLE = """
CREATE TABLE items (
    product_id    INTEGER NOT NULL,
    category_id   BIGINT,           -- 19-digit ids, beyond INTEGER range
    category_code TEXT,
    brand         TEXT
)
"""


def connect() -> psycopg.Connection:
    password = os.environ.get("PGPASSWORD")
    if password is None and sys.stdin.isatty():
        password = getpass.getpass("Password: ")
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        dbname=os.environ.get("PGDATABASE", "piscineds"),
        password=password,
        autocommit=True,
    )


def load_items(conn: psycopg.Connection, path: Path) -> int:
    """(Re)create the items table and copy the file into it."""
    with open(path, "rb") as f:
        header = next(csv.reader([f.readline().decode("utf-8-sig")]), [])
        if header != COLUMNS:
            raise ValueError(f"unexpected header {header}, expected {COLUMNS}")
        f.seek(0)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS items")
            cur.execute(CREATE_TABLE)
            with cur.copy("COPY items FROM STDIN (FORMAT csv, HEADER true)") as copy:
                while chunk := f.read(CHUNK_SIZE):
                    copy.write(chunk)
            rows = cur.rowcount
            cur.execute("ANALYZE items")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the items table from item.csv.")
    parser.add_argument("csv", nargs="?", type=Path, default=DEFAULT_CSV,
                        help="CSV file to load (default: %(default)s)")
    args = parser.parse_args()
    try:
        with connect() as conn:
            rows = load_items(conn, args.csv)
    except (OSError, ValueError, psycopg.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"items: {rows} rows loaded")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
