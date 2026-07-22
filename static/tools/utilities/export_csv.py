#!/usr/bin/env python3
"""Export a Speciedex SQLite table to RFC 4180 compatible CSV."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATABASE = Path("static/data/taxonomy/index.sqlite3")
DEFAULT_OUTPUT = Path("static/data/exports/taxa.csv")
SAFE_TABLES = {"taxa", "source_assertions", "synonyms", "conflicts", "revisions"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--table", choices=sorted(SAFE_TABLES), default="taxa")
    parser.add_argument("--rank", default="")
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--columns", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = [str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        raise SystemExit(f"SQLite table not found: {table}")
    return columns


def choose_columns(requested: str, available: Sequence[str]) -> list[str]:
    if not requested.strip():
        return list(available)
    selected = [part.strip() for part in requested.split(",") if part.strip()]
    missing = [column for column in selected if column not in available]
    if missing:
        raise SystemExit("Unknown columns: " + ", ".join(missing))
    return selected


def build_query(args: argparse.Namespace, selected: Sequence[str], available: set[str]) -> tuple[str, list[Any]]:
    projection = ", ".join(f'"{column}"' for column in selected)
    query = f'SELECT {projection} FROM "{args.table}"'
    clauses: list[str] = []
    parameters: list[Any] = []

    if args.rank:
        if "rank" not in available:
            raise SystemExit(f"Table {args.table} has no rank column.")
        clauses.append("rank = ?")
        parameters.append(args.rank.strip().casefold())

    statuses = sorted({value.strip().casefold() for value in args.status if value.strip()})
    if statuses:
        if "status" not in available:
            raise SystemExit(f"Table {args.table} has no status column.")
        clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
        parameters.extend(statuses)

    providers = sorted({value.strip().casefold() for value in args.provider if value.strip()})
    if providers:
        provider_column = "provider" if "provider" in available else "source_provider" if "source_provider" in available else ""
        if not provider_column:
            raise SystemExit(f"Table {args.table} has no provider column.")
        clauses.append(f"{provider_column} IN ({','.join('?' for _ in providers)})")
        parameters.extend(providers)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    order_columns = [name for name in ("speciedex_id", "provider", "provider_id", "canonical_name", "scientific_name", "id") if name in available]
    if order_columns:
        query += " ORDER BY " + ", ".join(order_columns)

    if args.limit:
        query += " LIMIT ?"
        parameters.append(args.limit)
        if args.offset:
            query += " OFFSET ?"
            parameters.append(args.offset)
    elif args.offset:
        query += " LIMIT -1 OFFSET ?"
        parameters.append(args.offset)
    return query, parameters


def normalize_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.database.is_file():
        raise SystemExit(f"SQLite database not found: {args.database}")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists: {args.output}. Use --overwrite.")
    if args.limit < 0 or args.offset < 0:
        raise SystemExit("--limit and --offset cannot be negative.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        available = table_columns(connection, args.table)
        selected = choose_columns(args.columns, available)
        query, parameters = build_query(args, selected, set(available))
        count = 0
        with args.output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, dialect="excel", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow(selected)
            for row in connection.execute(query, parameters):
                writer.writerow([normalize_cell(row[column]) for column in selected])
                count += 1
    finally:
        connection.close()

    print(json.dumps({"database": args.database.as_posix(), "table": args.table, "output": args.output.as_posix(), "records_exported": count, "columns": selected}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
