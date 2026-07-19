#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/revision_writer.py

Append-only revision journal writer for the Speciedex taxonomic archive.

This module owns:

- provider assertion revision events,
- deterministic revision volume filenames,
- append-only JSONL revision journals,
- automatic rollover by record count or file size,
- SHA-256 checksums for sealed revision volumes,
- manifest metadata,
- interrupted-write recovery,
- revision verification,
- ordered revision iteration,
- revision statistics.

RevisionWriter does not determine whether a revision is required. The archive
compares provider assertions and passes completed revision events to this
writer.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


REVISION_SCHEMA_VERSION = 1

DEFAULT_PREFIX = "revisions"
DEFAULT_RECORD_TARGET = 100_000
DEFAULT_TARGET_BYTES = 48 * 1024 * 1024
DEFAULT_MAXIMUM_BYTES = 90 * 1024 * 1024
DEFAULT_GITHUB_FAILURE_BYTES = 95 * 1024 * 1024

REVISION_FILENAME_PATTERN = re.compile(
    r"^(?P<prefix>[a-z0-9_-]+)-"
    r"(?P<number>[0-9]{6})\.jsonl$"
)


class RevisionWriterError(RuntimeError):
    """Raised when a revision-journal operation cannot complete safely."""


@dataclass(slots=True)
class RevisionAppendResult:
    """Result of appending one revision event."""

    revision_file: str
    line_number: int
    size_bytes: int
    record_bytes: int
    sealed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_file": self.revision_file,
            "line_number": self.line_number,
            "size_bytes": self.size_bytes,
            "record_bytes": self.record_bytes,
            "sealed": self.sealed,
        }


@dataclass(slots=True)
class RevisionVerification:
    """Verification result for one revision volume."""

    file: str
    exists: bool
    sealed: bool
    expected_size: int
    actual_size: int
    expected_records: int
    actual_records: int
    expected_sha256: str | None
    actual_sha256: str | None
    valid_jsonl: bool
    errors: list[str]

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "exists": self.exists,
            "sealed": self.sealed,
            "expected_size": self.expected_size,
            "actual_size": self.actual_size,
            "expected_records": self.expected_records,
            "actual_records": self.actual_records,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "valid_jsonl": self.valid_jsonl,
            "valid": self.valid,
            "errors": list(self.errors),
        }


def utc_now() -> str:
    """Return the current UTC timestamp in stable ISO-8601 form."""

    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_space(value: Any) -> str:
    """Collapse surrounding and repeated whitespace."""

    return " ".join(
        str(value if value is not None else "")
        .strip()
        .split()
    )


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def atomic_write_json(
    path: Path,
    value: Any,
) -> None:
    """Atomically write formatted UTF-8 JSON."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)

        temporary.replace(path)

    finally:
        if (
            temporary is not None
            and temporary.exists()
        ):
            temporary.unlink(
                missing_ok=True
            )


class RevisionWriter:
    """
    Durable append-only JSONL revision journal.

    The supplied archive manifest is updated in place. Revision metadata is
    stored under the manifest's ``revision_journal`` object.
    """

    def __init__(
        self,
        *,
        root: Path,
        manifest: dict[str, Any],
        manifest_path: Path | None = None,
        record_target: int = DEFAULT_RECORD_TARGET,
        target_bytes: int = DEFAULT_TARGET_BYTES,
        maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
        prefix: str = DEFAULT_PREFIX,
        github_failure_bytes: int = DEFAULT_GITHUB_FAILURE_BYTES,
        fsync_writes: bool = True,
        persist_manifest: bool = True,
    ) -> None:
        self.root = Path(root)
        self.revisions_root = self.root / "revisions"
        self.manifest = manifest

        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else self.root / "manifest.json"
        )

        self.record_target = int(record_target)
        self.target_bytes = int(target_bytes)
        self.maximum_bytes = int(maximum_bytes)
        self.github_failure_bytes = int(
            github_failure_bytes
        )

        self.prefix = normalize_space(
            prefix
        ).casefold()

        self.fsync_writes = bool(
            fsync_writes
        )

        self.persist_manifest = bool(
            persist_manifest
        )

        self._validate_configuration()

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.revisions_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._repair_manifest_defaults()
        self.recover()

    @property
    def journal(self) -> dict[str, Any]:
        """Return the revision-journal manifest object."""

        value = self.manifest.get(
            "revision_journal"
        )

        if not isinstance(value, dict):
            value = {}
            self.manifest[
                "revision_journal"
            ] = value

        return value

    def _validate_configuration(self) -> None:
        """Validate writer configuration."""

        if self.record_target < 1:
            raise ValueError(
                "record_target must be positive."
            )

        if self.target_bytes < 1:
            raise ValueError(
                "target_bytes must be positive."
            )

        if self.maximum_bytes < 1:
            raise ValueError(
                "maximum_bytes must be positive."
            )

        if (
            self.target_bytes
            >= self.maximum_bytes
        ):
            raise ValueError(
                "target_bytes must be below "
                "maximum_bytes."
            )

        if self.github_failure_bytes < 1:
            raise ValueError(
                "github_failure_bytes must be positive."
            )

        if (
            self.maximum_bytes
            >= self.github_failure_bytes
        ):
            raise ValueError(
                "maximum_bytes must remain below "
                "github_failure_bytes."
            )

        if not self.prefix:
            raise ValueError(
                "Revision prefix cannot be empty."
            )

        if not re.fullmatch(
            r"[a-z0-9_-]+",
            self.prefix,
        ):
            raise ValueError(
                "Revision prefix may contain only "
                "lowercase letters, digits, hyphens, "
                "and underscores."
            )

        if not isinstance(
            self.manifest,
            dict,
        ):
            raise TypeError(
                "manifest must be a dictionary."
            )

    def _repair_manifest_defaults(self) -> None:
        """Add required revision metadata without discarding state."""

        journal = self.journal

        defaults: dict[str, Any] = {
            "schema_version": (
                REVISION_SCHEMA_VERSION
            ),
            "record_format": "jsonl",
            "record_target": self.record_target,
            "target_bytes": self.target_bytes,
            "maximum_bytes": self.maximum_bytes,
            "total_records": int(
                self.manifest.get(
                    "total_revisions",
                    0,
                )
                or 0
            ),
            "volumes": [],
            "active_volume": None,
            "generated_at": utc_now(),
        }

        for key, value in defaults.items():
            if key not in journal:
                journal[key] = value

        if not isinstance(
            journal.get("volumes"),
            list,
        ):
            journal["volumes"] = []

        journal["schema_version"] = (
            REVISION_SCHEMA_VERSION
        )
        journal["record_format"] = "jsonl"
        journal["record_target"] = (
            self.record_target
        )
        journal["target_bytes"] = (
            self.target_bytes
        )
        journal["maximum_bytes"] = (
            self.maximum_bytes
        )

        self.manifest.setdefault(
            "total_revisions",
            int(
                journal.get(
                    "total_records",
                    0,
                )
                or 0
            ),
        )

    def save_manifest(self) -> None:
        """Persist the shared archive manifest."""

        timestamp = utc_now()

        self.manifest[
            "generated_at"
        ] = timestamp

        self.journal[
            "generated_at"
        ] = timestamp

        if self.persist_manifest:
            atomic_write_json(
                self.manifest_path,
                self.manifest,
            )

    def append(
        self,
        event: Mapping[str, Any],
    ) -> RevisionAppendResult:
        """Append one revision event to the active journal volume."""

        if not isinstance(event, Mapping):
            raise TypeError(
                "Revision events must be mapping objects."
            )

        normalized_event = (
            self._normalize_event(event)
        )

        encoded = json.dumps(
            normalized_event,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        line = (encoded + "\n").encode(
            "utf-8"
        )

        record_bytes = len(line)

        if record_bytes > self.maximum_bytes:
            raise RevisionWriterError(
                "A single revision event exceeds "
                "maximum_bytes."
            )

        entry = self.active_volume()
        path = self.root / str(entry["file"])

        current_size = (
            path.stat().st_size
            if path.exists()
            else 0
        )

        current_records = int(
            entry.get(
                "record_count",
                0,
            )
            or 0
        )

        must_roll = (
            current_records > 0
            and (
                current_records
                >= self.record_target
                or current_size
                + record_bytes
                > self.maximum_bytes
            )
        )

        if must_roll:
            self.seal(entry)

            entry = self.active_volume()
            path = (
                self.root
                / str(entry["file"])
            )

            current_size = (
                path.stat().st_size
                if path.exists()
                else 0
            )

            current_records = int(
                entry.get(
                    "record_count",
                    0,
                )
                or 0
            )

        line_number = (
            current_records + 1
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            with path.open("ab") as handle:
                handle.write(line)
                handle.flush()

                if self.fsync_writes:
                    os.fsync(
                        handle.fileno()
                    )

        except OSError as error:
            raise RevisionWriterError(
                "Unable to append revision event to "
                f"{entry['file']}: {error}"
            ) from error

        actual_size = path.stat().st_size
        expected_size = (
            current_size + record_bytes
        )

        if actual_size != expected_size:
            raise RevisionWriterError(
                "Revision volume size mismatch after "
                f"append: expected={expected_size}, "
                f"actual={actual_size}."
            )

        entry["record_count"] = (
            line_number
        )
        entry["size_bytes"] = (
            actual_size
        )
        entry["sha256"] = None

        total_records = (
            int(
                self.journal.get(
                    "total_records",
                    0,
                )
                or 0
            )
            + 1
        )

        self.journal[
            "total_records"
        ] = total_records

        self.manifest[
            "total_revisions"
        ] = total_records

        sealed = False

        if (
            line_number
            >= self.record_target
            or actual_size
            >= self.target_bytes
        ):
            self.seal(entry)
            sealed = True
        else:
            self.save_manifest()

        return RevisionAppendResult(
            revision_file=str(
                entry["file"]
            ),
            line_number=line_number,
            size_bytes=actual_size,
            record_bytes=record_bytes,
            sealed=sealed,
        )

    def append_many(
        self,
        events: Iterable[
            Mapping[str, Any]
        ],
    ) -> list[RevisionAppendResult]:
        """Append multiple revision events in order."""

        return [
            self.append(event)
            for event in events
        ]

    def active_volume(
        self,
    ) -> dict[str, Any]:
        """Return or create the active revision volume."""

        active_name = normalize_space(
            self.journal.get(
                "active_volume"
            )
        )

        if active_name:
            entry = self._entry_by_file(
                active_name
            )

            if (
                entry is not None
                and not bool(
                    entry.get("sealed")
                )
            ):
                path = (
                    self.root
                    / active_name
                )

                path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                path.touch(
                    exist_ok=True
                )

                return entry

            self.journal[
                "active_volume"
            ] = None

        entry = self._new_volume_entry()

        self.journal[
            "volumes"
        ].append(entry)

        self.journal[
            "active_volume"
        ] = entry["file"]

        path = (
            self.root
            / str(entry["file"])
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.touch(
            exist_ok=True
        )

        self.save_manifest()

        return entry

    def seal(
        self,
        entry: Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Seal one revision volume."""

        target = self._resolve_entry(
            entry
        )

        if bool(target.get("sealed")):
            return target

        relative_file = normalize_space(
            target.get("file")
        )

        if not relative_file:
            raise RevisionWriterError(
                "Cannot seal a revision volume "
                "without a file."
            )

        path = (
            self.root
            / relative_file
        )

        if not path.is_file():
            raise RevisionWriterError(
                "Cannot seal missing revision volume: "
                f"{relative_file}."
            )

        size_bytes = path.stat().st_size

        if (
            size_bytes
            >= self.github_failure_bytes
        ):
            raise RevisionWriterError(
                "Revision volume exceeds the "
                "GitHub-safe file threshold: "
                f"{relative_file}; "
                f"size={size_bytes}; "
                f"threshold="
                f"{self.github_failure_bytes}."
            )

        target["size_bytes"] = (
            size_bytes
        )
        target["record_count"] = (
            self._count_nonempty_lines(path)
        )
        target["sha256"] = (
            file_sha256(path)
        )
        target["sealed"] = True
        target["sealed_at"] = utc_now()

        if (
            normalize_space(
                self.journal.get(
                    "active_volume"
                )
            )
            == relative_file
        ):
            self.journal[
                "active_volume"
            ] = None

        self.save_manifest()

        return target

    def seal_active(
        self,
    ) -> dict[str, Any] | None:
        """Seal the active revision volume when nonempty."""

        active_name = normalize_space(
            self.journal.get(
                "active_volume"
            )
        )

        if not active_name:
            return None

        entry = self._entry_by_file(
            active_name
        )

        if entry is None:
            self.journal[
                "active_volume"
            ] = None
            self.save_manifest()
            return None

        path = self.root / active_name

        if (
            not path.exists()
            or path.stat().st_size == 0
        ):
            return entry

        return self.seal(entry)

    def recover(self) -> None:
        """
        Recover revision metadata after an interrupted process.

        Unknown matching files are registered. Unsealed metadata is refreshed.
        If multiple nonempty unsealed volumes exist, all except the latest are
        sealed.
        """

        entries = self._volume_entries()

        for path in self._discover_volumes():
            relative_file = (
                path.relative_to(
                    self.root
                ).as_posix()
            )

            if (
                self._entry_by_file(
                    relative_file
                )
                is not None
            ):
                continue

            entries.append(
                {
                    "file": relative_file,
                    "record_count": (
                        self._count_nonempty_lines(
                            path
                        )
                    ),
                    "size_bytes": (
                        path.stat().st_size
                    ),
                    "sha256": None,
                    "sealed": False,
                    "created_at": utc_now(),
                    "sealed_at": None,
                }
            )

        self.journal["volumes"] = sorted(
            entries,
            key=self._entry_number,
        )

        unsealed = [
            entry
            for entry
            in self._volume_entries()
            if not bool(
                entry.get("sealed")
            )
        ]

        for entry in unsealed:
            path = (
                self.root
                / str(entry["file"])
            )

            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            path.touch(
                exist_ok=True
            )

            entry["size_bytes"] = (
                path.stat().st_size
            )
            entry["record_count"] = (
                self._count_nonempty_lines(
                    path
                )
            )
            entry["sha256"] = None
            entry["sealed_at"] = None

        nonempty = [
            entry
            for entry in unsealed
            if int(
                entry.get(
                    "record_count",
                    0,
                )
                or 0
            )
            > 0
        ]

        if len(nonempty) > 1:
            nonempty.sort(
                key=self._entry_number
            )

            for entry in nonempty[:-1]:
                self.seal(entry)

            self.journal[
                "active_volume"
            ] = nonempty[-1]["file"]

        elif len(nonempty) == 1:
            self.journal[
                "active_volume"
            ] = nonempty[0]["file"]

        elif unsealed:
            unsealed.sort(
                key=self._entry_number
            )

            self.journal[
                "active_volume"
            ] = unsealed[-1]["file"]

        else:
            self.journal[
                "active_volume"
            ] = None

        self._recalculate_total()
        self.save_manifest()

    def verify(
        self,
        *,
        validate_json: bool = True,
    ) -> list[str]:
        """Verify all revision volumes and manifest totals."""

        errors: list[str] = []
        total_records = 0
        seen_files: set[str] = set()

        active_file = normalize_space(
            self.journal.get(
                "active_volume"
            )
        )

        for entry in self._volume_entries():
            relative_file = normalize_space(
                entry.get("file")
            )

            if not relative_file:
                errors.append(
                    "Revision volume entry has no file."
                )
                continue

            if relative_file in seen_files:
                errors.append(
                    "Duplicate revision volume entry: "
                    f"{relative_file}."
                )
                continue

            seen_files.add(relative_file)

            result = self.verify_volume(
                entry,
                validate_json=validate_json,
            )

            errors.extend(result.errors)
            total_records += (
                result.expected_records
            )

            if (
                not result.sealed
                and relative_file
                != active_file
            ):
                errors.append(
                    "Unsealed revision volume is not "
                    f"active: {relative_file}."
                )

        journal_total = int(
            self.journal.get(
                "total_records",
                0,
            )
            or 0
        )

        manifest_total = int(
            self.manifest.get(
                "total_revisions",
                0,
            )
            or 0
        )

        if total_records != journal_total:
            errors.append(
                "Revision journal total does not "
                "match volume totals: "
                f"journal={journal_total}, "
                f"volumes={total_records}."
            )

        if manifest_total != journal_total:
            errors.append(
                "Manifest total_revisions does not "
                "match revision journal total: "
                f"manifest={manifest_total}, "
                f"journal={journal_total}."
            )

        if active_file:
            active = self._entry_by_file(
                active_file
            )

            if active is None:
                errors.append(
                    "Revision active_volume is not "
                    f"registered: {active_file}."
                )

            elif bool(active.get("sealed")):
                errors.append(
                    "Revision active_volume points to "
                    f"a sealed file: {active_file}."
                )

        return errors

    def verify_volume(
        self,
        entry: Mapping[str, Any] | str,
        *,
        validate_json: bool = True,
    ) -> RevisionVerification:
        """Verify one revision volume."""

        target = self._resolve_entry(
            entry
        )

        relative_file = normalize_space(
            target.get("file")
        )

        sealed = bool(
            target.get("sealed")
        )

        expected_size = int(
            target.get(
                "size_bytes",
                0,
            )
            or 0
        )

        expected_records = int(
            target.get(
                "record_count",
                0,
            )
            or 0
        )

        expected_sha256 = (
            normalize_space(
                target.get("sha256")
            )
            or None
        )

        path = (
            self.root
            / relative_file
        )

        errors: list[str] = []

        if not path.exists():
            errors.append(
                "Missing revision volume: "
                f"{relative_file}."
            )

            return RevisionVerification(
                file=relative_file,
                exists=False,
                sealed=sealed,
                expected_size=expected_size,
                actual_size=0,
                expected_records=(
                    expected_records
                ),
                actual_records=0,
                expected_sha256=(
                    expected_sha256
                ),
                actual_sha256=None,
                valid_jsonl=False,
                errors=errors,
            )

        actual_size = path.stat().st_size

        actual_records = (
            self._count_nonempty_lines(
                path
            )
        )

        actual_sha256 = (
            file_sha256(path)
            if sealed
            else None
        )

        valid_jsonl = True

        if validate_json:
            json_errors = (
                self._validate_jsonl(path)
            )

            if json_errors:
                valid_jsonl = False
                errors.extend(json_errors)

        if actual_size != expected_size:
            errors.append(
                "Revision volume size mismatch: "
                f"{relative_file}; "
                f"manifest={expected_size}, "
                f"actual={actual_size}."
            )

        if (
            actual_records
            != expected_records
        ):
            errors.append(
                "Revision record-count mismatch: "
                f"{relative_file}; "
                f"manifest={expected_records}, "
                f"actual={actual_records}."
            )

        if (
            actual_size
            >= self.github_failure_bytes
        ):
            errors.append(
                "Revision volume exceeds GitHub-safe "
                f"limit: {relative_file}; "
                f"size={actual_size}; "
                f"threshold="
                f"{self.github_failure_bytes}."
            )

        if sealed:
            if not expected_sha256:
                errors.append(
                    "Sealed revision volume has no "
                    f"checksum: {relative_file}."
                )

            elif (
                actual_sha256
                != expected_sha256
            ):
                errors.append(
                    "Revision volume checksum mismatch: "
                    f"{relative_file}; "
                    f"manifest={expected_sha256}; "
                    f"actual={actual_sha256}."
                )

        elif expected_sha256:
            errors.append(
                "Unsealed revision volume has an "
                f"unexpected checksum: {relative_file}."
            )

        return RevisionVerification(
            file=relative_file,
            exists=True,
            sealed=sealed,
            expected_size=expected_size,
            actual_size=actual_size,
            expected_records=expected_records,
            actual_records=actual_records,
            expected_sha256=expected_sha256,
            actual_sha256=actual_sha256,
            valid_jsonl=valid_jsonl,
            errors=errors,
        )

    def iter_events(
        self,
    ) -> Iterator[dict[str, Any]]:
        """Iterate all revision events in journal order."""

        for (
            _relative_file,
            _line_number,
            event,
        ) in self.iter_events_with_location():
            yield event

    def iter_events_with_location(
        self,
    ) -> Iterator[
        tuple[str, int, dict[str, Any]]
    ]:
        """Iterate revision events with file and line location."""

        entries = sorted(
            self._volume_entries(),
            key=self._entry_number,
        )

        for entry in entries:
            relative_file = normalize_space(
                entry.get("file")
            )

            if not relative_file:
                continue

            path = (
                self.root
                / relative_file
            )

            if not path.is_file():
                continue

            with path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                for (
                    line_number,
                    line,
                ) in enumerate(
                    handle,
                    start=1,
                ):
                    stripped = line.strip()

                    if not stripped:
                        continue

                    try:
                        value = json.loads(
                            stripped
                        )

                    except json.JSONDecodeError as error:
                        raise RevisionWriterError(
                            "Invalid revision JSONL in "
                            f"{relative_file}:"
                            f"{line_number}: {error}"
                        ) from error

                    if not isinstance(
                        value,
                        dict,
                    ):
                        raise RevisionWriterError(
                            "Revision JSONL value is not "
                            "an object in "
                            f"{relative_file}:"
                            f"{line_number}."
                        )

                    yield (
                        relative_file,
                        line_number,
                        value,
                    )

    def statistics(self) -> dict[str, Any]:
        """Return aggregate revision-journal metadata."""

        entries = self._volume_entries()

        return {
            "total": int(
                self.journal.get(
                    "total_records",
                    0,
                )
                or 0
            ),
            "volumes": len(entries),
            "sealed_volumes": sum(
                1
                for entry in entries
                if bool(
                    entry.get("sealed")
                )
            ),
            "unsealed_volumes": sum(
                1
                for entry in entries
                if not bool(
                    entry.get("sealed")
                )
            ),
            "active_volume": (
                self.journal.get(
                    "active_volume"
                )
            ),
            "size_bytes": sum(
                int(
                    entry.get(
                        "size_bytes",
                        0,
                    )
                    or 0
                )
                for entry in entries
            ),
            "record_target": (
                self.record_target
            ),
            "target_bytes": (
                self.target_bytes
            ),
            "maximum_bytes": (
                self.maximum_bytes
            ),
        }

    def rebuild_manifest(
        self,
        *,
        seal_all_but_latest: bool = True,
    ) -> None:
        """Rebuild revision metadata from journal files."""

        entries: list[
            dict[str, Any]
        ] = []

        for path in self._discover_volumes():
            relative_file = (
                path.relative_to(
                    self.root
                ).as_posix()
            )

            stat = path.stat()

            entries.append(
                {
                    "file": relative_file,
                    "record_count": (
                        self._count_nonempty_lines(
                            path
                        )
                    ),
                    "size_bytes": stat.st_size,
                    "sha256": None,
                    "sealed": False,
                    "created_at": (
                        datetime.fromtimestamp(
                            stat.st_ctime,
                            tz=UTC,
                        )
                        .replace(
                            microsecond=0
                        )
                        .isoformat()
                        .replace(
                            "+00:00",
                            "Z",
                        )
                    ),
                    "sealed_at": None,
                }
            )

        entries.sort(
            key=self._entry_number
        )

        active_file: str | None = None

        if entries:
            if seal_all_but_latest:
                for entry in entries[:-1]:
                    path = (
                        self.root
                        / str(entry["file"])
                    )

                    entry["sealed"] = True
                    entry["sealed_at"] = (
                        utc_now()
                    )
                    entry["sha256"] = (
                        file_sha256(path)
                    )

                latest = entries[-1]

                latest_records = int(
                    latest.get(
                        "record_count",
                        0,
                    )
                    or 0
                )

                latest_size = int(
                    latest.get(
                        "size_bytes",
                        0,
                    )
                    or 0
                )

                if (
                    latest_records
                    >= self.record_target
                    or latest_size
                    >= self.target_bytes
                ):
                    path = (
                        self.root
                        / str(latest["file"])
                    )

                    latest["sealed"] = True
                    latest["sealed_at"] = (
                        utc_now()
                    )
                    latest["sha256"] = (
                        file_sha256(path)
                    )

                else:
                    active_file = str(
                        latest["file"]
                    )

            else:
                active_file = str(
                    entries[-1]["file"]
                )

        self.journal["volumes"] = (
            entries
        )

        self.journal[
            "active_volume"
        ] = active_file

        self._recalculate_total()
        self.save_manifest()

    def _normalize_event(
        self,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize and validate required revision-event fields."""

        result = dict(event)

        result["schema_version"] = (
            REVISION_SCHEMA_VERSION
        )

        result["event"] = (
            normalize_space(
                result.get("event")
            )
            or "provider_assertion_changed"
        )

        result["changed_at"] = (
            normalize_space(
                result.get("changed_at")
            )
            or utc_now()
        )

        required_fields = (
            "event",
            "speciedex_id",
            "provider",
            "provider_id",
            "changed_at",
        )

        missing = [
            field_name
            for field_name
            in required_fields
            if not normalize_space(
                result.get(field_name)
            )
        ]

        if missing:
            raise RevisionWriterError(
                "Revision event is missing required "
                "fields: "
                + ", ".join(missing)
            )

        return result

    def _new_volume_entry(
        self,
    ) -> dict[str, Any]:
        """Create metadata for the next revision volume."""

        number = (
            self._next_volume_number()
        )

        return {
            "file": (
                "revisions/"
                f"{self.prefix}-"
                f"{number:06d}.jsonl"
            ),
            "record_count": 0,
            "size_bytes": 0,
            "sha256": None,
            "sealed": False,
            "created_at": utc_now(),
            "sealed_at": None,
        }

    def _next_volume_number(
        self,
    ) -> int:
        """Return the next unused revision-volume number."""

        highest = 0

        for entry in self._volume_entries():
            highest = max(
                highest,
                self._entry_number(entry),
            )

        for path in self._discover_volumes():
            highest = max(
                highest,
                self._path_number(path),
            )

        return highest + 1

    def _resolve_entry(
        self,
        entry: Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Resolve a revision entry or relative filename."""

        if isinstance(entry, str):
            resolved = self._entry_by_file(
                entry
            )

            if resolved is None:
                raise RevisionWriterError(
                    "Unknown revision volume: "
                    f"{entry}."
                )

            return resolved

        relative_file = normalize_space(
            entry.get("file")
        )

        if not relative_file:
            raise RevisionWriterError(
                "Revision volume entry has no file."
            )

        resolved = self._entry_by_file(
            relative_file
        )

        if resolved is None:
            raise RevisionWriterError(
                "Revision volume is not registered: "
                f"{relative_file}."
            )

        return resolved

    def _entry_by_file(
        self,
        relative_file: str,
    ) -> dict[str, Any] | None:
        """Return one registered revision volume."""

        normalized = normalize_space(
            relative_file
        )

        for entry in self._volume_entries():
            if (
                normalize_space(
                    entry.get("file")
                )
                == normalized
            ):
                return entry

        return None

    def _volume_entries(
        self,
    ) -> list[dict[str, Any]]:
        """Return valid revision-volume entries."""

        volumes = self.journal.get(
            "volumes",
            [],
        )

        if not isinstance(volumes, list):
            self.journal["volumes"] = []
            return []

        return [
            entry
            for entry in volumes
            if isinstance(entry, dict)
        ]

    def _discover_volumes(
        self,
    ) -> list[Path]:
        """Return matching revision files in deterministic order."""

        return sorted(
            self.revisions_root.glob(
                f"{self.prefix}-*.jsonl"
            ),
            key=self._path_number,
        )

    def _recalculate_total(self) -> None:
        """Recalculate revision totals from volume metadata."""

        total = sum(
            int(
                entry.get(
                    "record_count",
                    0,
                )
                or 0
            )
            for entry
            in self._volume_entries()
        )

        self.journal[
            "total_records"
        ] = total

        self.manifest[
            "total_revisions"
        ] = total

    @staticmethod
    def _count_nonempty_lines(
        path: Path,
    ) -> int:
        """Count nonempty lines in a JSONL file."""

        count = 0

        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for line in handle:
                if line.strip():
                    count += 1

        return count

    @staticmethod
    def _validate_jsonl(
        path: Path,
    ) -> list[str]:
        """Validate revision JSONL records."""

        errors: list[str] = []

        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for (
                line_number,
                line,
            ) in enumerate(
                handle,
                start=1,
            ):
                stripped = line.strip()

                if not stripped:
                    continue

                try:
                    value = json.loads(
                        stripped
                    )

                except json.JSONDecodeError as error:
                    errors.append(
                        "Invalid revision JSONL in "
                        f"{path.name}:"
                        f"{line_number}: {error}."
                    )
                    continue

                if not isinstance(value, dict):
                    errors.append(
                        "Revision JSONL value is not "
                        "an object in "
                        f"{path.name}:"
                        f"{line_number}."
                    )
                    continue

                for required in (
                    "event",
                    "speciedex_id",
                    "provider",
                    "provider_id",
                    "changed_at",
                ):
                    if not normalize_space(
                        value.get(required)
                    ):
                        errors.append(
                            "Revision record missing "
                            f"{required!r} in "
                            f"{path.name}:"
                            f"{line_number}."
                        )

        return errors

    @staticmethod
    def _path_number(
        path: Path,
    ) -> int:
        """Extract the numeric suffix from a revision filename."""

        match = (
            REVISION_FILENAME_PATTERN
            .fullmatch(path.name)
        )

        if match is None:
            return 0

        return int(
            match.group("number")
        )

    @classmethod
    def _entry_number(
        cls,
        entry: Mapping[str, Any],
    ) -> int:
        """Extract the numeric suffix from a manifest entry."""

        relative_file = normalize_space(
            entry.get("file")
        )

        if not relative_file:
            return 0

        return cls._path_number(
            Path(relative_file)
        )
