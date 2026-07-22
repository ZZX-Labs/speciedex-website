#!/usr/bin/env python3
"""Conservatively repair recoverable Speciedex archive problems."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_DATA_ROOT = Path("static/data")
DEFAULT_TAXONOMY_ROOT = DEFAULT_DATA_ROOT / "taxonomy"
DEFAULT_STAT_GRABBER = Path("static/tools/stat-grabber.py")
REQUIRED_DIRECTORIES = ("volumes", "provider-state", "rejected", "checkpoints")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--taxonomy-root", type=Path, default=DEFAULT_TAXONOMY_ROOT)
    parser.add_argument("--stat-grabber", type=Path, default=DEFAULT_STAT_GRABBER)
    parser.add_argument("--create-directories", action="store_true")
    parser.add_argument("--remove-sqlite-sidecars", action="store_true")
    parser.add_argument("--normalize-json", action="store_true")
    parser.add_argument("--truncate-damaged-jsonl-tail", action="store_true")
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--history-limit", type=int, default=2016)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def normalize_json(path: Path, dry_run: bool) -> tuple[bool, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        return False, f"invalid JSON: {type(error).__name__}: {error}"
    normalized = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    current = path.read_text(encoding="utf-8-sig")
    if current == normalized:
        return False, "already normalized"
    if not dry_run:
        atomic_write(path, normalized)
    return True, "normalized"


def truncate_tail(path: Path, dry_run: bool) -> tuple[bool, str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    last_nonempty = max((index for index, line in enumerate(lines) if line.strip()), default=-1)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index != last_nonempty:
                return False, f"damage begins before final record at line {index + 1}; manual repair required"
            repaired = "".join(lines[:index])
            if repaired and not repaired.endswith("\n"):
                repaired += "\n"
            if not dry_run:
                atomic_write(path, repaired)
            return True, f"truncated damaged final record at line {index + 1}"
        if not isinstance(value, dict):
            if index != last_nonempty:
                return False, f"non-object record before final line at {index + 1}; manual repair required"
            repaired = "".join(lines[:index])
            if repaired and not repaired.endswith("\n"):
                repaired += "\n"
            if not dry_run:
                atomic_write(path, repaired)
            return True, f"truncated non-object final record at line {index + 1}"
    return False, "valid"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.history_limit < 1:
        raise SystemExit("--history-limit must be positive.")

    actions: list[dict[str, Any]] = []
    failures: list[str] = []

    if args.create_directories:
        for name in REQUIRED_DIRECTORIES:
            path = args.taxonomy_root / name
            existed = path.is_dir()
            if not args.dry_run:
                path.mkdir(parents=True, exist_ok=True)
            actions.append({"action": "create_directory", "path": path.as_posix(), "changed": not existed})

    if args.remove_sqlite_sidecars and args.taxonomy_root.exists():
        for path in sorted(args.taxonomy_root.rglob("*")):
            if path.is_file() and path.name.endswith(SIDECAR_SUFFIXES):
                if not args.dry_run:
                    path.unlink()
                actions.append({"action": "remove_sqlite_sidecar", "path": path.as_posix(), "changed": True})

    if args.normalize_json and args.data_root.exists():
        for path in sorted(args.data_root.rglob("*.json")):
            changed, message = normalize_json(path, args.dry_run)
            actions.append({"action": "normalize_json", "path": path.as_posix(), "changed": changed, "message": message})
            if message.startswith("invalid JSON"):
                failures.append(f"{path}: {message}")

    if args.truncate_damaged_jsonl_tail and args.taxonomy_root.exists():
        for pattern in ("*.jsonl", "*.ndjson"):
            for path in sorted(args.taxonomy_root.rglob(pattern)):
                changed, message = truncate_tail(path, args.dry_run)
                actions.append({"action": "truncate_jsonl_tail", "path": path.as_posix(), "changed": changed, "message": message})
                if "manual repair required" in message:
                    failures.append(f"{path}: {message}")

    if args.reindex:
        if not args.stat_grabber.is_file():
            failures.append(f"stat-grabber.py not found: {args.stat_grabber}")
        elif args.dry_run:
            actions.append({"action": "reindex", "changed": False, "message": "dry run"})
        else:
            completed = subprocess.run([sys.executable, str(args.stat_grabber), "reindex", "--history-limit", str(args.history_limit)], check=False)
            actions.append({"action": "reindex", "changed": completed.returncode == 0, "returncode": completed.returncode})
            if completed.returncode != 0:
                failures.append(f"reindex failed with exit code {completed.returncode}")

    print(json.dumps({"ok": not failures, "dry_run": args.dry_run, "actions": actions, "failures": failures}, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
