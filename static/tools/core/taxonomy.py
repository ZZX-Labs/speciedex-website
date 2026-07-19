#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/taxonomy.py

Taxonomic normalization, rank handling, lineage utilities, status policy,
canonical-name preparation, and record validation.

This module provides the shared taxonomy vocabulary used by providers,
reconciliation, archive storage, statistics, and validation.

It owns:

- taxonomic rank normalization,
- rank aliases and hierarchy ordering,
- taxonomic status normalization,
- accepted/synonym status classification,
- canonical scientific-name cleanup,
- lineage normalization,
- parent-rank lookup,
- rank comparison,
- Taxon normalization,
- taxonomic validation,
- stable taxonomy dictionaries.

Provider modules should normalize source records through these functions before
returning Taxon objects. Archive and reconciler code may also use this module
to enforce one consistent vocabulary across all providers.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from providers.common import Taxon


TAXONOMY_SCHEMA_VERSION = 1

UNKNOWN_RANK = "unranked"
UNKNOWN_STATUS = "unknown"

PRIMARY_RANKS = (
    "domain",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
    "species",
)

LINEAGE_FIELDS = (
    "domain",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
)

ACCEPTED_STATUSES = {
    "accepted",
    "valid",
    "provisionally accepted",
    "reference",
}

SYNONYM_STATUSES = {
    "synonym",
    "unaccepted",
    "invalid",
    "misapplied",
    "superseded",
    "deprecated",
}

UNCERTAIN_STATUSES = {
    "unknown",
    "uncertain",
    "doubtful",
    "incertae sedis",
    "quarantined",
}

TERMINAL_RANKS = {
    "species",
    "subspecies",
    "variety",
    "subvariety",
    "form",
    "subform",
    "strain",
    "cultivar",
    "pathovar",
    "serovar",
    "biovar",
    "isolate",
    "hybrid",
    "virus",
}

RANK_ORDER = (
    "domain",
    "superkingdom",
    "kingdom",
    "subkingdom",
    "infrakingdom",
    "superphylum",
    "phylum",
    "subphylum",
    "infraphylum",
    "superclass",
    "class",
    "subclass",
    "infraclass",
    "superorder",
    "order",
    "suborder",
    "infraorder",
    "parvorder",
    "superfamily",
    "family",
    "subfamily",
    "tribe",
    "subtribe",
    "genus",
    "subgenus",
    "section",
    "subsection",
    "series",
    "subseries",
    "species",
    "subspecies",
    "variety",
    "subvariety",
    "form",
    "subform",
    "strain",
    "cultivar",
    "pathovar",
    "serovar",
    "biovar",
    "isolate",
    "hybrid",
    "virus",
    "clade",
    "unranked",
)

RANK_INDEX = {
    rank: index
    for index, rank in enumerate(
        RANK_ORDER
    )
}

RANK_ALIASES = {
    "dom": "domain",
    "domain": "domain",
    "super kingdom": "superkingdom",
    "superkingdom": "superkingdom",
    "super kingdom rank": "superkingdom",
    "kingdom": "kingdom",
    "regnum": "kingdom",
    "sub kingdom": "subkingdom",
    "subkingdom": "subkingdom",
    "infra kingdom": "infrakingdom",
    "infrakingdom": "infrakingdom",
    "super phylum": "superphylum",
    "superphylum": "superphylum",
    "phylum": "phylum",
    "division": "phylum",
    "divisio": "phylum",
    "sub phylum": "subphylum",
    "subphylum": "subphylum",
    "subdivision": "subphylum",
    "infra phylum": "infraphylum",
    "infraphylum": "infraphylum",
    "super class": "superclass",
    "superclass": "superclass",
    "class": "class",
    "classis": "class",
    "sub class": "subclass",
    "subclass": "subclass",
    "infra class": "infraclass",
    "infraclass": "infraclass",
    "super order": "superorder",
    "superorder": "superorder",
    "order": "order",
    "ordo": "order",
    "sub order": "suborder",
    "suborder": "suborder",
    "infra order": "infraorder",
    "infraorder": "infraorder",
    "parv order": "parvorder",
    "parvorder": "parvorder",
    "super family": "superfamily",
    "superfamily": "superfamily",
    "family": "family",
    "familia": "family",
    "sub family": "subfamily",
    "subfamily": "subfamily",
    "tribe": "tribe",
    "tribus": "tribe",
    "sub tribe": "subtribe",
    "subtribe": "subtribe",
    "genus": "genus",
    "sub genus": "subgenus",
    "subgenus": "subgenus",
    "section": "section",
    "sectio": "section",
    "sub section": "subsection",
    "subsection": "subsection",
    "series": "series",
    "sub series": "subseries",
    "subseries": "subseries",
    "species": "species",
    "sp": "species",
    "sp.": "species",
    "specific": "species",
    "sub species": "subspecies",
    "subspecies": "subspecies",
    "ssp": "subspecies",
    "ssp.": "subspecies",
    "subsp": "subspecies",
    "subsp.": "subspecies",
    "var": "variety",
    "var.": "variety",
    "variety": "variety",
    "varietas": "variety",
    "sub variety": "subvariety",
    "subvariety": "subvariety",
    "form": "form",
    "forma": "form",
    "f": "form",
    "f.": "form",
    "sub form": "subform",
    "subform": "subform",
    "strain": "strain",
    "cultivar": "cultivar",
    "cv": "cultivar",
    "cv.": "cultivar",
    "pathovar": "pathovar",
    "pv": "pathovar",
    "pv.": "pathovar",
    "serovar": "serovar",
    "biovar": "biovar",
    "isolate": "isolate",
    "hybrid": "hybrid",
    "virus": "virus",
    "clade": "clade",
    "unranked": "unranked",
    "no rank": "unranked",
    "norank": "unranked",
    "none": "unranked",
    "unknown": "unranked",
    "taxon": "unranked",
}

STATUS_ALIASES = {
    "accepted": "accepted",
    "accepted name": "accepted",
    "accepted taxon": "accepted",
    "valid": "valid",
    "valid name": "valid",
    "valid taxon": "valid",
    "provisionally accepted": (
        "provisionally accepted"
    ),
    "provisional": "provisionally accepted",
    "provisionally valid": (
        "provisionally accepted"
    ),
    "reference": "reference",
    "reference taxon": "reference",
    "synonym": "synonym",
    "synonymized": "synonym",
    "junior synonym": "synonym",
    "objective synonym": "synonym",
    "subjective synonym": "synonym",
    "heterotypic synonym": "synonym",
    "homotypic synonym": "synonym",
    "unaccepted": "unaccepted",
    "not accepted": "unaccepted",
    "invalid": "invalid",
    "invalid name": "invalid",
    "misapplied": "misapplied",
    "misapplied name": "misapplied",
    "superseded": "superseded",
    "deprecated": "deprecated",
    "obsolete": "deprecated",
    "doubtful": "doubtful",
    "uncertain": "uncertain",
    "incertae sedis": "incertae sedis",
    "quarantined": "quarantined",
    "temporary": "provisionally accepted",
    "temporary name": "provisionally accepted",
    "unknown": "unknown",
    "": "unknown",
}

INFRASPECIFIC_MARKERS = {
    "subsp.": "subspecies",
    "subsp": "subspecies",
    "ssp.": "subspecies",
    "ssp": "subspecies",
    "var.": "variety",
    "var": "variety",
    "f.": "form",
    "f": "form",
    "cv.": "cultivar",
    "cv": "cultivar",
    "pv.": "pathovar",
    "pv": "pathovar",
}

AUTHORSHIP_PATTERN = re.compile(
    r"""
    \s+
    (?:
        \(
            [^()]+
        \)
        |
        [A-Z][A-Za-zÀ-ÖØ-öø-ÿ.'’\-]+
    )
    (?:
        \s*
        [,;&]
        \s*
        [A-Z][A-Za-zÀ-ÖØ-öø-ÿ.'’\-]+
    )*
    (?:\s*,?\s*\d{4}[a-z]?)?
    \s*$
    """,
    re.VERBOSE,
)

MULTISPACE_PATTERN = re.compile(
    r"\s+"
)

HYBRID_SPACE_PATTERN = re.compile(
    r"\s*[×x]\s*"
)

QUALIFIER_PATTERN = re.compile(
    r"""
    \s+
    (?:
        sensu
        |
        non
        |
        nec
        |
        auct\.
        |
        aff\.
        |
        cf\.
        |
        nr\.
    )
    \s+
    .+$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class TaxonomyError(ValueError):
    """Raised when taxonomic data cannot be normalized safely."""


@dataclass(slots=True)
class TaxonomyValidation:
    """Validation result for one taxonomic record."""

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
        return {
            "valid": self.valid,
            "errors": list(
                self.errors
            ),
            "warnings": list(
                self.warnings
            ),
        }


@dataclass(slots=True)
class Lineage:
    """Normalized taxonomic lineage."""

    values: dict[str, str] = field(
        default_factory=dict
    )

    def get(
        self,
        rank: str,
        default: str = "",
    ) -> str:
        return self.values.get(
            normalize_rank(rank),
            default,
        )

    def set(
        self,
        rank: str,
        name: Any,
    ) -> None:
        normalized_rank = normalize_rank(
            rank
        )

        normalized_name = normalize_taxon_name(
            name
        )

        if (
            normalized_rank
            and normalized_name
        ):
            self.values[
                normalized_rank
            ] = normalized_name

    def as_dict(
        self,
    ) -> dict[str, str]:
        return {
            rank: self.values[rank]
            for rank in sorted(
                self.values,
                key=rank_sort_key,
            )
        }

    def primary(
        self,
    ) -> dict[str, str]:
        return {
            rank: self.values.get(
                rank,
                "",
            )
            for rank in LINEAGE_FIELDS
        }

    def ancestors_of(
        self,
        rank: str,
    ) -> dict[str, str]:
        normalized_rank = normalize_rank(
            rank
        )

        target_index = rank_index(
            normalized_rank
        )

        return {
            lineage_rank: name
            for lineage_rank, name
            in self.as_dict().items()
            if rank_index(
                lineage_rank
            ) < target_index
        }

    def descendants_of(
        self,
        rank: str,
    ) -> dict[str, str]:
        normalized_rank = normalize_rank(
            rank
        )

        target_index = rank_index(
            normalized_rank
        )

        return {
            lineage_rank: name
            for lineage_rank, name
            in self.as_dict().items()
            if rank_index(
                lineage_rank
            ) > target_index
        }


def normalize_space(
    value: Any,
) -> str:
    """Normalize Unicode and collapse whitespace."""

    text = unicodedata.normalize(
        "NFKC",
        str(
            value
            if value is not None
            else ""
        ),
    )

    return MULTISPACE_PATTERN.sub(
        " ",
        text,
    ).strip()


def normalize_key(
    value: Any,
) -> str:
    """Normalize text for deterministic comparisons."""

    return normalize_space(
        value
    ).casefold()


def normalize_rank(
    value: Any,
    *,
    fallback: str = UNKNOWN_RANK,
) -> str:
    """Normalize provider-specific rank labels."""

    rank = normalize_key(
        value
    )

    rank = rank.replace(
        "_",
        " ",
    ).replace(
        "-",
        " ",
    )

    rank = MULTISPACE_PATTERN.sub(
        " ",
        rank,
    ).strip()

    if rank in RANK_ALIASES:
        return RANK_ALIASES[rank]

    compact = rank.replace(
        " ",
        "",
    )

    if compact in RANK_ALIASES:
        return RANK_ALIASES[compact]

    return (
        rank.replace(
            " ",
            "_",
        )
        if rank
        else fallback
    )


def normalize_status(
    value: Any,
    *,
    fallback: str = UNKNOWN_STATUS,
) -> str:
    """Normalize taxonomic status labels."""

    status = normalize_key(
        value
    )

    status = status.replace(
        "_",
        " ",
    ).replace(
        "-",
        " ",
    )

    status = MULTISPACE_PATTERN.sub(
        " ",
        status,
    ).strip()

    if status in STATUS_ALIASES:
        return STATUS_ALIASES[
            status
        ]

    for source, target in (
        STATUS_ALIASES.items()
    ):
        if (
            source
            and source in status
        ):
            return target

    return status or fallback


def normalize_taxon_name(
    value: Any,
) -> str:
    """Normalize a scientific or lineage name without changing case."""

    name = normalize_space(
        value
    )

    name = HYBRID_SPACE_PATTERN.sub(
        " × ",
        name,
    )

    return normalize_space(
        name
    )


def normalize_authorship(
    value: Any,
) -> str:
    """Normalize authorship punctuation and spacing."""

    authorship = normalize_space(
        value
    )

    authorship = re.sub(
        r"\s*,\s*",
        ", ",
        authorship,
    )

    authorship = re.sub(
        r"\s*&\s*",
        " & ",
        authorship,
    )

    return normalize_space(
        authorship
    )


def strip_name_qualifier(
    value: Any,
) -> str:
    """Remove trailing taxonomic qualifier phrases."""

    name = normalize_taxon_name(
        value
    )

    return normalize_space(
        QUALIFIER_PATTERN.sub(
            "",
            name,
        )
    )


def strip_authorship(
    scientific_name: Any,
    authorship: Any = "",
) -> str:
    """
    Remove a supplied or recognizable trailing authorship from a name.
    """

    name = strip_name_qualifier(
        scientific_name
    )

    normalized_authorship = (
        normalize_authorship(
            authorship
        )
    )

    if normalized_authorship:
        suffix = (
            " "
            + normalized_authorship
        )

        if name.endswith(
            suffix
        ):
            return normalize_space(
                name[
                    :-len(suffix)
                ]
            )

    match = AUTHORSHIP_PATTERN.search(
        name
    )

    if match is None:
        return name

    candidate = normalize_space(
        name[:match.start()]
    )

    if len(
        candidate.split()
    ) >= 2:
        return candidate

    return name


def canonical_name(
    scientific_name: Any,
    *,
    authorship: Any = "",
    rank: Any = "",
) -> str:
    """
    Produce a canonical scientific name suitable for reconciliation.
    """

    name = strip_authorship(
        scientific_name,
        authorship,
    )

    normalized_rank = normalize_rank(
        rank
    )

    tokens = name.split()

    if not tokens:
        return ""

    if normalized_rank == "genus":
        return tokens[0]

    if normalized_rank == "species":
        return " ".join(
            tokens[:2]
        )

    if normalized_rank in {
        "subspecies",
        "variety",
        "form",
        "cultivar",
        "pathovar",
        "serovar",
        "biovar",
    }:
        return _canonical_infraspecific_name(
            tokens,
            normalized_rank,
        )

    return normalize_taxon_name(
        name
    )


def infer_rank(
    scientific_name: Any,
    *,
    fallback: str = UNKNOWN_RANK,
) -> str:
    """Infer a probable rank from scientific-name structure."""

    name = normalize_taxon_name(
        scientific_name
    )

    tokens = name.split()

    if not tokens:
        return fallback

    lowered = [
        token.casefold()
        for token in tokens
    ]

    for marker, rank in (
        INFRASPECIFIC_MARKERS.items()
    ):
        if marker in lowered:
            return rank

    if len(tokens) == 1:
        return "genus"

    if len(tokens) == 2:
        return "species"

    if len(tokens) >= 3:
        return "subspecies"

    return fallback


def rank_index(
    rank: Any,
) -> int:
    """Return a deterministic hierarchy position."""

    normalized = normalize_rank(
        rank
    )

    return RANK_INDEX.get(
        normalized,
        len(RANK_ORDER),
    )


def rank_sort_key(
    rank: Any,
) -> tuple[int, str]:
    """Return a stable sorting key for ranks."""

    normalized = normalize_rank(
        rank
    )

    return (
        rank_index(
            normalized
        ),
        normalized,
    )


def compare_ranks(
    left: Any,
    right: Any,
) -> int:
    """
    Compare two ranks.

    Returns:
        -1 when left is higher,
         0 when equal,
         1 when left is lower.
    """

    left_index = rank_index(
        left
    )

    right_index = rank_index(
        right
    )

    if left_index < right_index:
        return -1

    if left_index > right_index:
        return 1

    return 0


def parent_rank(
    rank: Any,
) -> str | None:
    """Return the closest recognized parent rank."""

    normalized = normalize_rank(
        rank
    )

    index = rank_index(
        normalized
    )

    if (
        index <= 0
        or index >= len(
            RANK_ORDER
        )
    ):
        return None

    for candidate in reversed(
        RANK_ORDER[:index]
    ):
        if candidate != "unranked":
            return candidate

    return None


def child_rank(
    rank: Any,
) -> str | None:
    """Return the closest recognized child rank."""

    normalized = normalize_rank(
        rank
    )

    index = rank_index(
        normalized
    )

    if index >= (
        len(RANK_ORDER) - 1
    ):
        return None

    for candidate in RANK_ORDER[
        index + 1:
    ]:
        if candidate != "unranked":
            return candidate

    return None


def is_primary_rank(
    rank: Any,
) -> bool:
    """Return whether a rank belongs to the primary hierarchy."""

    return normalize_rank(
        rank
    ) in PRIMARY_RANKS


def is_terminal_rank(
    rank: Any,
) -> bool:
    """Return whether a rank is at or below species level."""

    return normalize_rank(
        rank
    ) in TERMINAL_RANKS


def is_accepted_status(
    status: Any,
) -> bool:
    """Return whether a status represents an accepted taxon."""

    return normalize_status(
        status
    ) in ACCEPTED_STATUSES


def is_synonym_status(
    status: Any,
) -> bool:
    """Return whether a status represents a synonym-like record."""

    return normalize_status(
        status
    ) in SYNONYM_STATUSES


def is_uncertain_status(
    status: Any,
) -> bool:
    """Return whether a status represents uncertain placement."""

    return normalize_status(
        status
    ) in UNCERTAIN_STATUSES


def normalize_lineage(
    value: Mapping[str, Any]
    | Iterable[Mapping[str, Any]]
    | None,
) -> Lineage:
    """
    Normalize lineage data from either a mapping or a list of rank/name rows.
    """

    lineage = Lineage()

    if value is None:
        return lineage

    if isinstance(
        value,
        Mapping,
    ):
        for rank, name in value.items():
            lineage.set(
                str(rank),
                name,
            )

        return lineage

    for item in value:
        if not isinstance(
            item,
            Mapping,
        ):
            continue

        rank = (
            item.get("rank")
            or item.get("rankName")
            or item.get("taxonRank")
        )

        name = (
            item.get("name")
            or item.get("scientificName")
            or item.get("taxonName")
            or item.get("canonicalName")
        )

        if rank and name:
            lineage.set(
                rank,
                name,
            )

    return lineage


def primary_lineage(
    value: Mapping[str, Any]
    | Iterable[Mapping[str, Any]]
    | None,
) -> dict[str, str]:
    """Return normalized primary lineage fields."""

    return normalize_lineage(
        value
    ).primary()


def lineage_from_taxon(
    record: Taxon,
) -> Lineage:
    """Build a Lineage from a Taxon object."""

    lineage = Lineage()

    for rank, name in (
        (
            "kingdom",
            record.kingdom,
        ),
        (
            "phylum",
            record.phylum,
        ),
        (
            "class",
            record.class_name,
        ),
        (
            "order",
            record.order,
        ),
        (
            "family",
            record.family,
        ),
        (
            "genus",
            record.genus,
        ),
    ):
        lineage.set(
            rank,
            name,
        )

    normalized_rank = normalize_rank(
        record.rank
    )

    if (
        normalized_rank
        and normalized_rank
        != UNKNOWN_RANK
    ):
        lineage.set(
            normalized_rank,
            record.canonical_name,
        )

    return lineage


def normalize_synonyms(
    values: Iterable[Any],
    *,
    scientific_name: Any = "",
    canonical: Any = "",
) -> list[str]:
    """Normalize and deduplicate synonym strings."""

    excluded = {
        normalize_key(
            scientific_name
        ),
        normalize_key(
            canonical
        ),
    }

    excluded.discard("")

    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        name = normalize_taxon_name(
            value
        )

        key = normalize_key(
            name
        )

        if (
            not key
            or key in excluded
            or key in seen
        ):
            continue

        seen.add(key)
        result.append(name)

    return result


def taxonomy_dict(
    record: Taxon,
) -> dict[str, str]:
    """Return a stable taxonomy dictionary for a Taxon."""

    return {
        "kingdom": normalize_taxon_name(
            record.kingdom
        ),
        "phylum": normalize_taxon_name(
            record.phylum
        ),
        "class": normalize_taxon_name(
            record.class_name
        ),
        "order": normalize_taxon_name(
            record.order
        ),
        "family": normalize_taxon_name(
            record.family
        ),
        "genus": normalize_taxon_name(
            record.genus
        ),
    }


def normalize_taxon(
    record: Taxon,
    *,
    infer_missing_rank: bool = True,
    canonicalize_name: bool = True,
) -> Taxon:
    """
    Return a normalized copy of a provider Taxon.

    The original object is not mutated.
    """

    provider = normalize_key(
        record.provider
    )

    provider_id = normalize_space(
        record.provider_id
    )

    scientific_name = (
        normalize_taxon_name(
            record.scientific_name
        )
    )

    authorship = normalize_authorship(
        record.authorship
    )

    rank = normalize_rank(
        record.rank
    )

    if (
        infer_missing_rank
        and rank == UNKNOWN_RANK
    ):
        rank = infer_rank(
            scientific_name
        )

    normalized_canonical = (
        canonical_name(
            record.canonical_name
            or scientific_name,
            authorship=authorship,
            rank=rank,
        )
        if canonicalize_name
        else normalize_taxon_name(
            record.canonical_name
            or scientific_name
        )
    )

    status = normalize_status(
        record.status
    )

    synonyms = normalize_synonyms(
        record.synonyms,
        scientific_name=scientific_name,
        canonical=normalized_canonical,
    )

    extra = (
        dict(record.extra)
        if isinstance(
            record.extra,
            Mapping,
        )
        else {}
    )

    extra.setdefault(
        "taxonomy_schema_version",
        TAXONOMY_SCHEMA_VERSION,
    )

    return replace(
        record,
        provider=provider,
        provider_id=provider_id,
        scientific_name=scientific_name,
        canonical_name=normalized_canonical,
        rank=rank,
        status=status,
        authorship=authorship,
        kingdom=normalize_taxon_name(
            record.kingdom
        ),
        phylum=normalize_taxon_name(
            record.phylum
        ),
        class_name=normalize_taxon_name(
            record.class_name
        ),
        order=normalize_taxon_name(
            record.order
        ),
        family=normalize_taxon_name(
            record.family
        ),
        genus=normalize_taxon_name(
            record.genus
        ),
        accepted_provider_id=normalize_space(
            record.accepted_provider_id
        ),
        source_url=normalize_space(
            record.source_url
        ),
        source_modified=normalize_space(
            record.source_modified
        ),
        retrieved_at=normalize_space(
            record.retrieved_at
        ),
        synonyms=synonyms,
        extra=extra,
    )


def validate_taxon(
    record: Taxon,
    *,
    require_status: bool = True,
    require_rank: bool = True,
    require_lineage_consistency: bool = True,
) -> TaxonomyValidation:
    """Validate a normalized or raw Taxon object."""

    errors: list[str] = []
    warnings: list[str] = []

    if not normalize_key(
        record.provider
    ):
        errors.append(
            "Taxon provider is required."
        )

    if not normalize_space(
        record.provider_id
    ):
        errors.append(
            "Taxon provider_id is required."
        )

    scientific_name = (
        normalize_taxon_name(
            record.scientific_name
        )
    )

    canonical = normalize_taxon_name(
        record.canonical_name
    )

    if not scientific_name:
        errors.append(
            "Taxon scientific_name is required."
        )

    if not canonical:
        errors.append(
            "Taxon canonical_name is required."
        )

    rank = normalize_rank(
        record.rank
    )

    if (
        require_rank
        and rank == UNKNOWN_RANK
    ):
        warnings.append(
            "Taxon rank is unranked or unknown."
        )

    status = normalize_status(
        record.status
    )

    if (
        require_status
        and status == UNKNOWN_STATUS
    ):
        warnings.append(
            "Taxon status is unknown."
        )

    if (
        record.accepted_provider_id
        and normalize_space(
            record.accepted_provider_id
        )
        == normalize_space(
            record.provider_id
        )
    ):
        warnings.append(
            "accepted_provider_id equals provider_id."
        )

    if is_synonym_status(status) and not (
        normalize_space(
            record.accepted_provider_id
        )
        or record.synonyms
    ):
        warnings.append(
            "Synonym-like taxon has no accepted "
            "provider identifier or synonym metadata."
        )

    if not isinstance(
        record.synonyms,
        list,
    ):
        errors.append(
            "Taxon synonyms must be a list."
        )

    if not isinstance(
        record.extra,
        dict,
    ):
        errors.append(
            "Taxon extra must be a dictionary."
        )

    if (
        require_lineage_consistency
        and scientific_name
    ):
        errors.extend(
            _lineage_consistency_errors(
                record
            )
        )

    if rank == "species":
        words = canonical.split()

        if len(words) < 2:
            warnings.append(
                "Species-level canonical name has "
                "fewer than two components."
            )

    if rank == "genus":
        words = canonical.split()

        if len(words) > 1:
            warnings.append(
                "Genus-level canonical name has "
                "multiple components."
            )

    return TaxonomyValidation(
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


def normalize_and_validate_taxon(
    record: Taxon,
    *,
    infer_missing_rank: bool = True,
    canonicalize_name: bool = True,
) -> tuple[
    Taxon,
    TaxonomyValidation,
]:
    """Normalize a Taxon and return its validation result."""

    normalized = normalize_taxon(
        record,
        infer_missing_rank=(
            infer_missing_rank
        ),
        canonicalize_name=(
            canonicalize_name
        ),
    )

    return (
        normalized,
        validate_taxon(
            normalized
        ),
    )


def _canonical_infraspecific_name(
    tokens: Sequence[str],
    rank: str,
) -> str:
    """Build a canonical infraspecific name."""

    if len(tokens) <= 2:
        return " ".join(tokens)

    marker_positions = [
        index
        for index, token in enumerate(
            tokens
        )
        if token.casefold()
        in INFRASPECIFIC_MARKERS
    ]

    if marker_positions:
        marker_index = (
            marker_positions[0]
        )

        if marker_index + 1 < len(
            tokens
        ):
            return " ".join(
                tokens[
                    : marker_index + 2
                ]
            )

    if rank == "cultivar":
        return " ".join(
            tokens[:3]
        )

    return " ".join(
        tokens[:3]
    )


def _lineage_consistency_errors(
    record: Taxon,
) -> list[str]:
    """Return structural lineage errors."""

    errors: list[str] = []

    rank = normalize_rank(
        record.rank
    )

    canonical = normalize_key(
        record.canonical_name
    )

    lineage = taxonomy_dict(
        record
    )

    if (
        rank == "genus"
        and lineage["genus"]
        and canonical
        != normalize_key(
            lineage["genus"]
        )
    ):
        errors.append(
            "Genus-level canonical name does not "
            "match lineage genus."
        )

    if rank in TERMINAL_RANKS:
        genus = normalize_key(
            lineage["genus"]
        )

        if genus and canonical:
            first_word = normalize_key(
                record.canonical_name.split()[0]
                if record.canonical_name.split()
                else ""
            )

            if (
                first_word
                and first_word != genus
            ):
                errors.append(
                    "Canonical name genus does not "
                    "match lineage genus."
                )

    return errors
