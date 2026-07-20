#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/archive.py

Append-only taxonomic archive manager.

This module owns durable JSONL archive storage and coordinates the selected
rebuildable database index through DatabaseManager.

Provider implementations must not write directly to the archive. They return
normalized Taxon objects, and the ingestion process passes those records to
Archive.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping

from providers.common import Taxon

if TYPE_CHECKING:
    from .database_backend import DatabaseBackend
    from .database_manager import DatabaseManager


SCHEMA_VERSION = 1

ACTIVE_STATUSES = {
    "accepted",
    "valid",
    "provisionally accepted",
    "unknown",
    "reference",
}

STATISTIC_RANKS = {
    "species": "species",
    "genera": "genus",
    "families": "family",
    "orders": "order",
    "classes": "class",
    "phyla": "phylum",
    "kingdoms": "kingdom",
}


def now() -> str:
    """Return the current UTC timestamp in stable ISO-8601 form."""

    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_space(value: Any) -> str:
    """Collapse leading, trailing, and repeated whitespace."""

    return " ".join(
        str(value if value is not None else "")
        .strip()
        .split()
    )


def normalize_key(value: Any) -> str:
    """Normalize text for deterministic comparisons."""

    return normalize_space(value).casefold()


def read_json(path: Path, default: Any) -> Any:
    """Read JSON or return the supplied default on failure."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    """Atomically write formatted UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
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
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def append_jsonl(
    path: Path,
    values: Iterable[Mapping[str, Any]],
) -> int:
    """Append mapping objects to a JSONL file and fsync the result."""

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with path.open(
        mode="a",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        for value in values:
            handle.write(
                json.dumps(
                    dict(value),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
            count += 1

        handle.flush()
        os.fsync(handle.fileno())

    return count


def file_hash(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


class Archive:
    """
    Append-only Speciedex archive with a rebuildable database index.

    JSONL volumes are the canonical durable data source. SQLite and MariaDB are
    interchangeable indexes selected through DatabaseManager.
    """

    def __init__(
        self,
        root: Path,
        target_bytes: int,
        maximum_bytes: int,
        *,
        database_config: Mapping[str, Any] | None = None,
        database_manager: "DatabaseManager | None" = None,
        backend: str = "sqlite",
        sqlite_path: Path | str | None = None,
        sqlite_timeout: float = 60.0,
        read_only: bool = False,
        **database_options: Any,
    ) -> None:
        if target_bytes < 1:
            raise ValueError("target_bytes must be positive")

        if maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")

        if target_bytes >= maximum_bytes:
            raise ValueError(
                "target_bytes must be below maximum_bytes"
            )

        self.root = Path(root)
        self.volumes = self.root / "volumes"
        self.revisions = self.root / "revisions"
        self.conflicts = self.root / "conflicts"
        self.provider_states = self.root / "provider-state"
        self.rejected = self.root / "rejected"
        self.manifest_path = self.root / "manifest.json"
        self.database_path = self.root / "index.sqlite3"

        self.target_bytes = int(target_bytes)
        self.maximum_bytes = int(maximum_bytes)
        self.read_only = bool(read_only)
        self._closed = False

        for directory in (
            self.root,
            self.volumes,
            self.revisions,
            self.conflicts,
            self.provider_states,
            self.rejected,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        # Lazy import prevents a cycle because the backend modules retain
        # compatibility imports of normalize_key/normalize_space/now here.
        from .database_manager import DatabaseManager

        if database_manager is not None:
            self.database_manager = database_manager
        elif database_config is not None:
            self.database_manager = DatabaseManager.from_config(
                database_config,
                archive_root=self.root,
            )
        else:
            self.database_manager = DatabaseManager(
                backend=backend,
                sqlite_path=(
                    Path(sqlite_path)
                    if sqlite_path is not None
                    else self.database_path
                ),
                sqlite_timeout=sqlite_timeout,
                read_only=self.read_only,
                **database_options,
            )

        self.index: "DatabaseBackend" = self.database_manager.index

        # Compatibility aliases for legacy code. Archive itself never issues
        # backend-specific SQL through these objects.
        self.database = self.database_manager.database
        self.connection = self.database_manager.connection

        self.manifest = self._load_manifest()
        self._repair_manifest_defaults()
        self._save_manifest()

    def __enter__(self) -> "Archive":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    @property
    def backend_name(self) -> str:
        """Return the active index backend name."""

        return self.database_manager.backend_name

    def _load_manifest(self) -> dict[str, Any]:
        """Load the manifest or create a fresh one."""

        manifest = read_json(self.manifest_path, {})

        if not isinstance(manifest, dict):
            manifest = {}

        if not manifest:
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": now(),
                "record_format": "jsonl",
                "target_volume_bytes": self.target_bytes,
                "maximum_volume_bytes": self.maximum_bytes,
                "total_primary_records": 0,
                "total_revisions": 0,
                "total_conflicts": 0,
                "volumes": [],
                "active_volume": None,
                "database": {},
            }

        return manifest

    def _repair_manifest_defaults(self) -> None:
        """Add missing manifest fields without discarding existing state."""

        defaults: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now(),
            "record_format": "jsonl",
            "target_volume_bytes": self.target_bytes,
            "maximum_volume_bytes": self.maximum_bytes,
            "total_primary_records": 0,
            "total_revisions": 0,
            "total_conflicts": 0,
            "volumes": [],
            "active_volume": None,
            "database": {},
        }

        for key, value in defaults.items():
            if key not in self.manifest:
                self.manifest[key] = value

        if not isinstance(self.manifest.get("volumes"), list):
            self.manifest["volumes"] = []

        if not isinstance(self.manifest.get("database"), dict):
            self.manifest["database"] = {}

        self.manifest["schema_version"] = SCHEMA_VERSION
        self.manifest["target_volume_bytes"] = self.target_bytes
        self.manifest["maximum_volume_bytes"] = self.maximum_bytes
        self.manifest["database"] = self.database_manager.describe()

    def _save_manifest(self) -> None:
        """Persist the archive manifest atomically."""

        self.manifest["generated_at"] = now()
        self.manifest["database"] = self.database_manager.describe()
        write_json(self.manifest_path, self.manifest)

    def close(self) -> None:
        """Flush and close the selected database index."""

        if self._closed:
            return

        try:
            self.database_manager.checkpoint(truncate=True)
        finally:
            self.database_manager.close()
            self._closed = True

    @staticmethod
    def value_hash(value: Any) -> str:
        """Create a deterministic SHA-256 hash of JSON-compatible data."""

        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def identity_key(self, record: Taxon) -> str:
        """Build the canonical Speciedex reconciliation key."""

        return "|".join(
            (
                normalize_key(record.canonical_name),
                normalize_key(record.rank),
                normalize_key(record.kingdom),
                normalize_key(record.authorship),
            )
        )

    @staticmethod
    def speciedex_id(identity_key: str) -> str:
        """Create a deterministic Speciedex identifier."""

        digest = hashlib.sha256(
            identity_key.encode("utf-8")
        ).hexdigest()

        return "spx:sha256:" + digest

    def active_volume(self) -> dict[str, Any]:
        """Return the active JSONL volume, creating one when necessary."""

        active_name = self.manifest.get("active_volume")

        for entry in self.manifest["volumes"]:
            if not isinstance(entry, dict):
                continue

            if (
                entry.get("file") == active_name
                and not bool(entry.get("sealed"))
            ):
                return entry

        number = self._next_volume_number()
        entry = {
            "file": f"volumes/species-{number:06d}.jsonl",
            "record_count": 0,
            "size_bytes": 0,
            "sha256": None,
            "sealed": False,
            "created_at": now(),
            "sealed_at": None,
        }

        self.manifest["volumes"].append(entry)
        self.manifest["active_volume"] = entry["file"]
        self._save_manifest()
        return entry

    def _next_volume_number(self) -> int:
        """Determine the next available primary-volume number."""

        highest = 0

        for entry in self.manifest.get("volumes", []):
            if not isinstance(entry, dict):
                continue

            filename = Path(
                normalize_space(entry.get("file"))
            ).stem
            suffix = filename.rsplit("-", 1)[-1]

            try:
                highest = max(highest, int(suffix))
            except ValueError:
                continue

        return highest + 1

    def _seal_volume(self, entry: dict[str, Any]) -> None:
        """Seal a JSONL volume and record its checksum."""

        path = self.root / normalize_space(entry.get("file"))

        if not path.exists():
            raise FileNotFoundError(
                f"Cannot seal missing volume: {entry.get('file')}"
            )

        entry["size_bytes"] = path.stat().st_size
        entry["record_count"] = self._count_jsonl_lines(path)
        entry["sealed"] = True
        entry["sealed_at"] = now()
        entry["sha256"] = file_hash(path)

        if self.manifest.get("active_volume") == entry.get("file"):
            self.manifest["active_volume"] = None

        self._save_manifest()

    def seal_if_needed(self, entry: dict[str, Any]) -> None:
        """Seal a volume once it reaches the configured target size."""

        path = self.root / normalize_space(entry.get("file"))
        entry["size_bytes"] = (
            path.stat().st_size
            if path.exists()
            else 0
        )

        if entry["size_bytes"] >= self.target_bytes:
            self._seal_volume(entry)
        else:
            self._save_manifest()

    def source_match(
        self,
        provider: str,
        provider_id: str,
    ) -> str | None:
        """Find an existing taxon through a provider source identifier."""

        return self.database_manager.source_match(
            provider,
            provider_id,
        )

    def identity_candidates(
        self,
        identity_key: str,
    ) -> list[Mapping[str, Any]]:
        """Return exact identity-key candidates."""

        return self.database_manager.identity_candidates(
            identity_key
        )

    def name_candidates(
        self,
        record: Taxon,
    ) -> list[Mapping[str, Any]]:
        """Return same-name, same-rank, same-kingdom candidates."""

        return self.database_manager.name_candidates(record)

    def synonym_candidates(self, synonym: str) -> list[str]:
        """Return canonical identifiers indexed under a synonym."""

        return self.database_manager.synonym_candidates(synonym)

    def taxon(
        self,
        identifier: str,
    ) -> Mapping[str, Any] | None:
        """Read one indexed canonical taxon."""

        return self.database_manager.taxon(identifier)

    def add_primary(self, record: Taxon) -> str:
        """Create a new canonical taxon and attach its first assertion."""

        identity_key = self.identity_key(record)
        identifier = self.speciedex_id(identity_key)

        if self.taxon(identifier) is not None:
            self.attach_assertion(identifier, record)
            return identifier

        first_seen = record.retrieved_at or now()
        primary = {
            "schema_version": SCHEMA_VERSION,
            "speciedex_id": identifier,
            "identity_key": identity_key,
            "canonical_name": record.canonical_name,
            "scientific_name": record.scientific_name,
            "rank": record.rank,
            "status": record.status,
            "authorship": record.authorship,
            "taxonomy": {
                "kingdom": record.kingdom,
                "phylum": record.phylum,
                "class": record.class_name,
                "order": record.order,
                "family": record.family,
                "genus": record.genus,
            },
            "first_seen": first_seen,
            "initial_source": {
                "provider": record.provider,
                "provider_id": record.provider_id,
                "url": record.source_url,
            },
        }

        encoded = json.dumps(
            primary,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record_bytes = (encoded + "\n").encode("utf-8")

        if len(record_bytes) > self.maximum_bytes:
            raise ValueError(
                "Canonical taxon record exceeds maximum volume size: "
                f"{len(record_bytes)} > {self.maximum_bytes}"
            )

        entry = self.active_volume()
        path = self.root / normalize_space(entry.get("file"))
        current_size = path.stat().st_size if path.exists() else 0

        if (
            current_size > 0
            and current_size + len(record_bytes) > self.maximum_bytes
        ):
            self._seal_volume(entry)
            entry = self.active_volume()
            path = self.root / normalize_space(entry.get("file"))
            current_size = 0

        line_number = int(entry.get("record_count", 0)) + 1
        append_offset = current_size

        append_jsonl(path, (primary,))
        entry["record_count"] = line_number
        entry["size_bytes"] = path.stat().st_size

        try:
            with self.database_manager.transaction():
                self.database_manager.insert_taxon(
                    identifier=identifier,
                    identity_key=identity_key,
                    record=record,
                    primary_json=encoded,
                    record_hash=self.value_hash(primary),
                    volume_file=normalize_space(entry.get("file")),
                    line_number=line_number,
                    created_at=first_seen,
                    updated_at=first_seen,
                    commit=False,
                )

                self._attach_assertion_index(
                    identifier,
                    record,
                    timestamp=first_seen,
                    commit=False,
                )
        except Exception:
            # The database index is rebuildable, but avoid leaving an
            # unmanifested canonical line when an immediate index write fails.
            try:
                with path.open("r+b") as handle:
                    handle.truncate(append_offset)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                entry["record_count"] = max(0, line_number - 1)
                entry["size_bytes"] = (
                    path.stat().st_size
                    if path.exists()
                    else 0
                )
                self._save_manifest()
            raise

        self.manifest["total_primary_records"] = (
            int(self.manifest.get("total_primary_records", 0))
            + 1
        )
        self._save_manifest()
        self.seal_if_needed(entry)
        return identifier

    def _attach_assertion_index(
        self,
        identifier: str,
        record: Taxon,
        *,
        timestamp: str | None = None,
        commit: bool = True,
    ) -> tuple[bool, Mapping[str, Any] | None, str, str]:
        """Write one assertion to the selected index."""

        assertion = record.to_dict()
        assertion_hash = self.value_hash(assertion)
        assertion_json = json.dumps(
            assertion,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        previous = self.database_manager.assertion(
            record.provider,
            record.provider_id,
        )

        changed = self.database_manager.attach_assertion(
            identifier=identifier,
            record=record,
            assertion_json=assertion_json,
            assertion_hash=assertion_hash,
            timestamp=timestamp or now(),
            commit=commit,
        )

        return changed, previous, assertion_hash, assertion_json

    def attach_assertion(
        self,
        identifier: str,
        record: Taxon,
        *,
        commit: bool = True,
    ) -> bool:
        """
        Attach or update a provider assertion.

        Return True when an existing provider assertion changed.
        """

        if not identifier:
            raise ValueError("identifier is required")

        if self.taxon(identifier) is None:
            raise KeyError(
                f"Unknown Speciedex identifier: {identifier}"
            )

        timestamp = now()

        if commit:
            with self.database_manager.transaction():
                (
                    changed,
                    previous,
                    assertion_hash,
                    _assertion_json,
                ) = self._attach_assertion_index(
                    identifier,
                    record,
                    timestamp=timestamp,
                    commit=False,
                )
        else:
            (
                changed,
                previous,
                assertion_hash,
                _assertion_json,
            ) = self._attach_assertion_index(
                identifier,
                record,
                timestamp=timestamp,
                commit=False,
            )

        if changed:
            revision_number = (
                int(self.manifest.get("total_revisions", 0))
                + 1
            )
            volume_number = ((revision_number - 1) // 100000) + 1
            revision = {
                "schema_version": SCHEMA_VERSION,
                "event": "provider_assertion_changed",
                "speciedex_id": identifier,
                "provider": record.provider,
                "provider_id": record.provider_id,
                "changed_at": timestamp,
                "previous_assertion_hash": (
                    previous.get("assertion_hash")
                    if previous is not None
                    else None
                ),
                "assertion_hash": assertion_hash,
                "assertion": record.to_dict(),
            }

            append_jsonl(
                self.revisions
                / f"revisions-{volume_number:06d}.jsonl",
                (revision,),
            )
            self.manifest["total_revisions"] = revision_number

        if commit:
            self._save_manifest()

        return changed

    def add_conflict(
        self,
        record: Taxon,
        candidates: list[str],
        reason: str,
    ) -> str:
        """Store one unresolved reconciliation conflict."""

        conflict = {
            "provider": record.provider,
            "provider_id": record.provider_id,
            "scientific_name": record.scientific_name,
            "canonical_name": record.canonical_name,
            "rank": record.rank,
            "kingdom": record.kingdom,
            "candidates": sorted(set(candidates)),
            "reason": normalize_space(reason),
            "created_at": now(),
        }

        stable_conflict = {
            key: value
            for key, value in conflict.items()
            if key != "created_at"
        }
        conflict_id = self.value_hash(stable_conflict)
        conflict["conflict_id"] = conflict_id

        conflict_json = json.dumps(
            conflict,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        inserted = self.database_manager.add_conflict(
            conflict_id=conflict_id,
            conflict_json=conflict_json,
            created_at=conflict["created_at"],
        )

        if inserted:
            append_jsonl(
                self.conflicts / "unresolved.jsonl",
                (conflict,),
            )
            self.manifest["total_conflicts"] = (
                int(self.manifest.get("total_conflicts", 0))
                + 1
            )
            self._save_manifest()

        return conflict_id

    def add_rejected(
        self,
        record: Taxon | Mapping[str, Any],
        reason: str,
    ) -> None:
        """Append a rejected provider record to the rejected archive."""

        if isinstance(record, Taxon):
            payload = record.to_dict()
            provider = normalize_key(record.provider) or "unknown"
        else:
            payload = dict(record)
            provider = (
                normalize_key(payload.get("provider"))
                or "unknown"
            )

        append_jsonl(
            self.rejected / f"{provider}.jsonl",
            (
                {
                    "schema_version": SCHEMA_VERSION,
                    "reason": normalize_space(reason),
                    "rejected_at": now(),
                    "record": payload,
                },
            ),
        )

    def statistics(self) -> dict[str, int]:
        """Return archive-wide taxonomic statistics."""

        ranks = self.database_manager.rank_counts(
            statuses=tuple(sorted(ACTIVE_STATUSES))
        )

        result = {
            output_name: int(ranks.get(rank, 0))
            for output_name, rank in STATISTIC_RANKS.items()
        }

        result.update(
            {
                "records_archived": self.database_manager.table_count(
                    "taxa"
                ),
                "source_assertions": self.database_manager.table_count(
                    "assertions"
                ),
                "source_identifiers": self.database_manager.table_count(
                    "source_ids"
                ),
                "synonyms": self.database_manager.table_count(
                    "synonyms"
                ),
                "unresolved_conflicts": self.database_manager.table_count(
                    "conflicts"
                ),
                "volumes": len(self.manifest.get("volumes", [])),
                "sealed_volumes": sum(
                    1
                    for entry in self.manifest.get("volumes", [])
                    if isinstance(entry, dict)
                    and bool(entry.get("sealed"))
                ),
                "revisions": int(
                    self.manifest.get("total_revisions", 0)
                ),
            }
        )

        return result

    def provider_statistics(self) -> dict[str, dict[str, int]]:
        """Return provider-specific index totals."""

        return self.database_manager.provider_statistics()

    def verify(self) -> list[str]:
        """Verify manifest, volume, and selected database consistency."""

        errors: list[str] = []
        volumes = self.manifest.get("volumes", [])

        if not isinstance(volumes, list):
            return ["Manifest volumes field is not a list."]

        manifest_record_count = 0
        seen_files: set[str] = set()
        active_volume = self.manifest.get("active_volume")

        for index, entry in enumerate(volumes, start=1):
            if not isinstance(entry, dict):
                errors.append(
                    f"Invalid volume entry at position {index}."
                )
                continue

            relative_file = normalize_space(entry.get("file"))

            if not relative_file:
                errors.append(
                    f"Volume entry at position {index} has no file."
                )
                continue

            if relative_file in seen_files:
                errors.append(
                    f"Duplicate volume entry: {relative_file}"
                )

            seen_files.add(relative_file)
            path = self.root / relative_file

            if not path.exists():
                errors.append(f"Missing volume: {relative_file}")
                continue

            actual_size = path.stat().st_size
            expected_size = int(entry.get("size_bytes", 0))

            if actual_size != expected_size:
                errors.append(
                    "Size mismatch: "
                    f"{relative_file}; manifest={expected_size}, "
                    f"actual={actual_size}"
                )

            actual_lines = self._count_jsonl_lines(path)
            expected_lines = int(entry.get("record_count", 0))

            if actual_lines != expected_lines:
                errors.append(
                    "Record-count mismatch: "
                    f"{relative_file}; manifest={expected_lines}, "
                    f"actual={actual_lines}"
                )

            manifest_record_count += expected_lines
            sealed = bool(entry.get("sealed"))
            expected_hash = entry.get("sha256")

            if sealed:
                if not expected_hash:
                    errors.append(
                        f"Sealed volume has no hash: {relative_file}"
                    )
                elif file_hash(path) != expected_hash:
                    errors.append(
                        f"Hash mismatch: {relative_file}"
                    )
            elif relative_file != active_volume:
                errors.append(
                    f"Unsealed non-active volume: {relative_file}"
                )

        manifest_total = int(
            self.manifest.get("total_primary_records", 0)
        )

        if manifest_record_count != manifest_total:
            errors.append(
                "Manifest primary-record total does not match "
                f"volume totals: manifest={manifest_total}, "
                f"volumes={manifest_record_count}"
            )

        database_total = self.database_manager.table_count("taxa")

        if database_total != manifest_total:
            errors.append(
                f"{self.backend_name} taxon total does not match "
                f"manifest total: database={database_total}, "
                f"manifest={manifest_total}"
            )

        errors.extend(self.database_manager.verify())
        return errors

    def rebuild_manifest_counts(self) -> None:
        """Recalculate volume and global counts without rewriting records."""

        total_records = 0

        for entry in self.manifest.get("volumes", []):
            if not isinstance(entry, dict):
                continue

            path = self.root / normalize_space(entry.get("file"))

            if not path.is_file():
                continue

            entry["record_count"] = self._count_jsonl_lines(path)
            entry["size_bytes"] = path.stat().st_size
            total_records += int(entry["record_count"])

            if bool(entry.get("sealed")):
                entry["sha256"] = file_hash(path)

        self.manifest["total_primary_records"] = total_records
        self.manifest["total_revisions"] = (
            self._count_directory_jsonl(self.revisions)
        )
        self.manifest["total_conflicts"] = (
            self.database_manager.table_count("conflicts")
        )
        self._save_manifest()

    def rebuild_index(self, *, clear: bool = True) -> int:
        """
        Rebuild canonical taxon rows from JSONL volumes.

        Source assertions and synonyms are not reconstructible from canonical
        primary records alone unless they are separately journaled. The method
        therefore rebuilds canonical taxa only, matching both backend classes'
        ``rebuild_from_records`` contract.
        """

        if clear:
            self.database_manager.clear()

        return self.database_manager.rebuild_from_records(
            self.iter_primary_records(include_locations=True)
        )

    def iter_primary_records(
        self,
        *,
        include_locations: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Iterate canonical records in manifest volume order."""

        for entry in self.manifest.get("volumes", []):
            if not isinstance(entry, dict):
                continue

            relative_file = normalize_space(entry.get("file"))

            if not relative_file:
                continue

            path = self.root / relative_file

            if not path.is_file():
                continue

            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    stripped = line.strip()

                    if not stripped:
                        continue

                    try:
                        value = json.loads(stripped)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            "Invalid JSONL in "
                            f"{relative_file}:{line_number}: {error}"
                        ) from error

                    if not isinstance(value, dict):
                        continue

                    if include_locations:
                        value = dict(value)
                        value["_volume_file"] = relative_file
                        value["_line_number"] = line_number

                    yield value

    def describe(self) -> dict[str, Any]:
        """Return non-secret archive and database metadata."""

        return {
            "root": self.root.as_posix(),
            "manifest": self.manifest_path.as_posix(),
            "target_volume_bytes": self.target_bytes,
            "maximum_volume_bytes": self.maximum_bytes,
            "database": self.database_manager.describe(),
            "volumes": len(self.manifest.get("volumes", [])),
            "closed": self._closed,
        }

    def _table_count(self, table: str) -> int:
        """Compatibility wrapper for older callers."""

        return self.database_manager.table_count(table)

    @staticmethod
    def _count_jsonl_lines(path: Path) -> int:
        """Count nonempty lines in a JSONL file."""

        count = 0

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1

        return count

    @classmethod
    def _count_directory_jsonl(cls, directory: Path) -> int:
        """Count all JSONL records in a directory."""

        if not directory.is_dir():
            return 0

        return sum(
            cls._count_jsonl_lines(path)
            for path in sorted(directory.glob("*.jsonl"))
        )
