#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/usda_plants.py

USDA PLANTS Database provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for USDA PLANTS symbols, accepted plant
names, synonymy, classification, native and introduced status, distribution by
United States state and territory, wetland indicator status, duration, growth
habit, federal status, references, external identifiers, and provenance.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete USDA PLANTS source object is preserved under
``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "usda_plants",
        "path": "static/data/providers/usda-plants/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "USDA PLANTS Database",
        "source_url": "https://plants.usda.gov/home"
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
    """File-backed USDA PLANTS provider."""

    PROVIDER_NAME = "usda_plants"

    DEFAULT_SOURCE_NAME = "USDA PLANTS Database"
    DEFAULT_SOURCE_URL = "https://plants.usda.gov/home"

    def fetch(self) -> Batch:
        """Read and normalize one resumable USDA PLANTS JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"USDA PLANTS export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"USDA PLANTS path is not a file: {source_path}"
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
                            f"Invalid USDA PLANTS JSON at "
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
        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "USDA PLANTS provider requires a path."
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
        """Normalize one USDA PLANTS taxon record."""

        symbol = normalize_space(
            self._first_value(
                raw,
                "symbol",
                "plant_symbol",
                "plantSymbol",
                "usda_symbol",
                "usdaSymbol",
            )
        ).upper()

        provider_id = normalize_space(
            self._first_value(
                raw,
                "usda_plants_id",
                "usdaPlantsId",
                "plant_id",
                "plantId",
                "taxon_id",
                "taxonId",
                "id",
                "symbol",
            )
        )

        if not provider_id and symbol:
            provider_id = symbol

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
            )
        )

        infraspecific_epithet = normalize_space(
            self._first_value(
                raw,
                "infraspecific_epithet",
                "infraspecificEpithet",
                "subspecies",
                "variety",
                "form",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "accepted_scientific_name",
                "acceptedScientificName",
                "latin_name",
                "latinName",
                "name",
            )
        )

        if not scientific_name and genus and specific_epithet:
            scientific_name = f"{genus} {specific_epithet}"

            if infraspecific_epithet:
                marker = normalize_space(
                    self._first_value(
                        raw,
                        "infraspecific_rank",
                        "infraspecificRank",
                        "rank_marker",
                        "rankMarker",
                    )
                )
                scientific_name = " ".join(
                    part
                    for part in (
                        scientific_name,
                        marker,
                        infraspecific_epithet,
                    )
                    if part
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
                "infraspecific_rank",
                "infraspecificRank",
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
                "accepted_status",
                "acceptedStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_symbol",
                "acceptedSymbol",
                "accepted_name_id",
                "acceptedNameId",
            )
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        parent_provider_id = normalize_space(
            self._first_value(
                raw,
                "parent_taxon_id",
                "parentTaxonId",
                "parent_symbol",
                "parentSymbol",
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
                "profile_url",
                "profileUrl",
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
            source_url = (
                f"{base}/plantProfile?symbol={symbol or provider_id}"
            )

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
            kingdom=lineage.get("kingdom", "Plantae"),
            phylum=lineage.get("phylum", ""),
            class_name=lineage.get("class", ""),
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
                "programme": "usda_plants",
                "reference_only": True,
                "usda_plants_id": provider_id,
                "symbol": symbol,
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
                    "infraspecific_epithet": infraspecific_epithet,
                    "infraspecific_rank": self._normalize_rank(
                        self._first_value(
                            raw,
                            "infraspecific_rank",
                            "infraspecificRank",
                        )
                    ),
                    "accepted_symbol": normalize_space(
                        self._first_value(
                            raw,
                            "accepted_symbol",
                            "acceptedSymbol",
                        )
                    ).upper(),
                    "common_name": normalize_space(
                        self._first_value(
                            raw,
                            "common_name",
                            "commonName",
                            "national_common_name",
                            "nationalCommonName",
                        )
                    ),
                },
                "taxonomy": {
                    "subkingdom": normalize_space(raw.get("subkingdom")),
                    "superdivision": normalize_space(
                        raw.get("superdivision")
                    ),
                    "division": normalize_space(raw.get("division")),
                    "subdivision": normalize_space(raw.get("subdivision")),
                    "superclass": normalize_space(raw.get("superclass")),
                    "subclass": normalize_space(raw.get("subclass")),
                    "superorder": normalize_space(raw.get("superorder")),
                    "suborder": normalize_space(raw.get("suborder")),
                    "superfamily": normalize_space(raw.get("superfamily")),
                    "subfamily": normalize_space(raw.get("subfamily")),
                    "tribe": normalize_space(raw.get("tribe")),
                    "subtribe": normalize_space(raw.get("subtribe")),
                    "subgenus": normalize_space(raw.get("subgenus")),
                    "section": normalize_space(raw.get("section")),
                    "nomenclatural_code": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_code",
                            "nomenclaturalCode",
                        )
                    ) or "ICNafp",
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "remarks",
                            "comments",
                        )
                    ),
                },
                "synonym_records": self._normalize_synonym_records(
                    self._first_value(
                        raw,
                        "synonyms",
                        "synonym_records",
                        "synonymRecords",
                        "scientific_synonyms",
                        "scientificSynonyms",
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
                            "national_common_name",
                            "nationalCommonName",
                        )
                    ),
                ),
                "distribution": {
                    "states": self._normalize_regions(
                        self._first_value(
                            raw,
                            "states",
                            "state_distribution",
                            "stateDistribution",
                            "state_records",
                            "stateRecords",
                        )
                    ),
                    "territories": self._normalize_regions(
                        self._first_value(
                            raw,
                            "territories",
                            "territory_distribution",
                            "territoryDistribution",
                        )
                    ),
                    "counties": self._normalize_regions(
                        self._first_value(
                            raw,
                            "counties",
                            "county_distribution",
                            "countyDistribution",
                        )
                    ),
                    "countries": self._normalize_regions(
                        self._first_value(
                            raw,
                            "countries",
                            "country_distribution",
                            "countryDistribution",
                        )
                    ),
                    "native_status": normalize_space(
                        self._first_value(
                            raw,
                            "native_status",
                            "nativeStatus",
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
                    "cultivated": self._optional_bool(
                        self._first_value(
                            raw,
                            "cultivated",
                            "is_cultivated",
                            "isCultivated",
                        )
                    ),
                    "invasive": self._optional_bool(
                        self._first_value(
                            raw,
                            "invasive",
                            "is_invasive",
                            "isInvasive",
                        )
                    ),
                    "noxious": self._optional_bool(
                        self._first_value(
                            raw,
                            "noxious",
                            "is_noxious",
                            "isNoxious",
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
                },
                "wetland": {
                    "national_indicator": normalize_space(
                        self._first_value(
                            raw,
                            "wetland_indicator_status",
                            "wetlandIndicatorStatus",
                            "national_wetland_indicator",
                            "nationalWetlandIndicator",
                        )
                    ),
                    "regional_indicators": self._normalize_wetland_indicators(
                        self._first_value(
                            raw,
                            "regional_wetland_indicators",
                            "regionalWetlandIndicators",
                            "wetland_regions",
                            "wetlandRegions",
                        )
                    ),
                },
                "plant_characteristics": {
                    "duration": self._list_value(
                        self._first_value(
                            raw,
                            "duration",
                            "life_cycle",
                            "lifeCycle",
                        )
                    ),
                    "growth_habit": self._list_value(
                        self._first_value(
                            raw,
                            "growth_habit",
                            "growthHabit",
                            "growth_form",
                            "growthForm",
                        )
                    ),
                    "growth_rate": normalize_space(
                        self._first_value(
                            raw,
                            "growth_rate",
                            "growthRate",
                        )
                    ),
                    "mature_height_ft": self._optional_float(
                        self._first_value(
                            raw,
                            "mature_height_ft",
                            "matureHeightFt",
                            "mature_height",
                            "matureHeight",
                        )
                    ),
                    "foliage_porosity": normalize_space(
                        self._first_value(
                            raw,
                            "foliage_porosity",
                            "foliagePorosity",
                        )
                    ),
                    "flower_color": self._list_value(
                        self._first_value(
                            raw,
                            "flower_color",
                            "flowerColor",
                        )
                    ),
                    "fruit_color": self._list_value(
                        self._first_value(
                            raw,
                            "fruit_color",
                            "fruitColor",
                        )
                    ),
                    "leaf_retention": normalize_space(
                        self._first_value(
                            raw,
                            "leaf_retention",
                            "leafRetention",
                        )
                    ),
                    "shape_and_orientation": normalize_space(
                        self._first_value(
                            raw,
                            "shape_and_orientation",
                            "shapeAndOrientation",
                        )
                    ),
                    "toxicity": normalize_space(
                        self._first_value(
                            raw,
                            "toxicity",
                        )
                    ),
                },
                "ecology": {
                    "adapted_to_coarse_soils": self._optional_bool(
                        self._first_value(
                            raw,
                            "adapted_to_coarse_soils",
                            "adaptedToCoarseSoils",
                        )
                    ),
                    "adapted_to_medium_soils": self._optional_bool(
                        self._first_value(
                            raw,
                            "adapted_to_medium_soils",
                            "adaptedToMediumSoils",
                        )
                    ),
                    "adapted_to_fine_soils": self._optional_bool(
                        self._first_value(
                            raw,
                            "adapted_to_fine_soils",
                            "adaptedToFineSoils",
                        )
                    ),
                    "drought_tolerance": normalize_space(
                        self._first_value(
                            raw,
                            "drought_tolerance",
                            "droughtTolerance",
                        )
                    ),
                    "shade_tolerance": normalize_space(
                        self._first_value(
                            raw,
                            "shade_tolerance",
                            "shadeTolerance",
                        )
                    ),
                    "salinity_tolerance": normalize_space(
                        self._first_value(
                            raw,
                            "salinity_tolerance",
                            "salinityTolerance",
                        )
                    ),
                    "fire_resistance": normalize_space(
                        self._first_value(
                            raw,
                            "fire_resistance",
                            "fireResistance",
                        )
                    ),
                    "minimum_temperature_f": self._optional_float(
                        self._first_value(
                            raw,
                            "minimum_temperature_f",
                            "minimumTemperatureF",
                        )
                    ),
                    "precipitation_min_in": self._optional_float(
                        self._first_value(
                            raw,
                            "precipitation_min_in",
                            "precipitationMinIn",
                        )
                    ),
                    "precipitation_max_in": self._optional_float(
                        self._first_value(
                            raw,
                            "precipitation_max_in",
                            "precipitationMaxIn",
                        )
                    ),
                },
                "status": {
                    "federal_status": normalize_space(
                        self._first_value(
                            raw,
                            "federal_status",
                            "federalStatus",
                        )
                    ),
                    "state_status": self._normalize_regions(
                        self._first_value(
                            raw,
                            "state_status",
                            "stateStatus",
                            "state_conservation_status",
                            "stateConservationStatus",
                        )
                    ),
                    "noxious_weed_status": self._normalize_regions(
                        self._first_value(
                            raw,
                            "noxious_weed_status",
                            "noxiousWeedStatus",
                        )
                    ),
                },
                "uses": self._normalize_uses(
                    self._first_value(
                        raw,
                        "uses",
                        "plant_uses",
                        "plantUses",
                    )
                ),
                "identifiers": self._normalize_identifiers(
                    self._first_value(
                        raw,
                        "identifiers",
                        "external_identifiers",
                        "externalIdentifiers",
                    ),
                    raw=raw,
                    symbol=symbol,
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
        lineage = {
            "kingdom": normalize_space(raw.get("kingdom")) or "Plantae",
            "subkingdom": normalize_space(raw.get("subkingdom")),
            "superdivision": normalize_space(raw.get("superdivision")),
            "phylum": normalize_space(
                cls._first_value(raw, "phylum", "division")
            ),
            "subphylum": normalize_space(
                cls._first_value(raw, "subphylum", "subdivision")
            ),
            "class": normalize_space(raw.get("class")),
            "subclass": normalize_space(raw.get("subclass")),
            "order": normalize_space(raw.get("order")),
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
        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "synonym_records",
                "synonymRecords",
                "scientific_synonyms",
                "scientificSynonyms",
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
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "symbol": normalize_space(
                            cls._first_value(
                                item,
                                "symbol",
                                "plant_symbol",
                                "plantSymbol",
                            )
                        ).upper(),
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "scientific_name",
                                "scientificName",
                            )
                        ),
                        "authorship": normalize_space(
                            cls._first_value(
                                item,
                                "authorship",
                                "authority",
                                "author",
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
                        "accepted_symbol": normalize_space(
                            cls._first_value(
                                item,
                                "accepted_symbol",
                                "acceptedSymbol",
                            )
                        ).upper(),
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
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "symbol": "",
                            "name": name,
                            "authorship": "",
                            "status": "synonym",
                            "accepted_symbol": "",
                            "reference": "",
                            "raw": item,
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
                        "state",
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
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = {
                    "name": normalize_space(
                        cls._first_value(
                            item,
                            "name",
                            "state",
                            "territory",
                            "county",
                            "country",
                            "region",
                        )
                    ),
                    "code": normalize_space(
                        cls._first_value(
                            item,
                            "code",
                            "state_code",
                            "stateCode",
                            "country_code",
                            "countryCode",
                            "fips",
                        )
                    ).upper(),
                    "status": normalize_space(
                        cls._first_value(
                            item,
                            "status",
                            "native_status",
                            "nativeStatus",
                            "presence",
                        )
                    ),
                    "native": cls._optional_bool(
                        cls._first_value(
                            item,
                            "native",
                            "is_native",
                            "isNative",
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
                    "present": cls._optional_bool(
                        cls._first_value(
                            item,
                            "present",
                            "is_present",
                            "isPresent",
                        )
                    ),
                    "raw": dict(item),
                }
            else:
                text = normalize_space(item)
                entry = {
                    "name": text,
                    "code": text.upper() if len(text) <= 3 else "",
                    "status": "",
                    "native": None,
                    "introduced": None,
                    "present": None,
                    "raw": item,
                }

            if entry["name"] or entry["code"]:
                result.append(entry)

        return result

    @classmethod
    def _normalize_wetland_indicators(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "region": normalize_space(
                            cls._first_value(
                                item,
                                "region",
                                "region_name",
                                "regionName",
                                "name",
                            )
                        ),
                        "region_code": normalize_space(
                            cls._first_value(
                                item,
                                "region_code",
                                "regionCode",
                                "code",
                            )
                        ),
                        "indicator": normalize_space(
                            cls._first_value(
                                item,
                                "indicator",
                                "status",
                                "wetland_indicator",
                                "wetlandIndicator",
                            )
                        ),
                        "year": normalize_space(
                            cls._first_value(
                                item,
                                "year",
                                "effective_year",
                                "effectiveYear",
                            )
                        ),
                        "raw": dict(item),
                    }
                )

        return result

    @classmethod
    def _normalize_uses(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        if isinstance(value, Mapping):
            value = [
                {
                    "category": key,
                    "value": item,
                }
                for key, item in value.items()
            ]

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "category": normalize_space(
                            cls._first_value(
                                item,
                                "category",
                                "type",
                                "name",
                            )
                        ),
                        "value": cls._first_value(
                            item,
                            "value",
                            "description",
                            "present",
                        ),
                        "source": normalize_space(
                            cls._first_value(
                                item,
                                "source",
                                "reference",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                text = normalize_space(item)

                if text:
                    result.append(
                        {
                            "category": text,
                            "value": True,
                            "source": "",
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
        symbol: str,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

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

            key = (source.casefold(), identifier.casefold())

            if identifier and key not in seen:
                seen.add(key)
                result.append(
                    {
                        "identifier": identifier,
                        "source": source,
                    }
                )

        known = {
            "USDA PLANTS Symbol": symbol,
            "ITIS": normalize_space(
                self._first_value(raw, "itis_tsn", "itisTsn")
            ),
            "Tropicos": normalize_space(
                self._first_value(raw, "tropicos_id", "tropicosId")
            ),
            "IPNI": normalize_space(
                self._first_value(raw, "ipni_id", "ipniId")
            ),
            "POWO": normalize_space(
                self._first_value(raw, "powo_id", "powoId")
            ),
            "World Flora Online": normalize_space(
                self._first_value(raw, "wfo_id", "wfoId")
            ),
            "GBIF": normalize_space(
                self._first_value(raw, "gbif_id", "gbifId")
            ),
            "Catalogue of Life": normalize_space(
                self._first_value(raw, "col_id", "colId")
            ),
            "NCBI Taxonomy": normalize_space(
                self._first_value(
                    raw,
                    "ncbi_taxid",
                    "ncbiTaxid",
                )
            ),
            "Wikidata": normalize_space(
                self._first_value(
                    raw,
                    "wikidata_id",
                    "wikidataId",
                )
            ),
        }

        for source, identifier in known.items():
            key = (source.casefold(), identifier.casefold())

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
                            item.get("doi")
                        ).removeprefix("https://doi.org/"),
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
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = {
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
                    "raw": dict(item),
                }
            else:
                entry = {
                    "url": normalize_space(item),
                    "thumbnail_url": "",
                    "type": "",
                    "title": "",
                    "creator": "",
                    "license": "",
                    "raw": item,
                }

            if entry["url"] or entry["thumbnail_url"]:
                result.append(entry)

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        rank = normalize_space(value).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "division": "phylum",
            "sub division": "subphylum",
            "subdivision": "subphylum",
            "super division": "superdivision",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub tribe": "subtribe",
            "sub order": "suborder",
            "sub class": "subclass",
            "var.": "variety",
            "subvar.": "subvariety",
            "forma": "form",
            "f.": "form",
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
        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "accepted",
            "current": "accepted",
            "accepted name": "accepted",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "excluded": "excluded",
            "invalid": "excluded",
            "illegitimate": "excluded",
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
        words = normalize_space(scientific_name).split()
        lowered = {word.casefold() for word in words}

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if "var." in lowered or "variety" in lowered:
            return "variety"

        if "subvar." in lowered:
            return "subvariety"

        if "f." in lowered or "forma" in lowered:
            return "form"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "infraspecific"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid USDA PLANTS cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "USDA PLANTS cursor must be non-negative."
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

        if isinstance(value, str) and "|" in value:
            return [
                part.strip()
                for part in value.split("|")
                if part.strip()
            ]

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
            "native",
            "introduced",
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
