#!/usr/bin/env python3
"""Create one table per CSV file found in the customer folder.

customer/<name>.csv is loaded into a table named <name>, e.g.
data_2022_oct.csv -> data_2022_oct. Each file is loaded in its own
transaction: a bad file is reported without affecting the others, and every
table is dropped and recreated, so the script can safely be run again.

Connection settings are read from the standard libpq environment variables
(PGUSER, PGPASSWORD, PGHOST, PGPORT, PGDATABASE). PGHOST defaults to
localhost, PGDATABASE to piscineds, and the password is prompted for when
PGPASSWORD is not set.
"""

import argparse
import csv
import getpass
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "subject" / "customer"
CHUNK_SIZE = 1 << 20
TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")

COLUMNS = ["event_time", "event_type", "product_id",
           "price", "user_id", "user_session"]


CREATE_EVENT_TYPE = """
DO $$
BEGIN
    CREATE TYPE event_type_enum AS ENUM
        ('view', 'cart', 'remove_from_cart', 'purchase');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$
"""

CREATE_TABLE = """
CREATE TABLE {table} (
    event_time   TIMESTAMPTZ     NOT NULL,
    event_type   event_type_enum NOT NULL,
    product_id   INTEGER         NOT NULL,
    price        NUMERIC(10, 2)  NOT NULL,
    user_id      BIGINT          NOT NULL,
    user_session UUID
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


def load_csv(conn: psycopg.Connection, path: Path) -> tuple[str, int]:
    """(Re)create the table named after `path` and copy the file into it."""
    table = path.stem
    if not TABLE_NAME.fullmatch(table):
        raise ValueError(f"'{table}' is not a valid table name")
    with open(path, "rb") as f:
        header = next(csv.reader([f.readline().decode("utf-8-sig")]), [])
        if header != COLUMNS:
            raise ValueError(f"unexpected header {header}, expected {COLUMNS}")
        f.seek(0)
        ident = sql.Identifier(table)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(CREATE_EVENT_TYPE)
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
            cur.execute(sql.SQL(CREATE_TABLE).format(table=ident))
            copy_sql = sql.SQL("COPY {} FROM STDIN (FORMAT csv, HEADER true)")
            with cur.copy(copy_sql.format(ident)) as copy:
                while chunk := f.read(CHUNK_SIZE):
                    copy.write(chunk)
            rows = cur.rowcount
            cur.execute(sql.SQL("ANALYZE {}").format(ident))
    return table, rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create one table per CSV file of the customer folder.")
    parser.add_argument("customer_dir", nargs="?", type=Path,
                        default=DEFAULT_DIR,
                        help="folder holding the CSV files "
                             "(default: %(default)s)")
    args = parser.parse_args()
    csv_files = sorted(args.customer_dir.glob("*.csv"))
    if not csv_files:
        print(f"error: no CSV file in {args.customer_dir}", file=sys.stderr)
        return 1
    failures = 0
    try:
        with connect() as conn:
            for path in csv_files:
                try:
                    table, rows = load_csv(conn, path)
                except (OSError, ValueError, psycopg.Error) as e:
                    failures += 1
                    print(f"error: {path.name}: {e}", file=sys.stderr)
                else:
                    print(f"{table}: {rows} rows loaded")
    except psycopg.Error as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
