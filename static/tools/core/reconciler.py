#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/reconciler.py

Taxonomic reconciliation engine.

This module compares normalized provider Taxon records against the canonical
Speciedex archive and determines whether each record should:

- match an existing canonical taxon,
- create a new canonical taxon,
- or be recorded as an unresolved conflict.

Reconciliation uses, in order:

1. exact provider/source identifier matches,
2. exact deterministic identity-key matches,
3. weighted canonical-name and lineage matching,
4. ambiguity detection,
5. creation when no sufficiently strong match exists.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from providers.common import Taxon

from .archive import Archive, normalize_key


DEFAULT_PROVIDER_WEIGHTS: dict[str, float] = {
    "catalogue_of_life": 1.00,
    "itis": 0.99,
    "worms": 0.99,
    "gbif": 0.98,
    "ncbi_taxonomy": 0.98,
    "world_flora_online": 0.97,
    "powo": 0.97,
    "ipni": 0.96,
    "index_fungorum": 0.96,
    "mycobank": 0.96,
    "irmng": 0.95,
    "fishbase": 0.95,
    "sealifebase": 0.95,
    "algaebase": 0.95,
    "zoobank": 0.94,
    "open_tree_of_life": 0.94,
    "lpsn": 0.94,
    "gtdb": 0.94,
    "silva": 0.93,
    "ictv": 0.93,
    "obis": 0.92,
    "bold": 0.91,
    "paleobiology": 0.91,
    "eol": 0.90,
    "ala": 0.90,
    "natureserve": 0.90,
    "iucn_red_list": 0.89,
    "iucn_green_status": 0.88,
    "iucn_green_list": 0.88,
    "species_plus": 0.88,
    "ebird": 0.87,
    "inaturalist": 0.86,
    "global_names": 0.86,
    "wikispecies": 0.84,
    "unite": 0.93,
}

DEFAULT_PROVIDER_WEIGHT = 0.85

DEFAULT_MATCH_THRESHOLD = 75
DEFAULT_CONFLICT_THRESHOLD = 50
DEFAULT_MINIMUM_NAME_SCORE = 35

DEFAULT_FIELD_WEIGHTS: dict[str, int] = {
    "canonical_name": 35,
    "scientific_name": 8,
    "authorship": 20,
    "rank": 10,
    "kingdom": 15,
    "phylum": 4,
    "class": 4,
    "order": 4,
    "family": 4,
    "genus": 4,
    "accepted_provider_id": 4,
}

MAX_LINEAGE_SCORE = 20


class CandidateRow(Protocol):
    """Minimal protocol for SQLite reconciliation rows."""

    def __getitem__(
        self,
        key: str,
    ) -> Any:
        ...


@dataclass(slots=True)
class CandidateScore:
    """Detailed score for one canonical taxon candidate."""

    speciedex_id: str
    raw_score: int
    weighted_score: float
    provider_weight: float
    matched_fields: list[str] = field(
        default_factory=list
    )
    mismatched_fields: list[str] = field(
        default_factory=list
    )
    notes: list[str] = field(
        default_factory=list
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible score description."""

        return {
            "speciedex_id": self.speciedex_id,
            "raw_score": self.raw_score,
            "weighted_score": round(
                self.weighted_score,
                4,
            ),
            "provider_weight": self.provider_weight,
            "matched_fields": list(
                self.matched_fields
            ),
            "mismatched_fields": list(
                self.mismatched_fields
            ),
            "notes": list(
                self.notes
            ),
        }


@dataclass(slots=True)
class ReconciliationResult:
    """Outcome of reconciling one provider Taxon."""

    action: str
    identifier: str | None
    candidates: list[str]
    reason: str
    score: float | None = None
    scored_candidates: list[
        CandidateScore
    ] = field(
        default_factory=list
    )

    def __post_init__(
        self,
    ) -> None:
        valid_actions = {
            "match",
            "create",
            "conflict",
        }

        if self.action not in valid_actions:
            raise ValueError(
                "Unsupported reconciliation action: "
                f"{self.action}"
            )

        if (
            self.action == "match"
            and not self.identifier
        ):
            raise ValueError(
                "A match result requires an identifier."
            )

        if (
            self.action != "match"
            and self.identifier is not None
        ):
            raise ValueError(
                "Only match results may contain "
                "an identifier."
            )

    def as_legacy_tuple(
        self,
    ) -> tuple[
        str,
        str | None,
        list[str],
        str,
    ]:
        """
        Return the tuple shape used by the original stat-grabber.py.
        """

        return (
            self.action,
            self.identifier,
            list(
                self.candidates
            ),
            self.reason,
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible result."""

        return {
            "action": self.action,
            "identifier": self.identifier,
            "candidates": list(
                self.candidates
            ),
            "reason": self.reason,
            "score": self.score,
            "scored_candidates": [
                candidate.to_dict()
                for candidate
                in self.scored_candidates
            ],
        }


class Reconciler:
    """
    Reconcile normalized provider records against an Archive.

    The class is intentionally stateless apart from configuration. It never
    mutates the archive. The caller remains responsible for creating taxa,
    attaching assertions, or storing conflicts after receiving a result.
    """

    def __init__(
        self,
        *,
        provider_weights: Mapping[
            str,
            float,
        ] | None = None,
        field_weights: Mapping[
            str,
            int,
        ] | None = None,
        match_threshold: float = (
            DEFAULT_MATCH_THRESHOLD
        ),
        conflict_threshold: float = (
            DEFAULT_CONFLICT_THRESHOLD
        ),
        minimum_name_score: int = (
            DEFAULT_MINIMUM_NAME_SCORE
        ),
    ) -> None:
        if match_threshold <= 0:
            raise ValueError(
                "match_threshold must be positive."
            )

        if conflict_threshold < 0:
            raise ValueError(
                "conflict_threshold cannot be negative."
            )

        if conflict_threshold >= match_threshold:
            raise ValueError(
                "conflict_threshold must be below "
                "match_threshold."
            )

        self.provider_weights = dict(
            DEFAULT_PROVIDER_WEIGHTS
        )

        if provider_weights:
            for provider, weight in (
                provider_weights.items()
            ):
                normalized_provider = normalize_key(
                    provider
                )

                if not normalized_provider:
                    continue

                parsed_weight = float(
                    weight
                )

                if parsed_weight <= 0:
                    raise ValueError(
                        "Provider weights must be "
                        "positive."
                    )

                self.provider_weights[
                    normalized_provider
                ] = parsed_weight

        self.field_weights = dict(
            DEFAULT_FIELD_WEIGHTS
        )

        if field_weights:
            for field_name, weight in (
                field_weights.items()
            ):
                parsed_weight = int(
                    weight
                )

                if parsed_weight < 0:
                    raise ValueError(
                        "Field weights cannot be "
                        "negative."
                    )

                self.field_weights[
                    str(field_name)
                ] = parsed_weight

        self.match_threshold = float(
            match_threshold
        )

        self.conflict_threshold = float(
            conflict_threshold
        )

        self.minimum_name_score = int(
            minimum_name_score
        )

    def resolve(
        self,
        archive: Archive,
        record: Taxon,
    ) -> ReconciliationResult:
        """
        Determine whether a record matches, creates, or conflicts.

        Resolution precedence:

        1. existing provider/source identifier,
        2. exact identity key,
        3. weighted same-name candidates,
        4. create when no candidate is strong enough.
        """

        self._validate_record(
            record
        )

        direct = archive.source_match(
            record.provider,
            record.provider_id,
        )

        if direct:
            return ReconciliationResult(
                action="match",
                identifier=direct,
                candidates=[
                    direct
                ],
                reason=(
                    "existing provider source identifier"
                ),
                score=100.0,
            )

        identity_key = archive.identity_key(
            record
        )

        exact_candidates = (
            archive.identity_candidates(
                identity_key
            )
        )

        if len(exact_candidates) == 1:
            identifier = str(
                exact_candidates[0][
                    "speciedex_id"
                ]
            )

            return ReconciliationResult(
                action="match",
                identifier=identifier,
                candidates=[
                    identifier
                ],
                reason=(
                    "exact canonical identity key"
                ),
                score=100.0,
            )

        if len(exact_candidates) > 1:
            identifiers = self._unique_sorted(
                str(
                    row["speciedex_id"]
                )
                for row in exact_candidates
            )

            return ReconciliationResult(
                action="conflict",
                identifier=None,
                candidates=identifiers,
                reason=(
                    "multiple canonical taxa share "
                    "the exact identity key"
                ),
                score=100.0,
            )

        rows = archive.name_candidates(
            record
        )

        if not rows:
            return ReconciliationResult(
                action="create",
                identifier=None,
                candidates=[],
                reason=(
                    "no canonical taxon with the "
                    "same normalized name, rank, "
                    "and kingdom"
                ),
                score=None,
            )

        scored = [
            self.score_candidate(
                record,
                row,
            )
            for row in rows
        ]

        scored.sort(
            key=lambda candidate: (
                candidate.weighted_score,
                candidate.raw_score,
                candidate.speciedex_id,
            ),
            reverse=True,
        )

        best_score = (
            scored[0].weighted_score
        )

        tied_best = [
            candidate
            for candidate in scored
            if self._scores_equal(
                candidate.weighted_score,
                best_score,
            )
        ]

        all_candidate_ids = [
            candidate.speciedex_id
            for candidate in scored
        ]

        if (
            best_score
            >= self.match_threshold
            and len(tied_best) == 1
        ):
            best = tied_best[0]

            return ReconciliationResult(
                action="match",
                identifier=(
                    best.speciedex_id
                ),
                candidates=all_candidate_ids,
                reason=(
                    "unique high-confidence weighted "
                    "taxonomy match"
                ),
                score=best_score,
                scored_candidates=scored,
            )

        if (
            best_score
            >= self.conflict_threshold
        ):
            conflict_ids = [
                candidate.speciedex_id
                for candidate in tied_best
            ]

            if len(conflict_ids) == 1:
                conflict_ids = all_candidate_ids

            return ReconciliationResult(
                action="conflict",
                identifier=None,
                candidates=self._unique_sorted(
                    conflict_ids
                ),
                reason=(
                    "candidate confidence is "
                    "significant but not uniquely "
                    "high enough to merge"
                ),
                score=best_score,
                scored_candidates=scored,
            )

        return ReconciliationResult(
            action="create",
            identifier=None,
            candidates=all_candidate_ids,
            reason=(
                "all candidate scores are below "
                "the reconciliation threshold"
            ),
            score=best_score,
            scored_candidates=scored,
        )

    def score_candidate(
        self,
        record: Taxon,
        row: CandidateRow,
    ) -> CandidateScore:
        """
        Score one canonical candidate against a provider record.

        The raw taxonomic score is multiplied by the configured confidence
        weight for the incoming provider. Exact source-identifier and identity
        matches are resolved before this function is called.
        """

        matched_fields: list[str] = []
        mismatched_fields: list[str] = []
        notes: list[str] = []

        raw_score = 0

        canonical_match = self._same(
            record.canonical_name,
            row["canonical_name"],
        )

        if canonical_match:
            raw_score += self._weight(
                "canonical_name"
            )
            matched_fields.append(
                "canonical_name"
            )
        else:
            mismatched_fields.append(
                "canonical_name"
            )

        scientific_name = self._row_value(
            row,
            "scientific_name",
        )

        if (
            record.scientific_name
            and scientific_name
        ):
            if self._same(
                record.scientific_name,
                scientific_name,
            ):
                raw_score += self._weight(
                    "scientific_name"
                )
                matched_fields.append(
                    "scientific_name"
                )
            else:
                mismatched_fields.append(
                    "scientific_name"
                )

        authorship_match = self._optional_match(
            record.authorship,
            self._row_value(
                row,
                "authorship",
            ),
        )

        if authorship_match is True:
            raw_score += self._weight(
                "authorship"
            )
            matched_fields.append(
                "authorship"
            )
        elif authorship_match is False:
            mismatched_fields.append(
                "authorship"
            )

        rank_match = self._same(
            record.rank,
            self._row_value(
                row,
                "rank",
            ),
        )

        if rank_match:
            raw_score += self._weight(
                "rank"
            )
            matched_fields.append(
                "rank"
            )
        else:
            mismatched_fields.append(
                "rank"
            )

        kingdom_match = self._optional_match(
            record.kingdom,
            self._row_value(
                row,
                "kingdom",
            ),
        )

        if kingdom_match is True:
            raw_score += self._weight(
                "kingdom"
            )
            matched_fields.append(
                "kingdom"
            )
        elif kingdom_match is False:
            mismatched_fields.append(
                "kingdom"
            )

        lineage_score = 0

        lineage_fields = (
            (
                "phylum",
                record.phylum,
                "phylum",
            ),
            (
                "class",
                record.class_name,
                "class_name",
            ),
            (
                "order",
                record.order,
                "order_name",
            ),
            (
                "family",
                record.family,
                "family",
            ),
            (
                "genus",
                record.genus,
                "genus",
            ),
        )

        for (
            logical_name,
            record_value,
            row_column,
        ) in lineage_fields:
            candidate_value = (
                self._row_value(
                    row,
                    row_column,
                )
            )

            comparison = (
                self._optional_match(
                    record_value,
                    candidate_value,
                )
            )

            if comparison is True:
                contribution = self._weight(
                    logical_name
                )

                remaining = (
                    MAX_LINEAGE_SCORE
                    - lineage_score
                )

                awarded = min(
                    contribution,
                    remaining,
                )

                lineage_score += awarded

                matched_fields.append(
                    logical_name
                )

            elif comparison is False:
                mismatched_fields.append(
                    logical_name
                )

            if (
                lineage_score
                >= MAX_LINEAGE_SCORE
            ):
                break

        raw_score += lineage_score

        accepted_provider_id = normalize_key(
            getattr(
                record,
                "accepted_provider_id",
                "",
            )
        )

        if accepted_provider_id:
            notes.append(
                "incoming record references an "
                "accepted provider identifier"
            )

            raw_score += self._weight(
                "accepted_provider_id"
            )

            matched_fields.append(
                "accepted_provider_id_present"
            )

        provider_weight = (
            self.provider_weight(
                record.provider
            )
        )

        weighted_score = (
            float(raw_score)
            * provider_weight
        )

        if (
            raw_score
            < self.minimum_name_score
        ):
            weighted_score = min(
                weighted_score,
                float(
                    self.conflict_threshold
                    - 0.0001
                ),
            )

            notes.append(
                "score capped because canonical "
                "name evidence is insufficient"
            )

        identifier = str(
            row["speciedex_id"]
        )

        return CandidateScore(
            speciedex_id=identifier,
            raw_score=raw_score,
            weighted_score=weighted_score,
            provider_weight=provider_weight,
            matched_fields=matched_fields,
            mismatched_fields=mismatched_fields,
            notes=notes,
        )

    def provider_weight(
        self,
        provider: str,
    ) -> float:
        """Return the configured confidence weight for a provider."""

        normalized = normalize_key(
            provider
        )

        return float(
            self.provider_weights.get(
                normalized,
                DEFAULT_PROVIDER_WEIGHT,
            )
        )

    def _weight(
        self,
        field_name: str,
    ) -> int:
        """Return a configured scoring weight."""

        return int(
            self.field_weights.get(
                field_name,
                0,
            )
        )

    @staticmethod
    def _validate_record(
        record: Taxon,
    ) -> None:
        """Reject records that cannot be reconciled safely."""

        if not normalize_key(
            record.provider
        ):
            raise ValueError(
                "Taxon provider is required."
            )

        if not normalize_key(
            record.provider_id
        ):
            raise ValueError(
                "Taxon provider_id is required."
            )

        if not normalize_key(
            record.scientific_name
        ):
            raise ValueError(
                "Taxon scientific_name is required."
            )

        if not normalize_key(
            record.canonical_name
        ):
            raise ValueError(
                "Taxon canonical_name is required."
            )

        if not normalize_key(
            record.rank
        ):
            raise ValueError(
                "Taxon rank is required."
            )

    @staticmethod
    def _same(
        left: Any,
        right: Any,
    ) -> bool:
        """Compare two normalized nonempty values."""

        left_key = normalize_key(
            left
        )

        right_key = normalize_key(
            right
        )

        return bool(
            left_key
            and right_key
            and left_key == right_key
        )

    @staticmethod
    def _optional_match(
        left: Any,
        right: Any,
    ) -> bool | None:
        """
        Compare optional values.

        None means one or both values were absent and therefore provide no
        positive or negative evidence.
        """

        left_key = normalize_key(
            left
        )

        right_key = normalize_key(
            right
        )

        if not left_key or not right_key:
            return None

        return left_key == right_key

    @staticmethod
    def _row_value(
        row: CandidateRow,
        key: str,
    ) -> Any:
        """Read a row column without depending on sqlite3.Row directly."""

        try:
            return row[key]
        except (
            KeyError,
            IndexError,
            TypeError,
        ):
            return ""

    @staticmethod
    def _scores_equal(
        left: float,
        right: float,
    ) -> bool:
        """Compare floating-point scores using a strict tolerance."""

        return abs(
            left - right
        ) < 0.000001

    @staticmethod
    def _unique_sorted(
        values: Iterable[str],
    ) -> list[str]:
        """Return deterministic unique identifiers."""

        return sorted(
            {
                str(value)
                for value in values
                if str(value)
            }
        )


def score_candidate(
    record: Taxon,
    row: CandidateRow,
    *,
    provider_weights: Mapping[
        str,
        float,
    ] | None = None,
    field_weights: Mapping[
        str,
        int,
    ] | None = None,
) -> int:
    """
    Compatibility wrapper for the original score_candidate function.

    Returns the rounded weighted candidate score as an integer.
    """

    reconciler = Reconciler(
        provider_weights=provider_weights,
        field_weights=field_weights,
    )

    result = reconciler.score_candidate(
        record,
        row,
    )

    return int(
        round(
            result.weighted_score
        )
    )


def resolve(
    archive: Archive,
    record: Taxon,
    *,
    provider_weights: Mapping[
        str,
        float,
    ] | None = None,
    field_weights: Mapping[
        str,
        int,
    ] | None = None,
    match_threshold: float = (
        DEFAULT_MATCH_THRESHOLD
    ),
    conflict_threshold: float = (
        DEFAULT_CONFLICT_THRESHOLD
    ),
) -> tuple[
    str,
    str | None,
    list[str],
    str,
]:
    """
    Compatibility wrapper for the original resolve function.

    It returns:

        action, identifier, candidates, reason
    """

    reconciler = Reconciler(
        provider_weights=provider_weights,
        field_weights=field_weights,
        match_threshold=match_threshold,
        conflict_threshold=conflict_threshold,
    )

    return reconciler.resolve(
        archive,
        record,
    ).as_legacy_tuple()
