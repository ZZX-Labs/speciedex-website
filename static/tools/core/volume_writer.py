#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/volume_writer.py

Append-only JSONL volume writer for the Speciedex taxonomic archive.

This module owns:

- active taxonomic JSONL volumes,
- deterministic volume filenames,
- record appends,
- target-size sealing,
- hard maximum-size rollover,
- volume checksums,
- volume manifest metadata,
- interrupted-write recovery,
- manifest reconstruction,
- JSONL validation,
- ordered record iteration,
- GitHub-safe volume size enforcement.

The VolumeWriter does not perform taxonomic reconciliation and does not write
to SQLite. The Archive coordinates SQLite indexing after a durable JSONL
record has been appended successfully.

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


VOLUME_SCHEMA_VERSION = 1
DEFAULT_PREFIX = "species"
DEFAULT_TARGET_BYTES = 48 * 1024 * 1024
DEFAULT_MAXIMUM_BYTES = 90 * 1024 * 1024
DEFAULT_GITHUB_FAILURE_BYTES = 95 * 1024 * 1024

VOLUME_FILENAME_PATTERN = re.compile(
    r"^(?P<prefix>[a-z0-9_-]+)-(?P<number>[0-9]{6})\.jsonl$"
)


class VolumeWriterError(RuntimeError):
    """Raised when a volume operation cannot be completed safely."""


@dataclass(slots=True)
class AppendResult:
    """Result of appending one object to a volume."""

    volume_file: str
    line_number: int
    size_bytes: int
    record_bytes: int
    sealed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible result."""

        return {
            "volume_file": self.volume_file,
            "line_number": self.line_number,
            "size_bytes": self.size_bytes,
            "record_bytes": self.record_bytes,
            "sealed": self.sealed,
        }


@dataclass(slots=True)
class VolumeVerification:
    """Verification result for one volume."""

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
        """Return whether the volume passed all checks."""

        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible verification result."""

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
        str(
            value
            if value is not None
            else ""
        ).strip().split()
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


class VolumeWriter:
    """
    Durable append-only JSONL volume manager.

    The supplied manifest dictionary is updated in place. Callers may pass the
    Archive manifest directly so both objects share one canonical state.
    """

    def __init__(
        self,
        *,
        root: Path,
        manifest: dict[str, Any],
        manifest_path: Path | None = None,
        target_bytes: int = DEFAULT_TARGET_BYTES,
        maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
        prefix: str = DEFAULT_PREFIX,
        github_failure_bytes: int = DEFAULT_GITHUB_FAILURE_BYTES,
        fsync_writes: bool = True,
        persist_manifest: bool = True,
    ) -> None:
        self.root = Path(root)
        self.volumes_root = self.root / "volumes"

        self.manifest = manifest

        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else self.root / "manifest.json"
        )

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

        self.volumes_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._repair_manifest_defaults()
        self.recover()

    def _validate_configuration(self) -> None:
        """Validate writer configuration."""

        if self.target_bytes < 1:
            raise ValueError(
                "target_bytes must be positive."
            )

        if self.maximum_bytes < 1:
            raise ValueError(
                "maximum_bytes must be positive."
            )

        if self.target_bytes >= self.maximum_bytes:
            raise ValueError(
                "target_bytes must be below maximum_bytes."
            )

        if self.github_failure_bytes < 1:
            raise ValueError(
                "github_failure_bytes must be positive."
            )

        if self.maximum_bytes >= self.github_failure_bytes:
            raise ValueError(
                "maximum_bytes must remain below "
                "github_failure_bytes."
            )

        if not self.prefix:
            raise ValueError(
                "Volume prefix cannot be empty."
            )

        if not re.fullmatch(
            r"[a-z0-9_-]+",
            self.prefix,
        ):
            raise ValueError(
                "Volume prefix may contain only "
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
        """Add required manifest fields without discarding state."""

        defaults: dict[str, Any] = {
            "schema_version": VOLUME_SCHEMA_VERSION,
            "generated_at": utc_now(),
            "record_format": "jsonl",
            "target_volume_bytes": self.target_bytes,
            "maximum_volume_bytes": self.maximum_bytes,
            "total_primary_records": 0,
            "volumes": [],
            "active_volume": None,
        }

        for key, value in defaults.items():
            if key not in self.manifest:
                self.manifest[key] = value

        if not isinstance(
            self.manifest.get("volumes"),
            list,
        ):
            self.manifest["volumes"] = []

        self.manifest["schema_version"] = (
            VOLUME_SCHEMA_VERSION
        )

        self.manifest["record_format"] = "jsonl"

        self.manifest["target_volume_bytes"] = (
            self.target_bytes
        )

        self.manifest["maximum_volume_bytes"] = (
            self.maximum_bytes
        )

    def save_manifest(self) -> None:
        """Persist the shared manifest when configured."""

        self.manifest["generated_at"] = utc_now()

        if self.persist_manifest:
            atomic_write_json(
                self.manifest_path,
                self.manifest,
            )

    def append(
        self,
        value: Mapping[str, Any],
    ) -> AppendResult:
        """
        Append one JSON object to the current volume.

        A volume is rolled over before writing when the encoded record would
        push a nonempty active volume beyond maximum_bytes.
        """

        if not isinstance(
            value,
            Mapping,
        ):
            raise TypeError(
                "Volume records must be mapping objects."
            )

        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        line = (
            encoded
            + "\n"
        ).encode("utf-8")

        record_bytes = len(line)

        if record_bytes > self.maximum_bytes:
            raise VolumeWriterError(
                "A single encoded record exceeds the "
                "configured maximum volume size: "
                f"record={record_bytes}, "
                f"maximum={self.maximum_bytes}."
            )

        entry = self.active_volume()

        path = self.root / str(
            entry["file"]
        )

        current_size = (
            path.stat().st_size
            if path.exists()
            else 0
        )

        if (
            current_size > 0
            and (
                current_size
                + record_bytes
                > self.maximum_bytes
            )
        ):
            self.seal(
                entry
            )

            entry = self.active_volume()

            path = self.root / str(
                entry["file"]
            )

            current_size = (
                path.stat().st_size
                if path.exists()
                else 0
            )

        line_number = (
            int(
                entry.get(
                    "record_count",
                    0,
                )
            )
            + 1
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            with path.open(
                "ab"
            ) as handle:
                handle.write(line)
                handle.flush()

                if self.fsync_writes:
                    os.fsync(
                        handle.fileno()
                    )

        except OSError as error:
            raise VolumeWriterError(
                f"Unable to append to volume "
                f"{entry['file']}: {error}"
            ) from error

        actual_size = (
            path.stat().st_size
        )

        expected_size = (
            current_size
            + record_bytes
        )

        if actual_size != expected_size:
            raise VolumeWriterError(
                "Volume size after append does not "
                "match the expected size: "
                f"file={entry['file']}, "
                f"expected={expected_size}, "
                f"actual={actual_size}."
            )

        entry["record_count"] = (
            line_number
        )

        entry["size_bytes"] = (
            actual_size
        )

        entry["sha256"] = None

        self.manifest[
            "total_primary_records"
        ] = (
            int(
                self.manifest.get(
                    "total_primary_records",
                    0,
                )
            )
            + 1
        )

        sealed = False

        if actual_size >= self.target_bytes:
            self.seal(
                entry
            )
            sealed = True
        else:
            self.save_manifest()

        return AppendResult(
            volume_file=str(
                entry["file"]
            ),
            line_number=line_number,
            size_bytes=actual_size,
            record_bytes=record_bytes,
            sealed=sealed,
        )

    def append_many(
        self,
        values: Iterable[
            Mapping[str, Any]
        ],
    ) -> list[AppendResult]:
        """Append multiple records in order."""

        return [
            self.append(value)
            for value in values
        ]

    def active_volume(
        self,
    ) -> dict[str, Any]:
        """Return or create the active writable volume."""

        active_name = normalize_space(
            self.manifest.get(
                "active_volume"
            )
        )

        if active_name:
            for entry in self._volume_entries():
                if (
                    normalize_space(
                        entry.get("file")
                    )
                    == active_name
                ):
                    if bool(
                        entry.get(
                            "sealed"
                        )
                    ):
                        self.manifest[
                            "active_volume"
                        ] = None
                        break

                    path = (
                        self.root
                        / active_name
                    )

                    if not path.exists():
                        path.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        path.touch()

                    return entry

        entry = self._new_volume_entry()

        self.manifest[
            "volumes"
        ].append(entry)

        self.manifest[
            "active_volume"
        ] = entry["file"]

        path = (
            self.root
            / str(
                entry["file"]
            )
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
        """Seal a volume and record its size, line count, and checksum."""

        target = self._resolve_entry(
            entry
        )

        if bool(
            target.get(
                "sealed"
            )
        ):
            return target

        relative_file = normalize_space(
            target.get(
                "file"
            )
        )

        if not relative_file:
            raise VolumeWriterError(
                "Cannot seal a volume without a file."
            )

        path = (
            self.root
            / relative_file
        )

        if not path.is_file():
            raise VolumeWriterError(
                f"Cannot seal missing volume: "
                f"{relative_file}."
            )

        actual_size = (
            path.stat().st_size
        )

        if (
            actual_size
            >= self.github_failure_bytes
        ):
            raise VolumeWriterError(
                "Volume exceeds the configured GitHub-safe "
                "failure threshold: "
                f"file={relative_file}, "
                f"size={actual_size}, "
                f"threshold={self.github_failure_bytes}."
            )

        target["size_bytes"] = (
            actual_size
        )

        target["record_count"] = (
            self._count_nonempty_lines(
                path
            )
        )

        target["sha256"] = (
            file_sha256(path)
        )

        target["sealed"] = True
        target["sealed_at"] = utc_now()

        if (
            normalize_space(
                self.manifest.get(
                    "active_volume"
                )
            )
            == relative_file
        ):
            self.manifest[
                "active_volume"
            ] = None

        self.save_manifest()

        return target

    def seal_active(self) -> dict[str, Any] | None:
        """Seal the active volume when it contains records."""

        active_name = normalize_space(
            self.manifest.get(
                "active_volume"
            )
        )

        if not active_name:
            return None

        entry = self._entry_by_file(
            active_name
        )

        if entry is None:
            self.manifest[
                "active_volume"
            ] = None

            self.save_manifest()

            return None

        path = (
            self.root
            / active_name
        )

        if (
            not path.exists()
            or path.stat().st_size == 0
        ):
            return entry

        return self.seal(
            entry
        )

    def seal_if_needed(
        self,
        entry: Mapping[str, Any] | str,
    ) -> bool:
        """Seal a volume when it reaches target_bytes."""

        target = self._resolve_entry(
            entry
        )

        relative_file = normalize_space(
            target.get(
                "file"
            )
        )

        path = (
            self.root
            / relative_file
        )

        size = (
            path.stat().st_size
            if path.exists()
            else 0
        )

        target["size_bytes"] = size

        if size >= self.target_bytes:
            self.seal(
                target
            )
            return True

        self.save_manifest()

        return False

    def recover(self) -> None:
        """
        Recover volume metadata after an interrupted process.

        Unsealed files are re-counted. A nonempty unsealed volume becomes the
        active volume. Multiple unsealed volumes are handled by keeping only
        the highest-numbered volume active and sealing older nonempty ones.
        """

        entries = self._volume_entries()

        discovered = self._discover_unregistered_volumes()

        for path in discovered:
            relative_file = (
                path.relative_to(
                    self.root
                ).as_posix()
            )

            if self._entry_by_file(
                relative_file
            ) is not None:
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

        self.manifest["volumes"] = sorted(
            entries,
            key=self._entry_number,
        )

        unsealed = [
            entry
            for entry in self._volume_entries()
            if not bool(
                entry.get(
                    "sealed"
                )
            )
        ]

        for entry in unsealed:
            relative_file = normalize_space(
                entry.get(
                    "file"
                )
            )

            path = (
                self.root
                / relative_file
            )

            if not path.exists():
                path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                path.touch()

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

        nonempty_unsealed = [
            entry
            for entry in unsealed
            if int(
                entry.get(
                    "size_bytes",
                    0,
                )
            ) > 0
        ]

        if len(
            nonempty_unsealed
        ) > 1:
            nonempty_unsealed.sort(
                key=self._entry_number
            )

            for entry in (
                nonempty_unsealed[:-1]
            ):
                self.seal(
                    entry
                )

            active = (
                nonempty_unsealed[-1]
            )

            self.manifest[
                "active_volume"
            ] = active["file"]

        elif len(
            nonempty_unsealed
        ) == 1:
            self.manifest[
                "active_volume"
            ] = (
                nonempty_unsealed[0]["file"]
            )

        elif unsealed:
            unsealed.sort(
                key=self._entry_number
            )

            self.manifest[
                "active_volume"
            ] = (
                unsealed[-1]["file"]
            )

        else:
            self.manifest[
                "active_volume"
            ] = None

        self._recalculate_manifest_total()
        self.save_manifest()

    def verify(
        self,
        *,
        validate_json: bool = True,
    ) -> list[str]:
        """Verify every manifest volume and global totals."""

        errors: list[str] = []
        total_records = 0
        seen_files: set[str] = set()

        active_file = normalize_space(
            self.manifest.get(
                "active_volume"
            )
        )

        for entry in self._volume_entries():
            relative_file = normalize_space(
                entry.get(
                    "file"
                )
            )

            if not relative_file:
                errors.append(
                    "A manifest volume entry has no file."
                )
                continue

            if relative_file in seen_files:
                errors.append(
                    f"Duplicate manifest volume: "
                    f"{relative_file}."
                )
                continue

            seen_files.add(
                relative_file
            )

            result = self.verify_volume(
                entry,
                validate_json=validate_json,
            )

            errors.extend(
                result.errors
            )

            total_records += (
                result.expected_records
            )

            if (
                not result.sealed
                and relative_file != active_file
            ):
                errors.append(
                    "Unsealed volume is not the active "
                    f"volume: {relative_file}."
                )

        manifest_total = int(
            self.manifest.get(
                "total_primary_records",
                0,
            )
        )

        if total_records != manifest_total:
            errors.append(
                "Manifest total_primary_records does "
                "not match volume totals: "
                f"manifest={manifest_total}, "
                f"volumes={total_records}."
            )

        if active_file:
            active_entry = self._entry_by_file(
                active_file
            )

            if active_entry is None:
                errors.append(
                    "Manifest active_volume does not "
                    f"exist in volumes: {active_file}."
                )

            elif bool(
                active_entry.get(
                    "sealed"
                )
            ):
                errors.append(
                    "Manifest active_volume points to a "
                    f"sealed volume: {active_file}."
                )

        return errors

    def verify_volume(
        self,
        entry: Mapping[str, Any] | str,
        *,
        validate_json: bool = True,
    ) -> VolumeVerification:
        """Verify one volume against its manifest entry."""

        target = self._resolve_entry(
            entry
        )

        relative_file = normalize_space(
            target.get(
                "file"
            )
        )

        sealed = bool(
            target.get(
                "sealed"
            )
        )

        expected_size = int(
            target.get(
                "size_bytes",
                0,
            )
        )

        expected_records = int(
            target.get(
                "record_count",
                0,
            )
        )

        expected_sha256 = (
            normalize_space(
                target.get(
                    "sha256"
                )
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
                f"Missing volume: {relative_file}."
            )

            return VolumeVerification(
                file=relative_file,
                exists=False,
                sealed=sealed,
                expected_size=expected_size,
                actual_size=0,
                expected_records=expected_records,
                actual_records=0,
                expected_sha256=expected_sha256,
                actual_sha256=None,
                valid_jsonl=False,
                errors=errors,
            )

        actual_size = (
            path.stat().st_size
        )

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
                self._validate_jsonl(
                    path
                )
            )

            if json_errors:
                valid_jsonl = False
                errors.extend(
                    json_errors
                )

        if actual_size != expected_size:
            errors.append(
                f"Volume size mismatch: "
                f"{relative_file}; "
                f"manifest={expected_size}, "
                f"actual={actual_size}."
            )

        if actual_records != expected_records:
            errors.append(
                f"Volume record-count mismatch: "
                f"{relative_file}; "
                f"manifest={expected_records}, "
                f"actual={actual_records}."
            )

        if actual_size >= self.github_failure_bytes:
            errors.append(
                f"Volume exceeds GitHub-safe limit: "
                f"{relative_file}; "
                f"size={actual_size}, "
                f"limit={self.github_failure_bytes}."
            )

        if sealed:
            if not expected_sha256:
                errors.append(
                    "Sealed volume has no checksum: "
                    f"{relative_file}."
                )

            elif actual_sha256 != expected_sha256:
                errors.append(
                    f"Volume checksum mismatch: "
                    f"{relative_file}."
                )

        elif expected_sha256:
            errors.append(
                "Unsealed volume unexpectedly has a "
                f"checksum: {relative_file}."
            )

        return VolumeVerification(
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

    def rebuild_manifest(
        self,
        *,
        seal_all_but_latest: bool = True,
    ) -> None:
        """Rebuild volume metadata from files on disk."""

        paths = self._discover_unregistered_volumes()

        entries: list[
            dict[str, Any]
        ] = []

        for path in paths:
            relative_file = (
                path.relative_to(
                    self.root
                ).as_posix()
            )

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
                    "created_at": (
                        datetime.fromtimestamp(
                            path.stat().st_ctime,
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

        if (
            seal_all_but_latest
            and entries
        ):
            for entry in entries[:-1]:
                path = (
                    self.root
                    / str(
                        entry["file"]
                    )
                )

                entry["sealed"] = True
                entry["sealed_at"] = utc_now()
                entry["sha256"] = (
                    file_sha256(path)
                )

            latest = entries[-1]

            if int(
                latest.get(
                    "size_bytes",
                    0,
                )
            ) >= self.target_bytes:
                path = (
                    self.root
                    / str(
                        latest["file"]
                    )
                )

                latest["sealed"] = True
                latest["sealed_at"] = utc_now()
                latest["sha256"] = (
                    file_sha256(path)
                )

                active_file = None
            else:
                active_file = latest["file"]

        else:
            active_file = (
                entries[-1]["file"]
                if entries
                else None
            )

        self.manifest["volumes"] = (
            entries
        )

        self.manifest["active_volume"] = (
            active_file
        )

        self._recalculate_manifest_total()
        self.save_manifest()

    def iter_records(
        self,
    ) -> Iterator[
        dict[str, Any]
    ]:
        """Iterate all volume records in manifest order."""

        entries = sorted(
            self._volume_entries(),
            key=self._entry_number,
        )

        for entry in entries:
            relative_file = normalize_space(
                entry.get(
                    "file"
                )
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
                for line_number, line in enumerate(
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
                        raise VolumeWriterError(
                            "Invalid JSONL record in "
                            f"{relative_file}:"
                            f"{line_number}: {error}"
                        ) from error

                    if not isinstance(
                        value,
                        dict,
                    ):
                        raise VolumeWriterError(
                            "JSONL record is not an object "
                            f"in {relative_file}:"
                            f"{line_number}."
                        )

                    yield value

    def iter_records_with_location(
        self,
    ) -> Iterator[
        tuple[
            str,
            int,
            dict[str, Any],
        ]
    ]:
        """Iterate records with volume filename and line number."""

        entries = sorted(
            self._volume_entries(),
            key=self._entry_number,
        )

        for entry in entries:
            relative_file = normalize_space(
                entry.get(
                    "file"
                )
            )

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
                for line_number, line in enumerate(
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
                        raise VolumeWriterError(
                            "Invalid JSONL record in "
                            f"{relative_file}:"
                            f"{line_number}: {error}"
                        ) from error

                    if not isinstance(
                        value,
                        dict,
                    ):
                        raise VolumeWriterError(
                            "JSONL record is not an object "
                            f"in {relative_file}:"
                            f"{line_number}."
                        )

                    yield (
                        relative_file,
                        line_number,
                        value,
                    )

    def volume_statistics(
        self,
    ) -> dict[str, Any]:
        """Return aggregate volume metadata."""

        entries = self._volume_entries()

        sealed = [
            entry
            for entry in entries
            if bool(
                entry.get(
                    "sealed"
                )
            )
        ]

        unsealed = [
            entry
            for entry in entries
            if not bool(
                entry.get(
                    "sealed"
                )
            )
        ]

        return {
            "volumes": len(entries),
            "sealed_volumes": len(
                sealed
            ),
            "unsealed_volumes": len(
                unsealed
            ),
            "records": sum(
                int(
                    entry.get(
                        "record_count",
                        0,
                    )
                )
                for entry in entries
            ),
            "size_bytes": sum(
                int(
                    entry.get(
                        "size_bytes",
                        0,
                    )
                )
                for entry in entries
            ),
            "active_volume": (
                self.manifest.get(
                    "active_volume"
                )
            ),
            "target_bytes": (
                self.target_bytes
            ),
            "maximum_bytes": (
                self.maximum_bytes
            ),
        }

    def _new_volume_entry(
        self,
    ) -> dict[str, Any]:
        """Create metadata for the next volume."""

        number = self._next_volume_number()

        return {
            "file": (
                "volumes/"
                f"{self.prefix}-{number:06d}.jsonl"
            ),
            "record_count": 0,
            "size_bytes": 0,
            "sha256": None,
            "sealed": False,
            "created_at": utc_now(),
            "sealed_at": None,
        }

    def _next_volume_number(self) -> int:
        """Return the next unused volume number."""

        highest = 0

        for entry in self._volume_entries():
            highest = max(
                highest,
                self._entry_number(
                    entry
                ),
            )

        for path in self.volumes_root.glob(
            f"{self.prefix}-*.jsonl"
        ):
            highest = max(
                highest,
                self._path_number(
                    path
                ),
            )

        return highest + 1

    def _resolve_entry(
        self,
        entry: Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Resolve a manifest entry or relative filename."""

        if isinstance(
            entry,
            str,
        ):
            resolved = self._entry_by_file(
                entry
            )

            if resolved is None:
                raise VolumeWriterError(
                    f"Unknown volume: {entry}."
                )

            return resolved

        relative_file = normalize_space(
            entry.get(
                "file"
            )
        )

        if not relative_file:
            raise VolumeWriterError(
                "Volume entry has no file."
            )

        resolved = self._entry_by_file(
            relative_file
        )

        if resolved is None:
            raise VolumeWriterError(
                "Volume entry is not registered in "
                f"the manifest: {relative_file}."
            )

        return resolved

    def _entry_by_file(
        self,
        relative_file: str,
    ) -> dict[str, Any] | None:
        """Return one manifest volume entry."""

        normalized = normalize_space(
            relative_file
        )

        for entry in self._volume_entries():
            if normalize_space(
                entry.get(
                    "file"
                )
            ) == normalized:
                return entry

        return None

    def _volume_entries(
        self,
    ) -> list[dict[str, Any]]:
        """Return valid dictionary volume entries."""

        volumes = self.manifest.get(
            "volumes",
            [],
        )

        if not isinstance(
            volumes,
            list,
        ):
            self.manifest[
                "volumes"
            ] = []

            return []

        return [
            entry
            for entry in volumes
            if isinstance(
                entry,
                dict,
            )
        ]

    def _discover_unregistered_volumes(
        self,
    ) -> list[Path]:
        """Return all matching volume files in deterministic order."""

        return sorted(
            self.volumes_root.glob(
                f"{self.prefix}-*.jsonl"
            ),
            key=self._path_number,
        )

    def _recalculate_manifest_total(self) -> None:
        """Recalculate total_primary_records from volume entries."""

        self.manifest[
            "total_primary_records"
        ] = sum(
            int(
                entry.get(
                    "record_count",
                    0,
                )
            )
            for entry in self._volume_entries()
        )

    @staticmethod
    def _count_nonempty_lines(
        path: Path,
    ) -> int:
        """Count nonempty JSONL lines."""

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
        """Validate that every nonempty line is a JSON object."""

        errors: list[str] = []

        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for line_number, line in enumerate(
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
                        "Invalid JSONL in "
                        f"{path.name}:"
                        f"{line_number}: {error}."
                    )
                    continue

                if not isinstance(
                    value,
                    dict,
                ):
                    errors.append(
                        "JSONL value is not an object in "
                        f"{path.name}:"
                        f"{line_number}."
                    )

        return errors

    @staticmethod
    def _path_number(
        path: Path,
    ) -> int:
        """Extract a numeric suffix from a volume path."""

        match = VOLUME_FILENAME_PATTERN.fullmatch(
            path.name
        )

        if match is None:
            return 0

        return int(
            match.group(
                "number"
            )
        )

    @classmethod
    def _entry_number(
        cls,
        entry: Mapping[str, Any],
    ) -> int:
        """Extract the numeric suffix from a manifest entry."""

        relative_file = normalize_space(
            entry.get(
                "file"
            )
        )

        if not relative_file:
            return 0

        return cls._path_number(
            Path(relative_file)
        )
