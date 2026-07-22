#!/usr/bin/env python3
"""Create a portable or filtered SQLite export of the Speciedex index."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections.abc import Sequence
from pathlib import Path

DEFAULT_SOURCE = Path("static/data/taxonomy/index.sqlite3")
DEFAULT_OUTPUT = Path("static/data/exports/speciedex.sqlite3")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rank", default="")
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    return parser.parse_args(argv)


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def filtered_copy(source: Path, output: Path, rank: str, statuses: Sequence[str]) -> None:
    if output.exists():
        output.unlink()
    source_connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(output)
    try:
        source_connection.backup(destination_connection)
    finally:
        source_connection.close()
        destination_connection.close()

    connection = sqlite3.connect(output)
    try:
        if not table_exists(connection, "taxa"):
            raise SystemExit("Exported database has no taxa table.")

        clauses: list[str] = []
        parameters: list[str] = []
        if rank:
            clauses.append("rank = ?")
            parameters.append(rank.strip().casefold())

        normalized_statuses = sorted({value.strip().casefold() for value in statuses if value.strip()})
        if normalized_statuses:
            clauses.append(f"status IN ({','.join('?' for _ in normalized_statuses)})")
            parameters.extend(normalized_statuses)

        if clauses:
            connection.execute("CREATE TEMP TABLE keep_taxa(speciedex_id TEXT PRIMARY KEY)")
            connection.execute(
                "INSERT OR IGNORE INTO keep_taxa(speciedex_id) SELECT speciedex_id FROM taxa WHERE " + " AND ".join(clauses),
                parameters,
            )
            for table in ("source_assertions", "synonyms", "conflicts", "revisions"):
                if table_exists(connection, table):
                    columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
                    if "speciedex_id" in columns:
                        connection.execute(f'DELETE FROM "{table}" WHERE speciedex_id NOT IN (SELECT speciedex_id FROM keep_taxa)')
            connection.execute("DELETE FROM taxa WHERE speciedex_id NOT IN (SELECT speciedex_id FROM keep_taxa)")
            connection.execute("DROP TABLE keep_taxa")

        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.commit()
    finally:
        connection.close()


def counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        tables = [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.source.is_file():
        raise SystemExit(f"Source database not found: {args.source}")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists: {args.output}. Use --overwrite.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.rank or args.status:
        filtered_copy(args.source, args.output, args.rank, args.status)
    else:
        if args.output.exists():
            args.output.unlink()
        shutil.copy2(args.source, args.output)

    if args.vacuum:
        connection = sqlite3.connect(args.output)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    print(json.dumps({"source": args.source.as_posix(), "output": args.output.as_posix(), "bytes": args.output.stat().st_size, "tables": counts(args.output)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
