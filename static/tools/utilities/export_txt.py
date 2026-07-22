#!/usr/bin/env python3
"""Export Speciedex records as deterministic plain text."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from export_common import (
    add_common_arguments,
    connect_readonly,
    ensure_parent,
    iter_rows,
    record_to_text,
    validate_common_arguments,
)

DEFAULT_OUTPUT = Path("static/data/exports/speciedex.txt")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Speciedex records to plain text."
    )
    add_common_arguments(parser, default_output=DEFAULT_OUTPUT)
    parser.add_argument(
        "--separator",
        default="\n\n" + ("=" * 78) + "\n\n",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_common_arguments(args)
    ensure_parent(args.output)

    connection = connect_readonly(args.database)
    count = 0
    try:
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(args.title.strip() + "\n")
            handle.write("=" * len(args.title.strip()) + "\n\n")
            first = True
            for record in iter_rows(connection, args.table, args):
                if not first:
                    handle.write(args.separator)
                handle.write(record_to_text(record))
                handle.write("\n")
                first = False
                count += 1
    finally:
        connection.close()

    print(json.dumps({
        "output": args.output.as_posix(),
        "records_exported": count,
        "format": "txt",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
