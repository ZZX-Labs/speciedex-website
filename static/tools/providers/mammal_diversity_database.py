#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/mammal_diversity_database.py

Mammal Diversity Database (MDD) provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for accepted mammal taxonomy, species
and subspecies, authorship, parent relationships, synonymy, common names,
distribution, endemicity, conservation status, references, external
identifiers, release/version history, and provenance metadata.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete Mammal Diversity Database source object is preserved under
``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "mammal_diversity_database",
        "path": "static/data/providers/mammal-diversity-database/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Mammal Diversity Database",
        "source_url": "https://www.mammaldiversity.org/"
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
    """File-backed Mammal Diversity Database provider."""

    PROVIDER_NAME = "mammal_diversity_database"

    DEFAULT_SOURCE_NAME = "Mammal Diversity Database"
    DEFAULT_SOURCE_URL = "https://www.mammaldiversity.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable MDD JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Mammal Diversity Database export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Mammal Diversity Database path is not a file: {source_path}"
            )

        offset = self._decode_cursor(self.cursor)
        configured_page_size = safe_int(
            self.definition.get("page_size", self.batch_size),
            self.batch_size,
        )
        page_size = max(1, min(configured_page_size, self.batch_size))

        records: list[Taxon] = []
        raw_count = 0
        next_offset = offset
        exhausted = True
        retrieved_at = now()

        with source_path.open("r", encoding="utf-8") as handle:
            logical_index = 0

            for physical_line, line in enumerate(handle, start=1):
                stripped = line.strip()

                if not stripped or stripped.startswith("#"):
                    continue

                if logical_index < offset:
                    logical_index += 1
                    continue

                if raw_count >= page_size:
                    exhausted = False
                    break

                next_offset = logical_index + 1
                logical_index += 1
                raw_count += 1

                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError as error:
                    if bool(self.definition.get("strict", False)):
                        raise ProviderError(
                            f"Invalid MDD JSON at "
                            f"{source_path}:{physical_line}: {error}"
                        ) from error
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
            next_cursor=None if exhausted else str(next_offset),
            exhausted=exhausted,
            requests=0,
            raw=raw_count,
        )

    def _source_path(self) -> Path:
        """Resolve the configured MDD JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "Mammal Diversity Database provider requires a path."
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
        """Normalize one MDD mammal taxon record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "mdd_id",
                "mddId",
                "mdd_taxon_id",
                "mddTaxonId",
                "taxon_id",
                "taxonId",
                "species_id",
                "speciesId",
                "id",
            )
        )

        genus = normalize_space(
            self._first_value(
                raw,
                "genus",
                "genus_name",
                "genusName",
            )
        )

        specific_epithet = normalize_space(
            self._first_value(
                raw,
                "specific_epithet",
                "specificEpithet",
                "species",
                "species_epithet",
                "speciesEpithet",
            )
        )

        subspecies_epithet = normalize_space(
            self._first_value(
                raw,
                "subspecies",
                "subspecies_epithet",
                "subspeciesEpithet",
                "infraspecific_epithet",
                "infraspecificEpithet",
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

        if not scientific_name and genus and specific_epithet:
            scientific_name = f"{genus} {specific_epithet}"

            if subspecies_epithet:
                scientific_name = (
                    f"{scientific_name} {subspecies_epithet}"
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
            rank = (
                "subspecies"
                if subspecies_epithet
                else self._infer_rank(canonical_name)
            )

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
                "accepted_name_id",
                "acceptedNameId",
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_id",
                "acceptedId",
            )
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        parent_provider_id = normalize_space(
            self._first_value(
                raw,
                "parent_taxon_id",
                "parentTaxonId",
                "parent_id",
                "parentId",
            )
        )

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
            base = normalize_space(
                self.definition.get(
                    "source_url",
                    self.DEFAULT_SOURCE_URL,
                )
            ).rstrip("/")
            source_url = f"{base}/taxon/{provider_id}"

        lineage = self._extract_lineage(raw, genus=genus)

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
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "authority",
                    "author",
                )
            ),
            kingdom=lineage.get("kingdom", "Animalia"),
            phylum=lineage.get("phylum", "Chordata"),
            class_name=lineage.get("class", "Mammalia"),
            order=lineage.get("order", ""),
            family=lineage.get("family", ""),
            genus=lineage.get("genus", genus),
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
                "programme": "mammal_diversity_database",
                "reference_only": True,
                "mdd_id": provider_id,
                "accepted_name_id": accepted_provider_id,
                "lineage": lineage,
                "parent": {
                    "id": parent_provider_id,
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
                "name": {
                    "genus": genus,
                    "specific_epithet": specific_epithet,
                    "subspecies_epithet": subspecies_epithet,
                    "original_combination": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination",
                            "originalCombination",
                            "original_name",
                            "originalName",
                        )
                    ),
                    "basionym": normalize_space(
                        self._first_value(
                            raw,
                            "basionym",
                            "basionym_name",
                            "basionymName",
                        )
                    ),
                    "authority_year": normalize_space(
                        self._first_value(
                            raw,
                            "authority_year",
                            "authorityYear",
                            "year",
                            "publication_year",
                            "publicationYear",
                        )
                    ),
                    "authority_parentheses": self._optional_bool(
                        self._first_value(
                            raw,
                            "authority_parentheses",
                            "authorityParentheses",
                            "in_parentheses",
                            "inParentheses",
                        )
                    ),
                },
                "taxonomy": {
                    "suborder": normalize_space(
                        self._first_value(raw, "suborder")
                    ),
                    "infraorder": normalize_space(
                        self._first_value(raw, "infraorder")
                    ),
                    "parvorder": normalize_space(
                        self._first_value(raw, "parvorder")
                    ),
                    "superfamily": normalize_space(
                        self._first_value(raw, "superfamily")
                    ),
                    "subfamily": normalize_space(
                        self._first_value(raw, "subfamily")
                    ),
                    "tribe": normalize_space(
                        self._first_value(raw, "tribe")
                    ),
                    "subtribe": normalize_space(
                        self._first_value(raw, "subtribe")
                    ),
                    "species_group": normalize_space(
                        self._first_value(
                            raw,
                            "species_group",
                            "speciesGroup",
                        )
                    ),
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "comments",
                            "remarks",
                        )
                    ),
                    "nomenclatural_code": "ICZN",
                },
                "synonym_records": self._normalize_synonym_records(
                    self._first_value(
                        raw,
                        "synonyms",
                        "synonym_records",
                        "synonymRecords",
                        "taxonomic_synonyms",
                        "taxonomicSynonyms",
                    )
                ),
                "common_names": self._normalize_common_names(
                    self._first_value(
                        raw,
                        "common_names",
                        "commonNames",
                        "vernacular_names",
                        "vernacularNames",
                    ),
                    preferred=normalize_space(
                        self._first_value(
                            raw,
                            "common_name",
                            "commonName",
                            "english_name",
                            "englishName",
                        )
                    ),
                ),
                "distribution": {
                    "summary": self._first_value(
                        raw,
                        "distribution",
                        "range",
                        "geographic_distribution",
                        "geographicDistribution",
                    ),
                    "countries": self._normalize_regions(
                        self._first_value(
                            raw,
                            "countries",
                            "country_records",
                            "countryRecords",
                        )
                    ),
                    "regions": self._normalize_regions(
                        self._first_value(
                            raw,
                            "regions",
                            "region_records",
                            "regionRecords",
                        )
                    ),
                    "native": self._optional_bool(
                        self._first_value(
                            raw,
                            "native",
                            "is_native",
                            "isNative",
                        )
                    ),
                    "introduced": self._optional_bool(
                        self._first_value(
                            raw,
                            "introduced",
                            "is_introduced",
                            "isIntroduced",
                        )
                    ),
                    "endemic": self._optional_bool(
                        self._first_value(
                            raw,
                            "endemic",
                            "is_endemic",
                            "isEndemic",
                        )
                    ),
                    "endemic_to": self._normalize_regions(
                        self._first_value(
                            raw,
                            "endemic_to",
                            "endemicTo",
                            "endemic_areas",
                            "endemicAreas",
                        )
                    ),
                    "marine": self._optional_bool(
                        self._first_value(
                            raw,
                            "marine",
                            "is_marine",
                            "isMarine",
                        )
                    ),
                    "terrestrial": self._optional_bool(
                        self._first_value(
                            raw,
                            "terrestrial",
                            "is_terrestrial",
                            "isTerrestrial",
                        )
                    ),
                    "freshwater": self._optional_bool(
                        self._first_value(
                            raw,
                            "freshwater",
                            "is_freshwater",
                            "isFreshwater",
                        )
                    ),
                },
                "ecology": {
                    "habitats": self._list_value(
                        self._first_value(
                            raw,
                            "habitats",
                            "habitat",
                        )
                    ),
                    "ecoregions": self._list_value(
                        self._first_value(
                            raw,
                            "ecoregions",
                            "ecoregion",
                        )
                    ),
                    "elevation_min_m": self._optional_float(
                        self._first_value(
                            raw,
                            "elevation_min_m",
                            "elevationMinM",
                            "minimum_elevation",
                            "minimumElevation",
                        )
                    ),
                    "elevation_max_m": self._optional_float(
                        self._first_value(
                            raw,
                            "elevation_max_m",
                            "elevationMaxM",
                            "maximum_elevation",
                            "maximumElevation",
                        )
                    ),
                    "diet": self._list_value(
                        self._first_value(
                            raw,
                            "diet",
                            "feeding",
                        )
                    ),
                    "activity_pattern": normalize_space(
                        self._first_value(
                            raw,
                            "activity_pattern",
                            "activityPattern",
                        )
                    ),
                    "social_system": normalize_space(
                        self._first_value(
                            raw,
                            "social_system",
                            "socialSystem",
                        )
                    ),
                },
                "conservation": {
                    "iucn_status": normalize_space(
                        self._first_value(
                            raw,
                            "iucn_status",
                            "iucnStatus",
                            "conservation_status",
                            "conservationStatus",
                        )
                    ),
                    "iucn_assessment_id": normalize_space(
                        self._first_value(
                            raw,
                            "iucn_assessment_id",
                            "iucnAssessmentId",
                        )
                    ),
                    "cites_status": normalize_space(
                        self._first_value(
                            raw,
                            "cites_status",
                            "citesStatus",
                        )
                    ),
                    "cms_status": normalize_space(
                        self._first_value(
                            raw,
                            "cms_status",
                            "cmsStatus",
                        )
                    ),
                    "population_trend": normalize_space(
                        self._first_value(
                            raw,
                            "population_trend",
                            "populationTrend",
                        )
                    ),
                    "extinct": self._optional_bool(
                        self._first_value(
                            raw,
                            "extinct",
                            "is_extinct",
                            "isExtinct",
                        )
                    ),
                    "domesticated": self._optional_bool(
                        self._first_value(
                            raw,
                            "domesticated",
                            "is_domesticated",
                            "isDomesticated",
                        )
                    ),
                },
                "mdd_release": {
                    "version": normalize_space(
                        self._first_value(
                            raw,
                            "mdd_version",
                            "mddVersion",
                            "release_version",
                            "releaseVersion",
                            "version",
                        )
                    ),
                    "release_date": normalize_space(
                        self._first_value(
                            raw,
                            "release_date",
                            "releaseDate",
                        )
                    ),
                    "change_type": normalize_space(
                        self._first_value(
                            raw,
                            "change_type",
                            "changeType",
                        )
                    ),
                    "change_notes": normalize_space(
                        self._first_value(
                            raw,
                            "change_notes",
                            "changeNotes",
                        )
                    ),
                    "history": self._normalize_history(
                        self._first_value(
                            raw,
                            "history",
                            "change_history",
                            "changeHistory",
                            "mdd_history",
                            "mddHistory",
                        )
                    ),
                },
                "identifiers": self._normalize_identifiers(
                    self._first_value(
                        raw,
                        "identifiers",
                        "external_identifiers",
                        "externalIdentifiers",
                    ),
                    raw=raw,
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
                        "bibliography",
                    )
                ),
                "media": self._normalize_media(
                    self._first_value(
                        raw,
                        "media",
                        "images",
                        "image",
                        "range_maps",
                        "rangeMaps",
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
        *,
        genus: str,
    ) -> dict[str, str]:
        """Extract mammalian classification."""

        lineage = {
            "kingdom": normalize_space(
                raw.get("kingdom")
            ) or "Animalia",
            "phylum": normalize_space(
                raw.get("phylum")
            ) or "Chordata",
            "class": normalize_space(
                raw.get("class")
            ) or "Mammalia",
            "order": normalize_space(raw.get("order")),
            "suborder": normalize_space(raw.get("suborder")),
            "infraorder": normalize_space(raw.get("infraorder")),
            "superfamily": normalize_space(raw.get("superfamily")),
            "family": normalize_space(raw.get("family")),
            "subfamily": normalize_space(raw.get("subfamily")),
            "tribe": normalize_space(raw.get("tribe")),
            "genus": genus or normalize_space(raw.get("genus")),
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
        """Extract and deduplicate mammal synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "synonym_records",
                "synonymRecords",
                "taxonomic_synonyms",
                "taxonomicSynonyms",
                "former_names",
                "formerNames",
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
                name = normalize_space(
                    cls._first_value(
                        item,
                        "scientific_name",
                        "scientificName",
                        "name",
                    )
                )
            else:
                name = normalize_space(item)

            key = name.casefold()

            if not name or key in seen:
                continue

            seen.add(key)
            result.append(name)

        return result

    @classmethod
    def _normalize_synonym_records(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize mammal synonym records."""

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
                                "scientific_name",
                                "scientificName",
                            )
                        ),
                        "id": normalize_space(
                            cls._first_value(
                                item,
                                "id",
                                "taxon_id",
                                "taxonId",
                                "mdd_id",
                                "mddId",
                            )
                        ),
                        "authorship": normalize_space(
                            cls._first_value(
                                item,
                                "authorship",
                                "author",
                                "authority",
                            )
                        ),
                        "year": normalize_space(
                            cls._first_value(
                                item,
                                "year",
                                "publication_year",
                                "publicationYear",
                            )
                        ),
                        "status": cls._normalize_status(
                            cls._first_value(
                                item,
                                "status",
                                "taxonomic_status",
                                "taxonomicStatus",
                            )
                        ),
                        "relationship": normalize_space(
                            cls._first_value(
                                item,
                                "relationship",
                                "relation",
                                "type",
                            )
                        ),
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
                            )
                        ),
                    }
                )

                if entry.get("name"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "id": "",
                            "authorship": "",
                            "year": "",
                            "status": "synonym",
                            "relationship": "",
                            "reference": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_common_names(
        cls,
        value: Any,
        *,
        preferred: str,
    ) -> list[dict[str, Any]]:
        """Normalize mammal common names."""

        values = cls._list_value(value)

        if preferred:
            values.insert(
                0,
                {
                    "name": preferred,
                    "language": "en",
                    "preferred": True,
                },
            )

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        for item in values:
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
                region = normalize_space(
                    cls._first_value(
                        item,
                        "region",
                        "country",
                        "area",
                    )
                )
                preferred_value = cls._optional_bool(
                    cls._first_value(
                        item,
                        "preferred",
                        "is_preferred",
                        "isPreferred",
                    )
                )
                raw_item = dict(item)
            else:
                name = normalize_space(item)
                language = ""
                region = ""
                preferred_value = None
                raw_item = item

            key = (
                name.casefold(),
                language.casefold(),
                region.casefold(),
            )

            if not name or key in seen:
                continue

            seen.add(key)
            result.append(
                {
                    "name": name,
                    "language": language,
                    "region": region,
                    "preferred": preferred_value,
                    "raw": raw_item,
                }
            )

        return result

    @classmethod
    def _normalize_regions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize mammal distribution regions."""

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
                                "region",
                                "area",
                            )
                        ),
                        "code": normalize_space(
                            cls._first_value(
                                item,
                                "code",
                                "country_code",
                                "countryCode",
                                "region_code",
                                "regionCode",
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
                    }
                )

                if entry.get("name") or entry.get("code"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "code": "",
                            "status": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_history(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize MDD release and taxonomic-change history."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "version": normalize_space(
                            cls._first_value(
                                item,
                                "version",
                                "mdd_version",
                                "mddVersion",
                            )
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "release_date",
                                "releaseDate",
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
                        "previous_name": normalize_space(
                            cls._first_value(
                                item,
                                "previous_name",
                                "previousName",
                            )
                        ),
                        "new_name": normalize_space(
                            cls._first_value(
                                item,
                                "new_name",
                                "newName",
                            )
                        ),
                        "description": normalize_space(
                            cls._first_value(
                                item,
                                "description",
                                "change",
                                "notes",
                            )
                        ),
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                description = normalize_space(item)

                if description:
                    result.append(
                        {
                            "version": "",
                            "date": "",
                            "change_type": "",
                            "previous_name": "",
                            "new_name": "",
                            "description": description,
                            "reference": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
        *,
        raw: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        """Normalize MDD and external mammal identifiers."""

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

        known_fields = {
            "mdd_id": "Mammal Diversity Database",
            "mddId": "Mammal Diversity Database",
            "msw_id": "Mammal Species of the World",
            "mswId": "Mammal Species of the World",
            "iucn_id": "IUCN",
            "iucnId": "IUCN",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "col_id": "Catalogue of Life",
            "colId": "Catalogue of Life",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "eol_id": "Encyclopedia of Life",
            "eolId": "Encyclopedia of Life",
            "cites_id": "CITES",
            "citesId": "CITES",
            "cms_id": "CMS",
            "cmsId": "CMS",
        }

        seen = {
            (
                entry["source"].casefold(),
                entry["identifier"].casefold(),
            )
            for entry in result
        }

        for field, source in known_fields.items():
            identifier = normalize_space(raw.get(field))
            key = (
                source.casefold(),
                identifier.casefold(),
            )

            if not identifier or key in seen:
                continue

            seen.add(key)
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
        """Normalize mammalian taxonomic references."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "citation": normalize_space(
                            cls._first_value(
                                item,
                                "citation",
                                "title",
                                "reference",
                            )
                        ),
                        "authors": normalize_space(
                            cls._first_value(
                                item,
                                "authors",
                                "author",
                            )
                        ),
                        "year": normalize_space(
                            cls._first_value(
                                item,
                                "year",
                                "publication_year",
                                "publicationYear",
                            )
                        ),
                        "doi": normalize_space(
                            cls._first_value(item, "doi")
                        ),
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "source_url",
                                "sourceUrl",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                citation = normalize_space(item)

                if citation:
                    result.append(
                        {
                            "citation": citation,
                            "authors": "",
                            "year": "",
                            "doi": "",
                            "url": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_media(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize mammal images and range maps."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "identifier",
                                "media_url",
                                "mediaUrl",
                                "image_url",
                                "imageUrl",
                            )
                        ),
                        "thumbnail_url": normalize_space(
                            cls._first_value(
                                item,
                                "thumbnail_url",
                                "thumbnailUrl",
                                "thumbnail",
                            )
                        ),
                        "type": normalize_space(
                            cls._first_value(
                                item,
                                "type",
                                "media_type",
                                "mediaType",
                            )
                        ).casefold(),
                        "title": normalize_space(
                            cls._first_value(
                                item,
                                "title",
                                "caption",
                                "description",
                            )
                        ),
                        "creator": normalize_space(
                            cls._first_value(
                                item,
                                "creator",
                                "author",
                                "photographer",
                            )
                        ),
                        "license": normalize_space(
                            cls._first_value(
                                item,
                                "license",
                                "rights",
                            )
                        ),
                    }
                )
            else:
                entry = {
                    "url": normalize_space(item),
                    "thumbnail_url": "",
                    "type": "",
                    "title": "",
                    "creator": "",
                    "license": "",
                }

            if entry.get("url") or entry.get("thumbnail_url"):
                result.append(entry)

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize mammalian taxonomic ranks."""

        rank = normalize_space(value).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub tribe": "subtribe",
            "sub order": "suborder",
            "infra order": "infraorder",
            "parv order": "parvorder",
            "super family": "superfamily",
            "species group": "species_group",
            "no rank": "unranked",
        }

        if not rank:
            return "unknown"

        return aliases.get(
            rank,
            rank.replace(" ", "_"),
        )

    @staticmethod
    def _normalize_status(value: Any) -> str:
        """Normalize MDD taxonomic and nomenclatural statuses."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "accepted",
            "current": "accepted",
            "recognized": "accepted",
            "provisional": "provisionally accepted",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "subjective synonym": "synonym",
            "objective synonym": "synonym",
            "unaccepted": "synonym",
            "lumped": "synonym",
            "split": "accepted",
            "misapplied": "misapplied",
            "extinct": "accepted",
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
        """Infer mammal rank from scientific-name structure."""

        words = normalize_space(scientific_name).split()
        lowered = {word.casefold() for word in words}

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        """Decode a non-negative JSONL record offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid Mammal Diversity Database cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Mammal Diversity Database cursor must be non-negative."
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

        if isinstance(value, tuple):
            return list(value)

        if isinstance(value, set):
            return list(value)

        return [value]

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
            "present",
            "active",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "absent",
            "inactive",
        }:
            return False

        return None
