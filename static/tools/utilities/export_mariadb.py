#!/usr/bin/env python3
"""Export Speciedex SQLite data into MariaDB.

Credentials are read only from environment variables:

    SPECIEDEX_MARIADB_HOST
    SPECIEDEX_MARIADB_PORT
    SPECIEDEX_MARIADB_DATABASE
    SPECIEDEX_MARIADB_USERNAME
    SPECIEDEX_MARIADB_PASSWORD

The password is never accepted on the command line and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATABASE = Path("static/data/taxonomy/index.sqlite3")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy Speciedex SQLite tables into MariaDB."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--table",
        action="append",
        default=[],
        help="Table to export; repeat as needed. Defaults to all user tables.",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--mode",
        choices=("replace", "append"),
        default="replace",
    )
    parser.add_argument("--ssl-disabled", action="store_true")
    return parser.parse_args(argv)


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def quote_identifier(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise SystemExit(f"Unsafe SQL identifier: {name!r}")
    return f"`{name}`"


def sqlite_type_to_mariadb(
    declared_type: str,
) -> str:
    normalized = declared_type.strip().upper()
    if "INT" in normalized:
        return "BIGINT"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE"
    if "BLOB" in normalized:
        return "LONGBLOB"
    return "LONGTEXT"


def list_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def table_schema(
    connection: sqlite3.Connection,
    table: str,
) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or "TEXT"),
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5]),
        }
        for row in connection.execute(
            f'PRAGMA table_info("{table}")'
        )
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.source.is_file():
        raise SystemExit(f"SQLite source not found: {args.source}")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive.")

    try:
        import pymysql
    except ImportError as error:
        raise SystemExit(
            "MariaDB export requires PyMySQL: python -m pip install pymysql"
        ) from error

    host = required_environment("SPECIEDEX_MARIADB_HOST")
    database = required_environment("SPECIEDEX_MARIADB_DATABASE")
    username = required_environment("SPECIEDEX_MARIADB_USERNAME")
    password = required_environment("SPECIEDEX_MARIADB_PASSWORD")
    port = int(os.environ.get("SPECIEDEX_MARIADB_PORT", "3306"))

    source = sqlite3.connect(
        f"file:{args.source.resolve()}?mode=ro",
        uri=True,
    )
    source.row_factory = sqlite3.Row

    destination = pymysql.connect(
        host=host,
        port=port,
        user=username,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        ssl_disabled=args.ssl_disabled,
    )

    report: dict[str, int] = {}
    try:
        available_tables = list_tables(source)
        tables = args.table or available_tables
        unknown = sorted(set(tables) - set(available_tables))
        if unknown:
            raise SystemExit(
                "Unknown source tables: " + ", ".join(unknown)
            )

        with destination.cursor() as cursor:
            for table in tables:
                columns = table_schema(source, table)
                if not columns:
                    continue

                quoted_table = quote_identifier(table)

                if args.mode == "replace":
                    cursor.execute(f"DROP TABLE IF EXISTS {quoted_table}")

                definitions = []
                primary = []
                for column in columns:
                    name = quote_identifier(column["name"])
                    sql_type = sqlite_type_to_mariadb(column["type"])
                    nullable = " NOT NULL" if column["notnull"] else ""
                    definitions.append(f"{name} {sql_type}{nullable}")
                    if column["pk"]:
                        primary.append(name)

                if primary:
                    definitions.append(
                        "PRIMARY KEY (" + ", ".join(primary) + ")"
                    )

                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {quoted_table} "
                    f"({', '.join(definitions)}) "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )

                if args.mode == "replace":
                    cursor.execute(f"TRUNCATE TABLE {quoted_table}")

                column_names = [column["name"] for column in columns]
                quoted_columns = ", ".join(
                    quote_identifier(name)
                    for name in column_names
                )
                placeholders = ", ".join("%s" for _ in column_names)
                insert_sql = (
                    f"INSERT INTO {quoted_table} "
                    f"({quoted_columns}) VALUES ({placeholders})"
                )

                source_cursor = source.execute(
                    f'SELECT * FROM "{table}"'
                )
                count = 0
                while True:
                    rows = source_cursor.fetchmany(args.batch_size)
                    if not rows:
                        break
                    values = [
                        tuple(row[name] for name in column_names)
                        for row in rows
                    ]
                    cursor.executemany(insert_sql, values)
                    count += len(values)
                    destination.commit()
                report[table] = count
    except Exception:
        destination.rollback()
        raise
    finally:
        source.close()
        destination.close()

    print(json.dumps({
        "source": args.source.as_posix(),
        "destination_host": host,
        "destination_database": database,
        "tables": report,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
