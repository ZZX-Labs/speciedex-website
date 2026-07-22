#!/usr/bin/env python3
"""Shared export helpers for Speciedex utilities."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATABASE = Path("static/data/taxonomy/index.sqlite3")
SAFE_TABLES = {
    "taxa",
    "source_assertions",
    "synonyms",
    "conflicts",
    "revisions",
}


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_output: Path,
) -> None:
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--table", choices=sorted(SAFE_TABLES), default="taxa")
    parser.add_argument("--rank", default="")
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--title", default="Speciedex Export")
    parser.add_argument("--overwrite", action="store_true")


def validate_common_arguments(args: argparse.Namespace) -> None:
    if not args.database.is_file():
        raise SystemExit(f"SQLite database not found: {args.database}")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(
            f"Output already exists: {args.output}. Use --overwrite."
        )
    if args.limit < 0 or args.offset < 0:
        raise SystemExit("--limit and --offset cannot be negative.")


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> list[str]:
    rows = connection.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall()
    columns = [str(row["name"]) for row in rows]
    if not columns:
        raise SystemExit(f"SQLite table not found: {table}")
    return columns


def build_query(
    table: str,
    columns: Sequence[str],
    args: argparse.Namespace,
) -> tuple[str, list[Any]]:
    available = set(columns)
    clauses: list[str] = []
    parameters: list[Any] = []

    if args.rank:
        if "rank" not in available:
            raise SystemExit(f"Table {table} has no rank column.")
        clauses.append("rank = ?")
        parameters.append(args.rank.strip().casefold())

    statuses = sorted({
        str(value).strip().casefold()
        for value in args.status
        if str(value).strip()
    })
    if statuses:
        if "status" not in available:
            raise SystemExit(f"Table {table} has no status column.")
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        parameters.extend(statuses)

    providers = sorted({
        str(value).strip().casefold()
        for value in args.provider
        if str(value).strip()
    })
    if providers:
        provider_column = (
            "provider" if "provider" in available
            else "source_provider" if "source_provider" in available
            else ""
        )
        if not provider_column:
            raise SystemExit(f"Table {table} has no provider column.")
        placeholders = ",".join("?" for _ in providers)
        clauses.append(f"{provider_column} IN ({placeholders})")
        parameters.extend(providers)

    query = f'SELECT * FROM "{table}"'
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    order = [
        key for key in (
            "speciedex_id",
            "canonical_name",
            "scientific_name",
            "provider",
            "provider_id",
            "id",
        )
        if key in available
    ]
    if order:
        query += " ORDER BY " + ", ".join(order)

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
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def iter_rows(
    connection: sqlite3.Connection,
    table: str,
    args: argparse.Namespace,
) -> Iterator[dict[str, Any]]:
    columns = table_columns(connection, table)
    query, parameters = build_query(table, columns, args)
    for row in connection.execute(query, parameters):
        yield {
            key: normalize_value(row[key])
            for key in row.keys()
        }


def preferred_fields(record: dict[str, Any]) -> list[tuple[str, Any]]:
    ordered_names = (
        "speciedex_id",
        "scientific_name",
        "canonical_name",
        "common_name",
        "rank",
        "status",
        "kingdom",
        "phylum",
        "class_name",
        "order_name",
        "family",
        "genus",
        "provider",
        "provider_id",
        "source_url",
        "source_modified",
        "retrieved_at",
    )
    seen: set[str] = set()
    output: list[tuple[str, Any]] = []
    for name in ordered_names:
        if name in record:
            output.append((name, record[name]))
            seen.add(name)
    for name in sorted(record):
        if name not in seen:
            output.append((name, record[name]))
    return output


def record_to_text(record: dict[str, Any]) -> str:
    return "\n".join(
        f"{name}: {value}"
        for name, value in preferred_fields(record)
    )


def record_to_html(record: dict[str, Any]) -> str:
    rows = "\n".join(
        "<tr><th>"
        + html.escape(name)
        + "</th><td>"
        + html.escape(str(value))
        + "</td></tr>"
        for name, value in preferred_fields(record)
    )
    return f"<table>{rows}</table>"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
