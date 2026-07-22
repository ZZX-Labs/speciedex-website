#!/usr/bin/env python3
"""Export Speciedex records to PDF using ReportLab."""

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
    preferred_fields,
    validate_common_arguments,
)

DEFAULT_OUTPUT = Path("static/data/exports/speciedex.pdf")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Speciedex records to PDF."
    )
    add_common_arguments(parser, default_output=DEFAULT_OUTPUT)
    parser.add_argument("--page-size", choices=("letter", "a4"), default="letter")
    parser.add_argument("--records-per-document", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_common_arguments(args)
    ensure_parent(args.output)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise SystemExit(
            "PDF export requires ReportLab: python -m pip install reportlab"
        ) from error

    page_size = A4 if args.page_size == "a4" else letter
    styles = getSampleStyleSheet()
    story = [Paragraph(args.title, styles["Title"]), Spacer(1, 0.2 * inch)]

    connection = connect_readonly(args.database)
    count = 0
    try:
        for record in iter_rows(connection, args.table, args):
            heading = (
                str(record.get("scientific_name"))
                or str(record.get("canonical_name"))
                or str(record.get("speciedex_id"))
                or f"Record {count + 1}"
            )
            story.append(Paragraph(heading, styles["Heading2"]))
            rows = [
                [
                    Paragraph(str(name), styles["BodyText"]),
                    Paragraph(str(value), styles["BodyText"]),
                ]
                for name, value in preferred_fields(record)
            ]
            table = Table(rows, colWidths=[1.55 * inch, 5.45 * inch])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#999999")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.extend([table, Spacer(1, 0.18 * inch)])
            count += 1
            if args.records_per_document and count % args.records_per_document == 0:
                story.append(PageBreak())
    finally:
        connection.close()

    document = SimpleDocTemplate(
        str(args.output),
        pagesize=page_size,
        title=args.title,
        author="Speciedex.org",
        subject=f"Export of {args.table}",
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    document.build(story)

    print(json.dumps({
        "output": args.output.as_posix(),
        "records_exported": count,
        "format": "pdf",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
