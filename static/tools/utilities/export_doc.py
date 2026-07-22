#!/usr/bin/env python3
"""Export Speciedex records to legacy Microsoft Word DOC.

The exporter first generates DOCX and then uses LibreOffice in headless mode
to convert the file to the legacy binary DOC format.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from export_docx import main as export_docx_main

DEFAULT_OUTPUT = Path("static/data/exports/speciedex.doc")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    known, remaining = parser.parse_known_args(argv)
    known.remaining = remaining
    return known


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    executable = (
        shutil.which("libreoffice")
        or shutil.which("soffice")
    )
    if not executable:
        raise SystemExit(
            "Legacy DOC export requires LibreOffice or soffice in PATH."
        )

    if args.output.exists() and not args.overwrite:
        raise SystemExit(
            f"Output already exists: {args.output}. Use --overwrite."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="speciedex-doc-") as directory:
        temporary_directory = Path(directory)
        docx_path = temporary_directory / "speciedex.docx"

        docx_argv = [
            *args.remaining,
            "--output",
            str(docx_path),
            "--overwrite",
        ]
        result = export_docx_main(docx_argv)
        if result:
            return result

        completed = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "doc:MS Word 97",
                "--outdir",
                str(temporary_directory),
                str(docx_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "LibreOffice conversion failed:\n"
                + completed.stderr.strip()
            )

        converted = temporary_directory / "speciedex.doc"
        if not converted.is_file():
            raise SystemExit(
                "LibreOffice did not create the expected DOC output."
            )

        shutil.copy2(converted, args.output)

    print(f"Created {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
