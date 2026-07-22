#!/usr/bin/env python3
"""Export Speciedex records to EPUB 3."""

from __future__ import annotations

import argparse
import html
import json
from collections.abc import Sequence
from pathlib import Path

from export_common import (
    add_common_arguments,
    connect_readonly,
    ensure_parent,
    iter_rows,
    preferred_fields,
    validate_common_arguments,
)

DEFAULT_OUTPUT = Path("static/data/exports/speciedex.epub")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Speciedex records to EPUB."
    )
    add_common_arguments(parser, default_output=DEFAULT_OUTPUT)
    parser.add_argument("--records-per-chapter", type=int, default=100)
    parser.add_argument("--language", default="en")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_common_arguments(args)
    ensure_parent(args.output)

    if args.records_per_chapter < 1:
        raise SystemExit("--records-per-chapter must be positive.")

    try:
        from ebooklib import epub
    except ImportError as error:
        raise SystemExit(
            "EPUB export requires EbookLib: python -m pip install EbookLib"
        ) from error

    book = epub.EpubBook()
    book.set_identifier("speciedex-export")
    book.set_title(args.title)
    book.set_language(args.language)
    book.add_author("Speciedex.org")

    css = """
    body { font-family: sans-serif; line-height: 1.45; }
    table { border-collapse: collapse; width: 100%; margin-bottom: 1.5em; }
    th, td { border: 1px solid #777; padding: 0.35em; vertical-align: top; }
    th { width: 28%; text-align: left; background: #eee; }
    """
    style_item = epub.EpubItem(
        uid="style",
        file_name="style/speciedex.css",
        media_type="text/css",
        content=css,
    )
    book.add_item(style_item)

    connection = connect_readonly(args.database)
    chapters = []
    chapter_records: list[str] = []
    count = 0
    chapter_number = 1

    def flush_chapter() -> None:
        nonlocal chapter_records, chapter_number
        if not chapter_records:
            return
        chapter = epub.EpubHtml(
            title=f"Records {((chapter_number - 1) * args.records_per_chapter) + 1}",
            file_name=f"chapter-{chapter_number:05d}.xhtml",
            lang=args.language,
        )
        chapter.content = (
            "<html><head><title>"
            + html.escape(args.title)
            + "</title></head><body>"
            + "".join(chapter_records)
            + "</body></html>"
        )
        chapter.add_item(style_item)
        book.add_item(chapter)
        chapters.append(chapter)
        chapter_records = []
        chapter_number += 1

    try:
        for record in iter_rows(connection, args.table, args):
            heading = (
                str(record.get("scientific_name"))
                or str(record.get("canonical_name"))
                or str(record.get("speciedex_id"))
                or f"Record {count + 1}"
            )
            rows = "".join(
                "<tr><th>"
                + html.escape(str(name))
                + "</th><td>"
                + html.escape(str(value))
                + "</td></tr>"
                for name, value in preferred_fields(record)
            )
            chapter_records.append(
                f"<section><h2>{html.escape(heading)}</h2>"
                f"<table>{rows}</table></section>"
            )
            count += 1
            if count % args.records_per_chapter == 0:
                flush_chapter()
        flush_chapter()
    finally:
        connection.close()

    book.toc = tuple(chapters)
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(args.output), book, {})

    print(json.dumps({
        "output": args.output.as_posix(),
        "records_exported": count,
        "chapters": len(chapters),
        "format": "epub",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
