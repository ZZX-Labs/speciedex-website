#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/deduplication.py

Canonical taxon duplicate detection, duplicate clustering, merge planning,
and duplicate-resolution utilities.

This module owns:

- duplicate candidate discovery,
- deterministic duplicate fingerprints,
- exact duplicate detection,
- near-duplicate scoring,
- provider-source duplicate detection,
- canonical-name duplicate detection,
- lineage-aware duplicate detection,
- synonym-assisted duplicate detection,
- duplicate clustering,
- duplicate merge recommendations,
- duplicate conflict reporting,
- SQLite duplicate scans,
- archive-wide duplicate statistics,
- duplicate verification,
- compatibility helpers for reconciliation and maintenance tools.

This module does not directly delete or merge canonical archive records.
It identifies likely duplicates and produces deterministic plans that may be
reviewed and applied by Archive or a dedicated maintenance command.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from providers.common import Taxon

from .authority import compare_authorities
from .hashing import stable_json_hash
from .lineage import compare_lineages
from .sqlite_index import SQLiteIndex
from .taxonomy import (
    ACCEPTED_STATUSES,
    SYNONYM_STATUSES,
    canonical_name,
    normalize_key,
    normalize_rank,
    normalize_status,
    normalize_taxon_name,
)


DEDUPLICATION_SCHEMA_VERSION = 1

DEFAULT_EXACT_THRESHOLD = 0.98
DEFAULT_MATCH_THRESHOLD = 0.84
DEFAULT_REVIEW_THRESHOLD = 0.68
DEFAULT_MAXIMUM_CANDIDATES = 250

DEFAULT_FIELD_WEIGHTS: dict[str, float] = {
    "canonical_name": 0.30,
    "scientific_name": 0.08,
    "authorship": 0.14,
    "rank": 0.10,
    "kingdom": 0.08,
    "lineage": 0.18,
    "status": 0.04,
    "provider_support": 0.05,
    "synonym_support": 0.03,
}

DUPLICATE_REASONS = {
    "same_identity_key",
    "same_canonical_name",
    "same_scientific_name",
    "same_provider_source",
    "same_lineage",
    "authority_equivalent",
    "synonym_overlap",
    "high_weighted_similarity",
}

MERGE_ACTIONS = {
    "merge",
    "review",
    "ignore",
    "conflict",
}


class DeduplicationError(RuntimeError):
    """Raised when duplicate analysis cannot complete safely."""


@dataclass(slots=True, frozen=True)
class TaxonSignature:
    """Normalized identity signature for one canonical taxon."""

    speciedex_id: str
    canonical_name: str
    scientific_name: str
    authorship: str
    rank: str
    status: str
    kingdom: str
    phylum: str
    class_name: str
    order: str
    family: str
    genus: str
    identity_key: str = ""
    record_hash: str = ""
    providers: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()

    @property
    def lineage(self) -> dict[str, str]:
        """Return the normalized primary lineage."""

        return {
            "kingdom": self.kingdom,
            "phylum": self.phylum,
            "class": self.class_name,
            "order": self.order,
            "family": self.family,
            "genus": self.genus,
        }

    def fingerprint_payload(
        self,
    ) -> dict[str, Any]:
        """Return a deterministic duplicate fingerprint payload."""

        return {
            "schema_version": (
                DEDUPLICATION_SCHEMA_VERSION
            ),
            "canonical_name": self.canonical_name,
            "scientific_name": self.scientific_name,
            "authorship": self.authorship,
            "rank": self.rank,
            "kingdom": self.kingdom,
            "lineage": self.lineage,
        }

    def fingerprint(
        self,
    ) -> str:
        """Return a deterministic duplicate fingerprint."""

        return stable_json_hash(
            self.fingerprint_payload()
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible signature."""

        return {
            "speciedex_id": self.speciedex_id,
            "canonical_name": self.canonical_name,
            "scientific_name": self.scientific_name,
            "authorship": self.authorship,
            "rank": self.rank,
            "status": self.status,
            "kingdom": self.kingdom,
            "phylum": self.phylum,
            "class_name": self.class_name,
            "order": self.order,
            "family": self.family,
            "genus": self.genus,
            "identity_key": self.identity_key,
            "record_hash": self.record_hash,
            "providers": list(self.providers),
            "synonyms": list(self.synonyms),
            "fingerprint": self.fingerprint(),
        }


@dataclass(slots=True)
class DuplicateScore:
    """Detailed duplicate score between two canonical taxa."""

    left_id: str
    right_id: str
    score: float
    exact: bool
    probable_duplicate: bool
    review_required: bool
    reasons: list[str] = field(
        default_factory=list
    )
    matched_fields: list[str] = field(
        default_factory=list
    )
    mismatched_fields: list[str] = field(
        default_factory=list
    )
    components: dict[str, float] = field(
        default_factory=dict
    )
    notes: list[str] = field(
        default_factory=list
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible score."""

        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "score": round(
                self.score,
                6,
            ),
            "exact": self.exact,
            "probable_duplicate": (
                self.probable_duplicate
            ),
            "review_required": (
                self.review_required
            ),
            "reasons": list(self.reasons),
            "matched_fields": list(
                self.matched_fields
            ),
            "mismatched_fields": list(
                self.mismatched_fields
            ),
            "components": {
                key: round(
                    value,
                    6,
                )
                for key, value
                in self.components.items()
            },
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DuplicatePair:
    """One duplicate candidate pair."""

    duplicate_id: str
    left: TaxonSignature
    right: TaxonSignature
    score: DuplicateScore
    recommended_action: str
    preferred_id: str | None
    created_at: str = ""

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible duplicate pair."""

        return {
            "schema_version": (
                DEDUPLICATION_SCHEMA_VERSION
            ),
            "duplicate_id": self.duplicate_id,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "score": self.score.to_dict(),
            "recommended_action": (
                self.recommended_action
            ),
            "preferred_id": self.preferred_id,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class DuplicateCluster:
    """Connected cluster of duplicate candidate taxa."""

    cluster_id: str
    members: list[str]
    pairs: list[DuplicatePair]
    preferred_id: str | None
    recommended_action: str
    maximum_score: float
    minimum_score: float
    average_score: float

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible duplicate cluster."""

        return {
            "schema_version": (
                DEDUPLICATION_SCHEMA_VERSION
            ),
            "cluster_id": self.cluster_id,
            "members": list(self.members),
            "pairs": [
                pair.to_dict()
                for pair in self.pairs
            ],
            "preferred_id": self.preferred_id,
            "recommended_action": (
                self.recommended_action
            ),
            "maximum_score": round(
                self.maximum_score,
                6,
            ),
            "minimum_score": round(
                self.minimum_score,
                6,
            ),
            "average_score": round(
                self.average_score,
                6,
            ),
        }


@dataclass(slots=True)
class DuplicateMergePlan:
    """Non-destructive plan for resolving a duplicate cluster."""

    cluster_id: str
    keep_id: str
    merge_ids: list[str]
    source_id_moves: list[
        dict[str, str]
    ]
    assertion_moves: list[
        dict[str, str]
    ]
    synonym_moves: list[
        dict[str, str]
    ]
    conflicts: list[str]
    reason: str
    safe_to_apply: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible merge plan."""

        return {
            "schema_version": (
                DEDUPLICATION_SCHEMA_VERSION
            ),
            "cluster_id": self.cluster_id,
            "keep_id": self.keep_id,
            "merge_ids": list(
                self.merge_ids
            ),
            "source_id_moves": list(
                self.source_id_moves
            ),
            "assertion_moves": list(
                self.assertion_moves
            ),
            "synonym_moves": list(
                self.synonym_moves
            ),
            "conflicts": list(
                self.conflicts
            ),
            "reason": self.reason,
            "safe_to_apply": (
                self.safe_to_apply
            ),
        }


@dataclass(slots=True)
class DeduplicationStatistics:
    """Archive-wide duplicate statistics."""

    taxa_scanned: int
    candidate_pairs: int
    exact_pairs: int
    probable_pairs: int
    review_pairs: int
    clusters: int
    mergeable_clusters: int
    conflicting_clusters: int

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible statistics object."""

        return {
            "taxa_scanned": self.taxa_scanned,
            "candidate_pairs": (
                self.candidate_pairs
            ),
            "exact_pairs": self.exact_pairs,
            "probable_pairs": (
                self.probable_pairs
            ),
            "review_pairs": self.review_pairs,
            "clusters": self.clusters,
            "mergeable_clusters": (
                self.mergeable_clusters
            ),
            "conflicting_clusters": (
                self.conflicting_clusters
            ),
        }


@dataclass(slots=True)
class DeduplicationVerification:
    """Verification result for duplicate metadata."""

    valid: bool
    errors: list[str] = field(
        default_factory=list
    )
    warnings: list[str] = field(
        default_factory=list
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible verification result."""

        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(
                self.warnings
            ),
        }


class Deduplicator:
    """
    Detect likely duplicate canonical taxa in a SQLiteIndex.

    Candidate discovery is deliberately conservative. It first reduces the
    search space using exact normalized names, scientific names, identity
    keys, provider identifiers, or synonym overlap. Weighted comparison is
    then used to determine whether each candidate is exact, probable, or
    requires review.
    """

    def __init__(
        self,
        index: SQLiteIndex,
        *,
        field_weights: Mapping[
            str,
            float,
        ] | None = None,
        exact_threshold: float = (
            DEFAULT_EXACT_THRESHOLD
        ),
        match_threshold: float = (
            DEFAULT_MATCH_THRESHOLD
        ),
        review_threshold: float = (
            DEFAULT_REVIEW_THRESHOLD
        ),
        maximum_candidates: int = (
            DEFAULT_MAXIMUM_CANDIDATES
        ),
    ) -> None:
        self.index = index

        self.field_weights = dict(
            DEFAULT_FIELD_WEIGHTS
        )

        if field_weights:
            for field_name, weight in (
                field_weights.items()
            ):
                parsed_weight = float(weight)

                if parsed_weight < 0:
                    raise ValueError(
                        "Deduplication field weights "
                        "cannot be negative."
                    )

                self.field_weights[
                    str(field_name)
                ] = parsed_weight

        self.exact_threshold = float(
            exact_threshold
        )

        self.match_threshold = float(
            match_threshold
        )

        self.review_threshold = float(
            review_threshold
        )

        self.maximum_candidates = max(
            1,
            int(maximum_candidates),
        )

        self._validate_thresholds()

    @property
    def connection(
        self,
    ) -> sqlite3.Connection:
        """Return the underlying SQLite connection."""

        return self.index.connection

    def _validate_thresholds(
        self,
    ) -> None:
        """Validate configured duplicate thresholds."""

        if not (
            0.0
            <= self.review_threshold
            <= self.match_threshold
            <= self.exact_threshold
            <= 1.0
        ):
            raise ValueError(
                "Thresholds must satisfy "
                "0 <= review <= match <= exact <= 1."
            )

        weight_total = sum(
            self.field_weights.values()
        )

        if weight_total <= 0:
            raise ValueError(
                "At least one deduplication field "
                "weight must be positive."
            )

    def signature(
        self,
        identifier: str,
    ) -> TaxonSignature | None:
        """Load one canonical taxon signature."""

        row = self.index.taxon(
            identifier
        )

        if row is None:
            return None

        return self._signature_from_row(
            row
        )

    def compare(
        self,
        left: TaxonSignature
        | Taxon
        | Mapping[str, Any]
        | str,
        right: TaxonSignature
        | Taxon
        | Mapping[str, Any]
        | str,
    ) -> DuplicateScore:
        """Compare two taxa and return a weighted duplicate score."""

        left_signature = (
            self._coerce_signature(left)
        )

        right_signature = (
            self._coerce_signature(right)
        )

        if (
            left_signature.speciedex_id
            and right_signature.speciedex_id
            and left_signature.speciedex_id
            == right_signature.speciedex_id
        ):
            return DuplicateScore(
                left_id=(
                    left_signature.speciedex_id
                ),
                right_id=(
                    right_signature.speciedex_id
                ),
                score=1.0,
                exact=True,
                probable_duplicate=True,
                review_required=False,
                reasons=[
                    "same_speciedex_id"
                ],
                matched_fields=[
                    "speciedex_id"
                ],
            )

        components: dict[str, float] = {}
        matched_fields: list[str] = []
        mismatched_fields: list[str] = []
        reasons: list[str] = []
        notes: list[str] = []

        canonical_score = (
            self._text_similarity(
                left_signature.canonical_name,
                right_signature.canonical_name,
            )
        )

        components[
            "canonical_name"
        ] = canonical_score

        if canonical_score == 1.0:
            matched_fields.append(
                "canonical_name"
            )
            reasons.append(
                "same_canonical_name"
            )
        elif canonical_score > 0.0:
            matched_fields.append(
                "canonical_name_partial"
            )
        else:
            mismatched_fields.append(
                "canonical_name"
            )

        scientific_score = (
            self._text_similarity(
                left_signature.scientific_name,
                right_signature.scientific_name,
            )
        )

        components[
            "scientific_name"
        ] = scientific_score

        if scientific_score == 1.0:
            matched_fields.append(
                "scientific_name"
            )
            reasons.append(
                "same_scientific_name"
            )
        elif scientific_score == 0.0:
            mismatched_fields.append(
                "scientific_name"
            )

        authority_comparison = (
            compare_authorities(
                left_signature.authorship,
                right_signature.authorship,
            )
        )

        authority_score = (
            authority_comparison.total_score
            if (
                left_signature.authorship
                or right_signature.authorship
            )
            else 0.5
        )

        components[
            "authorship"
        ] = authority_score

        if authority_comparison.exact:
            matched_fields.append(
                "authorship"
            )
            reasons.append(
                "authority_equivalent"
            )
        elif authority_comparison.equivalent:
            matched_fields.append(
                "authorship_equivalent"
            )
            reasons.append(
                "authority_equivalent"
            )
        elif (
            left_signature.authorship
            and right_signature.authorship
        ):
            mismatched_fields.append(
                "authorship"
            )

        rank_score = (
            1.0
            if left_signature.rank
            == right_signature.rank
            else 0.0
        )

        components["rank"] = rank_score

        if rank_score == 1.0:
            matched_fields.append(
                "rank"
            )
        else:
            mismatched_fields.append(
                "rank"
            )

        kingdom_score = (
            self._optional_exact_score(
                left_signature.kingdom,
                right_signature.kingdom,
            )
        )

        components[
            "kingdom"
        ] = kingdom_score

        if kingdom_score == 1.0:
            matched_fields.append(
                "kingdom"
            )
        elif kingdom_score == 0.0:
            mismatched_fields.append(
                "kingdom"
            )

        lineage_comparison = (
            compare_lineages(
                left_signature.lineage,
                right_signature.lineage,
                ranks=(
                    "kingdom",
                    "phylum",
                    "class",
                    "order",
                    "family",
                    "genus",
                ),
                weights={
                    "kingdom": 16,
                    "phylum": 10,
                    "class": 8,
                    "order": 8,
                    "family": 10,
                    "genus": 12,
                },
            )
        )

        lineage_score = (
            lineage_comparison
            .normalized_score
        )

        components[
            "lineage"
        ] = lineage_score

        if lineage_score == 1.0:
            matched_fields.append(
                "lineage"
            )
            reasons.append(
                "same_lineage"
            )
        elif lineage_comparison.mismatched:
            mismatched_fields.extend(
                f"lineage:{rank}"
                for rank
                in lineage_comparison.mismatched
            )

        status_score = (
            self._status_similarity(
                left_signature.status,
                right_signature.status,
            )
        )

        components[
            "status"
        ] = status_score

        provider_score = (
            self._provider_support_similarity(
                left_signature.providers,
                right_signature.providers,
            )
        )

        components[
            "provider_support"
        ] = provider_score

        synonym_score = (
            self._synonym_similarity(
                left_signature.synonyms,
                right_signature.synonyms,
                left_signature.canonical_name,
                right_signature.canonical_name,
            )
        )

        components[
            "synonym_support"
        ] = synonym_score

        if synonym_score > 0:
            matched_fields.append(
                "synonym_overlap"
            )
            reasons.append(
                "synonym_overlap"
            )

        if (
            left_signature.identity_key
            and right_signature.identity_key
            and left_signature.identity_key
            == right_signature.identity_key
        ):
            reasons.append(
                "same_identity_key"
            )
            notes.append(
                "canonical identity keys are equal"
            )

        weighted_total = 0.0
        total_weight = 0.0

        for field_name, value in (
            components.items()
        ):
            weight = self.field_weights.get(
                field_name,
                0.0,
            )

            weighted_total += (
                value * weight
            )

            total_weight += weight

        score = (
            weighted_total / total_weight
            if total_weight > 0
            else 0.0
        )

        if (
            "same_identity_key"
            in reasons
        ):
            score = max(
                score,
                self.exact_threshold,
            )

        if (
            canonical_score == 1.0
            and rank_score == 1.0
            and lineage_score >= 0.90
            and authority_score >= 0.85
        ):
            score = max(
                score,
                0.95,
            )

        score = max(
            0.0,
            min(
                1.0,
                score,
            ),
        )

        exact = (
            score >= self.exact_threshold
        )

        probable_duplicate = (
            score >= self.match_threshold
        )

        review_required = (
            not probable_duplicate
            and score
            >= self.review_threshold
        )

        if probable_duplicate:
            reasons.append(
                "high_weighted_similarity"
            )

        return DuplicateScore(
            left_id=(
                left_signature.speciedex_id
            ),
            right_id=(
                right_signature.speciedex_id
            ),
            score=score,
            exact=exact,
            probable_duplicate=(
                probable_duplicate
            ),
            review_required=(
                review_required
            ),
            reasons=sorted(
                set(reasons)
            ),
            matched_fields=sorted(
                set(matched_fields)
            ),
            mismatched_fields=sorted(
                set(mismatched_fields)
            ),
            components=components,
            notes=notes,
        )

    def candidate_ids(
        self,
        signature: TaxonSignature
        | Taxon
        | Mapping[str, Any]
        | str,
        *,
        include_self: bool = False,
        limit: int | None = None,
    ) -> list[str]:
        """Return likely duplicate candidate identifiers."""

        target = self._coerce_signature(
            signature
        )

        maximum = min(
            self.maximum_candidates,
            max(
                1,
                int(
                    limit
                    if limit is not None
                    else self.maximum_candidates
                ),
            ),
        )

        candidates: set[str] = set()

        if target.identity_key:
            rows = self.connection.execute(
                """
                SELECT speciedex_id
                FROM taxa
                WHERE identity_key = ?
                LIMIT ?
                """,
                (
                    target.identity_key,
                    maximum,
                ),
            )

            candidates.update(
                str(row["speciedex_id"])
                for row in rows
            )

        if target.canonical_name:
            rows = self.connection.execute(
                """
                SELECT speciedex_id
                FROM taxa
                WHERE canonical_name = ?
                  AND rank = ?
                LIMIT ?
                """,
                (
                    target.canonical_name,
                    target.rank,
                    maximum,
                ),
            )

            candidates.update(
                str(row["speciedex_id"])
                for row in rows
            )

        if target.scientific_name:
            rows = self.connection.execute(
                """
                SELECT speciedex_id
                FROM taxa
                WHERE scientific_name = ?
                LIMIT ?
                """,
                (
                    target.scientific_name,
                    maximum,
                ),
            )

            candidates.update(
                str(row["speciedex_id"])
                for row in rows
            )

        synonym_terms = {
            *target.synonyms,
            target.canonical_name,
            target.scientific_name,
        }

        synonym_terms.discard("")

        for synonym in synonym_terms:
            rows = self.connection.execute(
                """
                SELECT DISTINCT speciedex_id
                FROM synonyms
                WHERE synonym_key = ?
                LIMIT ?
                """,
                (
                    normalize_key(synonym),
                    maximum,
                ),
            )

            candidates.update(
                str(row["speciedex_id"])
                for row in rows
            )

        if (
            target.speciedex_id
            and not include_self
        ):
            candidates.discard(
                target.speciedex_id
            )

        return sorted(
            candidates
        )[:maximum]

    def find_duplicates_for(
        self,
        signature: TaxonSignature
        | Taxon
        | Mapping[str, Any]
        | str,
        *,
        include_review: bool = True,
        limit: int | None = None,
    ) -> list[DuplicatePair]:
        """Find scored duplicate candidates for one taxon."""

        target = self._coerce_signature(
            signature
        )

        pairs: list[
            DuplicatePair
        ] = []

        for candidate_id in self.candidate_ids(
            target,
            include_self=False,
            limit=limit,
        ):
            candidate = self.signature(
                candidate_id
            )

            if candidate is None:
                continue

            score = self.compare(
                target,
                candidate,
            )

            if not (
                score.probable_duplicate
                or (
                    include_review
                    and score.review_required
                )
            ):
                continue

            recommended_action = (
                "merge"
                if score.probable_duplicate
                else "review"
            )

            preferred_id = (
                self.preferred_identifier(
                    target,
                    candidate,
                )
            )

            pairs.append(
                DuplicatePair(
                    duplicate_id=(
                        self.duplicate_identifier(
                            target.speciedex_id,
                            candidate.speciedex_id,
                        )
                    ),
                    left=target,
                    right=candidate,
                    score=score,
                    recommended_action=(
                        recommended_action
                    ),
                    preferred_id=preferred_id,
                )
            )

        pairs.sort(
            key=lambda pair: (
                pair.score.score,
                pair.duplicate_id,
            ),
            reverse=True,
        )

        return pairs

    def scan(
        self,
        *,
        limit_taxa: int | None = None,
        include_review: bool = True,
    ) -> list[DuplicatePair]:
        """Scan the canonical index for duplicate candidate pairs."""

        query = """
            SELECT *
            FROM taxa
            ORDER BY speciedex_id
        """

        parameters: tuple[Any, ...] = ()

        if (
            limit_taxa is not None
            and limit_taxa > 0
        ):
            query += " LIMIT ?"
            parameters = (
                int(limit_taxa),
            )

        seen_pairs: set[
            tuple[str, str]
        ] = set()

        results: list[
            DuplicatePair
        ] = []

        for row in self.connection.execute(
            query,
            parameters,
        ):
            signature = (
                self._signature_from_row(
                    row
                )
            )

            for pair in self.find_duplicates_for(
                signature,
                include_review=include_review,
            ):
                pair_key = tuple(
                    sorted(
                        (
                            pair.left.speciedex_id,
                            pair.right.speciedex_id,
                        )
                    )
                )

                if pair_key in seen_pairs:
                    continue

                seen_pairs.add(
                    pair_key
                )

                results.append(
                    pair
                )

        results.sort(
            key=lambda pair: (
                pair.score.score,
                pair.duplicate_id,
            ),
            reverse=True,
        )

        return results

    def clusters(
        self,
        pairs: Sequence[
            DuplicatePair
        ],
    ) -> list[DuplicateCluster]:
        """Build connected duplicate clusters from scored pairs."""

        adjacency: dict[
            str,
            set[str],
        ] = {}

        pair_map: dict[
            tuple[str, str],
            DuplicatePair,
        ] = {}

        for pair in pairs:
            left_id = (
                pair.left.speciedex_id
            )

            right_id = (
                pair.right.speciedex_id
            )

            adjacency.setdefault(
                left_id,
                set(),
            ).add(
                right_id
            )

            adjacency.setdefault(
                right_id,
                set(),
            ).add(
                left_id
            )

            pair_key = tuple(
                sorted(
                    (
                        left_id,
                        right_id,
                    )
                )
            )

            pair_map[
                pair_key
            ] = pair

        visited: set[str] = set()
        clusters: list[
            DuplicateCluster
        ] = []

        for start in sorted(
            adjacency
        ):
            if start in visited:
                continue

            stack = [start]
            members: set[str] = set()

            while stack:
                current = stack.pop()

                if current in visited:
                    continue

                visited.add(current)
                members.add(current)

                stack.extend(
                    sorted(
                        adjacency.get(
                            current,
                            set(),
                        )
                        - visited
                    )
                )

            cluster_pairs = [
                pair
                for pair_key, pair
                in pair_map.items()
                if (
                    pair_key[0] in members
                    and pair_key[1] in members
                )
            ]

            scores = [
                pair.score.score
                for pair in cluster_pairs
            ]

            signatures = [
                signature
                for member in sorted(
                    members
                )
                if (
                    signature
                    := self.signature(member)
                )
                is not None
            ]

            preferred_id = (
                self.preferred_from_many(
                    signatures
                )
            )

            recommended_action = (
                "merge"
                if cluster_pairs
                and all(
                    pair.score
                    .probable_duplicate
                    for pair
                    in cluster_pairs
                )
                else "review"
            )

            cluster_id = (
                self.cluster_identifier(
                    members
                )
            )

            clusters.append(
                DuplicateCluster(
                    cluster_id=cluster_id,
                    members=sorted(
                        members
                    ),
                    pairs=cluster_pairs,
                    preferred_id=(
                        preferred_id
                    ),
                    recommended_action=(
                        recommended_action
                    ),
                    maximum_score=(
                        max(scores)
                        if scores
                        else 0.0
                    ),
                    minimum_score=(
                        min(scores)
                        if scores
                        else 0.0
                    ),
                    average_score=(
                        sum(scores)
                        / len(scores)
                        if scores
                        else 0.0
                    ),
                )
            )

        clusters.sort(
            key=lambda cluster: (
                cluster.average_score,
                cluster.cluster_id,
            ),
            reverse=True,
        )

        return clusters

    def build_merge_plan(
        self,
        cluster: DuplicateCluster,
    ) -> DuplicateMergePlan:
        """Build a non-destructive merge plan for one cluster."""

        if not cluster.members:
            raise DeduplicationError(
                "Cannot build a merge plan for "
                "an empty duplicate cluster."
            )

        keep_id = (
            cluster.preferred_id
            or sorted(
                cluster.members
            )[0]
        )

        merge_ids = [
            identifier
            for identifier
            in cluster.members
            if identifier != keep_id
        ]

        source_id_moves: list[
            dict[str, str]
        ] = []

        assertion_moves: list[
            dict[str, str]
        ] = []

        synonym_moves: list[
            dict[str, str]
        ] = []

        conflicts: list[str] = []

        for merge_id in merge_ids:
            source_rows = (
                self.connection.execute(
                    """
                    SELECT
                        provider,
                        provider_id,
                        speciedex_id
                    FROM source_ids
                    WHERE speciedex_id = ?
                    """,
                    (
                        merge_id,
                    ),
                )
            )

            for row in source_rows:
                existing = (
                    self.connection.execute(
                        """
                        SELECT speciedex_id
                        FROM source_ids
                        WHERE provider = ?
                          AND provider_id = ?
                        """,
                        (
                            row["provider"],
                            row["provider_id"],
                        ),
                    ).fetchone()
                )

                if (
                    existing is not None
                    and str(
                        existing["speciedex_id"]
                    )
                    not in {
                        merge_id,
                        keep_id,
                    }
                ):
                    conflicts.append(
                        "source identifier collision: "
                        f"{row['provider']}:"
                        f"{row['provider_id']}"
                    )
                    continue

                source_id_moves.append(
                    {
                        "provider": str(
                            row["provider"]
                        ),
                        "provider_id": str(
                            row["provider_id"]
                        ),
                        "from": merge_id,
                        "to": keep_id,
                    }
                )

            assertion_rows = (
                self.connection.execute(
                    """
                    SELECT
                        provider,
                        provider_id,
                        speciedex_id
                    FROM assertions
                    WHERE speciedex_id = ?
                    """,
                    (
                        merge_id,
                    ),
                )
            )

            for row in assertion_rows:
                assertion_moves.append(
                    {
                        "provider": str(
                            row["provider"]
                        ),
                        "provider_id": str(
                            row["provider_id"]
                        ),
                        "from": merge_id,
                        "to": keep_id,
                    }
                )

            synonym_rows = (
                self.connection.execute(
                    """
                    SELECT
                        synonym_key,
                        provider,
                        speciedex_id
                    FROM synonyms
                    WHERE speciedex_id = ?
                    """,
                    (
                        merge_id,
                    ),
                )
            )

            for row in synonym_rows:
                synonym_moves.append(
                    {
                        "synonym_key": str(
                            row["synonym_key"]
                        ),
                        "provider": str(
                            row["provider"]
                        ),
                        "from": merge_id,
                        "to": keep_id,
                    }
                )

        safe_to_apply = (
            cluster.recommended_action
            == "merge"
            and not conflicts
        )

        reason = (
            "all duplicate pairs exceed the "
            "automatic merge threshold"
            if safe_to_apply
            else (
                "manual review required because "
                "the cluster contains uncertain "
                "matches or identifier collisions"
            )
        )

        return DuplicateMergePlan(
            cluster_id=(
                cluster.cluster_id
            ),
            keep_id=keep_id,
            merge_ids=merge_ids,
            source_id_moves=(
                source_id_moves
            ),
            assertion_moves=(
                assertion_moves
            ),
            synonym_moves=(
                synonym_moves
            ),
            conflicts=sorted(
                set(conflicts)
            ),
            reason=reason,
            safe_to_apply=(
                safe_to_apply
            ),
        )

    def preferred_identifier(
        self,
        left: TaxonSignature,
        right: TaxonSignature,
    ) -> str | None:
        """Choose the preferred canonical taxon identifier."""

        return self.preferred_from_many(
            (
                left,
                right,
            )
        )

    def preferred_from_many(
        self,
        signatures: Iterable[
            TaxonSignature
        ],
    ) -> str | None:
        """Choose the strongest canonical record from several signatures."""

        values = list(
            signatures
        )

        if not values:
            return None

        ranked = sorted(
            values,
            key=lambda signature: (
                self._preference_score(
                    signature
                ),
                signature.speciedex_id,
            ),
            reverse=True,
        )

        return (
            ranked[0].speciedex_id
        )

    def statistics(
        self,
        pairs: Sequence[
            DuplicatePair
        ],
        clusters: Sequence[
            DuplicateCluster
        ] | None = None,
    ) -> DeduplicationStatistics:
        """Return aggregate duplicate statistics."""

        cluster_values = (
            list(clusters)
            if clusters is not None
            else self.clusters(pairs)
        )

        taxa_scanned = self.index.table_count(
            "taxa"
        )

        return DeduplicationStatistics(
            taxa_scanned=taxa_scanned,
            candidate_pairs=len(pairs),
            exact_pairs=sum(
                1
                for pair in pairs
                if pair.score.exact
            ),
            probable_pairs=sum(
                1
                for pair in pairs
                if pair.score
                .probable_duplicate
            ),
            review_pairs=sum(
                1
                for pair in pairs
                if pair.score
                .review_required
            ),
            clusters=len(
                cluster_values
            ),
            mergeable_clusters=sum(
                1
                for cluster
                in cluster_values
                if cluster.recommended_action
                == "merge"
            ),
            conflicting_clusters=sum(
                1
                for cluster
                in cluster_values
                if cluster.recommended_action
                != "merge"
            ),
        )

    def verify_pairs(
        self,
        pairs: Sequence[
            DuplicatePair
        ],
    ) -> DeduplicationVerification:
        """Verify duplicate pair consistency."""

        errors: list[str] = []
        warnings: list[str] = []
        seen_ids: set[str] = set()

        for pair in pairs:
            if (
                pair.recommended_action
                not in MERGE_ACTIONS
            ):
                errors.append(
                    "Duplicate pair has unsupported "
                    "recommended action: "
                    f"{pair.recommended_action}."
                )

            if (
                pair.left.speciedex_id
                == pair.right.speciedex_id
            ):
                errors.append(
                    "Duplicate pair compares a taxon "
                    "to itself: "
                    f"{pair.left.speciedex_id}."
                )

            if pair.duplicate_id in seen_ids:
                errors.append(
                    "Duplicate duplicate_id: "
                    f"{pair.duplicate_id}."
                )

            seen_ids.add(
                pair.duplicate_id
            )

            expected_id = (
                self.duplicate_identifier(
                    pair.left.speciedex_id,
                    pair.right.speciedex_id,
                )
            )

            if (
                pair.duplicate_id
                != expected_id
            ):
                errors.append(
                    "Duplicate pair identifier does "
                    "not match its member IDs: "
                    f"{pair.duplicate_id}."
                )

            if not (
                0.0
                <= pair.score.score
                <= 1.0
            ):
                errors.append(
                    "Duplicate score is outside "
                    "0.0–1.0: "
                    f"{pair.duplicate_id}."
                )

            if (
                pair.score.probable_duplicate
                and pair.score.score
                < self.match_threshold
            ):
                errors.append(
                    "Duplicate pair is marked probable "
                    "below the configured threshold: "
                    f"{pair.duplicate_id}."
                )

            if (
                pair.recommended_action
                == "merge"
                and not pair.score
                .probable_duplicate
            ):
                warnings.append(
                    "Merge was recommended for a pair "
                    "that is not marked probable: "
                    f"{pair.duplicate_id}."
                )

        return DeduplicationVerification(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def duplicate_identifier(
        left_id: str,
        right_id: str,
    ) -> str:
        """Return a deterministic duplicate-pair identifier."""

        members = sorted(
            {
                str(left_id),
                str(right_id),
            }
        )

        if len(members) != 2:
            raise ValueError(
                "Duplicate identifier requires "
                "two distinct taxon IDs."
            )

        digest = stable_json_hash(
            {
                "schema_version": (
                    DEDUPLICATION_SCHEMA_VERSION
                ),
                "members": members,
            }
        )

        return (
            "spx-duplicate:sha256:"
            + digest
        )

    @staticmethod
    def cluster_identifier(
        members: Iterable[str],
    ) -> str:
        """Return a deterministic duplicate-cluster identifier."""

        normalized = sorted(
            {
                str(member)
                for member in members
                if str(member)
            }
        )

        if len(normalized) < 2:
            raise ValueError(
                "Duplicate cluster requires at "
                "least two members."
            )

        digest = stable_json_hash(
            {
                "schema_version": (
                    DEDUPLICATION_SCHEMA_VERSION
                ),
                "members": normalized,
            }
        )

        return (
            "spx-duplicate-cluster:sha256:"
            + digest
        )

    def _coerce_signature(
        self,
        value: TaxonSignature
        | Taxon
        | Mapping[str, Any]
        | str,
    ) -> TaxonSignature:
        """Convert supported inputs into TaxonSignature."""

        if isinstance(
            value,
            TaxonSignature,
        ):
            return value

        if isinstance(value, str):
            signature = self.signature(
                value
            )

            if signature is None:
                raise KeyError(
                    f"Unknown canonical taxon: "
                    f"{value}"
                )

            return signature

        if isinstance(value, Taxon):
            return self._signature_from_taxon(
                value
            )

        if isinstance(value, Mapping):
            return self._signature_from_mapping(
                value
            )

        raise TypeError(
            "Unsupported duplicate signature "
            f"source: {type(value).__name__}"
        )

    def _signature_from_row(
        self,
        row: sqlite3.Row,
    ) -> TaxonSignature:
        """Build a signature from one canonical SQLite row."""

        identifier = str(
            row["speciedex_id"]
        )

        providers = tuple(
            sorted(
                {
                    str(
                        provider_row[
                            "provider"
                        ]
                    )
                    for provider_row
                    in self.connection.execute(
                        """
                        SELECT DISTINCT provider
                        FROM assertions
                        WHERE speciedex_id = ?
                        ORDER BY provider
                        """,
                        (
                            identifier,
                        ),
                    )
                }
            )
        )

        synonyms = tuple(
            sorted(
                {
                    str(
                        synonym_row[
                            "synonym_key"
                        ]
                    )
                    for synonym_row
                    in self.connection.execute(
                        """
                        SELECT DISTINCT synonym_key
                        FROM synonyms
                        WHERE speciedex_id = ?
                        ORDER BY synonym_key
                        """,
                        (
                            identifier,
                        ),
                    )
                }
            )
        )

        return TaxonSignature(
            speciedex_id=identifier,
            canonical_name=normalize_key(
                row["canonical_name"]
            ),
            scientific_name=normalize_key(
                row["scientific_name"]
            ),
            authorship=str(
                row["authorship"]
                or ""
            ),
            rank=normalize_rank(
                row["rank"]
            ),
            status=normalize_status(
                row["status"]
            ),
            kingdom=normalize_key(
                row["kingdom"]
            ),
            phylum=normalize_key(
                row["phylum"]
            ),
            class_name=normalize_key(
                row["class_name"]
            ),
            order=normalize_key(
                row["order_name"]
            ),
            family=normalize_key(
                row["family"]
            ),
            genus=normalize_key(
                row["genus"]
            ),
            identity_key=str(
                row["identity_key"]
                or ""
            ),
            record_hash=str(
                row["record_hash"]
                or ""
            ),
            providers=providers,
            synonyms=synonyms,
        )

    @staticmethod
    def _signature_from_taxon(
        record: Taxon,
    ) -> TaxonSignature:
        """Build a signature from a provider Taxon."""

        canonical = (
            canonical_name(
                record.canonical_name
                or record.scientific_name,
                authorship=record.authorship,
                rank=record.rank,
            )
        )

        return TaxonSignature(
            speciedex_id="",
            canonical_name=normalize_key(
                canonical
            ),
            scientific_name=normalize_key(
                record.scientific_name
            ),
            authorship=str(
                record.authorship
                or ""
            ),
            rank=normalize_rank(
                record.rank
            ),
            status=normalize_status(
                record.status
            ),
            kingdom=normalize_key(
                record.kingdom
            ),
            phylum=normalize_key(
                record.phylum
            ),
            class_name=normalize_key(
                record.class_name
            ),
            order=normalize_key(
                record.order
            ),
            family=normalize_key(
                record.family
            ),
            genus=normalize_key(
                record.genus
            ),
            providers=(
                (
                    normalize_key(
                        record.provider
                    ),
                )
                if record.provider
                else ()
            ),
            synonyms=tuple(
                sorted(
                    {
                        normalize_key(
                            synonym
                        )
                        for synonym
                        in record.synonyms
                        if normalize_key(
                            synonym
                        )
                    }
                )
            ),
        )

    @staticmethod
    def _signature_from_mapping(
        value: Mapping[str, Any],
    ) -> TaxonSignature:
        """Build a signature from a mapping."""

        taxonomy = value.get(
            "taxonomy",
            {},
        )

        if not isinstance(
            taxonomy,
            Mapping,
        ):
            taxonomy = {}

        providers = value.get(
            "providers",
            (),
        )

        if isinstance(providers, str):
            providers = (
                providers,
            )

        synonyms = value.get(
            "synonyms",
            (),
        )

        if isinstance(synonyms, str):
            synonyms = (
                synonyms,
            )

        return TaxonSignature(
            speciedex_id=str(
                value.get(
                    "speciedex_id",
                    value.get(
                        "id",
                        "",
                    ),
                )
                or ""
            ),
            canonical_name=normalize_key(
                value.get(
                    "canonical_name",
                    value.get(
                        "canonicalName",
                        "",
                    ),
                )
            ),
            scientific_name=normalize_key(
                value.get(
                    "scientific_name",
                    value.get(
                        "scientificName",
                        "",
                    ),
                )
            ),
            authorship=str(
                value.get(
                    "authorship",
                    value.get(
                        "authority",
                        "",
                    ),
                )
                or ""
            ),
            rank=normalize_rank(
                value.get(
                    "rank",
                    value.get(
                        "taxonRank",
                        "",
                    ),
                )
            ),
            status=normalize_status(
                value.get(
                    "status",
                    ""
                )
            ),
            kingdom=normalize_key(
                taxonomy.get(
                    "kingdom",
                    value.get(
                        "kingdom",
                        "",
                    ),
                )
            ),
            phylum=normalize_key(
                taxonomy.get(
                    "phylum",
                    value.get(
                        "phylum",
                        "",
                    ),
                )
            ),
            class_name=normalize_key(
                taxonomy.get(
                    "class",
                    value.get(
                        "class_name",
                        value.get(
                            "class",
                            "",
                        ),
                    ),
                )
            ),
            order=normalize_key(
                taxonomy.get(
                    "order",
                    value.get(
                        "order",
                        "",
                    ),
                )
            ),
            family=normalize_key(
                taxonomy.get(
                    "family",
                    value.get(
                        "family",
                        "",
                    ),
                )
            ),
            genus=normalize_key(
                taxonomy.get(
                    "genus",
                    value.get(
                        "genus",
                        "",
                    ),
                )
            ),
            identity_key=str(
                value.get(
                    "identity_key",
                    "",
                )
                or ""
            ),
            record_hash=str(
                value.get(
                    "record_hash",
                    "",
                )
                or ""
            ),
            providers=tuple(
                sorted(
                    {
                        normalize_key(
                            provider
                        )
                        for provider
                        in providers
                        if normalize_key(
                            provider
                        )
                    }
                )
            ),
            synonyms=tuple(
                sorted(
                    {
                        normalize_key(
                            synonym
                        )
                        for synonym
                        in synonyms
                        if normalize_key(
                            synonym
                        )
                    }
                )
            ),
        )

    @staticmethod
    def _text_similarity(
        left: str,
        right: str,
    ) -> float:
        """Return a conservative normalized text similarity."""

        left_key = normalize_key(left)
        right_key = normalize_key(right)

        if not left_key and not right_key:
            return 0.5

        if not left_key or not right_key:
            return 0.0

        if left_key == right_key:
            return 1.0

        left_tokens = left_key.split()
        right_tokens = right_key.split()

        left_set = set(left_tokens)
        right_set = set(right_tokens)

        union = left_set | right_set

        if not union:
            return 0.0

        jaccard = (
            len(
                left_set
                & right_set
            )
            / len(union)
        )

        prefix = 0.0

        if (
            left_tokens
            and right_tokens
            and left_tokens[0]
            == right_tokens[0]
        ):
            prefix = 0.25

        length_ratio = (
            min(
                len(left_key),
                len(right_key),
            )
            / max(
                len(left_key),
                len(right_key),
            )
        )

        return max(
            0.0,
            min(
                1.0,
                (
                    jaccard * 0.60
                    + length_ratio * 0.15
                    + prefix
                ),
            ),
        )

    @staticmethod
    def _optional_exact_score(
        left: str,
        right: str,
    ) -> float:
        """Score optional exact fields."""

        left_key = normalize_key(left)
        right_key = normalize_key(right)

        if not left_key and not right_key:
            return 0.5

        if not left_key or not right_key:
            return 0.25

        return (
            1.0
            if left_key == right_key
            else 0.0
        )

    @staticmethod
    def _status_similarity(
        left: str,
        right: str,
    ) -> float:
        """Compare taxonomic statuses."""

        left_status = normalize_status(
            left
        )

        right_status = normalize_status(
            right
        )

        if left_status == right_status:
            return 1.0

        if (
            left_status in ACCEPTED_STATUSES
            and right_status
            in ACCEPTED_STATUSES
        ):
            return 0.85

        if (
            left_status in SYNONYM_STATUSES
            and right_status
            in SYNONYM_STATUSES
        ):
            return 0.75

        if (
            (
                left_status
                in ACCEPTED_STATUSES
                and right_status
                in SYNONYM_STATUSES
            )
            or (
                right_status
                in ACCEPTED_STATUSES
                and left_status
                in SYNONYM_STATUSES
            )
        ):
            return 0.35

        return 0.5

    @staticmethod
    def _provider_support_similarity(
        left: Sequence[str],
        right: Sequence[str],
    ) -> float:
        """Compare provider-support sets."""

        left_set = {
            normalize_key(value)
            for value in left
            if normalize_key(value)
        }

        right_set = {
            normalize_key(value)
            for value in right
            if normalize_key(value)
        }

        if not left_set and not right_set:
            return 0.5

        if not left_set or not right_set:
            return 0.25

        union = left_set | right_set

        if not union:
            return 0.0

        return (
            len(
                left_set
                & right_set
            )
            / len(union)
        )

    @staticmethod
    def _synonym_similarity(
        left: Sequence[str],
        right: Sequence[str],
        left_canonical: str,
        right_canonical: str,
    ) -> float:
        """Compare synonym sets and cross-name overlap."""

        left_set = {
            normalize_key(value)
            for value in left
            if normalize_key(value)
        }

        right_set = {
            normalize_key(value)
            for value in right
            if normalize_key(value)
        }

        left_name = normalize_key(
            left_canonical
        )

        right_name = normalize_key(
            right_canonical
        )

        if (
            left_name
            and left_name in right_set
        ):
            return 1.0

        if (
            right_name
            and right_name in left_set
        ):
            return 1.0

        if not left_set or not right_set:
            return 0.0

        intersection = (
            left_set & right_set
        )

        union = left_set | right_set

        if not union:
            return 0.0

        return (
            len(intersection)
            / len(union)
        )

    def _preference_score(
        self,
        signature: TaxonSignature,
    ) -> tuple[
        float,
        int,
        int,
        int,
        int,
    ]:
        """Return a deterministic canonical-record preference score."""

        accepted_score = (
            1.0
            if signature.status
            in ACCEPTED_STATUSES
            else 0.0
        )

        lineage_completeness = sum(
            1
            for value
            in signature.lineage.values()
            if value
        )

        provider_support = len(
            signature.providers
        )

        synonym_support = len(
            signature.synonyms
        )

        has_authorship = (
            1
            if signature.authorship
            else 0
        )

        return (
            accepted_score,
            provider_support,
            lineage_completeness,
            has_authorship,
            synonym_support,
        )


def duplicate_score(
    index: SQLiteIndex,
    left: TaxonSignature
    | Taxon
    | Mapping[str, Any]
    | str,
    right: TaxonSignature
    | Taxon
    | Mapping[str, Any]
    | str,
    *,
    field_weights: Mapping[
        str,
        float,
    ] | None = None,
) -> DuplicateScore:
    """Compatibility helper for comparing two taxa."""

    return Deduplicator(
        index,
        field_weights=field_weights,
    ).compare(
        left,
        right,
    )


def find_duplicates(
    index: SQLiteIndex,
    *,
    limit_taxa: int | None = None,
    include_review: bool = True,
    field_weights: Mapping[
        str,
        float,
    ] | None = None,
    exact_threshold: float = (
        DEFAULT_EXACT_THRESHOLD
    ),
    match_threshold: float = (
        DEFAULT_MATCH_THRESHOLD
    ),
    review_threshold: float = (
        DEFAULT_REVIEW_THRESHOLD
    ),
) -> list[DuplicatePair]:
    """Scan a SQLite index for duplicate taxon pairs."""

    manager = Deduplicator(
        index,
        field_weights=field_weights,
        exact_threshold=exact_threshold,
        match_threshold=match_threshold,
        review_threshold=review_threshold,
    )

    return manager.scan(
        limit_taxa=limit_taxa,
        include_review=include_review,
    )


def build_duplicate_clusters(
    index: SQLiteIndex,
    pairs: Sequence[
        DuplicatePair
    ],
) -> list[DuplicateCluster]:
    """Compatibility helper for duplicate clustering."""

    return Deduplicator(
        index
    ).clusters(
        pairs
    )
