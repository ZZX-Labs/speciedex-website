#!/usr/bin/env python3
"""
Speciedex taxonomy normalizer.

Reads JSON, JSONL, or NDJSON taxonomic source records and writes a canonical
Speciedex JSONL stream for the icon-generation pipeline.

Expected location:
    static/tools/icon-forge/normalize-taxonomy.py
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

SUPPORTED_SUFFIXES = {".json", ".jsonl", ".ndjson"}


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text)


def clean_rank(value: Any) -> str:
    return clean_text(value).lower().replace("-", "_").replace(" ", "_") or "unranked"


def normalize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            clean_text(key): normalize_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: clean_text(pair[0]),
            )
        }

    if isinstance(value, list):
        return [normalize_value(item) for item in value]

    if isinstance(value, str):
        return clean_text(value)

    return value


def normalize_lineage(
    raw: Any,
    scientific_name: str,
    taxon_rank: str,
) -> list[dict[str, str]]:
    lineage: list[dict[str, str]] = []

    if isinstance(raw, str):
        for name in (
            clean_text(part)
            for part in raw.split("|")
            if clean_text(part)
        ):
            lineage.append({
                "rank": "unranked",
                "name": name,
            })

    elif isinstance(raw, Mapping):
        for rank_name, taxon_name in raw.items():
            name = clean_text(taxon_name)
            if name:
                lineage.append({
                    "rank": clean_rank(rank_name),
                    "name": name,
                })

    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                name = clean_text(
                    item.get("name")
                    or item.get("scientific_name")
                )

                if not name:
                    continue

                row = {
                    "rank": clean_rank(item.get("rank")),
                    "name": name,
                }

                identifier = clean_text(item.get("id"))
                if identifier:
                    row["id"] = identifier

                lineage.append(row)

            elif (
                isinstance(item, (list, tuple))
                and len(item) >= 2
            ):
                lineage.append({
                    "rank": clean_rank(item[0]),
                    "name": clean_text(item[1]),
                })

            else:
                name = clean_text(item)
                if name:
                    lineage.append({
                        "rank": "unranked",
                        "name": name,
                    })

    if (
        not lineage
        or lineage[-1]["name"].casefold()
        != scientific_name.casefold()
    ):
        lineage.append({
            "rank": taxon_rank,
            "name": scientific_name,
        })

    return lineage


def normalize_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    scientific_name = clean_text(
        raw.get("scientific_name")
        or raw.get("canonical_name")
        or raw.get("name")
        or raw.get("taxon")
    )

    if not scientific_name:
        raise ValueError(
            "missing scientific_name, canonical_name, name, or taxon"
        )

    taxon_rank = clean_rank(raw.get("rank"))
    source = clean_text(
        raw.get("source")
        or raw.get("provider")
    ).lower()

    source_id = clean_text(
        raw.get("source_id")
        or raw.get("key")
        or raw.get("taxon_id")
    )

    identifier = clean_text(
        raw.get("id")
        or (
            f"{source}:{source_id}"
            if source and source_id
            else ""
        )
    )

    return {
        "id": identifier,
        "source": source,
        "source_id": source_id,
        "scientific_name": scientific_name,
        "canonical_name": clean_text(
            raw.get("canonical_name")
            or scientific_name
        ),
        "common_name": clean_text(raw.get("common_name")),
        "rank": taxon_rank,
        "status": clean_text(
            raw.get("status")
            or "accepted"
        ).lower(),
        "parent_id": clean_text(raw.get("parent_id")),
        "accepted_id": clean_text(raw.get("accepted_id")),
        "lineage": normalize_lineage(
            raw.get("lineage", []),
            scientific_name,
            taxon_rank,
        ),
        "traits": normalize_value(
            raw.get("traits", {})
        ),
    }


def iter_records(path: Path) -> Iterable[Mapping[str, Any]]:
    suffix = path.suffix.lower()

    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue

                payload = json.loads(line)

                if not isinstance(payload, Mapping):
                    raise ValueError(
                        f"{path}:{line_number}: record must be an object"
                    )

                yield payload

        return

    if suffix == ".json":
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )

        if isinstance(payload, Mapping):
            payload = (
                payload.get("taxa")
                or payload.get("records")
                or [payload]
            )

        if not isinstance(payload, list):
            raise ValueError(
                f"{path}: JSON root must be an object or array"
            )

        for item in payload:
            if not isinstance(item, Mapping):
                raise ValueError(
                    f"{path}: each record must be an object"
                )

            yield item

        return

    raise ValueError(
        f"unsupported input format: {path.suffix}"
    )


def collect_input_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]

    if not path.exists():
        raise FileNotFoundError(
            f"input path does not exist: {path}"
        )

    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and candidate.suffix.lower() in SUPPORTED_SUFFIXES
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize taxonomic records for Speciedex Icon Forge."
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Input file or directory containing JSON/JSONL/NDJSON records.",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Output normalized JSONL file.",
    )

    parser.add_argument(
        "--rejected",
        required=True,
        help="Output JSONL file for rejected records and errors.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    rejected_path = Path(args.rejected)

    files = collect_input_files(input_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rejected_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    accepted_count = 0
    rejected_count = 0

    with (
        output_path.open(
            "w",
            encoding="utf-8",
        ) as output_handle,
        rejected_path.open(
            "w",
            encoding="utf-8",
        ) as rejected_handle,
    ):
        for source_file in files:
            try:
                records = iter_records(source_file)

                for raw_record in records:
                    try:
                        normalized = normalize_record(raw_record)

                        output_handle.write(
                            json.dumps(
                                normalized,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                        )

                        accepted_count += 1

                    except Exception as exc:
                        rejected_handle.write(
                            json.dumps(
                                {
                                    "source_file": source_file.as_posix(),
                                    "error": str(exc),
                                    "record": raw_record,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

                        rejected_count += 1

            except Exception as exc:
                rejected_handle.write(
                    json.dumps(
                        {
                            "source_file": source_file.as_posix(),
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                rejected_count += 1

    print(
        f"normalized={accepted_count} "
        f"rejected={rejected_count}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
