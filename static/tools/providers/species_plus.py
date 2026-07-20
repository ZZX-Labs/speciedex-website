#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/species_plus.py

Species+ / CITES provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete Species+ / CITES object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "species_plus",
        "path": "static/data/providers/species-plus/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Species+ / CITES",
        "source_url": "https://speciesplus.net/"
    }

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    """File-backed Species+ / CITES provider."""

    PROVIDER_NAME = "species_plus"

    DEFAULT_SOURCE_NAME = "Species+ / CITES"
    DEFAULT_SOURCE_URL = "https://speciesplus.net/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable Species+ JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Species+ export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Species+ path is not a file: {source_path}"
            )

        offset = self._decode_cursor(self.cursor)

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
            for line_number, line in enumerate(handle):
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
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                if not isinstance(value, Mapping):
                    continue

                record = self._normalize_record(
                    dict(value),
                    source_path=source_path,
                    retrieved_at=retrieved_at,
                )

                if record is not None:
                    records.append(record)

        return Batch(
            records=records,
            next_cursor=(
                None
                if exhausted
                else str(next_offset)
            ),
            exhausted=exhausted,
            requests=0,
            raw=raw_count,
        )

    def _source_path(self) -> Path:
        """Resolve the configured Species+ JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
        )

        if not configured:
            raise ProviderError(
                "Species+ provider requires a path."
            )

        path = Path(configured)

        if not path.is_absolute():
            path = self.repo_root / path

        return path

    def _normalize_record(
        self,
        raw: dict[str, Any],
        *,
        source_path: Path,
        retrieved_at: str,
    ) -> Taxon | None:
        """Normalize one Species+ or CITES taxon record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "taxon_concept_id",
                "taxonConceptId",
                "taxon_id",
                "taxonId",
                "taxonID",
                "species_plus_id",
                "speciesPlusId",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "full_name",
                "fullName",
                "taxon_name",
                "taxonName",
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
                "name_without_authorship",
                "nameWithoutAuthorship",
                "full_name",
                "fullName",
            )
        ) or scientific_name

        rank = self._normalize_rank(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
            )
        )

        if rank == "unknown":
            rank = self._infer_rank(canonical_name)

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "name_status",
                "nameStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_taxon_concept_id",
                "acceptedTaxonConceptId",
                "accepted_name_id",
                "acceptedNameId",
                "accepted_id",
                "acceptedId",
            )
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        source_url = normalize_space(
            self._first_value(
                raw,
                "url",
                "source_url",
                "sourceUrl",
                "record_url",
                "recordUrl",
            )
        )

        if not source_url:
            source_url = (
                normalize_space(
                    self.definition.get(
                        "source_url",
                        self.DEFAULT_SOURCE_URL,
                    )
                ).rstrip("/")
                + "/taxon-concepts/"
                + provider_id
            )

        lineage = self._extract_lineage(raw)

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
                    "authorship",
                    "author",
                    "authority",
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                )
            ),
            kingdom=lineage.get("kingdom", ""),
            phylum=lineage.get(
                "phylum",
                lineage.get("division", ""),
            ),
            class_name=lineage.get("class", ""),
            order=lineage.get("order", ""),
            family=lineage.get("family", ""),
            genus=lineage.get("genus", ""),
            accepted_provider_id=accepted_provider_id,
            source_url=source_url,
            source_modified=normalize_space(
                self._first_value(
                    raw,
                    "modified",
                    "last_modified",
                    "lastModified",
                    "updated",
                    "updated_at",
                    "updatedAt",
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
                "programme": "species_plus_cites",
                "reference_only": True,
                "taxon_concept_id": provider_id,
                "accepted_taxon_concept_id": accepted_provider_id,
                "lineage": lineage,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_taxon_id",
                            "parentTaxonId",
                            "parent_taxon_concept_id",
                            "parentTaxonConceptId",
                            "parent_id",
                            "parentId",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            raw,
                            "parent_name",
                            "parentName",
                        )
                    ),
                    "rank": self._normalize_rank(
                        self._first_value(
                            raw,
                            "parent_rank",
                            "parentRank",
                        )
                    ),
                },
                "cites": {
                    "current_listing": self._normalize_current_listing(
                        raw
                    ),
                    "listings": self._normalize_listings(
                        self._first_value(
                            raw,
                            "cites_listings",
                            "citesListings",
                            "listings",
                            "appendix_listings",
                            "appendixListings",
                        )
                    ),
                    "reservations": self._normalize_reservations(
                        self._first_value(
                            raw,
                            "reservations",
                            "cites_reservations",
                            "citesReservations",
                        )
                    ),
                    "annotations": self._normalize_annotations(
                        self._first_value(
                            raw,
                            "annotations",
                            "cites_annotations",
                            "citesAnnotations",
                        )
                    ),
                    "party_statuses": self._normalize_party_statuses(
                        self._first_value(
                            raw,
                            "party_statuses",
                            "partyStatuses",
                            "country_statuses",
                            "countryStatuses",
                        )
                    ),
                },
                "trade_controls": {
                    "quotas": self._normalize_quotas(
                        self._first_value(
                            raw,
                            "quotas",
                            "cites_quotas",
                            "citesQuotas",
                        )
                    ),
                    "suspensions": self._normalize_suspensions(
                        self._first_value(
                            raw,
                            "suspensions",
                            "trade_suspensions",
                            "tradeSuspensions",
                        )
                    ),
                    "restrictions": self._normalize_restrictions(
                        self._first_value(
                            raw,
                            "trade_restrictions",
                            "tradeRestrictions",
                            "restrictions",
                        )
                    ),
                    "eu_decisions": self._normalize_decisions(
                        self._first_value(
                            raw,
                            "eu_decisions",
                            "euDecisions",
                            "decisions",
                        )
                    ),
                },
                "distribution": {
                    "countries": self._normalize_distribution(
                        self._first_value(
                            raw,
                            "distributions",
                            "distribution",
                            "countries",
                            "country",
                        )
                    ),
                    "introduced": self._normalize_distribution(
                        self._first_value(
                            raw,
                            "introduced_distributions",
                            "introducedDistributions",
                            "introduced",
                        )
                    ),
                    "possibly_extinct": self._normalize_distribution(
                        self._first_value(
                            raw,
                            "possibly_extinct_distributions",
                            "possiblyExtinctDistributions",
                            "possibly_extinct",
                            "possiblyExtinct",
                        )
                    ),
                    "extinct": self._normalize_distribution(
                        self._first_value(
                            raw,
                            "extinct_distributions",
                            "extinctDistributions",
                            "extinct",
                        )
                    ),
                },
                "conservation": {
                    "iucn_status": normalize_space(
                        self._first_value(
                            raw,
                            "iucn_status",
                            "iucnStatus",
                            "red_list_status",
                            "redListStatus",
                        )
                    ),
                    "eu_annex": self._list_value(
                        self._first_value(
                            raw,
                            "eu_annex",
                            "euAnnex",
                            "eu_annexes",
                            "euAnnexes",
                        )
                    ),
                    "cms_listing": self._list_value(
                        self._first_value(
                            raw,
                            "cms_listing",
                            "cmsListing",
                            "cms_appendix",
                            "cmsAppendix",
                        )
                    ),
                    "threats": self._list_value(
                        self._first_value(
                            raw,
                            "threats",
                            "threat",
                        )
                    ),
                },
                "nomenclature": {
                    "basionym": normalize_space(
                        self._first_value(
                            raw,
                            "basionym",
                            "basionym_name",
                            "basionymName",
                        )
                    ),
                    "basionym_id": normalize_space(
                        self._first_value(
                            raw,
                            "basionym_id",
                            "basionymId",
                        )
                    ),
                    "nomenclatural_status": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_status",
                            "nomenclaturalStatus",
                            "name_status",
                            "nameStatus",
                        )
                    ),
                    "nomenclatural_code": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_code",
                            "nomenclaturalCode",
                            "code",
                        )
                    ),
                    "published_in": normalize_space(
                        self._first_value(
                            raw,
                            "published_in",
                            "publishedIn",
                            "publication",
                        )
                    ),
                },
                "common_names": self._normalize_common_names(
                    self._first_value(
                        raw,
                        "common_names",
                        "commonNames",
                        "vernacular_names",
                        "vernacularNames",
                    )
                ),
                "identifiers": self._normalize_identifiers(
                    self._first_value(
                        raw,
                        "identifiers",
                        "external_identifiers",
                        "externalIdentifiers",
                    )
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
                        "bibliography",
                    )
                ),
                "notes": self._list_value(
                    self._first_value(
                        raw,
                        "notes",
                        "remarks",
                        "comments",
                    )
                ),
                "bulk_source": source_path.as_posix(),
                "raw": raw,
            },
        )

    @classmethod
    def _extract_lineage(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, str]:
        """Extract major taxonomic lineage values."""

        lineage = {
            "kingdom": normalize_space(raw.get("kingdom")),
            "phylum": normalize_space(raw.get("phylum")),
            "division": normalize_space(raw.get("division")),
            "class": normalize_space(raw.get("class")),
            "order": normalize_space(raw.get("order")),
            "family": normalize_space(raw.get("family")),
            "genus": normalize_space(raw.get("genus")),
            "species": normalize_space(raw.get("species")),
        }

        lineage_value = cls._first_value(
            raw,
            "lineage",
            "classification",
            "higher_taxa",
            "higherTaxa",
        )

        for item in cls._list_value(lineage_value):
            if not isinstance(item, Mapping):
                continue

            rank = cls._normalize_rank(
                cls._first_value(
                    item,
                    "rank",
                    "taxon_rank",
                    "taxonRank",
                )
            )

            name = normalize_space(
                cls._first_value(
                    item,
                    "name",
                    "scientific_name",
                    "scientificName",
                )
            )

            if rank and name and not lineage.get(rank):
                lineage[rank] = name

        return lineage

    @classmethod
    def _extract_synonyms(
        cls,
        raw: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """Extract and deduplicate scientific-name synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "taxonomic_synonyms",
                "taxonomicSynonyms",
                "alternative_names",
                "alternativeNames",
            )
        )

        excluded = {
            scientific_name.casefold(),
            canonical_name.casefold(),
        }

        result: list[str] = []
        seen: set[str] = set(excluded)

        for item in values:
            if isinstance(item, Mapping):
                normalized = normalize_space(
                    cls._first_value(
                        item,
                        "scientific_name",
                        "scientificName",
                        "name",
                    )
                )
            else:
                normalized = normalize_space(item)

            key = normalized.casefold()

            if not normalized or key in seen:
                continue

            seen.add(key)
            result.append(normalized)

        return result

    @classmethod
    def _normalize_current_listing(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize the currently effective CITES listing."""

        return {
            "appendix": normalize_space(
                cls._first_value(
                    raw,
                    "cites_appendix",
                    "citesAppendix",
                    "appendix",
                    "current_appendix",
                    "currentAppendix",
                )
            ).upper(),
            "effective_at": normalize_space(
                cls._first_value(
                    raw,
                    "cites_effective_at",
                    "citesEffectiveAt",
                    "effective_at",
                    "effectiveAt",
                )
            ),
            "annotation": normalize_space(
                cls._first_value(
                    raw,
                    "cites_annotation",
                    "citesAnnotation",
                    "annotation",
                )
            ),
            "change_type": normalize_space(
                cls._first_value(
                    raw,
                    "cites_change_type",
                    "citesChangeType",
                    "change_type",
                    "changeType",
                )
            ),
        }

    @classmethod
    def _normalize_listings(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize historic and current CITES appendix listings."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "appendix": normalize_space(
                            cls._first_value(
                                item,
                                "appendix",
                                "cites_appendix",
                                "citesAppendix",
                            )
                        ).upper(),
                        "effective_at": normalize_space(
                            cls._first_value(
                                item,
                                "effective_at",
                                "effectiveAt",
                                "effective_date",
                                "effectiveDate",
                            )
                        ),
                        "end_date": normalize_space(
                            cls._first_value(
                                item,
                                "end_date",
                                "endDate",
                                "expiry_date",
                                "expiryDate",
                            )
                        ),
                        "change_type": normalize_space(
                            cls._first_value(
                                item,
                                "change_type",
                                "changeType",
                                "type",
                            )
                        ),
                        "annotation": normalize_space(
                            cls._first_value(
                                item,
                                "annotation",
                                "annotation_text",
                                "annotationText",
                            )
                        ),
                        "party": normalize_space(
                            cls._first_value(
                                item,
                                "party",
                                "country",
                                "iso_code2",
                                "isoCode2",
                            )
                        ),
                    }
                )

                result.append(entry)
            else:
                appendix = normalize_space(item).upper()

                if appendix:
                    result.append(
                        {
                            "appendix": appendix,
                            "effective_at": "",
                            "end_date": "",
                            "change_type": "",
                            "annotation": "",
                            "party": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_reservations(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize party reservations against CITES listings."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.update(
                {
                    "party": normalize_space(
                        cls._first_value(
                            item,
                            "party",
                            "country",
                            "country_name",
                            "countryName",
                        )
                    ),
                    "iso_code2": normalize_space(
                        cls._first_value(
                            item,
                            "iso_code2",
                            "isoCode2",
                            "country_code",
                            "countryCode",
                        )
                    ),
                    "appendix": normalize_space(
                        cls._first_value(
                            item,
                            "appendix",
                        )
                    ).upper(),
                    "start_date": normalize_space(
                        cls._first_value(
                            item,
                            "start_date",
                            "startDate",
                            "effective_at",
                            "effectiveAt",
                        )
                    ),
                    "end_date": normalize_space(
                        cls._first_value(
                            item,
                            "end_date",
                            "endDate",
                        )
                    ),
                    "notes": normalize_space(
                        cls._first_value(
                            item,
                            "notes",
                            "remarks",
                            "annotation",
                        )
                    ),
                }
            )

            result.append(entry)

        return result

    @classmethod
    def _normalize_annotations(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize CITES listing annotations."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "symbol": normalize_space(
                            cls._first_value(
                                item,
                                "symbol",
                                "code",
                                "annotation_symbol",
                                "annotationSymbol",
                            )
                        ),
                        "text": normalize_space(
                            cls._first_value(
                                item,
                                "text",
                                "annotation",
                                "description",
                            )
                        ),
                        "effective_at": normalize_space(
                            cls._first_value(
                                item,
                                "effective_at",
                                "effectiveAt",
                            )
                        ),
                    }
                )
                result.append(entry)
            else:
                text = normalize_space(item)

                if text:
                    result.append(
                        {
                            "symbol": "",
                            "text": text,
                            "effective_at": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_party_statuses(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize party-specific listing and treaty status."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.update(
                {
                    "party": normalize_space(
                        cls._first_value(
                            item,
                            "party",
                            "country",
                            "name",
                        )
                    ),
                    "iso_code2": normalize_space(
                        cls._first_value(
                            item,
                            "iso_code2",
                            "isoCode2",
                            "country_code",
                            "countryCode",
                        )
                    ),
                    "appendix": normalize_space(
                        cls._first_value(
                            item,
                            "appendix",
                        )
                    ).upper(),
                    "status": normalize_space(
                        cls._first_value(
                            item,
                            "status",
                        )
                    ),
                }
            )

            result.append(entry)

        return result

    @classmethod
    def _normalize_quotas(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize annual CITES export quota records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.update(
                {
                    "year": cls._optional_int(
                        cls._first_value(
                            item,
                            "year",
                        )
                    ),
                    "party": normalize_space(
                        cls._first_value(
                            item,
                            "party",
                            "country",
                            "country_name",
                            "countryName",
                        )
                    ),
                    "iso_code2": normalize_space(
                        cls._first_value(
                            item,
                            "iso_code2",
                            "isoCode2",
                            "country_code",
                            "countryCode",
                        )
                    ),
                    "quota": cls._optional_float(
                        cls._first_value(
                            item,
                            "quota",
                            "quantity",
                            "amount",
                        )
                    ),
                    "unit": normalize_space(
                        cls._first_value(
                            item,
                            "unit",
                            "quota_unit",
                            "quotaUnit",
                        )
                    ),
                    "term": normalize_space(
                        cls._first_value(
                            item,
                            "term",
                            "specimen",
                            "trade_term",
                            "tradeTerm",
                        )
                    ),
                    "source": normalize_space(
                        cls._first_value(
                            item,
                            "source",
                            "source_code",
                            "sourceCode",
                        )
                    ),
                    "notes": normalize_space(
                        cls._first_value(
                            item,
                            "notes",
                            "remarks",
                            "publication",
                        )
                    ),
                }
            )

            result.append(entry)

        return result

    @classmethod
    def _normalize_suspensions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize trade suspension records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.update(
                {
                    "party": normalize_space(
                        cls._first_value(
                            item,
                            "party",
                            "country",
                            "country_name",
                            "countryName",
                        )
                    ),
                    "iso_code2": normalize_space(
                        cls._first_value(
                            item,
                            "iso_code2",
                            "isoCode2",
                            "country_code",
                            "countryCode",
                        )
                    ),
                    "start_date": normalize_space(
                        cls._first_value(
                            item,
                            "start_date",
                            "startDate",
                            "effective_at",
                            "effectiveAt",
                        )
                    ),
                    "end_date": normalize_space(
                        cls._first_value(
                            item,
                            "end_date",
                            "endDate",
                            "withdrawn_at",
                            "withdrawnAt",
                        )
                    ),
                    "scope": normalize_space(
                        cls._first_value(
                            item,
                            "scope",
                            "trade_scope",
                            "tradeScope",
                        )
                    ),
                    "notes": normalize_space(
                        cls._first_value(
                            item,
                            "notes",
                            "remarks",
                            "reason",
                        )
                    ),
                }
            )

            result.append(entry)

        return result

    @classmethod
    def _normalize_restrictions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize additional trade restrictions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "type": normalize_space(
                            cls._first_value(
                                item,
                                "type",
                                "restriction_type",
                                "restrictionType",
                            )
                        ),
                        "party": normalize_space(
                            cls._first_value(
                                item,
                                "party",
                                "country",
                            )
                        ),
                        "start_date": normalize_space(
                            cls._first_value(
                                item,
                                "start_date",
                                "startDate",
                            )
                        ),
                        "end_date": normalize_space(
                            cls._first_value(
                                item,
                                "end_date",
                                "endDate",
                            )
                        ),
                        "description": normalize_space(
                            cls._first_value(
                                item,
                                "description",
                                "notes",
                                "remarks",
                            )
                        ),
                    }
                )
                result.append(entry)
            else:
                description = normalize_space(item)

                if description:
                    result.append(
                        {
                            "type": "",
                            "party": "",
                            "start_date": "",
                            "end_date": "",
                            "description": description,
                        }
                    )

        return result

    @classmethod
    def _normalize_decisions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize EU wildlife-trade decisions and opinions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.update(
                {
                    "decision_type": normalize_space(
                        cls._first_value(
                            item,
                            "decision_type",
                            "decisionType",
                            "type",
                        )
                    ),
                    "country": normalize_space(
                        cls._first_value(
                            item,
                            "country",
                            "party",
                        )
                    ),
                    "start_date": normalize_space(
                        cls._first_value(
                            item,
                            "start_date",
                            "startDate",
                            "date",
                        )
                    ),
                    "end_date": normalize_space(
                        cls._first_value(
                            item,
                            "end_date",
                            "endDate",
                        )
                    ),
                    "term": normalize_space(
                        cls._first_value(
                            item,
                            "term",
                            "specimen",
                        )
                    ),
                    "source": normalize_space(
                        cls._first_value(
                            item,
                            "source",
                            "source_code",
                            "sourceCode",
                        )
                    ),
                    "notes": normalize_space(
                        cls._first_value(
                            item,
                            "notes",
                            "remarks",
                            "reason",
                        )
                    ),
                }
            )

            result.append(entry)

        return result

    @classmethod
    def _normalize_distribution(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize range-state and distribution records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "country",
                                "territory",
                                "region",
                            )
                        ),
                        "iso_code2": normalize_space(
                            cls._first_value(
                                item,
                                "iso_code2",
                                "isoCode2",
                                "country_code",
                                "countryCode",
                            )
                        ),
                        "status": normalize_space(
                            cls._first_value(
                                item,
                                "status",
                                "presence",
                                "occurrence_status",
                                "occurrenceStatus",
                            )
                        ),
                        "introduced": cls._optional_bool(
                            cls._first_value(
                                item,
                                "introduced",
                                "is_introduced",
                                "isIntroduced",
                            )
                        ),
                        "extinct": cls._optional_bool(
                            cls._first_value(
                                item,
                                "extinct",
                                "is_extinct",
                                "isExtinct",
                            )
                        ),
                        "possibly_extinct": cls._optional_bool(
                            cls._first_value(
                                item,
                                "possibly_extinct",
                                "possiblyExtinct",
                            )
                        ),
                    }
                )

                if entry.get("name") or entry.get("iso_code2"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "iso_code2": "",
                            "status": "",
                            "introduced": None,
                            "extinct": None,
                            "possibly_extinct": None,
                        }
                    )

        return result

    @classmethod
    def _normalize_common_names(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize common names with language metadata."""

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "common_name",
                        "commonName",
                        "vernacular_name",
                        "vernacularName",
                    )
                )
                language = normalize_space(
                    cls._first_value(
                        item,
                        "language",
                        "lang",
                        "language_code",
                        "languageCode",
                    )
                )
                preferred = cls._optional_bool(
                    cls._first_value(
                        item,
                        "preferred",
                        "is_preferred",
                        "isPreferred",
                    )
                )
                raw = dict(item)
            else:
                name = normalize_space(item)
                language = ""
                preferred = None
                raw = item

            key = (
                name.casefold(),
                language.casefold(),
            )

            if not name or key in seen:
                continue

            seen.add(key)
            result.append(
                {
                    "name": name,
                    "language": language,
                    "preferred": preferred,
                    "raw": raw,
                }
            )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize external taxonomy and conservation identifiers."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                identifier = normalize_space(
                    cls._first_value(
                        item,
                        "identifier",
                        "id",
                        "value",
                    )
                )
                source = normalize_space(
                    cls._first_value(
                        item,
                        "source",
                        "database",
                        "namespace",
                    )
                )
            else:
                identifier = normalize_space(item)
                source = ""

            if identifier:
                result.append(
                    {
                        "identifier": identifier,
                        "source": source,
                    }
                )

        return result

    @classmethod
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize Species+ and CITES references."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "citation": normalize_space(
                            cls._first_value(
                                item,
                                "citation",
                                "title",
                                "reference",
                            )
                        ),
                        "document": normalize_space(
                            cls._first_value(
                                item,
                                "document",
                                "document_code",
                                "documentCode",
                            )
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "published_at",
                                "publishedAt",
                            )
                        ),
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "source_url",
                                "sourceUrl",
                            )
                        ),
                    }
                )
                result.append(entry)
            else:
                citation = normalize_space(item)

                if citation:
                    result.append(
                        {
                            "citation": citation,
                            "document": "",
                            "date": "",
                            "url": "",
                        }
                    )

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize taxonomic rank labels."""

        rank = normalize_space(
            value
        ).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "division": "phylum",
            "sub division": "subphylum",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub order": "suborder",
            "sub class": "subclass",
            "var.": "variety",
            "forma": "form",
            "f.": "form",
        }

        if not rank:
            return "unknown"

        return aliases.get(
            rank,
            rank.replace(" ", "_"),
        )

    @staticmethod
    def _normalize_status(value: Any) -> str:
        """Normalize taxonomic and nomenclatural status labels."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "provisional": "provisionally accepted",
            "hybrid": "reference",
            "excluded": "excluded",
            "doubtful": "unknown",
            "unresolved": "unknown",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _infer_rank(scientific_name: str) -> str:
        """Infer rank from scientific-name structure."""

        words = normalize_space(
            scientific_name
        ).split()

        lowered = {
            word.casefold()
            for word in words
        }

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if "var." in lowered or "variety" in lowered:
            return "variety"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        """Decode a non-negative JSONL line offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid Species+ cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Species+ cursor must be non-negative."
            )

        return offset

    @staticmethod
    def _first_value(
        record: Mapping[str, Any],
        *keys: str,
    ) -> Any:
        for key in keys:
            value = record.get(key)

            if value not in (
                None,
                "",
                [],
                {},
            ):
                return value

        return None

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        if value is None:
            return []

        if isinstance(value, list):
            return value

        return [value]

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            return bool(value)

        normalized = normalize_space(value).casefold()

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
