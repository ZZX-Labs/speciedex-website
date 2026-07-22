#!/usr/bin/env python3
"""Export Speciedex SQLite tables to deterministic JSON or JSONL."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATABASE = Path("static/data/taxonomy/index.sqlite3")
DEFAULT_OUTPUT = Path("static/data/exports/taxa.jsonl")
SAFE_TABLES = {"taxa", "source_assertions", "synonyms", "conflicts", "revisions"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--table", choices=sorted(SAFE_TABLES), default="taxa")
    parser.add_argument("--format", choices=("json", "jsonl"), default="jsonl")
    parser.add_argument("--rank", default="")
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def columns_for(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        raise SystemExit(f"SQLite table not found: {table}")
    return {str(row["name"]) for row in rows}


def build_query(args: argparse.Namespace, columns: set[str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []

    if args.rank:
        if "rank" not in columns:
            raise SystemExit(f"Table {args.table} has no rank column.")
        clauses.append("rank = ?")
        parameters.append(args.rank.strip().casefold())

    statuses = sorted({value.strip().casefold() for value in args.status if value.strip()})
    if statuses:
        if "status" not in columns:
            raise SystemExit(f"Table {args.table} has no status column.")
        clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
        parameters.extend(statuses)

    providers = sorted({value.strip().casefold() for value in args.provider if value.strip()})
    if providers:
        provider_column = "provider" if "provider" in columns else "source_provider" if "source_provider" in columns else ""
        if not provider_column:
            raise SystemExit(f"Table {args.table} has no provider column.")
        clauses.append(f"{provider_column} IN ({','.join('?' for _ in providers)})")
        parameters.extend(providers)

    query = f'SELECT * FROM "{args.table}"'
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    order_columns = [name for name in ("speciedex_id", "provider", "provider_id", "canonical_name", "scientific_name", "id") if name in columns]
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


def normalize_value(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes) else value


def iter_records(cursor: sqlite3.Cursor, batch_size: int) -> Iterator[dict[str, Any]]:
    while rows := cursor.fetchmany(batch_size):
        for row in rows:
            yield {key: normalize_value(row[key]) for key in row.keys()}


def write_jsonl(path: Path, records: Iterator[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def write_json(path: Path, records: Iterator[dict[str, Any]], pretty: bool) -> int:
    values = list(records)
    path.write_text(json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None, separators=None if pretty else (",", ":")) + "\n", encoding="utf-8")
    return len(values)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.database.is_file():
        raise SystemExit(f"SQLite database not found: {args.database}")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists: {args.output}. Use --overwrite.")
    if args.limit < 0 or args.offset < 0 or args.batch_size < 1:
        raise SystemExit("--limit/--offset must be nonnegative and --batch-size positive.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        query, parameters = build_query(args, columns_for(connection, args.table))
        records = iter_records(connection.execute(query, parameters), args.batch_size)
        count = write_jsonl(args.output, records) if args.format == "jsonl" else write_json(args.output, records, args.pretty)
    finally:
        connection.close()

    print(json.dumps({"database": args.database.as_posix(), "table": args.table, "output": args.output.as_posix(), "format": args.format, "records_exported": count}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
