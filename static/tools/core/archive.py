#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/archive.py

Append-only taxonomic archive manager.

This module owns:

- canonical taxon storage,
- SQLite indexing,
- source-identifier mappings,
- provider assertions,
- synonym indexing,
- revision events,
- conflict records,
- JSONL volume rotation,
- archive manifests,
- archive statistics,
- archive verification.

Provider implementations must not write directly to the archive. They return
normalized Taxon objects, and the main ingestion process passes those records
to this Archive class.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from providers.common import Taxon


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
        str(
            value
            if value is not None
            else ""
        ).strip().split()
    )


def normalize_key(value: Any) -> str:
    """Normalize text for deterministic database comparisons."""

    return normalize_space(
        value
    ).casefold()


def read_json(
    path: Path,
    default: Any,
) -> Any:
    """Read JSON or return the supplied default on failure."""

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return default


def write_json(
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
            os.fsync(
                handle.fileno()
            )
            temporary = Path(
                handle.name
            )

        temporary.replace(
            path
        )

    finally:
        if (
            temporary is not None
            and temporary.exists()
        ):
            temporary.unlink(
                missing_ok=True
            )


def append_jsonl(
    path: Path,
    values: Iterable[
        dict[str, Any]
    ],
) -> int:
    """Append JSON objects to a JSONL file and fsync the result."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    count = 0

    with path.open(
        mode="a",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        for value in values:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(
                        ",",
                        ":",
                    ),
                )
            )
            handle.write(
                "\n"
            )
            count += 1

        handle.flush()
        os.fsync(
            handle.fileno()
        )

    return count


def file_hash(
    path: Path,
) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(
                chunk
            )

    return digest.hexdigest()


class Archive:
    """
    Append-only Speciedex archive and SQLite index.

    The JSONL volumes are the durable canonical archive. SQLite provides the
    fast local index used for reconciliation, statistics, and source lookup.
    """

    def __init__(
        self,
        root: Path,
        target_bytes: int,
        maximum_bytes: int,
    ) -> None:
        if target_bytes < 1:
            raise ValueError(
                "target_bytes must be positive"
            )

        if maximum_bytes < 1:
            raise ValueError(
                "maximum_bytes must be positive"
            )

        if target_bytes >= maximum_bytes:
            raise ValueError(
                "target_bytes must be below "
                "maximum_bytes"
            )

        self.root = Path(
            root
        )

        self.volumes = (
            self.root
            / "volumes"
        )

        self.revisions = (
            self.root
            / "revisions"
        )

        self.conflicts = (
            self.root
            / "conflicts"
        )

        self.provider_states = (
            self.root
            / "provider-state"
        )

        self.rejected = (
            self.root
            / "rejected"
        )

        self.manifest_path = (
            self.root
            / "manifest.json"
        )

        self.database_path = (
            self.root
            / "index.sqlite3"
        )

        self.target_bytes = int(
            target_bytes
        )

        self.maximum_bytes = int(
            maximum_bytes
        )

        self._closed = False

        for directory in (
            self.root,
            self.volumes,
            self.revisions,
            self.conflicts,
            self.provider_states,
            self.rejected,
        ):
            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

        self.database = sqlite3.connect(
            self.database_path,
            timeout=60,
        )

        self.database.row_factory = (
            sqlite3.Row
        )

        self._configure_database()
        self._initialize_schema()

        self.manifest = self._load_manifest()
        self._repair_manifest_defaults()
        self._save_manifest()

    def __enter__(
        self,
    ) -> Archive:
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def _configure_database(
        self,
    ) -> None:
        """Configure SQLite for durable archive operations."""

        self.database.execute(
            "PRAGMA foreign_keys=ON"
        )

        self.database.execute(
            "PRAGMA journal_mode=WAL"
        )

        self.database.execute(
            "PRAGMA synchronous=FULL"
        )

        self.database.execute(
            "PRAGMA temp_store=MEMORY"
        )

        self.database.execute(
            "PRAGMA busy_timeout=60000"
        )

        self.database.execute(
            "PRAGMA wal_autocheckpoint=1000"
        )

    def _initialize_schema(
        self,
    ) -> None:
        """Create the SQLite index schema."""

        self.database.executescript(
            """
            CREATE TABLE IF NOT EXISTS taxa(
                speciedex_id TEXT PRIMARY KEY,
                identity_key TEXT NOT NULL,
                scientific_name TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                rank TEXT NOT NULL,
                status TEXT NOT NULL,
                authorship TEXT NOT NULL,
                kingdom TEXT NOT NULL,
                phylum TEXT NOT NULL,
                class_name TEXT NOT NULL,
                order_name TEXT NOT NULL,
                family TEXT NOT NULL,
                genus TEXT NOT NULL,
                record_json TEXT NOT NULL,
                record_hash TEXT NOT NULL,
                volume_file TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS taxa_identity
            ON taxa(identity_key);

            CREATE INDEX IF NOT EXISTS taxa_name
            ON taxa(
                canonical_name,
                rank,
                kingdom
            );

            CREATE INDEX IF NOT EXISTS taxa_rank
            ON taxa(rank);

            CREATE INDEX IF NOT EXISTS taxa_status
            ON taxa(status);

            CREATE INDEX IF NOT EXISTS taxa_kingdom
            ON taxa(kingdom);

            CREATE TABLE IF NOT EXISTS source_ids(
                provider TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                speciedex_id TEXT NOT NULL,
                PRIMARY KEY(
                    provider,
                    provider_id
                )
            );

            CREATE INDEX IF NOT EXISTS source_ids_taxon
            ON source_ids(speciedex_id);

            CREATE TABLE IF NOT EXISTS assertions(
                provider TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                speciedex_id TEXT NOT NULL,
                assertion_json TEXT NOT NULL,
                assertion_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(
                    provider,
                    provider_id
                )
            );

            CREATE INDEX IF NOT EXISTS assertions_taxon
            ON assertions(speciedex_id);

            CREATE TABLE IF NOT EXISTS synonyms(
                synonym_key TEXT NOT NULL,
                speciedex_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                PRIMARY KEY(
                    synonym_key,
                    speciedex_id,
                    provider
                )
            );

            CREATE INDEX IF NOT EXISTS synonyms_taxon
            ON synonyms(speciedex_id);

            CREATE INDEX IF NOT EXISTS synonyms_name
            ON synonyms(synonym_key);

            CREATE TABLE IF NOT EXISTS conflicts(
                conflict_id TEXT PRIMARY KEY,
                conflict_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS archive_metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        self.database.execute(
            """
            INSERT INTO archive_metadata(
                key,
                value
            )
            VALUES(
                'schema_version',
                ?
            )
            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
            """,
            (
                str(
                    SCHEMA_VERSION
                ),
            ),
        )

        self.database.commit()

    def _load_manifest(
        self,
    ) -> dict[str, Any]:
        """Load the manifest or create a fresh one."""

        manifest = read_json(
            self.manifest_path,
            {},
        )

        if not isinstance(
            manifest,
            dict,
        ):
            manifest = {}

        if not manifest:
            manifest = {
                "schema_version": (
                    SCHEMA_VERSION
                ),
                "generated_at": now(),
                "record_format": "jsonl",
                "target_volume_bytes": (
                    self.target_bytes
                ),
                "maximum_volume_bytes": (
                    self.maximum_bytes
                ),
                "total_primary_records": 0,
                "total_revisions": 0,
                "total_conflicts": 0,
                "volumes": [],
                "active_volume": None,
            }

        return manifest

    def _repair_manifest_defaults(
        self,
    ) -> None:
        """Add missing manifest fields without discarding existing state."""

        defaults = {
            "schema_version": (
                SCHEMA_VERSION
            ),
            "generated_at": now(),
            "record_format": "jsonl",
            "target_volume_bytes": (
                self.target_bytes
            ),
            "maximum_volume_bytes": (
                self.maximum_bytes
            ),
            "total_primary_records": 0,
            "total_revisions": 0,
            "total_conflicts": 0,
            "volumes": [],
            "active_volume": None,
        }

        for key, value in (
            defaults.items()
        ):
            if key not in self.manifest:
                self.manifest[key] = value

        if not isinstance(
            self.manifest.get(
                "volumes"
            ),
            list,
        ):
            self.manifest[
                "volumes"
            ] = []

        self.manifest[
            "schema_version"
        ] = SCHEMA_VERSION

        self.manifest[
            "target_volume_bytes"
        ] = self.target_bytes

        self.manifest[
            "maximum_volume_bytes"
        ] = self.maximum_bytes

    def _save_manifest(
        self,
    ) -> None:
        """Persist the archive manifest atomically."""

        self.manifest[
            "generated_at"
        ] = now()

        write_json(
            self.manifest_path,
            self.manifest,
        )

    def close(
        self,
    ) -> None:
        """Commit, checkpoint, and close the SQLite index."""

        if self._closed:
            return

        try:
            self.database.commit()

            try:
                self.database.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                )
            except sqlite3.DatabaseError:
                pass

        finally:
            self.database.close()
            self._closed = True

    @staticmethod
    def value_hash(
        value: Any,
    ) -> str:
        """Create a deterministic SHA-256 hash of JSON-compatible data."""

        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
            ).encode(
                "utf-8"
            )
        ).hexdigest()

    def identity_key(
        self,
        record: Taxon,
    ) -> str:
        """Build the canonical Speciedex reconciliation key."""

        return "|".join(
            (
                normalize_key(
                    record.canonical_name
                ),
                normalize_key(
                    record.rank
                ),
                normalize_key(
                    record.kingdom
                ),
                normalize_key(
                    record.authorship
                ),
            )
        )

    @staticmethod
    def speciedex_id(
        identity_key: str,
    ) -> str:
        """Create a deterministic Speciedex identifier."""

        digest = hashlib.sha256(
            identity_key.encode(
                "utf-8"
            )
        ).hexdigest()

        return (
            "spx:sha256:"
            + digest
        )

    def active_volume(
        self,
    ) -> dict[str, Any]:
        """Return the active JSONL volume, creating one when necessary."""

        active_name = self.manifest.get(
            "active_volume"
        )

        for entry in self.manifest[
            "volumes"
        ]:
            if not isinstance(
                entry,
                dict,
            ):
                continue

            if (
                entry.get("file")
                == active_name
                and not bool(
                    entry.get(
                        "sealed"
                    )
                )
            ):
                return entry

        number = (
            self._next_volume_number()
        )

        entry = {
            "file": (
                "volumes/"
                f"species-{number:06d}.jsonl"
            ),
            "record_count": 0,
            "size_bytes": 0,
            "sha256": None,
            "sealed": False,
            "created_at": now(),
            "sealed_at": None,
        }

        self.manifest[
            "volumes"
        ].append(
            entry
        )

        self.manifest[
            "active_volume"
        ] = entry["file"]

        self._save_manifest()

        return entry

    def _next_volume_number(
        self,
    ) -> int:
        """Determine the next available volume number."""

        highest = 0

        for entry in self.manifest.get(
            "volumes",
            [],
        ):
            if not isinstance(
                entry,
                dict,
            ):
                continue

            filename = Path(
                str(
                    entry.get(
                        "file",
                        "",
                    )
                )
            ).stem

            suffix = filename.rsplit(
                "-",
                1,
            )[-1]

            try:
                highest = max(
                    highest,
                    int(suffix),
                )
            except ValueError:
                continue

        return highest + 1

    def _seal_volume(
        self,
        entry: dict[str, Any],
    ) -> None:
        """Seal a JSONL volume and record its checksum."""

        path = (
            self.root
            / str(
                entry["file"]
            )
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Cannot seal missing volume: "
                f"{entry['file']}"
            )

        entry[
            "size_bytes"
        ] = path.stat().st_size

        entry[
            "record_count"
        ] = self._count_jsonl_lines(
            path
        )

        entry["sealed"] = True
        entry["sealed_at"] = now()
        entry["sha256"] = file_hash(
            path
        )

        if (
            self.manifest.get(
                "active_volume"
            )
            == entry["file"]
        ):
            self.manifest[
                "active_volume"
            ] = None

        self._save_manifest()

    def seal_if_needed(
        self,
        entry: dict[str, Any],
    ) -> None:
        """Seal a volume once it reaches the configured target size."""

        path = (
            self.root
            / str(
                entry["file"]
            )
        )

        entry[
            "size_bytes"
        ] = (
            path.stat().st_size
            if path.exists()
            else 0
        )

        if (
            entry["size_bytes"]
            >= self.target_bytes
        ):
            self._seal_volume(
                entry
            )
        else:
            self._save_manifest()

    def source_match(
        self,
        provider: str,
        provider_id: str,
    ) -> str | None:
        """Find an existing taxon through a provider source identifier."""

        row = self.database.execute(
            """
            SELECT speciedex_id
            FROM source_ids
            WHERE provider = ?
              AND provider_id = ?
            """,
            (
                provider,
                provider_id,
            ),
        ).fetchone()

        if row is None:
            return None

        return str(
            row["speciedex_id"]
        )

    def identity_candidates(
        self,
        identity_key: str,
    ) -> list[sqlite3.Row]:
        """Return exact identity-key candidates."""

        return list(
            self.database.execute(
                """
                SELECT *
                FROM taxa
                WHERE identity_key = ?
                """,
                (
                    identity_key,
                ),
            )
        )

    def name_candidates(
        self,
        record: Taxon,
    ) -> list[sqlite3.Row]:
        """Return same-name, same-rank, same-kingdom candidates."""

        return list(
            self.database.execute(
                """
                SELECT *
                FROM taxa
                WHERE canonical_name = ?
                  AND rank = ?
                  AND kingdom = ?
                """,
                (
                    normalize_key(
                        record.canonical_name
                    ),
                    normalize_key(
                        record.rank
                    ),
                    normalize_key(
                        record.kingdom
                    ),
                ),
            )
        )

    def taxon(
        self,
        identifier: str,
    ) -> sqlite3.Row | None:
        """Read one indexed canonical taxon."""

        return self.database.execute(
            """
            SELECT *
            FROM taxa
            WHERE speciedex_id = ?
            """,
            (
                identifier,
            ),
        ).fetchone()

    def add_primary(
        self,
        record: Taxon,
    ) -> str:
        """Create a new canonical taxon and attach its first assertion."""

        identity_key = self.identity_key(
            record
        )

        identifier = self.speciedex_id(
            identity_key
        )

        existing = self.taxon(
            identifier
        )

        if existing is not None:
            self.attach_assertion(
                identifier,
                record,
            )
            return identifier

        first_seen = (
            record.retrieved_at
            or now()
        )

        primary = {
            "schema_version": (
                SCHEMA_VERSION
            ),
            "speciedex_id": identifier,
            "identity_key": identity_key,
            "canonical_name": (
                record.canonical_name
            ),
            "scientific_name": (
                record.scientific_name
            ),
            "rank": record.rank,
            "status": record.status,
            "authorship": (
                record.authorship
            ),
            "taxonomy": {
                "kingdom": (
                    record.kingdom
                ),
                "phylum": record.phylum,
                "class": (
                    record.class_name
                ),
                "order": record.order,
                "family": record.family,
                "genus": record.genus,
            },
            "first_seen": first_seen,
            "initial_source": {
                "provider": (
                    record.provider
                ),
                "provider_id": (
                    record.provider_id
                ),
                "url": (
                    record.source_url
                ),
            },
        }

        encoded = json.dumps(
            primary,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        estimated_size = (
            len(
                encoded.encode(
                    "utf-8"
                )
            )
            + 1
        )

        entry = self.active_volume()

        path = (
            self.root
            / str(
                entry["file"]
            )
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
                + estimated_size
                > self.maximum_bytes
            )
        ):
            self._seal_volume(
                entry
            )

            entry = self.active_volume()

            path = (
                self.root
                / str(
                    entry["file"]
                )
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

        append_jsonl(
            path,
            (
                primary,
            ),
        )

        entry[
            "record_count"
        ] = line_number

        entry[
            "size_bytes"
        ] = path.stat().st_size

        timestamp = first_seen

        try:
            self.database.execute(
                """
                INSERT INTO taxa(
                    speciedex_id,
                    identity_key,
                    scientific_name,
                    canonical_name,
                    rank,
                    status,
                    authorship,
                    kingdom,
                    phylum,
                    class_name,
                    order_name,
                    family,
                    genus,
                    record_json,
                    record_hash,
                    volume_file,
                    line_number,
                    created_at,
                    updated_at
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    identifier,
                    identity_key,
                    normalize_key(
                        record.scientific_name
                    ),
                    normalize_key(
                        record.canonical_name
                    ),
                    normalize_key(
                        record.rank
                    ),
                    normalize_key(
                        record.status
                    ),
                    normalize_key(
                        record.authorship
                    ),
                    normalize_key(
                        record.kingdom
                    ),
                    normalize_key(
                        record.phylum
                    ),
                    normalize_key(
                        record.class_name
                    ),
                    normalize_key(
                        record.order
                    ),
                    normalize_key(
                        record.family
                    ),
                    normalize_key(
                        record.genus
                    ),
                    encoded,
                    self.value_hash(
                        primary
                    ),
                    entry["file"],
                    line_number,
                    timestamp,
                    timestamp,
                ),
            )

            self.attach_assertion(
                identifier,
                record,
                commit=False,
            )

            self.database.commit()

        except Exception:
            self.database.rollback()
            raise

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

        self._save_manifest()
        self.seal_if_needed(
            entry
        )

        return identifier

    def attach_assertion(
        self,
        identifier: str,
        record: Taxon,
        *,
        commit: bool = True,
    ) -> bool:
        """
        Attach or update a provider assertion.

        Returns True when an existing provider assertion changed.
        """

        if not identifier:
            raise ValueError(
                "identifier is required"
            )

        if self.taxon(
            identifier
        ) is None:
            raise KeyError(
                f"Unknown Speciedex identifier: "
                f"{identifier}"
            )

        assertion = record.to_dict()

        assertion_hash = self.value_hash(
            assertion
        )

        previous = self.database.execute(
            """
            SELECT
                assertion_hash,
                assertion_json
            FROM assertions
            WHERE provider = ?
              AND provider_id = ?
            """,
            (
                record.provider,
                record.provider_id,
            ),
        ).fetchone()

        changed = bool(
            previous is not None
            and previous[
                "assertion_hash"
            ]
            != assertion_hash
        )

        assertion_json = json.dumps(
            assertion,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        timestamp = now()

        try:
            self.database.execute(
                """
                INSERT INTO source_ids(
                    provider,
                    provider_id,
                    speciedex_id
                )
                VALUES(
                    ?, ?, ?
                )
                ON CONFLICT(
                    provider,
                    provider_id
                )
                DO UPDATE SET
                    speciedex_id =
                        excluded.speciedex_id
                """,
                (
                    record.provider,
                    record.provider_id,
                    identifier,
                ),
            )

            self.database.execute(
                """
                INSERT INTO assertions(
                    provider,
                    provider_id,
                    speciedex_id,
                    assertion_json,
                    assertion_hash,
                    updated_at
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(
                    provider,
                    provider_id
                )
                DO UPDATE SET
                    speciedex_id =
                        excluded.speciedex_id,
                    assertion_json =
                        excluded.assertion_json,
                    assertion_hash =
                        excluded.assertion_hash,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    record.provider,
                    record.provider_id,
                    identifier,
                    assertion_json,
                    assertion_hash,
                    timestamp,
                ),
            )

            self.database.execute(
                """
                DELETE FROM synonyms
                WHERE speciedex_id = ?
                  AND provider = ?
                """,
                (
                    identifier,
                    record.provider,
                ),
            )

            for synonym in (
                record.synonyms
            ):
                synonym_key = normalize_key(
                    synonym
                )

                if not synonym_key:
                    continue

                self.database.execute(
                    """
                    INSERT OR IGNORE INTO synonyms(
                        synonym_key,
                        speciedex_id,
                        provider
                    )
                    VALUES(
                        ?, ?, ?
                    )
                    """,
                    (
                        synonym_key,
                        identifier,
                        record.provider,
                    ),
                )

            self.database.execute(
                """
                UPDATE taxa
                SET updated_at = ?
                WHERE speciedex_id = ?
                """,
                (
                    timestamp,
                    identifier,
                ),
            )

            if changed:
                revision_number = (
                    int(
                        self.manifest.get(
                            "total_revisions",
                            0,
                        )
                    )
                    + 1
                )

                volume_number = (
                    (
                        revision_number
                        - 1
                    )
                    // 100000
                    + 1
                )

                revision = {
                    "schema_version": (
                        SCHEMA_VERSION
                    ),
                    "event": (
                        "provider_assertion_changed"
                    ),
                    "speciedex_id": (
                        identifier
                    ),
                    "provider": (
                        record.provider
                    ),
                    "provider_id": (
                        record.provider_id
                    ),
                    "changed_at": timestamp,
                    "previous_assertion_hash": (
                        previous[
                            "assertion_hash"
                        ]
                        if previous
                        else None
                    ),
                    "assertion_hash": (
                        assertion_hash
                    ),
                    "assertion": assertion,
                }

                append_jsonl(
                    self.revisions
                    / (
                        "revisions-"
                        f"{volume_number:06d}.jsonl"
                    ),
                    (
                        revision,
                    ),
                )

                self.manifest[
                    "total_revisions"
                ] = revision_number

            if commit:
                self.database.commit()
                self._save_manifest()

        except Exception:
            if commit:
                self.database.rollback()

            raise

        return changed

    def add_conflict(
        self,
        record: Taxon,
        candidates: list[str],
        reason: str,
    ) -> str:
        """Store one unresolved reconciliation conflict."""

        conflict = {
            "provider": (
                record.provider
            ),
            "provider_id": (
                record.provider_id
            ),
            "scientific_name": (
                record.scientific_name
            ),
            "canonical_name": (
                record.canonical_name
            ),
            "rank": record.rank,
            "kingdom": (
                record.kingdom
            ),
            "candidates": sorted(
                set(
                    candidates
                )
            ),
            "reason": reason,
            "created_at": now(),
        }

        stable_conflict = {
            key: value
            for key, value
            in conflict.items()
            if key != "created_at"
        }

        conflict_id = self.value_hash(
            stable_conflict
        )

        conflict[
            "conflict_id"
        ] = conflict_id

        conflict_json = json.dumps(
            conflict,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        cursor = self.database.execute(
            """
            INSERT OR IGNORE INTO conflicts(
                conflict_id,
                conflict_json,
                created_at
            )
            VALUES(
                ?, ?, ?
            )
            """,
            (
                conflict_id,
                conflict_json,
                conflict["created_at"],
            ),
        )

        inserted = (
            cursor.rowcount > 0
        )

        if inserted:
            append_jsonl(
                self.conflicts
                / "unresolved.jsonl",
                (
                    conflict,
                ),
            )

            self.manifest[
                "total_conflicts"
            ] = (
                int(
                    self.manifest.get(
                        "total_conflicts",
                        0,
                    )
                )
                + 1
            )

        self.database.commit()

        if inserted:
            self._save_manifest()

        return conflict_id

    def add_rejected(
        self,
        record: Taxon | dict[str, Any],
        reason: str,
    ) -> None:
        """Append a rejected provider record to the rejected archive."""

        if isinstance(
            record,
            Taxon,
        ):
            payload = (
                record.to_dict()
            )
            provider = (
                record.provider
            )
        else:
            payload = dict(
                record
            )
            provider = normalize_key(
                payload.get(
                    "provider",
                    "unknown",
                )
            ) or "unknown"

        append_jsonl(
            self.rejected
            / f"{provider}.jsonl",
            (
                {
                    "schema_version": (
                        SCHEMA_VERSION
                    ),
                    "reason": reason,
                    "rejected_at": now(),
                    "record": payload,
                },
            ),
        )

    def statistics(
        self,
    ) -> dict[str, int]:
        """Return archive-wide taxonomic statistics."""

        result: dict[str, int] = {}

        placeholders = ",".join(
            "?"
            for _ in ACTIVE_STATUSES
        )

        active_statuses = tuple(
            sorted(
                ACTIVE_STATUSES
            )
        )

        for (
            output_name,
            rank,
        ) in STATISTIC_RANKS.items():
            row = self.database.execute(
                (
                    "SELECT COUNT(*) AS count "
                    "FROM taxa "
                    "WHERE rank = ? "
                    f"AND status IN ({placeholders})"
                ),
                (
                    rank,
                    *active_statuses,
                ),
            ).fetchone()

            result[
                output_name
            ] = int(
                row["count"]
                if row
                else 0
            )

        result[
            "records_archived"
        ] = self._table_count(
            "taxa"
        )

        result[
            "source_assertions"
        ] = self._table_count(
            "assertions"
        )

        result[
            "source_identifiers"
        ] = self._table_count(
            "source_ids"
        )

        result[
            "synonyms"
        ] = self._table_count(
            "synonyms"
        )

        result[
            "unresolved_conflicts"
        ] = self._table_count(
            "conflicts"
        )

        result[
            "volumes"
        ] = len(
            self.manifest.get(
                "volumes",
                [],
            )
        )

        result[
            "sealed_volumes"
        ] = sum(
            1
            for entry
            in self.manifest.get(
                "volumes",
                [],
            )
            if (
                isinstance(
                    entry,
                    dict,
                )
                and bool(
                    entry.get(
                        "sealed"
                    )
                )
            )
        )

        result[
            "revisions"
        ] = int(
            self.manifest.get(
                "total_revisions",
                0,
            )
        )

        return result

    def provider_statistics(
        self,
    ) -> dict[str, dict[str, int]]:
        """Return assertion and identifier counts grouped by provider."""

        result: dict[
            str,
            dict[str, int],
        ] = {}

        rows = self.database.execute(
            """
            SELECT
                provider,
                COUNT(*) AS assertion_count
            FROM assertions
            GROUP BY provider
            ORDER BY provider
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            result[
                provider
            ] = {
                "assertions": int(
                    row[
                        "assertion_count"
                    ]
                ),
                "source_identifiers": 0,
                "synonyms": 0,
            }

        rows = self.database.execute(
            """
            SELECT
                provider,
                COUNT(*) AS source_count
            FROM source_ids
            GROUP BY provider
            ORDER BY provider
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            result.setdefault(
                provider,
                {
                    "assertions": 0,
                    "source_identifiers": 0,
                    "synonyms": 0,
                },
            )

            result[
                provider
            ][
                "source_identifiers"
            ] = int(
                row["source_count"]
            )

        rows = self.database.execute(
            """
            SELECT
                provider,
                COUNT(*) AS synonym_count
            FROM synonyms
            GROUP BY provider
            ORDER BY provider
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            result.setdefault(
                provider,
                {
                    "assertions": 0,
                    "source_identifiers": 0,
                    "synonyms": 0,
                },
            )

            result[
                provider
            ][
                "synonyms"
            ] = int(
                row["synonym_count"]
            )

        return result

    def verify(
        self,
    ) -> list[str]:
        """Verify manifest, volume, and SQLite consistency."""

        errors: list[str] = []

        volumes = self.manifest.get(
            "volumes",
            [],
        )

        if not isinstance(
            volumes,
            list,
        ):
            return [
                "Manifest volumes field is not a list."
            ]

        manifest_record_count = 0
        seen_files: set[str] = set()

        active_volume = self.manifest.get(
            "active_volume"
        )

        for index, entry in enumerate(
            volumes,
            start=1,
        ):
            if not isinstance(
                entry,
                dict,
            ):
                errors.append(
                    "Invalid volume entry at "
                    f"position {index}."
                )
                continue

            relative_file = normalize_space(
                entry.get(
                    "file"
                )
            )

            if not relative_file:
                errors.append(
                    "Volume entry at position "
                    f"{index} has no file."
                )
                continue

            if relative_file in seen_files:
                errors.append(
                    "Duplicate volume entry: "
                    f"{relative_file}"
                )

            seen_files.add(
                relative_file
            )

            path = (
                self.root
                / relative_file
            )

            if not path.exists():
                errors.append(
                    f"Missing volume: "
                    f"{relative_file}"
                )
                continue

            actual_size = (
                path.stat().st_size
            )

            expected_size = int(
                entry.get(
                    "size_bytes",
                    0,
                )
            )

            if (
                actual_size
                != expected_size
            ):
                errors.append(
                    "Size mismatch: "
                    f"{relative_file}; "
                    f"manifest={expected_size}, "
                    f"actual={actual_size}"
                )

            actual_lines = (
                self._count_jsonl_lines(
                    path
                )
            )

            expected_lines = int(
                entry.get(
                    "record_count",
                    0,
                )
            )

            if (
                actual_lines
                != expected_lines
            ):
                errors.append(
                    "Record-count mismatch: "
                    f"{relative_file}; "
                    f"manifest={expected_lines}, "
                    f"actual={actual_lines}"
                )

            manifest_record_count += (
                expected_lines
            )

            sealed = bool(
                entry.get(
                    "sealed"
                )
            )

            expected_hash = (
                entry.get(
                    "sha256"
                )
            )

            if sealed:
                if not expected_hash:
                    errors.append(
                        "Sealed volume has no hash: "
                        f"{relative_file}"
                    )
                elif (
                    file_hash(
                        path
                    )
                    != expected_hash
                ):
                    errors.append(
                        "Hash mismatch: "
                        f"{relative_file}"
                    )

            elif (
                relative_file
                != active_volume
            ):
                errors.append(
                    "Unsealed non-active volume: "
                    f"{relative_file}"
                )

        manifest_total = int(
            self.manifest.get(
                "total_primary_records",
                0,
            )
        )

        if (
            manifest_record_count
            != manifest_total
        ):
            errors.append(
                "Manifest primary-record total "
                "does not match volume totals: "
                f"manifest={manifest_total}, "
                f"volumes={manifest_record_count}"
            )

        database_total = (
            self._table_count(
                "taxa"
            )
        )

        if database_total != manifest_total:
            errors.append(
                "SQLite taxon total does not "
                "match manifest total: "
                f"sqlite={database_total}, "
                f"manifest={manifest_total}"
            )

        orphaned_sources = int(
            self.database.execute(
                """
                SELECT COUNT(*) AS count
                FROM source_ids AS source
                LEFT JOIN taxa AS taxon
                  ON taxon.speciedex_id =
                     source.speciedex_id
                WHERE taxon.speciedex_id IS NULL
                """
            ).fetchone()["count"]
        )

        if orphaned_sources:
            errors.append(
                "Orphaned source identifiers: "
                f"{orphaned_sources}"
            )

        orphaned_assertions = int(
            self.database.execute(
                """
                SELECT COUNT(*) AS count
                FROM assertions AS assertion
                LEFT JOIN taxa AS taxon
                  ON taxon.speciedex_id =
                     assertion.speciedex_id
                WHERE taxon.speciedex_id IS NULL
                """
            ).fetchone()["count"]
        )

        if orphaned_assertions:
            errors.append(
                "Orphaned provider assertions: "
                f"{orphaned_assertions}"
            )

        orphaned_synonyms = int(
            self.database.execute(
                """
                SELECT COUNT(*) AS count
                FROM synonyms AS synonym
                LEFT JOIN taxa AS taxon
                  ON taxon.speciedex_id =
                     synonym.speciedex_id
                WHERE taxon.speciedex_id IS NULL
                """
            ).fetchone()["count"]
        )

        if orphaned_synonyms:
            errors.append(
                "Orphaned synonyms: "
                f"{orphaned_synonyms}"
            )

        integrity = self.database.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if (
            integrity is None
            or str(
                integrity[0]
            ).casefold()
            != "ok"
        ):
            errors.append(
                "SQLite integrity check failed: "
                f"{integrity[0] if integrity else 'no result'}"
            )

        return errors

    def rebuild_manifest_counts(
        self,
    ) -> None:
        """Recalculate volume and global counts without rewriting records."""

        total_records = 0

        for entry in self.manifest.get(
            "volumes",
            [],
        ):
            if not isinstance(
                entry,
                dict,
            ):
                continue

            path = (
                self.root
                / str(
                    entry.get(
                        "file",
                        "",
                    )
                )
            )

            if not path.is_file():
                continue

            entry[
                "record_count"
            ] = self._count_jsonl_lines(
                path
            )

            entry[
                "size_bytes"
            ] = path.stat().st_size

            total_records += int(
                entry[
                    "record_count"
                ]
            )

            if bool(
                entry.get(
                    "sealed"
                )
            ):
                entry[
                    "sha256"
                ] = file_hash(
                    path
                )

        self.manifest[
            "total_primary_records"
        ] = total_records

        self.manifest[
            "total_revisions"
        ] = self._count_directory_jsonl(
            self.revisions
        )

        self.manifest[
            "total_conflicts"
        ] = self._table_count(
            "conflicts"
        )

        self._save_manifest()

    def iter_primary_records(
        self,
    ) -> Iterator[
        dict[str, Any]
    ]:
        """Iterate canonical records in manifest volume order."""

        for entry in self.manifest.get(
            "volumes",
            [],
        ):
            if not isinstance(
                entry,
                dict,
            ):
                continue

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
                        raise ValueError(
                            "Invalid JSONL in "
                            f"{relative_file}:"
                            f"{line_number}: "
                            f"{error}"
                        ) from error

                    if isinstance(
                        value,
                        dict,
                    ):
                        yield value

    def _table_count(
        self,
        table: str,
    ) -> int:
        """Return a row count from a trusted internal table name."""

        allowed = {
            "taxa",
            "source_ids",
            "assertions",
            "synonyms",
            "conflicts",
        }

        if table not in allowed:
            raise ValueError(
                f"Unsupported table: "
                f"{table}"
            )

        row = self.database.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    @staticmethod
    def _count_jsonl_lines(
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

    @classmethod
    def _count_directory_jsonl(
        cls,
        directory: Path,
    ) -> int:
        """Count all JSONL records in a directory."""

        total = 0

        if not directory.is_dir():
            return 0

        for path in sorted(
            directory.glob(
                "*.jsonl"
            )
        ):
            total += (
                cls._count_jsonl_lines(
                    path
                )
            )

        return total
