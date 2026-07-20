#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/odonata_central.py

OdonataCentral provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for dragonfly and damselfly taxonomy,
accepted names, synonymy, common names, occurrence records, checklists,
observations, localities, collectors, event dates, coordinates, habitat,
media, references, external identifiers, and provenance metadata.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete OdonataCentral source object is preserved under
``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "odonata_central",
        "path": "static/data/providers/odonata-central/records.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "OdonataCentral",
        "source_url": "https://www.odonatacentral.org/"
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
    """File-backed OdonataCentral provider."""

    PROVIDER_NAME = "odonata_central"

    DEFAULT_SOURCE_NAME = "OdonataCentral"
    DEFAULT_SOURCE_URL = "https://www.odonatacentral.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable OdonataCentral JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"OdonataCentral export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"OdonataCentral path is not a file: {source_path}"
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
                            f"Invalid OdonataCentral JSON at "
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
                "OdonataCentral provider requires a path."
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
        provider_id = normalize_space(
            self._first_value(
                raw,
                "odonata_central_id",
                "odonataCentralId",
                "oc_id",
                "ocId",
                "taxon_id",
                "taxonId",
                "occurrence_id",
                "occurrenceId",
                "observation_id",
                "observationId",
                "record_id",
                "recordId",
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
            )
        )

        infraspecific_epithet = normalize_space(
            self._first_value(
                raw,
                "infraspecific_epithet",
                "infraspecificEpithet",
                "subspecies",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "accepted_scientific_name",
                "acceptedScientificName",
                "taxon_name",
                "taxonName",
                "name",
            )
        )

        if not scientific_name and genus and specific_epithet:
            scientific_name = f"{genus} {specific_epithet}"

            if infraspecific_epithet:
                scientific_name = (
                    f"{scientific_name} {infraspecific_epithet}"
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
            rank = self._infer_rank(canonical_name)

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "occurrence_status",
                "occurrenceStatus",
                "record_status",
                "recordStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
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
                "observation_url",
                "observationUrl",
            )
        )

        if not source_url:
            base = normalize_space(
                self.definition.get(
                    "source_url",
                    self.DEFAULT_SOURCE_URL,
                )
            ).rstrip("/")
            source_url = f"{base}/record/{provider_id}"

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
            phylum=lineage.get("phylum", "Arthropoda"),
            class_name=lineage.get("class", "Insecta"),
            order=lineage.get("order", "Odonata"),
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
                "programme": "odonata_central",
                "reference_only": True,
                "odonata_central_id": provider_id,
                "accepted_name_id": accepted_provider_id,
                "lineage": lineage,
                "taxonomy": {
                    "suborder": normalize_space(
                        self._first_value(raw, "suborder")
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
                    "subgenus": normalize_space(
                        self._first_value(raw, "subgenus")
                    ),
                    "genus": genus,
                    "specific_epithet": specific_epithet,
                    "infraspecific_epithet": infraspecific_epithet,
                    "nomenclatural_code": "ICZN",
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "remarks",
                        )
                    ),
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
                "occurrence": {
                    "occurrence_id": normalize_space(
                        self._first_value(
                            raw,
                            "occurrence_id",
                            "occurrenceId",
                            "occurrenceID",
                        )
                    ),
                    "observation_id": normalize_space(
                        self._first_value(
                            raw,
                            "observation_id",
                            "observationId",
                        )
                    ),
                    "checklist_id": normalize_space(
                        self._first_value(
                            raw,
                            "checklist_id",
                            "checklistId",
                        )
                    ),
                    "basis_of_record": normalize_space(
                        self._first_value(
                            raw,
                            "basis_of_record",
                            "basisOfRecord",
                        )
                    ),
                    "occurrence_status": normalize_space(
                        self._first_value(
                            raw,
                            "occurrence_status",
                            "occurrenceStatus",
                        )
                    ),
                    "individual_count": self._optional_int(
                        self._first_value(
                            raw,
                            "individual_count",
                            "individualCount",
                            "count",
                        )
                    ),
                    "sex": normalize_space(
                        self._first_value(raw, "sex")
                    ),
                    "life_stage": normalize_space(
                        self._first_value(
                            raw,
                            "life_stage",
                            "lifeStage",
                        )
                    ),
                    "behavior": normalize_space(
                        self._first_value(raw, "behavior")
                    ),
                    "recorded_by": normalize_space(
                        self._first_value(
                            raw,
                            "recorded_by",
                            "recordedBy",
                            "observer",
                            "collector",
                        )
                    ),
                    "identified_by": normalize_space(
                        self._first_value(
                            raw,
                            "identified_by",
                            "identifiedBy",
                            "identifier",
                        )
                    ),
                    "date_identified": normalize_space(
                        self._first_value(
                            raw,
                            "date_identified",
                            "dateIdentified",
                        )
                    ),
                    "type_status": normalize_space(
                        self._first_value(
                            raw,
                            "type_status",
                            "typeStatus",
                        )
                    ),
                    "catalog_number": normalize_space(
                        self._first_value(
                            raw,
                            "catalog_number",
                            "catalogNumber",
                        )
                    ),
                    "institution_code": normalize_space(
                        self._first_value(
                            raw,
                            "institution_code",
                            "institutionCode",
                        )
                    ),
                    "collection_code": normalize_space(
                        self._first_value(
                            raw,
                            "collection_code",
                            "collectionCode",
                        )
                    ),
                    "voucher": normalize_space(
                        self._first_value(
                            raw,
                            "voucher",
                            "specimen_voucher",
                            "specimenVoucher",
                        )
                    ),
                },
                "event": {
                    "event_id": normalize_space(
                        self._first_value(
                            raw,
                            "event_id",
                            "eventId",
                            "eventID",
                        )
                    ),
                    "event_date": normalize_space(
                        self._first_value(
                            raw,
                            "event_date",
                            "eventDate",
                            "observation_date",
                            "observationDate",
                        )
                    ),
                    "year": self._optional_int(
                        self._first_value(raw, "year")
                    ),
                    "month": self._optional_int(
                        self._first_value(raw, "month")
                    ),
                    "day": self._optional_int(
                        self._first_value(raw, "day")
                    ),
                    "start_time": normalize_space(
                        self._first_value(
                            raw,
                            "start_time",
                            "startTime",
                        )
                    ),
                    "end_time": normalize_space(
                        self._first_value(
                            raw,
                            "end_time",
                            "endTime",
                        )
                    ),
                    "sampling_protocol": normalize_space(
                        self._first_value(
                            raw,
                            "sampling_protocol",
                            "samplingProtocol",
                            "survey_method",
                            "surveyMethod",
                        )
                    ),
                    "sampling_effort": normalize_space(
                        self._first_value(
                            raw,
                            "sampling_effort",
                            "samplingEffort",
                        )
                    ),
                },
                "location": {
                    "location_id": normalize_space(
                        self._first_value(
                            raw,
                            "location_id",
                            "locationId",
                            "locationID",
                        )
                    ),
                    "site_name": normalize_space(
                        self._first_value(
                            raw,
                            "site_name",
                            "siteName",
                            "locality",
                        )
                    ),
                    "country": normalize_space(
                        self._first_value(raw, "country")
                    ),
                    "country_code": normalize_space(
                        self._first_value(
                            raw,
                            "country_code",
                            "countryCode",
                        )
                    ),
                    "state_province": normalize_space(
                        self._first_value(
                            raw,
                            "state_province",
                            "stateProvince",
                            "state",
                            "province",
                        )
                    ),
                    "county": normalize_space(
                        self._first_value(raw, "county")
                    ),
                    "municipality": normalize_space(
                        self._first_value(raw, "municipality")
                    ),
                    "locality": normalize_space(
                        self._first_value(raw, "locality")
                    ),
                    "water_body": normalize_space(
                        self._first_value(
                            raw,
                            "water_body",
                            "waterBody",
                        )
                    ),
                    "decimal_latitude": self._optional_float(
                        self._first_value(
                            raw,
                            "decimal_latitude",
                            "decimalLatitude",
                            "latitude",
                            "lat",
                        )
                    ),
                    "decimal_longitude": self._optional_float(
                        self._first_value(
                            raw,
                            "decimal_longitude",
                            "decimalLongitude",
                            "longitude",
                            "lon",
                            "lng",
                        )
                    ),
                    "coordinate_uncertainty_m": self._optional_float(
                        self._first_value(
                            raw,
                            "coordinate_uncertainty_m",
                            "coordinateUncertaintyInMeters",
                        )
                    ),
                    "geodetic_datum": normalize_space(
                        self._first_value(
                            raw,
                            "geodetic_datum",
                            "geodeticDatum",
                        )
                    ),
                    "minimum_elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "minimum_elevation_m",
                            "minimumElevationInMeters",
                        )
                    ),
                    "maximum_elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "maximum_elevation_m",
                            "maximumElevationInMeters",
                        )
                    ),
                },
                "habitat": {
                    "habitat": normalize_space(
                        self._first_value(raw, "habitat")
                    ),
                    "microhabitat": normalize_space(
                        self._first_value(
                            raw,
                            "microhabitat",
                            "microHabitat",
                        )
                    ),
                    "water_type": normalize_space(
                        self._first_value(
                            raw,
                            "water_type",
                            "waterType",
                        )
                    ),
                    "lotic": self._optional_bool(
                        self._first_value(
                            raw,
                            "lotic",
                            "is_lotic",
                            "isLotic",
                        )
                    ),
                    "lentic": self._optional_bool(
                        self._first_value(
                            raw,
                            "lentic",
                            "is_lentic",
                            "isLentic",
                        )
                    ),
                    "wetland_type": normalize_space(
                        self._first_value(
                            raw,
                            "wetland_type",
                            "wetlandType",
                        )
                    ),
                    "vegetation": normalize_space(
                        self._first_value(raw, "vegetation")
                    ),
                    "weather": normalize_space(
                        self._first_value(raw, "weather")
                    ),
                    "temperature_c": self._optional_float(
                        self._first_value(
                            raw,
                            "temperature_c",
                            "temperatureC",
                        )
                    ),
                },
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
                    "state_status": normalize_space(
                        self._first_value(
                            raw,
                            "state_status",
                            "stateStatus",
                        )
                    ),
                    "national_status": normalize_space(
                        self._first_value(
                            raw,
                            "national_status",
                            "nationalStatus",
                        )
                    ),
                    "population_trend": normalize_space(
                        self._first_value(
                            raw,
                            "population_trend",
                            "populationTrend",
                        )
                    ),
                },
                "media": self._normalize_media(
                    self._first_value(
                        raw,
                        "media",
                        "images",
                        "image",
                        "associated_media",
                        "associatedMedia",
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
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
                        "bibliography",
                    )
                ),
                "rights": {
                    "license": normalize_space(
                        self._first_value(
                            raw,
                            "license",
                            "rights",
                        )
                    ),
                    "rights_holder": normalize_space(
                        self._first_value(
                            raw,
                            "rights_holder",
                            "rightsHolder",
                        )
                    ),
                    "access_rights": normalize_space(
                        self._first_value(
                            raw,
                            "access_rights",
                            "accessRights",
                        )
                    ),
                },
                "notes": self._list_value(
                    self._first_value(
                        raw,
                        "notes",
                        "remarks",
                        "comments",
                        "occurrence_remarks",
                        "occurrenceRemarks",
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
            "kingdom": normalize_space(
                raw.get("kingdom")
            ) or "Animalia",
            "phylum": normalize_space(
                raw.get("phylum")
            ) or "Arthropoda",
            "class": normalize_space(
                raw.get("class")
            ) or "Insecta",
            "order": normalize_space(
                raw.get("order")
            ) or "Odonata",
            "suborder": normalize_space(raw.get("suborder")),
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
        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "synonym_records",
                "synonymRecords",
                "taxonomic_synonyms",
                "taxonomicSynonyms",
            )
        )

        excluded = {
            scientific_name.casefold(),
            canonical_name.casefold(),
        }
        result: list[str] = []
        seen: set[str] = set(excluded)

        for item in values:
            name = normalize_space(
                cls._first_value(
                    item,
                    "scientific_name",
                    "scientificName",
                    "name",
                )
                if isinstance(item, Mapping)
                else item
            )
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
                        "status": cls._normalize_status(
                            cls._first_value(
                                item,
                                "status",
                                "taxonomic_status",
                                "taxonomicStatus",
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
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "id": "",
                            "authorship": "",
                            "status": "synonym",
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
                    "raw": dict(item),
                }
            else:
                entry = {
                    "name": normalize_space(item),
                    "code": "",
                    "status": "",
                    "raw": item,
                }

            if entry["name"] or entry["code"]:
                result.append(entry)

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
                            "observer",
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

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
        *,
        raw: Mapping[str, Any],
    ) -> list[dict[str, str]]:
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
            "odonata_central_id": "OdonataCentral",
            "odonataCentralId": "OdonataCentral",
            "oc_id": "OdonataCentral",
            "ocId": "OdonataCentral",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "col_id": "Catalogue of Life",
            "colId": "Catalogue of Life",
            "iucn_id": "IUCN",
            "iucnId": "IUCN",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "eol_id": "Encyclopedia of Life",
            "eolId": "Encyclopedia of Life",
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
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub tribe": "subtribe",
            "sub order": "suborder",
            "super family": "superfamily",
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
            "present": "accepted",
            "verified": "accepted",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "absent": "inactive",
            "doubtful": "unknown",
            "unresolved": "unknown",
            "unverified": "reference",
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

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid OdonataCentral cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "OdonataCentral cursor must be non-negative."
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
            "present",
            "verified",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "absent",
            "unverified",
        }:
            return False

        return None
