#!/usr/bin/env python3
"""Verify Speciedex JSON, JSONL, manifests, statistics, and SQLite integrity."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATA_ROOT = Path("static/data")
DEFAULT_TAXONOMY_ROOT = DEFAULT_DATA_ROOT / "taxonomy"
DEFAULT_DATABASE = DEFAULT_TAXONOMY_ROOT / "index.sqlite3"


@dataclass(slots=True)
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_checked: int = 0
    json_records_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--taxonomy-root", type=Path, default=DEFAULT_TAXONOMY_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--check-checksums", action="store_true")
    parser.add_argument("--max-errors", type=int, default=100)
    return parser.parse_args(argv)


def load_json(path: Path, result: Result) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        result.errors.append(f"{path}: invalid JSON: {type(error).__name__}: {error}")
        return None
    result.files_checked += 1
    return value


def verify_json(root: Path, result: Result, max_errors: int) -> None:
    if not root.exists():
        result.errors.append(f"Missing data root: {root}")
        return
    for path in sorted(root.rglob("*.json")):
        load_json(path, result)
        if len(result.errors) >= max_errors:
            return


def jsonl_paths(root: Path) -> Iterable[Path]:
    for pattern in ("*.jsonl", "*.ndjson"):
        yield from sorted(root.rglob(pattern))


def verify_jsonl(root: Path, result: Result, full: bool, max_errors: int) -> None:
    for path in jsonl_paths(root):
        result.files_checked += 1
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                if not full and line_number > 100:
                    break
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError as error:
                    result.errors.append(f"{path}:{line_number}: invalid JSONL: {error}")
                    if len(result.errors) >= max_errors:
                        return
                    continue
                if not isinstance(value, dict):
                    result.errors.append(f"{path}:{line_number}: record root must be an object")
                    if len(result.errors) >= max_errors:
                        return
                    continue
                result.json_records_checked += 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_checksum(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("sha256:") else text


def verify_manifests(root: Path, result: Result, check_checksums: bool, max_errors: int) -> None:
    manifests = sorted({*root.rglob("*manifest*.json"), *root.rglob("checksums.json")})
    for path in manifests:
        value = load_json(path, result)
        if not isinstance(value, dict):
            continue
        references: list[tuple[str, str]] = []
        for key in ("payload_file", "file", "path", "volume_file", "block_file"):
            file_name = value.get(key)
            if isinstance(file_name, str) and file_name.strip():
                checksum = normalized_checksum(value.get("payload_sha256", value.get("sha256", value.get("checksum", ""))))
                references.append((file_name, checksum))
        if isinstance(value.get("files"), list):
            for entry in value["files"]:
                if isinstance(entry, dict):
                    file_name = str(entry.get("path", entry.get("file", ""))).strip()
                    if file_name:
                        references.append((file_name, normalized_checksum(entry.get("sha256", entry.get("checksum", "")))))

        for file_name, expected in references:
            candidate = path.parent / file_name
            if not candidate.exists():
                alternate = root / file_name
                candidate = alternate if alternate.exists() else candidate
            if not candidate.is_file():
                result.errors.append(f"{path}: referenced file not found: {file_name}")
            elif check_checksums and expected:
                actual = sha256_file(candidate)
                if actual.casefold() != expected.casefold():
                    result.errors.append(f"{path}: checksum mismatch for {file_name}: expected {expected}, received {actual}")
            if len(result.errors) >= max_errors:
                return


def verify_statistics(data_root: Path, result: Result) -> None:
    path = data_root / "statistics.json"
    if not path.is_file():
        result.errors.append(f"Missing statistics file: {path}")
        return
    value = load_json(path, result)
    if not isinstance(value, dict):
        return
    required = {"species", "subspecies", "genera", "families", "orders", "classes", "phyla", "kingdoms", "records_archived", "source_assertions", "rank_counts"}
    missing = sorted(required - set(value))
    if missing:
        result.errors.append(f"{path}: missing fields: {', '.join(missing)}")
    for key in required - {"rank_counts"}:
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            result.errors.append(f"{path}: invalid nonnegative integer {key}: {item!r}")
    if not isinstance(value.get("rank_counts"), dict):
        result.errors.append(f"{path}: rank_counts must be an object")


def verify_sqlite(path: Path, result: Result) -> None:
    if not path.exists():
        result.warnings.append(f"Derived SQLite index is absent: {path}")
        return
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        result.errors.append(f"{path}: cannot open SQLite database: {error}")
        return
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).casefold() != "ok":
            result.errors.append(f"{path}: integrity_check failed: {integrity!r}")
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "taxa" not in tables:
            result.errors.append(f"{path}: required taxa table is missing")
            return
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(taxa)")}
        missing = sorted({"speciedex_id", "canonical_name", "rank", "status"} - columns)
        if missing:
            result.errors.append(f"{path}: taxa table missing columns: {', '.join(missing)}")
        duplicates = int(connection.execute("SELECT COUNT(*) FROM (SELECT speciedex_id FROM taxa WHERE speciedex_id <> '' GROUP BY speciedex_id HAVING COUNT(*) > 1)").fetchone()[0])
        if duplicates:
            result.errors.append(f"{path}: duplicate speciedex_id groups: {duplicates}")
    except sqlite3.Error as error:
        result.errors.append(f"{path}: SQLite verification failed: {error}")
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_errors < 1:
        raise SystemExit("--max-errors must be positive.")
    result = Result()
    verify_json(args.data_root, result, args.max_errors)
    if len(result.errors) < args.max_errors:
        verify_jsonl(args.taxonomy_root, result, args.full, args.max_errors)
    if len(result.errors) < args.max_errors:
        verify_manifests(args.taxonomy_root, result, args.check_checksums, args.max_errors)
    verify_statistics(args.data_root, result)
    verify_sqlite(args.database, result)
    print(json.dumps({"ok": result.ok, "files_checked": result.files_checked, "json_records_checked": result.json_records_checked, "warnings": result.warnings, "errors": result.errors}, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
