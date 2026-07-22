#!/usr/bin/env python3
"""Create ZIP, 7z, RAR, TAR, TAR.GZ, or TAR.XZ archives."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

SUPPORTED_FORMATS = (
    "zip",
    "7z",
    "rar",
    "tar",
    "tar.gz",
    "tar.xz",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive Speciedex files and directories."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=SUPPORTED_FORMATS, required=True)
    parser.add_argument("--base-directory", type=Path, default=Path.cwd())
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--compression-level", type=int, default=9)
    return parser.parse_args(argv)


def archive_name(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file():
            yield candidate


def create_zip(args: argparse.Namespace) -> int:
    count = 0
    compression = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(
        args.output,
        "w",
        compression=compression,
        compresslevel=args.compression_level,
    ) as archive:
        for source in args.inputs:
            if source.is_file():
                archive.write(
                    source,
                    arcname=archive_name(source, args.base_directory),
                )
                count += 1
            else:
                for file_path in iter_files(source):
                    archive.write(
                        file_path,
                        arcname=archive_name(
                            file_path,
                            args.base_directory,
                        ),
                    )
                    count += 1
    return count


def create_tar(args: argparse.Namespace) -> int:
    mode = {
        "tar": "w",
        "tar.gz": "w:gz",
        "tar.xz": "w:xz",
    }[args.format]
    count = 0
    with tarfile.open(args.output, mode) as archive:
        for source in args.inputs:
            archive.add(
                source,
                arcname=archive_name(source, args.base_directory),
                recursive=True,
            )
            count += sum(1 for _ in iter_files(source))
    return count


def create_external(args: argparse.Namespace) -> int:
    if args.format == "7z":
        executable = shutil.which("7z") or shutil.which("7zz")
        if not executable:
            raise SystemExit(
                "7z export requires 7z or 7zz in PATH."
            )
        command = [
            executable,
            "a",
            f"-mx={args.compression_level}",
            str(args.output),
            *[str(path) for path in args.inputs],
        ]
    else:
        executable = shutil.which("rar")
        if not executable:
            raise SystemExit(
                "RAR export requires the proprietary rar executable in PATH."
            )
        command = [
            executable,
            "a",
            f"-m{min(max(args.compression_level, 0), 5)}",
            str(args.output),
            *[str(path) for path in args.inputs],
        ]

    completed = subprocess.run(
        command,
        cwd=args.base_directory,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"{args.format} archive creation failed:\n"
            + completed.stderr.strip()
        )
    return sum(
        1
        for source in args.inputs
        for _ in iter_files(source)
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    missing = [
        path
        for path in args.inputs
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing input paths: "
            + ", ".join(str(path) for path in missing)
        )

    if args.output.exists():
        if not args.overwrite:
            raise SystemExit(
                f"Output already exists: {args.output}. Use --overwrite."
            )
        args.output.unlink()

    if not 0 <= args.compression_level <= 9:
        raise SystemExit("--compression-level must be between 0 and 9.")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "zip":
        count = create_zip(args)
    elif args.format in {"tar", "tar.gz", "tar.xz"}:
        count = create_tar(args)
    else:
        count = create_external(args)

    print(json.dumps({
        "output": args.output.as_posix(),
        "format": args.format,
        "files_archived": count,
        "bytes": args.output.stat().st_size,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
