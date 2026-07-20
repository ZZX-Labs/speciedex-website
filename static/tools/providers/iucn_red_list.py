#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/iucn_red_list.py

IUCN Red List provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented or
unlicensed public API. Every source object is preserved under
``Taxon.extra["raw"]`` while taxonomic and conservation fields are normalized
for Speciedex.

Required provider configuration:

    {
        "name": "iucn_red_list",
        "path": "static/data/providers/iucn/red-list.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "IUCN Red List",
        "source_url": "https://www.iucnredlist.org/"
    }

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .common import (
    BaseProvider,
    Batch,
    ProviderError,
    Taxon,
    normalize_space,
    now,
    safe_int,
)


class Provider(BaseProvider):
    """File-backed IUCN Red List provider."""

    PROVIDER_NAME = "iucn_red_list"

    DEFAULT_SOURCE_NAME = "IUCN Red List"
    DEFAULT_SOURCE_URL = "https://www.iucnredlist.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"IUCN Red List export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"IUCN Red List path is not a file: {source_path}"
            )

        offset = self._decode_cursor(
            self.cursor
        )

        configured_page_size = safe_int(
            self.definition.get(
                "page_size",
                self.batch_size,
            ),
            self.batch_size,
        )

        page_size = max(
            1,
            min(
                configured_page_size,
                self.batch_size,
            ),
        )

        records: list[Taxon] = []
        raw_count = 0
        next_offset = offset
        exhausted = True
        retrieved_at = now()

        with source_path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for line_number, line in enumerate(
                handle
            ):
                if line_number < offset:
                    continue

                if raw_count >= page_size:
                    exhausted = False
                    break

                next_offset = line_number + 1
                raw_count += 1

                stripped = line.strip()

                if not stripped:
                    continue

                try:
                    value = json.loads(
                        stripped
                    )
                except json.JSONDecodeError:
                    continue

                if not isinstance(
                    value,
                    Mapping,
                ):
                    continue

                taxon = self._normalize_record(
                    dict(value),
                    source_path=source_path,
                    retrieved_at=retrieved_at,
                )

                if taxon is not None:
                    records.append(
                        taxon
                    )

        next_cursor = (
            None
            if exhausted
            else str(
                next_offset
            )
        )

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=0,
            raw=raw_count,
        )

    def _source_path(
        self,
    ) -> Path:
        """Resolve the configured JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "IUCN Red List provider requires a path."
            )

        path = Path(
            configured
        )

        if not path.is_absolute():
            path = (
                self.repo_root
                / path
            )

        return path

    def _normalize_record(
        self,
        raw: dict[str, Any],
        *,
        source_path: Path,
        retrieved_at: str,
    ) -> Taxon | None:
        """Normalize one Red List export record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "assessment_id",
                "assessmentId",
                "assessmentID",
                "sis_taxon_id",
                "sisTaxonId",
                "taxon_id",
                "taxonId",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "taxon_name",
                "taxonName",
                "binomial",
                "name",
            )
        )

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "taxon_name",
                "taxonName",
                "binomial",
            )
        ) or scientific_name

        rank = normalize_space(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
            )
        ).casefold() or self._infer_rank(
            canonical_name
        )

        status = self._normalize_taxonomic_status(
            self._first_value(
                raw,
                "taxonomic_status",
                "taxonomicStatus",
                "status",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_id",
                "acceptedId",
            )
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        assessment_url = normalize_space(
            self._first_value(
                raw,
                "url",
                "assessment_url",
                "assessmentUrl",
                "source_url",
                "sourceUrl",
            )
        )

        if not assessment_url:
            assessment_url = normalize_space(
                self.definition.get(
                    "source_url",
                    self.DEFAULT_SOURCE_URL,
                )
            )

        category = self._normalize_red_list_category(
            self._first_value(
                raw,
                "red_list_category",
                "redListCategory",
                "category",
                "code",
            )
        )

        population_trend = normalize_space(
            self._first_value(
                raw,
                "population_trend",
                "populationTrend",
                "trend",
            )
        ).casefold()

        synonyms = self._extract_synonyms(
            raw,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
        )

        return Taxon(
            provider=self.name,
            provider_id=provider_id,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank,
            status=status,
            authorship=normalize_space(
                self._first_value(
                    raw,
                    "authority",
                    "authorship",
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                )
            ),
            kingdom=normalize_space(
                self._first_value(
                    raw,
                    "kingdom",
                )
            ),
            phylum=normalize_space(
                self._first_value(
                    raw,
                    "phylum",
                )
            ),
            class_name=normalize_space(
                self._first_value(
                    raw,
                    "class",
                    "class_name",
                    "className",
                )
            ),
            order=normalize_space(
                self._first_value(
                    raw,
                    "order",
                )
            ),
            family=normalize_space(
                self._first_value(
                    raw,
                    "family",
                )
            ),
            genus=normalize_space(
                self._first_value(
                    raw,
                    "genus",
                )
            ),
            accepted_provider_id=accepted_provider_id,
            source_url=assessment_url,
            source_modified=normalize_space(
                self._first_value(
                    raw,
                    "modified",
                    "last_modified",
                    "lastModified",
                    "assessment_date",
                    "assessmentDate",
                    "year_published",
                    "yearPublished",
                )
            ),
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": normalize_space(
                    self.definition.get(
                        "source_name",
                        self.DEFAULT_SOURCE_NAME,
                    )
                ) or self.DEFAULT_SOURCE_NAME,
                "iucn_program": "red_list",
                "reference_only": True,
                "assessment_id": provider_id,
                "taxon_id": normalize_space(
                    self._first_value(
                        raw,
                        "taxon_id",
                        "taxonId",
                        "sis_taxon_id",
                        "sisTaxonId",
                    )
                ),
                "red_list_category": category,
                "population_trend": population_trend,
                "assessment": {
                    "year": normalize_space(
                        self._first_value(
                            raw,
                            "assessment_year",
                            "assessmentYear",
                            "year_published",
                            "yearPublished",
                        )
                    ),
                    "criteria": normalize_space(
                        self._first_value(
                            raw,
                            "criteria",
                            "criteria_version",
                            "criteriaVersion",
                        )
                    ),
                    "version": normalize_space(
                        self._first_value(
                            raw,
                            "assessment_version",
                            "assessmentVersion",
                        )
                    ),
                    "scope": normalize_space(
                        self._first_value(
                            raw,
                            "scope",
                            "assessment_scope",
                            "assessmentScope",
                        )
                    ),
                    "possibly_extinct": self._optional_bool(
                        self._first_value(
                            raw,
                            "possibly_extinct",
                            "possiblyExtinct",
                        )
                    ),
                    "possibly_extinct_in_the_wild": self._optional_bool(
                        self._first_value(
                            raw,
                            "possibly_extinct_in_the_wild",
                            "possiblyExtinctInTheWild",
                        )
                    ),
                },
                "habitats": self._list_value(
                    self._first_value(
                        raw,
                        "habitats",
                        "habitat",
                    )
                ),
                "threats": self._list_value(
                    self._first_value(
                        raw,
                        "threats",
                        "threat",
                    )
                ),
                "conservation_actions": self._list_value(
                    self._first_value(
                        raw,
                        "conservation_actions",
                        "conservationActions",
                        "actions",
                    )
                ),
                "countries": self._list_value(
                    self._first_value(
                        raw,
                        "countries",
                        "country_occurrence",
                        "countryOccurrence",
                    )
                ),
                "biogeographic_realms": self._list_value(
                    self._first_value(
                        raw,
                        "biogeographic_realms",
                        "biogeographicRealms",
                    )
                ),
                "systems": self._list_value(
                    self._first_value(
                        raw,
                        "systems",
                        "system",
                    )
                ),
                "common_names": self._list_value(
                    self._first_value(
                        raw,
                        "common_names",
                        "commonNames",
                        "vernacular_names",
                        "vernacularNames",
                    )
                ),
                "references": self._list_value(
                    self._first_value(
                        raw,
                        "references",
                        "bibliography",
                    )
                ),
                "bulk_source": source_path.as_posix(),
                "raw": raw,
            },
        )

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> int:
        """Decode a non-negative JSONL line offset."""

        if not cursor:
            return 0

        try:
            offset = int(
                cursor
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ProviderError(
                f"Invalid IUCN Red List cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "IUCN Red List cursor must be non-negative."
            )

        return offset

    @staticmethod
    def _normalize_taxonomic_status(
        value: Any,
    ) -> str:
        """Normalize the taxonomic status independently of threat category."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _normalize_red_list_category(
        value: Any,
    ) -> str:
        """Normalize common Red List category labels and codes."""

        category = normalize_space(
            value
        ).casefold()

        aliases = {
            "ex": "extinct",
            "ew": "extinct in the wild",
            "cr": "critically endangered",
            "en": "endangered",
            "vu": "vulnerable",
            "nt": "near threatened",
            "lc": "least concern",
            "dd": "data deficient",
            "ne": "not evaluated",
            "re": "regionally extinct",
            "critically endangered": "critically endangered",
            "endangered": "endangered",
            "vulnerable": "vulnerable",
            "near threatened": "near threatened",
            "least concern": "least concern",
            "data deficient": "data deficient",
            "not evaluated": "not evaluated",
            "extinct": "extinct",
            "extinct in the wild": "extinct in the wild",
            "regionally extinct": "regionally extinct",
        }

        return aliases.get(
            category,
            category,
        )

    @classmethod
    def _extract_synonyms(
        cls,
        raw: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """Extract and deduplicate synonym-like names."""

        value = cls._first_value(
            raw,
            "synonyms",
            "synonym",
            "taxonomic_synonyms",
            "taxonomicSynonyms",
        )

        candidates = cls._list_value(
            value
        )

        excluded = {
            scientific_name.casefold(),
            canonical_name.casefold(),
        }

        result: list[str] = []
        seen: set[str] = set(
            excluded
        )

        for item in candidates:
            if isinstance(
                item,
                Mapping,
            ):
                normalized = normalize_space(
                    cls._first_value(
                        item,
                        "scientific_name",
                        "scientificName",
                        "name",
                    )
                )
            else:
                normalized = normalize_space(
                    item
                )

            key = normalized.casefold()

            if (
                not normalized
                or key in seen
            ):
                continue

            seen.add(
                key
            )
            result.append(
                normalized
            )

        return result

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        words = normalize_space(
            scientific_name
        ).split()

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _first_value(
        record: Mapping[str, Any],
        *keys: str,
    ) -> Any:
        for key in keys:
            value = record.get(
                key
            )

            if value not in (
                None,
                "",
                [],
                {},
            ):
                return value

        return None

    @staticmethod
    def _list_value(
        value: Any,
    ) -> list[Any]:
        if value is None:
            return []

        if isinstance(
            value,
            list,
        ):
            return value

        return [
            value
        ]

    @staticmethod
    def _optional_bool(
        value: Any,
    ) -> bool | None:
        if isinstance(
            value,
            bool,
        ):
            return value

        if isinstance(
            value,
            int,
        ):
            return bool(
                value
            )

        normalized = normalize_space(
            value
        ).casefold()

        if normalized in {
            "1",
            "true",
            "yes",
            "y",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
        }:
            return False

        return None
