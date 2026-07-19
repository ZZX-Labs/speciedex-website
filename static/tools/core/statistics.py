#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/statistics.py

Taxonomic archive statistics, history, source reporting, and dashboard
generation.

This module reads aggregate data from the canonical Archive and writes the
public JSON statistics consumed by the Speciedex website.

Generated outputs include:

    static/data/statistics.json
    static/data/statistics-sources.json
    static/data/statistics-history.json

Optional detailed outputs include:

    static/data/statistics/summary.json
    static/data/statistics/ranks.json
    static/data/statistics/statuses.json
    static/data/statistics/providers.json
    static/data/statistics/kingdoms.json
    static/data/statistics/revisions.json
    static/data/statistics/conflicts.json
    static/data/statistics/dashboard.json

The module preserves compatibility with the existing stat-grabber.py output
structure while providing reusable classes and functions for later CLI
modularization.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .archive import (
    ACTIVE_STATUSES,
    Archive,
    normalize_key,
    normalize_space,
    now,
    read_json,
    write_json,
)


DEFAULT_HISTORY_LIMIT = 672

COUNT_METHOD = (
    "local-deduplicated-append-only-canonical-corpus"
)

DEFAULT_RANK_OUTPUTS: dict[str, str] = {
    "domains": "domain",
    "superkingdoms": "superkingdom",
    "kingdoms": "kingdom",
    "subkingdoms": "subkingdom",
    "infrakingdoms": "infrakingdom",
    "superphyla": "superphylum",
    "phyla": "phylum",
    "subphyla": "subphylum",
    "infraphyla": "infraphylum",
    "superclasses": "superclass",
    "classes": "class",
    "subclasses": "subclass",
    "infraclasses": "infraclass",
    "superorders": "superorder",
    "orders": "order",
    "suborders": "suborder",
    "infraorders": "infraorder",
    "parvorders": "parvorder",
    "superfamilies": "superfamily",
    "families": "family",
    "subfamilies": "subfamily",
    "tribes": "tribe",
    "subtribes": "subtribe",
    "genera": "genus",
    "subgenera": "subgenus",
    "sections": "section",
    "subsections": "subsection",
    "series": "series",
    "species": "species",
    "subspecies": "subspecies",
    "varieties": "variety",
    "subvarieties": "subvariety",
    "forms": "form",
    "subforms": "subform",
    "strains": "strain",
    "cultivars": "cultivar",
    "hybrids": "hybrid",
    "clades": "clade",
    "unranked": "unranked",
}

PRIMARY_HISTORY_FIELDS = (
    "last_updated",
    "species",
    "genera",
    "families",
    "orders",
    "classes",
    "phyla",
    "kingdoms",
    "records_archived",
    "source_assertions",
    "synonyms",
    "unresolved_conflicts",
    "revisions",
)

SUMMARY_FIELDS = (
    "species",
    "genera",
    "families",
    "orders",
    "classes",
    "phyla",
    "kingdoms",
    "records_archived",
    "source_assertions",
    "source_identifiers",
    "synonyms",
    "unresolved_conflicts",
    "revisions",
    "volumes",
    "sealed_volumes",
)


@dataclass(slots=True)
class StatisticsPaths:
    """Filesystem locations for generated statistics."""

    data_root: Path
    statistics: Path = field(init=False)
    sources: Path = field(init=False)
    history: Path = field(init=False)
    details_root: Path = field(init=False)

    def __post_init__(
        self,
    ) -> None:
        self.data_root = Path(
            self.data_root
        )

        self.statistics = (
            self.data_root
            / "statistics.json"
        )

        self.sources = (
            self.data_root
            / "statistics-sources.json"
        )

        self.history = (
            self.data_root
            / "statistics-history.json"
        )

        self.details_root = (
            self.data_root
            / "statistics"
        )


@dataclass(slots=True)
class StatisticsReport:
    """Complete statistics payload produced for one ingestion run."""

    summary: dict[str, Any]
    sources: dict[str, Any]
    history: list[dict[str, Any]]
    ranks: dict[str, int]
    statuses: dict[str, int]
    kingdoms: dict[str, int]
    providers: dict[str, dict[str, Any]]
    revisions: dict[str, Any]
    conflicts: dict[str, Any]
    dashboard: dict[str, Any]

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return all report sections as JSON-compatible data."""

        return {
            "summary": self.summary,
            "sources": self.sources,
            "history": self.history,
            "ranks": self.ranks,
            "statuses": self.statuses,
            "kingdoms": self.kingdoms,
            "providers": self.providers,
            "revisions": self.revisions,
            "conflicts": self.conflicts,
            "dashboard": self.dashboard,
        }


class StatisticsManager:
    """
    Generate and persist statistics from an Archive.

    This class does not modify canonical taxa, assertions, synonyms, or
    reconciliation data. It only reads the archive and writes derived JSON.
    """

    def __init__(
        self,
        archive: Archive,
        data_root: Path,
        *,
        generator_name: str = (
            "Speciedex Stat Grabber"
        ),
        generator_version: str = "3.0.0",
        history_limit: int = (
            DEFAULT_HISTORY_LIMIT
        ),
        active_statuses: Iterable[str] | None = None,
        rank_outputs: Mapping[str, str] | None = None,
        write_detailed_files: bool = True,
    ) -> None:
        self.archive = archive

        self.paths = StatisticsPaths(
            Path(data_root)
        )

        self.generator_name = (
            normalize_space(
                generator_name
            )
            or "Speciedex Stat Grabber"
        )

        self.generator_version = (
            normalize_space(
                generator_version
            )
            or "unknown"
        )

        self.history_limit = max(
            0,
            int(history_limit),
        )

        configured_statuses = (
            active_statuses
            if active_statuses is not None
            else ACTIVE_STATUSES
        )

        self.active_statuses = {
            normalize_key(status)
            for status in configured_statuses
            if normalize_key(status)
        }

        self.rank_outputs = dict(
            DEFAULT_RANK_OUTPUTS
        )

        if rank_outputs:
            for output_name, rank in (
                rank_outputs.items()
            ):
                normalized_output = (
                    normalize_key(
                        output_name
                    ).replace(
                        " ",
                        "_",
                    )
                )

                normalized_rank = (
                    normalize_key(rank)
                )

                if (
                    normalized_output
                    and normalized_rank
                ):
                    self.rank_outputs[
                        normalized_output
                    ] = normalized_rank

        self.write_detailed_files = bool(
            write_detailed_files
        )

    def generate(
        self,
        *,
        provider_summaries: Sequence[
            Mapping[str, Any]
        ] | None = None,
        skipped_providers: Sequence[
            Mapping[str, Any]
        ] | None = None,
    ) -> StatisticsReport:
        """Build a complete report without writing files."""

        generated_at = now()

        ranks = self.rank_distribution(
            active_only=True,
        )

        statuses = self.status_distribution()

        kingdoms = self.kingdom_distribution(
            active_only=True,
        )

        providers = self.provider_distribution()

        archive_statistics = (
            self.archive.statistics()
        )

        expanded_rank_counts = (
            self.named_rank_counts(
                active_only=True,
            )
        )

        summary: dict[str, Any] = {
            **archive_statistics,
            **expanded_rank_counts,
            "last_updated": generated_at,
            "count_method": COUNT_METHOD,
            "active_statuses": sorted(
                self.active_statuses
            ),
            "generator": {
                "name": self.generator_name,
                "version": (
                    self.generator_version
                ),
            },
        }

        normalized_summaries = (
            self._normalize_provider_summaries(
                provider_summaries or []
            )
        )

        normalized_skipped = (
            self._normalize_skipped_providers(
                skipped_providers or []
            )
        )

        sources = {
            "generated_at": generated_at,
            "providers": normalized_summaries,
            "skipped": normalized_skipped,
            "archive_totals": providers,
        }

        history = self.update_history_data(
            existing_history=read_json(
                self.paths.history,
                [],
            ),
            summary=summary,
        )

        revisions = self.revision_statistics()

        conflicts = self.conflict_statistics()

        dashboard = self.dashboard_data(
            summary=summary,
            providers=providers,
            provider_summaries=(
                normalized_summaries
            ),
            skipped_providers=(
                normalized_skipped
            ),
            ranks=ranks,
            statuses=statuses,
            kingdoms=kingdoms,
            revisions=revisions,
            conflicts=conflicts,
        )

        return StatisticsReport(
            summary=summary,
            sources=sources,
            history=history,
            ranks=ranks,
            statuses=statuses,
            kingdoms=kingdoms,
            providers=providers,
            revisions=revisions,
            conflicts=conflicts,
            dashboard=dashboard,
        )

    def write(
        self,
        *,
        provider_summaries: Sequence[
            Mapping[str, Any]
        ] | None = None,
        skipped_providers: Sequence[
            Mapping[str, Any]
        ] | None = None,
    ) -> StatisticsReport:
        """Generate and atomically write all configured statistics files."""

        report = self.generate(
            provider_summaries=(
                provider_summaries
            ),
            skipped_providers=(
                skipped_providers
            ),
        )

        self.paths.data_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        write_json(
            self.paths.statistics,
            report.summary,
        )

        write_json(
            self.paths.sources,
            report.sources,
        )

        write_json(
            self.paths.history,
            report.history,
        )

        if self.write_detailed_files:
            self._write_detailed_report(
                report
            )

        return report

    def named_rank_counts(
        self,
        *,
        active_only: bool,
    ) -> dict[str, int]:
        """
        Count every configured rank using stable public output names.
        """

        rank_counts = self.rank_distribution(
            active_only=active_only,
        )

        return {
            output_name: int(
                rank_counts.get(
                    normalize_key(rank),
                    0,
                )
            )
            for output_name, rank
            in self.rank_outputs.items()
        }

    def rank_distribution(
        self,
        *,
        active_only: bool = False,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by normalized rank."""

        query = (
            "SELECT rank, COUNT(*) AS count "
            "FROM taxa"
        )

        parameters: tuple[Any, ...] = ()

        if (
            active_only
            and self.active_statuses
        ):
            placeholders = ",".join(
                "?"
                for _ in self.active_statuses
            )

            query += (
                " WHERE status IN "
                f"({placeholders})"
            )

            parameters = tuple(
                sorted(
                    self.active_statuses
                )
            )

        query += (
            " GROUP BY rank "
            "ORDER BY count DESC, rank"
        )

        result: dict[str, int] = {}

        for row in self.archive.database.execute(
            query,
            parameters,
        ):
            rank = (
                normalize_key(
                    row["rank"]
                )
                or "unknown"
            )

            result[rank] = int(
                row["count"]
            )

        return result

    def status_distribution(
        self,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by status."""

        result: dict[str, int] = {}

        rows = self.archive.database.execute(
            """
            SELECT
                status,
                COUNT(*) AS count
            FROM taxa
            GROUP BY status
            ORDER BY count DESC, status
            """
        )

        for row in rows:
            status = (
                normalize_key(
                    row["status"]
                )
                or "unknown"
            )

            result[status] = int(
                row["count"]
            )

        return result

    def kingdom_distribution(
        self,
        *,
        active_only: bool = False,
    ) -> dict[str, int]:
        """Return canonical taxon counts grouped by kingdom."""

        query = (
            "SELECT kingdom, COUNT(*) AS count "
            "FROM taxa"
        )

        parameters: tuple[Any, ...] = ()

        conditions = [
            "kingdom <> ''"
        ]

        if (
            active_only
            and self.active_statuses
        ):
            placeholders = ",".join(
                "?"
                for _ in self.active_statuses
            )

            conditions.append(
                "status IN "
                f"({placeholders})"
            )

            parameters = tuple(
                sorted(
                    self.active_statuses
                )
            )

        if conditions:
            query += (
                " WHERE "
                + " AND ".join(
                    conditions
                )
            )

        query += (
            " GROUP BY kingdom "
            "ORDER BY count DESC, kingdom"
        )

        result: dict[str, int] = {}

        for row in self.archive.database.execute(
            query,
            parameters,
        ):
            kingdom = (
                normalize_space(
                    row["kingdom"]
                )
                or "unknown"
            )

            result[kingdom] = int(
                row["count"]
            )

        return result

    def provider_distribution(
        self,
    ) -> dict[str, dict[str, Any]]:
        """
        Return provider assertion, identifier, synonym, and taxon totals.
        """

        result = {
            provider: {
                **values,
                "canonical_taxa": 0,
                "accepted_assertions": 0,
                "synonym_assertions": 0,
                "unknown_assertions": 0,
                "latest_assertion": "",
            }
            for provider, values
            in self.archive.provider_statistics().items()
        }

        rows = self.archive.database.execute(
            """
            SELECT
                provider,
                COUNT(
                    DISTINCT speciedex_id
                ) AS taxon_count,
                MAX(updated_at) AS latest_assertion
            FROM assertions
            GROUP BY provider
            ORDER BY provider
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            self._ensure_provider_entry(
                result,
                provider,
            )

            result[
                provider
            ][
                "canonical_taxa"
            ] = int(
                row["taxon_count"]
            )

            result[
                provider
            ][
                "latest_assertion"
            ] = normalize_space(
                row["latest_assertion"]
            )

        rows = self.archive.database.execute(
            """
            SELECT
                assertion.provider AS provider,
                taxon.status AS status,
                COUNT(*) AS count
            FROM assertions AS assertion
            JOIN taxa AS taxon
              ON taxon.speciedex_id =
                 assertion.speciedex_id
            GROUP BY
                assertion.provider,
                taxon.status
            ORDER BY
                assertion.provider,
                taxon.status
            """
        )

        for row in rows:
            provider = str(
                row["provider"]
            )

            status = normalize_key(
                row["status"]
            )

            count = int(
                row["count"]
            )

            self._ensure_provider_entry(
                result,
                provider,
            )

            if status in {
                "accepted",
                "valid",
                "provisionally accepted",
            }:
                result[
                    provider
                ][
                    "accepted_assertions"
                ] += count

            elif status in {
                "synonym",
                "unaccepted",
                "invalid",
                "misapplied",
            }:
                result[
                    provider
                ][
                    "synonym_assertions"
                ] += count

            else:
                result[
                    provider
                ][
                    "unknown_assertions"
                ] += count

        return dict(
            sorted(
                result.items()
            )
        )

    def revision_statistics(
        self,
    ) -> dict[str, Any]:
        """Return revision totals and file information."""

        files: list[dict[str, Any]] = []
        total_records = 0
        total_bytes = 0

        if self.archive.revisions.is_dir():
            for path in sorted(
                self.archive.revisions.glob(
                    "*.jsonl"
                )
            ):
                record_count = (
                    self._count_nonempty_lines(
                        path
                    )
                )

                size_bytes = (
                    path.stat().st_size
                )

                total_records += record_count
                total_bytes += size_bytes

                files.append(
                    {
                        "file": path.name,
                        "records": record_count,
                        "size_bytes": size_bytes,
                    }
                )

        return {
            "total": int(
                self.archive.manifest.get(
                    "total_revisions",
                    total_records,
                )
            ),
            "files": files,
            "file_count": len(files),
            "size_bytes": total_bytes,
        }

    def conflict_statistics(
        self,
    ) -> dict[str, Any]:
        """Return unresolved conflict totals grouped by reason/provider."""

        total = self._table_count(
            "conflicts"
        )

        by_reason: dict[str, int] = {}
        by_provider: dict[str, int] = {}

        rows = self.archive.database.execute(
            """
            SELECT conflict_json
            FROM conflicts
            """
        )

        for row in rows:
            conflict = self._decode_object(
                row["conflict_json"]
            )

            reason = (
                normalize_space(
                    conflict.get(
                        "reason"
                    )
                )
                or "unknown"
            )

            provider = (
                normalize_space(
                    conflict.get(
                        "provider"
                    )
                )
                or "unknown"
            )

            by_reason[reason] = (
                by_reason.get(
                    reason,
                    0,
                )
                + 1
            )

            by_provider[provider] = (
                by_provider.get(
                    provider,
                    0,
                )
                + 1
            )

        return {
            "total": total,
            "by_reason": dict(
                sorted(
                    by_reason.items(),
                    key=lambda item: (
                        -item[1],
                        item[0],
                    ),
                )
            ),
            "by_provider": dict(
                sorted(
                    by_provider.items(),
                    key=lambda item: (
                        -item[1],
                        item[0],
                    ),
                )
            ),
        }

    def update_history_data(
        self,
        *,
        existing_history: Any,
        summary: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Add or replace the latest history snapshot.

        When no tracked count changed, the latest snapshot timestamp is
        refreshed rather than appending an identical entry.
        """

        if isinstance(
            existing_history,
            list,
        ):
            history = [
                dict(item)
                for item in existing_history
                if isinstance(
                    item,
                    Mapping,
                )
            ]
        else:
            history = []

        snapshot = {
            key: summary.get(key)
            for key in PRIMARY_HISTORY_FIELDS
        }

        comparison_keys = [
            key
            for key in PRIMARY_HISTORY_FIELDS
            if key != "last_updated"
        ]

        if (
            history
            and all(
                history[-1].get(key)
                == snapshot.get(key)
                for key in comparison_keys
            )
        ):
            history[-1] = snapshot
        else:
            history.append(
                snapshot
            )

        if self.history_limit > 0:
            history = history[
                -self.history_limit:
            ]

        return history

    def dashboard_data(
        self,
        *,
        summary: Mapping[str, Any],
        providers: Mapping[
            str,
            Mapping[str, Any],
        ],
        provider_summaries: Sequence[
            Mapping[str, Any]
        ],
        skipped_providers: Sequence[
            Mapping[str, Any]
        ],
        ranks: Mapping[str, int],
        statuses: Mapping[str, int],
        kingdoms: Mapping[str, int],
        revisions: Mapping[str, Any],
        conflicts: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a website-ready dashboard payload."""

        run_totals = {
            "providers_attempted": len(
                provider_summaries
            ),
            "providers_succeeded": sum(
                1
                for item in provider_summaries
                if not item.get("error")
            ),
            "providers_failed": sum(
                1
                for item in provider_summaries
                if item.get("error")
            ),
            "providers_skipped": len(
                skipped_providers
            ),
            "fetched": sum(
                self._safe_nonnegative_int(
                    item.get("fetched")
                )
                for item in provider_summaries
            ),
            "created": sum(
                self._safe_nonnegative_int(
                    item.get("created")
                )
                for item in provider_summaries
            ),
            "matched": sum(
                self._safe_nonnegative_int(
                    item.get("matched")
                )
                for item in provider_summaries
            ),
            "revised": sum(
                self._safe_nonnegative_int(
                    item.get("revised")
                )
                for item in provider_summaries
            ),
            "conflicted": sum(
                self._safe_nonnegative_int(
                    item.get(
                        "conflicted",
                        item.get(
                            "conflicts"
                        ),
                    )
                )
                for item in provider_summaries
            ),
            "rejected": sum(
                self._safe_nonnegative_int(
                    item.get("rejected")
                )
                for item in provider_summaries
            ),
            "requests": sum(
                self._safe_nonnegative_int(
                    item.get("requests")
                )
                for item in provider_summaries
            ),
        }

        headline = {
            key: summary.get(
                key,
                0,
            )
            for key in SUMMARY_FIELDS
        }

        return {
            "generated_at": summary.get(
                "last_updated",
                now(),
            ),
            "headline": headline,
            "current_run": run_totals,
            "providers": providers,
            "provider_runs": list(
                provider_summaries
            ),
            "skipped_providers": list(
                skipped_providers
            ),
            "ranks": dict(ranks),
            "statuses": dict(statuses),
            "kingdoms": dict(kingdoms),
            "revisions": dict(
                revisions
            ),
            "conflicts": dict(
                conflicts
            ),
            "generator": summary.get(
                "generator",
                {
                    "name": self.generator_name,
                    "version": (
                        self.generator_version
                    ),
                },
            ),
        }

    def _write_detailed_report(
        self,
        report: StatisticsReport,
    ) -> None:
        """Write expanded statistics files."""

        root = self.paths.details_root

        root.mkdir(
            parents=True,
            exist_ok=True,
        )

        write_json(
            root / "summary.json",
            report.summary,
        )

        write_json(
            root / "ranks.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                "ranks": report.ranks,
            },
        )

        write_json(
            root / "statuses.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                "statuses": (
                    report.statuses
                ),
            },
        )

        write_json(
            root / "providers.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                "providers": (
                    report.providers
                ),
            },
        )

        write_json(
            root / "kingdoms.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                "kingdoms": (
                    report.kingdoms
                ),
            },
        )

        write_json(
            root / "revisions.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                **report.revisions,
            },
        )

        write_json(
            root / "conflicts.json",
            {
                "generated_at": (
                    report.summary[
                        "last_updated"
                    ]
                ),
                **report.conflicts,
            },
        )

        write_json(
            root / "dashboard.json",
            report.dashboard,
        )

    @staticmethod
    def _normalize_provider_summaries(
        summaries: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[dict[str, Any]]:
        """Normalize run summaries into a stable JSON structure."""

        result: list[dict[str, Any]] = []

        for summary in summaries:
            provider = normalize_space(
                summary.get(
                    "provider"
                )
            )

            if not provider:
                continue

            conflicted = (
                summary.get(
                    "conflicted"
                )
            )

            if conflicted is None:
                conflicted = (
                    summary.get(
                        "conflicts",
                        0,
                    )
                )

            result.append(
                {
                    "provider": provider,
                    "fetched": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "fetched"
                            )
                        )
                    ),
                    "created": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "created"
                            )
                        )
                    ),
                    "matched": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "matched"
                            )
                        )
                    ),
                    "revised": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "revised"
                            )
                        )
                    ),
                    "conflicted": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            conflicted
                        )
                    ),
                    "rejected": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "rejected"
                            )
                        )
                    ),
                    "requests": (
                        StatisticsManager
                        ._safe_nonnegative_int(
                            summary.get(
                                "requests"
                            )
                        )
                    ),
                    "error": (
                        normalize_space(
                            summary.get(
                                "error"
                            )
                        )
                        or None
                    ),
                }
            )

        return result

    @staticmethod
    def _normalize_skipped_providers(
        skipped: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[dict[str, str]]:
        """Normalize skipped-provider records."""

        result: list[
            dict[str, str]
        ] = []

        for item in skipped:
            provider = normalize_space(
                item.get(
                    "provider"
                )
            )

            if not provider:
                continue

            result.append(
                {
                    "provider": provider,
                    "reason": (
                        normalize_space(
                            item.get(
                                "reason"
                            )
                        )
                        or "unspecified"
                    ),
                }
            )

        return result

    @staticmethod
    def _ensure_provider_entry(
        result: dict[
            str,
            dict[str, Any],
        ],
        provider: str,
    ) -> None:
        """Ensure a provider aggregate exists."""

        result.setdefault(
            provider,
            {
                "assertions": 0,
                "source_identifiers": 0,
                "synonyms": 0,
                "canonical_taxa": 0,
                "accepted_assertions": 0,
                "synonym_assertions": 0,
                "unknown_assertions": 0,
                "latest_assertion": "",
            },
        )

    def _table_count(
        self,
        table: str,
    ) -> int:
        """Count rows from a trusted internal table."""

        allowed = {
            "taxa",
            "source_ids",
            "assertions",
            "synonyms",
            "conflicts",
        }

        if table not in allowed:
            raise ValueError(
                f"Unsupported statistics table: "
                f"{table}"
            )

        row = self.archive.database.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    @staticmethod
    def _decode_object(
        value: Any,
    ) -> dict[str, Any]:
        """Decode a JSON object stored in SQLite."""

        if isinstance(
            value,
            dict,
        ):
            return value

        if not isinstance(
            value,
            str,
        ):
            return {}

        try:
            import json

            decoded = json.loads(
                value
            )
        except json.JSONDecodeError:
            return {}

        return (
            decoded
            if isinstance(
                decoded,
                dict,
            )
            else {}
        )

    @staticmethod
    def _safe_nonnegative_int(
        value: Any,
    ) -> int:
        """Convert a value to a nonnegative integer."""

        try:
            parsed = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return 0

        return max(
            0,
            parsed,
        )

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


def generate_statistics(
    archive: Archive,
    data_root: Path,
    *,
    provider_summaries: Sequence[
        Mapping[str, Any]
    ] | None = None,
    skipped_providers: Sequence[
        Mapping[str, Any]
    ] | None = None,
    generator_name: str = (
        "Speciedex Stat Grabber"
    ),
    generator_version: str = "3.0.0",
    history_limit: int = (
        DEFAULT_HISTORY_LIMIT
    ),
    write_detailed_files: bool = True,
) -> StatisticsReport:
    """
    Generate and write all Speciedex statistics.

    This is the primary convenience function for stat-grabber.py.
    """

    manager = StatisticsManager(
        archive=archive,
        data_root=data_root,
        generator_name=generator_name,
        generator_version=(
            generator_version
        ),
        history_limit=history_limit,
        write_detailed_files=(
            write_detailed_files
        ),
    )

    return manager.write(
        provider_summaries=(
            provider_summaries
        ),
        skipped_providers=(
            skipped_providers
        ),
    )


def write_statistics(
    archive: Archive,
    data_root: Path,
    summaries: Sequence[
        Mapping[str, Any]
    ],
    skipped: Sequence[
        Mapping[str, Any]
    ],
    *,
    generator_name: str = (
        "Speciedex Stat Grabber"
    ),
    generator_version: str = "3.0.0",
    history_limit: int = (
        DEFAULT_HISTORY_LIMIT
    ),
) -> dict[str, Any]:
    """
    Compatibility wrapper returning the public statistics.json payload.
    """

    report = generate_statistics(
        archive=archive,
        data_root=data_root,
        provider_summaries=summaries,
        skipped_providers=skipped,
        generator_name=generator_name,
        generator_version=(
            generator_version
        ),
        history_limit=history_limit,
    )

    return report.summary
