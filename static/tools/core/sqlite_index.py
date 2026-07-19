#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/sqlite_index.py

SQLite-backed canonical taxonomic index.

This module owns all direct SQLite operations for the Speciedex archive:

- database initialization,
- schema migrations,
- canonical taxon indexing,
- provider source identifiers,
- provider assertions,
- synonym indexing,
- conflict indexing,
- provider and archive aggregate queries,
- integrity verification,
- WAL checkpointing,
- transactions.

The JSONL archive remains the durable append-only record store. SQLite is the
rebuildable lookup and reconciliation index.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from providers.common import Taxon

from .archive import normalize_key, normalize_space, now


SQLITE_SCHEMA_VERSION = 1


class SQLiteIndexError(RuntimeError):
    """Raised when the SQLite index cannot complete an operation."""


class SQLiteIndex:
    """
    Rebuildable SQLite index for the Speciedex archive.

    This class does not write canonical JSONL volumes. It stores normalized
    canonical taxon metadata, provider assertions, source identifiers,
    synonyms, and conflict indexes for fast reconciliation.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 60.0,
        read_only: bool = False,
    ) -> None:
        self.path = Path(path)
        self.timeout = float(timeout)
        self.read_only = bool(read_only)
        self._closed = False
        self._transaction_depth = 0

        if not self.read_only:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self.connection = self._connect()
        self.connection.row_factory = sqlite3.Row

        self._configure_connection()

        if not self.read_only:
            self._initialize_schema()

    def __enter__(
        self,
    ) -> SQLiteIndex:
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def _connect(
        self,
    ) -> sqlite3.Connection:
        """Create the SQLite connection."""

        try:
            if self.read_only:
                uri = (
                    f"file:{self.path.resolve().as_posix()}"
                    "?mode=ro"
                )

                return sqlite3.connect(
                    uri,
                    timeout=self.timeout,
                    uri=True,
                )

            return sqlite3.connect(
                self.path,
                timeout=self.timeout,
            )

        except sqlite3.Error as error:
            raise SQLiteIndexError(
                f"Unable to open SQLite index "
                f"{self.path}: {error}"
            ) from error

    def _configure_connection(
        self,
    ) -> None:
        """Configure SQLite behavior."""

        try:
            self.connection.execute(
                "PRAGMA foreign_keys=ON"
            )

            self.connection.execute(
                "PRAGMA busy_timeout=60000"
            )

            self.connection.execute(
                "PRAGMA temp_store=MEMORY"
            )

            self.connection.execute(
                "PRAGMA cache_size=-32768"
            )

            if not self.read_only:
                self.connection.execute(
                    "PRAGMA journal_mode=WAL"
                )

                self.connection.execute(
                    "PRAGMA synchronous=FULL"
                )

                self.connection.execute(
                    "PRAGMA wal_autocheckpoint=1000"
                )

        except sqlite3.Error as error:
            raise SQLiteIndexError(
                f"Unable to configure SQLite index: "
                f"{error}"
            ) from error

    def _initialize_schema(
        self,
    ) -> None:
        """Create all tables and indexes."""

        try:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

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

                CREATE INDEX IF NOT EXISTS taxa_scientific_name
                ON taxa(scientific_name);

                CREATE INDEX IF NOT EXISTS taxa_rank
                ON taxa(rank);

                CREATE INDEX IF NOT EXISTS taxa_status
                ON taxa(status);

                CREATE INDEX IF NOT EXISTS taxa_kingdom
                ON taxa(kingdom);

                CREATE INDEX IF NOT EXISTS taxa_family
                ON taxa(family);

                CREATE INDEX IF NOT EXISTS taxa_genus
                ON taxa(genus);

                CREATE INDEX IF NOT EXISTS taxa_volume
                ON taxa(
                    volume_file,
                    line_number
                );

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

                CREATE INDEX IF NOT EXISTS source_ids_provider
                ON source_ids(provider);

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

                CREATE INDEX IF NOT EXISTS assertions_provider
                ON assertions(provider);

                CREATE INDEX IF NOT EXISTS assertions_updated
                ON assertions(updated_at);

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

                CREATE INDEX IF NOT EXISTS synonyms_name
                ON synonyms(synonym_key);

                CREATE INDEX IF NOT EXISTS synonyms_taxon
                ON synonyms(speciedex_id);

                CREATE INDEX IF NOT EXISTS synonyms_provider
                ON synonyms(provider);

                CREATE TABLE IF NOT EXISTS conflicts(
                    conflict_id TEXT PRIMARY KEY,
                    conflict_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS conflicts_created
                ON conflicts(created_at);
                """
            )

            self.set_metadata(
                "schema_version",
                str(SQLITE_SCHEMA_VERSION),
                commit=False,
            )

            self.connection.commit()

        except sqlite3.Error as error:
            self.connection.rollback()

            raise SQLiteIndexError(
                f"Unable to initialize SQLite schema: "
                f"{error}"
            ) from error

    @property
    def database(
        self,
    ) -> sqlite3.Connection:
        """
        Compatibility alias for code expecting archive.database.
        """

        return self.connection

    @contextmanager
    def transaction(
        self,
    ) -> Iterator[sqlite3.Connection]:
        """
        Run a transaction.

        Nested calls reuse the outer transaction rather than issuing nested
        BEGIN statements.
        """

        if self.read_only:
            raise SQLiteIndexError(
                "Cannot start a write transaction "
                "on a read-only SQLite index."
            )

        outermost = (
            self._transaction_depth == 0
        )

        try:
            if outermost:
                self.connection.execute(
                    "BEGIN IMMEDIATE"
                )

            self._transaction_depth += 1

            yield self.connection

            self._transaction_depth -= 1

            if outermost:
                self.connection.commit()

        except Exception:
            self._transaction_depth = max(
                0,
                self._transaction_depth - 1,
            )

            if outermost:
                self.connection.rollback()

            raise

    def commit(
        self,
    ) -> None:
        """Commit pending changes."""

        if not self.read_only:
            self.connection.commit()

    def rollback(
        self,
    ) -> None:
        """Rollback pending changes."""

        if not self.read_only:
            self.connection.rollback()

    def checkpoint(
        self,
        *,
        truncate: bool = False,
    ) -> None:
        """Checkpoint the WAL file."""

        if self.read_only:
            return

        mode = (
            "TRUNCATE"
            if truncate
            else "PASSIVE"
        )

        try:
            self.connection.execute(
                f"PRAGMA wal_checkpoint({mode})"
            )

        except sqlite3.Error as error:
            raise SQLiteIndexError(
                f"Unable to checkpoint SQLite WAL: "
                f"{error}"
            ) from error

    def close(
        self,
    ) -> None:
        """Commit and close the SQLite index."""

        if self._closed:
            return

        try:
            if not self.read_only:
                self.connection.commit()

                try:
                    self.connection.execute(
                        "PRAGMA wal_checkpoint(TRUNCATE)"
                    )
                except sqlite3.Error:
                    pass

        finally:
            self.connection.close()
            self._closed = True

    def set_metadata(
        self,
        key: str,
        value: Any,
        *,
        commit: bool = True,
    ) -> None:
        """Create or update one metadata value."""

        normalized_key = normalize_space(
            key
        )

        if not normalized_key:
            raise ValueError(
                "Metadata key cannot be empty."
            )

        self.connection.execute(
            """
            INSERT INTO archive_metadata(
                key,
                value
            )
            VALUES(
                ?,
                ?
            )
            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
            """,
            (
                normalized_key,
                str(value),
            ),
        )

        if commit:
            self.connection.commit()

    def metadata(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        """Read one metadata value."""

        row = self.connection.execute(
            """
            SELECT value
            FROM archive_metadata
            WHERE key = ?
            """,
            (
                normalize_space(key),
            ),
        ).fetchone()

        if row is None:
            return default

        return row["value"]

    def insert_taxon(
        self,
        *,
        identifier: str,
        identity_key: str,
        record: Taxon,
        primary_json: str,
        record_hash: str,
        volume_file: str,
        line_number: int,
        created_at: str,
        updated_at: str | None = None,
        commit: bool = True,
    ) -> None:
        """Insert one canonical taxon into the index."""

        timestamp = (
            updated_at
            or created_at
            or now()
        )

        try:
            self.connection.execute(
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
                    primary_json,
                    record_hash,
                    volume_file,
                    int(line_number),
                    created_at,
                    timestamp,
                ),
            )

            if commit:
                self.connection.commit()

        except sqlite3.IntegrityError as error:
            if commit:
                self.connection.rollback()

            raise SQLiteIndexError(
                f"Unable to insert canonical taxon "
                f"{identifier}: {error}"
            ) from error

    def update_taxon_timestamp(
        self,
        identifier: str,
        timestamp: str | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """Update a canonical taxon's last-modified timestamp."""

        self.connection.execute(
            """
            UPDATE taxa
            SET updated_at = ?
            WHERE speciedex_id = ?
            """,
            (
                timestamp or now(),
                identifier,
            ),
        )

        if commit:
            self.connection.commit()

    def taxon(
        self,
        identifier: str,
    ) -> sqlite3.Row | None:
        """Return one canonical taxon row."""

        return self.connection.execute(
            """
            SELECT *
            FROM taxa
            WHERE speciedex_id = ?
            """,
            (
                identifier,
            ),
        ).fetchone()

    def source_match(
        self,
        provider: str,
        provider_id: str,
    ) -> str | None:
        """Return a canonical identifier for a provider source ID."""

        row = self.connection.execute(
            """
            SELECT speciedex_id
            FROM source_ids
            WHERE provider = ?
              AND provider_id = ?
            """,
            (
                normalize_key(provider),
                normalize_space(provider_id),
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
            self.connection.execute(
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
            self.connection.execute(
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

    def synonym_candidates(
        self,
        synonym: str,
    ) -> list[str]:
        """Return canonical taxa indexed under a synonym."""

        rows = self.connection.execute(
            """
            SELECT DISTINCT speciedex_id
            FROM synonyms
            WHERE synonym_key = ?
            ORDER BY speciedex_id
            """,
            (
                normalize_key(
                    synonym
                ),
            ),
        )

        return [
            str(
                row["speciedex_id"]
            )
            for row in rows
        ]

    def attach_assertion(
        self,
        *,
        identifier: str,
        record: Taxon,
        assertion_json: str,
        assertion_hash: str,
        timestamp: str | None = None,
        commit: bool = True,
    ) -> bool:
        """
        Create or update one provider assertion.

        Returns True when an existing assertion changed.
        """

        current_timestamp = (
            timestamp
            or now()
        )

        previous = self.connection.execute(
            """
            SELECT
                assertion_hash,
                assertion_json
            FROM assertions
            WHERE provider = ?
              AND provider_id = ?
            """,
            (
                normalize_key(
                    record.provider
                ),
                normalize_space(
                    record.provider_id
                ),
            ),
        ).fetchone()

        changed = bool(
            previous is not None
            and previous["assertion_hash"]
            != assertion_hash
        )

        try:
            self.connection.execute(
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
                    normalize_key(
                        record.provider
                    ),
                    normalize_space(
                        record.provider_id
                    ),
                    identifier,
                ),
            )

            self.connection.execute(
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
                    normalize_key(
                        record.provider
                    ),
                    normalize_space(
                        record.provider_id
                    ),
                    identifier,
                    assertion_json,
                    assertion_hash,
                    current_timestamp,
                ),
            )

            self.replace_synonyms(
                identifier=identifier,
                provider=record.provider,
                synonyms=record.synonyms,
                commit=False,
            )

            self.update_taxon_timestamp(
                identifier,
                current_timestamp,
                commit=False,
            )

            if commit:
                self.connection.commit()

        except sqlite3.Error as error:
            if commit:
                self.connection.rollback()

            raise SQLiteIndexError(
                f"Unable to attach provider assertion "
                f"{record.provider}:"
                f"{record.provider_id}: {error}"
            ) from error

        return changed

    def assertion(
        self,
        provider: str,
        provider_id: str,
    ) -> sqlite3.Row | None:
        """Return one provider assertion row."""

        return self.connection.execute(
            """
            SELECT *
            FROM assertions
            WHERE provider = ?
              AND provider_id = ?
            """,
            (
                normalize_key(provider),
                normalize_space(provider_id),
            ),
        ).fetchone()

    def assertions_for_taxon(
        self,
        identifier: str,
    ) -> list[sqlite3.Row]:
        """Return all provider assertions for one taxon."""

        return list(
            self.connection.execute(
                """
                SELECT *
                FROM assertions
                WHERE speciedex_id = ?
                ORDER BY provider, provider_id
                """,
                (
                    identifier,
                ),
            )
        )

    def replace_synonyms(
        self,
        *,
        identifier: str,
        provider: str,
        synonyms: Iterable[str],
        commit: bool = True,
    ) -> None:
        """Replace one provider's synonym set for a canonical taxon."""

        normalized_provider = normalize_key(
            provider
        )

        self.connection.execute(
            """
            DELETE FROM synonyms
            WHERE speciedex_id = ?
              AND provider = ?
            """,
            (
                identifier,
                normalized_provider,
            ),
        )

        values = sorted(
            {
                normalize_key(
                    synonym
                )
                for synonym in synonyms
                if normalize_key(
                    synonym
                )
            }
        )

        self.connection.executemany(
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
            [
                (
                    synonym,
                    identifier,
                    normalized_provider,
                )
                for synonym in values
            ],
        )

        if commit:
            self.connection.commit()

    def add_conflict(
        self,
        *,
        conflict_id: str,
        conflict_json: str,
        created_at: str,
        commit: bool = True,
    ) -> bool:
        """
        Insert one unresolved conflict.

        Returns True when the conflict was newly inserted.
        """

        cursor = self.connection.execute(
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
                created_at,
            ),
        )

        inserted = (
            cursor.rowcount > 0
        )

        if commit:
            self.connection.commit()

        return inserted

    def conflict(
        self,
        conflict_id: str,
    ) -> sqlite3.Row | None:
        """Return one conflict row."""

        return self.connection.execute(
            """
            SELECT *
            FROM conflicts
            WHERE conflict_id = ?
            """,
            (
                conflict_id,
            ),
        ).fetchone()

    def iter_conflicts(
        self,
    ) -> Iterator[sqlite3.Row]:
        """Iterate unresolved conflicts."""

        yield from self.connection.execute(
            """
            SELECT *
            FROM conflicts
            ORDER BY created_at, conflict_id
            """
        )

    def table_count(
        self,
        table: str,
    ) -> int:
        """Return a row count from a trusted table."""

        allowed = {
            "taxa",
            "source_ids",
            "assertions",
            "synonyms",
            "conflicts",
            "archive_metadata",
        }

        if table not in allowed:
            raise ValueError(
                f"Unsupported SQLite table: "
                f"{table}"
            )

        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    def rank_counts(
        self,
        *,
        statuses: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by rank."""

        query = (
            "SELECT rank, COUNT(*) AS count "
            "FROM taxa"
        )

        parameters: tuple[Any, ...] = ()

        normalized_statuses = tuple(
            sorted(
                {
                    normalize_key(
                        status
                    )
                    for status in (
                        statuses or []
                    )
                    if normalize_key(
                        status
                    )
                }
            )
        )

        if normalized_statuses:
            placeholders = ",".join(
                "?"
                for _ in normalized_statuses
            )

            query += (
                " WHERE status IN "
                f"({placeholders})"
            )

            parameters = (
                normalized_statuses
            )

        query += (
            " GROUP BY rank "
            "ORDER BY count DESC, rank"
        )

        return {
            str(
                row["rank"]
            ): int(
                row["count"]
            )
            for row in self.connection.execute(
                query,
                parameters,
            )
        }

    def status_counts(
        self,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by status."""

        return {
            str(
                row["status"]
            ): int(
                row["count"]
            )
            for row in self.connection.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS count
                FROM taxa
                GROUP BY status
                ORDER BY count DESC, status
                """
            )
        }

    def kingdom_counts(
        self,
        *,
        statuses: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by kingdom."""

        query = (
            "SELECT kingdom, COUNT(*) AS count "
            "FROM taxa "
            "WHERE kingdom <> ''"
        )

        parameters: tuple[Any, ...] = ()

        normalized_statuses = tuple(
            sorted(
                {
                    normalize_key(
                        status
                    )
                    for status in (
                        statuses or []
                    )
                    if normalize_key(
                        status
                    )
                }
            )
        )

        if normalized_statuses:
            placeholders = ",".join(
                "?"
                for _ in normalized_statuses
            )

            query += (
                " AND status IN "
                f"({placeholders})"
            )

            parameters = (
                normalized_statuses
            )

        query += (
            " GROUP BY kingdom "
            "ORDER BY count DESC, kingdom"
        )

        return {
            str(
                row["kingdom"]
            ): int(
                row["count"]
            )
            for row in self.connection.execute(
                query,
                parameters,
            )
        }

    def provider_statistics(
        self,
    ) -> dict[str, dict[str, int]]:
        """Return provider-specific archive totals."""

        result: dict[
            str,
            dict[str, int],
        ] = {}

        rows = self.connection.execute(
            """
            SELECT
                provider,
                COUNT(*) AS assertion_count,
                COUNT(
                    DISTINCT speciedex_id
                ) AS canonical_taxa
            FROM assertions
            GROUP BY provider
            ORDER BY provider
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            result[provider] = {
                "assertions": int(
                    row["assertion_count"]
                ),
                "canonical_taxa": int(
                    row["canonical_taxa"]
                ),
                "source_identifiers": 0,
                "synonyms": 0,
            }

        rows = self.connection.execute(
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
                    "canonical_taxa": 0,
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

        rows = self.connection.execute(
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
                    "canonical_taxa": 0,
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

    def latest_provider_assertions(
        self,
    ) -> dict[str, str]:
        """Return the latest assertion timestamp for each provider."""

        return {
            str(
                row["provider"]
            ): normalize_space(
                row["latest_assertion"]
            )
            for row in self.connection.execute(
                """
                SELECT
                    provider,
                    MAX(updated_at) AS latest_assertion
                FROM assertions
                GROUP BY provider
                ORDER BY provider
                """
            )
        }

    def orphan_counts(
        self,
    ) -> dict[str, int]:
        """Return counts of index rows referencing missing taxa."""

        source_ids = int(
            self.connection.execute(
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

        assertions = int(
            self.connection.execute(
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

        synonyms = int(
            self.connection.execute(
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

        return {
            "source_ids": source_ids,
            "assertions": assertions,
            "synonyms": synonyms,
        }

    def integrity_check(
        self,
    ) -> list[str]:
        """Run SQLite integrity checks and return errors."""

        errors: list[str] = []

        try:
            rows = list(
                self.connection.execute(
                    "PRAGMA integrity_check"
                )
            )

            if not rows:
                errors.append(
                    "SQLite integrity check returned "
                    "no result."
                )

            for row in rows:
                result = normalize_space(
                    row[0]
                )

                if result.casefold() != "ok":
                    errors.append(
                        f"SQLite integrity error: "
                        f"{result}"
                    )

            foreign_keys = list(
                self.connection.execute(
                    "PRAGMA foreign_key_check"
                )
            )

            for row in foreign_keys:
                errors.append(
                    "SQLite foreign-key violation: "
                    + ", ".join(
                        normalize_space(
                            value
                        )
                        for value in row
                    )
                )

        except sqlite3.Error as error:
            errors.append(
                f"SQLite integrity check failed: "
                f"{error}"
            )

        return errors

    def verify(
        self,
    ) -> list[str]:
        """Verify SQLite structure and logical consistency."""

        errors = self.integrity_check()

        orphans = self.orphan_counts()

        if orphans["source_ids"]:
            errors.append(
                "Orphaned source identifiers: "
                f"{orphans['source_ids']}"
            )

        if orphans["assertions"]:
            errors.append(
                "Orphaned provider assertions: "
                f"{orphans['assertions']}"
            )

        if orphans["synonyms"]:
            errors.append(
                "Orphaned synonyms: "
                f"{orphans['synonyms']}"
            )

        schema_version = self.metadata(
            "schema_version"
        )

        if str(
            schema_version
        ) != str(
            SQLITE_SCHEMA_VERSION
        ):
            errors.append(
                "SQLite schema version mismatch: "
                f"expected={SQLITE_SCHEMA_VERSION}, "
                f"actual={schema_version}"
            )

        return errors

    def vacuum(
        self,
    ) -> None:
        """Compact the SQLite index."""

        if self.read_only:
            raise SQLiteIndexError(
                "Cannot vacuum a read-only index."
            )

        self.connection.commit()
        self.connection.execute(
            "VACUUM"
        )

    def analyze(
        self,
    ) -> None:
        """Refresh SQLite query-planner statistics."""

        if self.read_only:
            return

        self.connection.execute(
            "ANALYZE"
        )

        self.connection.commit()

    def optimize(
        self,
    ) -> None:
        """Run SQLite's lightweight optimization command."""

        if self.read_only:
            return

        self.connection.execute(
            "PRAGMA optimize"
        )

    def clear(
        self,
    ) -> None:
        """
        Remove all rebuildable index data.

        Archive metadata is retained.
        """

        if self.read_only:
            raise SQLiteIndexError(
                "Cannot clear a read-only index."
            )

        with self.transaction():
            for table in (
                "synonyms",
                "assertions",
                "source_ids",
                "conflicts",
                "taxa",
            ):
                self.connection.execute(
                    f"DELETE FROM {table}"
                )

    def rebuild_from_records(
        self,
        records: Iterable[
            Mapping[str, Any]
        ],
    ) -> int:
        """
        Rebuild canonical taxon rows from archive JSONL records.

        Provider assertions and synonyms must be rebuilt separately from their
        own durable records when those are available.
        """

        if self.read_only:
            raise SQLiteIndexError(
                "Cannot rebuild a read-only index."
            )

        inserted = 0

        with self.transaction():
            self.connection.execute(
                "DELETE FROM taxa"
            )

            for value in records:
                if not isinstance(
                    value,
                    Mapping,
                ):
                    continue

                identifier = normalize_space(
                    value.get(
                        "speciedex_id"
                    )
                )

                identity_key = normalize_space(
                    value.get(
                        "identity_key"
                    )
                )

                taxonomy = value.get(
                    "taxonomy",
                    {},
                )

                if not isinstance(
                    taxonomy,
                    Mapping,
                ):
                    taxonomy = {}

                if (
                    not identifier
                    or not identity_key
                ):
                    continue

                primary_json = json.dumps(
                    dict(value),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

                self.connection.execute(
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
                            value.get(
                                "scientific_name"
                            )
                        ),
                        normalize_key(
                            value.get(
                                "canonical_name"
                            )
                        ),
                        normalize_key(
                            value.get(
                                "rank"
                            )
                        ),
                        normalize_key(
                            value.get(
                                "status"
                            )
                        ),
                        normalize_key(
                            value.get(
                                "authorship"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "kingdom"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "phylum"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "class"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "order"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "family"
                            )
                        ),
                        normalize_key(
                            taxonomy.get(
                                "genus"
                            )
                        ),
                        primary_json,
                        "",
                        normalize_space(
                            value.get(
                                "_volume_file"
                            )
                        ),
                        int(
                            value.get(
                                "_line_number",
                                0,
                            )
                            or 0
                        ),
                        normalize_space(
                            value.get(
                                "first_seen"
                            )
                        )
                        or now(),
                        normalize_space(
                            value.get(
                                "first_seen"
                            )
                        )
                        or now(),
                    ),
                )

                inserted += 1

        return inserted
