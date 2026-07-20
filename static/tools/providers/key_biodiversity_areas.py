#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/key_biodiversity_areas.py

Key Biodiversity Areas (KBA) provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for KBA site records, site boundaries,
designation criteria, qualifying trigger taxa, ecosystems, countries,
administrative areas, threats, conservation actions, protected-area overlap,
assessment history, references, external identifiers, and provenance metadata.

KBA is principally a site and conservation-priority authority rather than a
taxonomic authority. Records are represented through the shared Speciedex Taxon
contract as reference-oriented KBA entities. Where a record represents a
qualifying trigger taxon, the taxonomic name is used directly. Otherwise, the
KBA site name is used as the reference entity name.

The complete source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "key_biodiversity_areas",
        "path": "static/data/providers/key-biodiversity-areas/records.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Key Biodiversity Areas",
        "source_url": "https://www.keybiodiversityareas.org/",
        "allow_site_records": true
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
    """File-backed Key Biodiversity Areas provider."""

    PROVIDER_NAME = "key_biodiversity_areas"

    DEFAULT_SOURCE_NAME = "Key Biodiversity Areas"
    DEFAULT_SOURCE_URL = "https://www.keybiodiversityareas.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable KBA JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"KBA export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"KBA path is not a file: {source_path}"
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
                            f"Invalid KBA JSON at "
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
        """Resolve the configured KBA JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "Key Biodiversity Areas provider requires a path."
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
        """Normalize one KBA site or trigger-taxon record."""

        site_id = normalize_space(
            self._first_value(
                raw,
                "kba_id",
                "kbaId",
                "site_id",
                "siteId",
                "site_code",
                "siteCode",
                "global_id",
                "globalId",
                "id",
            )
        )

        trigger_taxon_id = normalize_space(
            self._first_value(
                raw,
                "trigger_taxon_id",
                "triggerTaxonId",
                "taxon_id",
                "taxonId",
                "species_id",
                "speciesId",
            )
        )

        provider_id = trigger_taxon_id or site_id

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "trigger_taxon_name",
                "triggerTaxonName",
                "taxon_name",
                "taxonName",
                "species_name",
                "speciesName",
            )
        )

        site_name = normalize_space(
            self._first_value(
                raw,
                "site_name",
                "siteName",
                "kba_name",
                "kbaName",
                "name",
            )
        )

        allow_site_records = bool(
            self.definition.get("allow_site_records", True)
        )

        is_taxonomic = bool(scientific_name)

        if not scientific_name and allow_site_records:
            scientific_name = site_name

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
                self._infer_rank(canonical_name)
                if is_taxonomic
                else "biodiversity_site"
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "kba_status",
                "kbaStatus",
                "assessment_status",
                "assessmentStatus",
                "taxonomic_status",
                "taxonomicStatus",
            ),
            taxonomic=is_taxonomic,
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_name_id",
                "acceptedNameId",
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
                "site_url",
                "siteUrl",
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
                f"{base}/site/factsheet/{site_id}"
                if site_id
                else base
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
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "authority",
                )
            ),
            kingdom=lineage.get("kingdom", ""),
            phylum=lineage.get("phylum", ""),
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
                "programme": "key_biodiversity_areas",
                "reference_only": True,
                "entity_type": (
                    "trigger_taxon"
                    if is_taxonomic
                    else "key_biodiversity_area"
                ),
                "kba_id": site_id,
                "trigger_taxon_id": trigger_taxon_id,
                "lineage": lineage,
                "site": {
                    "name": site_name,
                    "site_code": normalize_space(
                        self._first_value(
                            raw,
                            "site_code",
                            "siteCode",
                            "kba_code",
                            "kbaCode",
                        )
                    ),
                    "global_id": normalize_space(
                        self._first_value(
                            raw,
                            "global_id",
                            "globalId",
                        )
                    ),
                    "regional_id": normalize_space(
                        self._first_value(
                            raw,
                            "regional_id",
                            "regionalId",
                        )
                    ),
                    "national_id": normalize_space(
                        self._first_value(
                            raw,
                            "national_id",
                            "nationalId",
                        )
                    ),
                    "site_type": normalize_space(
                        self._first_value(
                            raw,
                            "site_type",
                            "siteType",
                            "designation_type",
                            "designationType",
                        )
                    ),
                    "designation_status": normalize_space(
                        self._first_value(
                            raw,
                            "designation_status",
                            "designationStatus",
                            "kba_status",
                            "kbaStatus",
                        )
                    ),
                    "designation_date": normalize_space(
                        self._first_value(
                            raw,
                            "designation_date",
                            "designationDate",
                        )
                    ),
                    "description": normalize_space(
                        self._first_value(
                            raw,
                            "description",
                            "site_description",
                            "siteDescription",
                        )
                    ),
                    "area_km2": self._optional_float(
                        self._first_value(
                            raw,
                            "area_km2",
                            "areaKm2",
                            "site_area_km2",
                            "siteAreaKm2",
                            "area",
                        )
                    ),
                    "marine_area_km2": self._optional_float(
                        self._first_value(
                            raw,
                            "marine_area_km2",
                            "marineAreaKm2",
                        )
                    ),
                    "terrestrial_area_km2": self._optional_float(
                        self._first_value(
                            raw,
                            "terrestrial_area_km2",
                            "terrestrialAreaKm2",
                        )
                    ),
                    "freshwater_area_km2": self._optional_float(
                        self._first_value(
                            raw,
                            "freshwater_area_km2",
                            "freshwaterAreaKm2",
                        )
                    ),
                    "transboundary": self._optional_bool(
                        self._first_value(
                            raw,
                            "transboundary",
                            "is_transboundary",
                            "isTransboundary",
                        )
                    ),
                },
                "criteria": self._normalize_criteria(
                    self._first_value(
                        raw,
                        "criteria",
                        "kba_criteria",
                        "kbaCriteria",
                        "qualifying_criteria",
                        "qualifyingCriteria",
                    )
                ),
                "trigger_taxa": self._normalize_trigger_taxa(
                    self._first_value(
                        raw,
                        "trigger_taxa",
                        "triggerTaxa",
                        "qualifying_species",
                        "qualifyingSpecies",
                        "species",
                    )
                ),
                "trigger_ecosystems": self._normalize_ecosystems(
                    self._first_value(
                        raw,
                        "trigger_ecosystems",
                        "triggerEcosystems",
                        "qualifying_ecosystems",
                        "qualifyingEcosystems",
                        "ecosystems",
                    )
                ),
                "geography": {
                    "countries": self._normalize_regions(
                        self._first_value(
                            raw,
                            "countries",
                            "country_records",
                            "countryRecords",
                        )
                    ),
                    "country_codes": self._list_value(
                        self._first_value(
                            raw,
                            "country_codes",
                            "countryCodes",
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
                    "administrative_areas": self._normalize_regions(
                        self._first_value(
                            raw,
                            "administrative_areas",
                            "administrativeAreas",
                            "admin_areas",
                            "adminAreas",
                        )
                    ),
                    "latitude": self._optional_float(
                        self._first_value(
                            raw,
                            "latitude",
                            "lat",
                            "centroid_latitude",
                            "centroidLatitude",
                        )
                    ),
                    "longitude": self._optional_float(
                        self._first_value(
                            raw,
                            "longitude",
                            "lon",
                            "lng",
                            "centroid_longitude",
                            "centroidLongitude",
                        )
                    ),
                    "minimum_elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "minimum_elevation_m",
                            "minimumElevationM",
                            "minimum_elevation",
                            "minimumElevation",
                        )
                    ),
                    "maximum_elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "maximum_elevation_m",
                            "maximumElevationM",
                            "maximum_elevation",
                            "maximumElevation",
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
                "boundary": {
                    "geometry": self._first_value(
                        raw,
                        "geometry",
                        "boundary",
                        "geojson",
                    ),
                    "bbox": self._normalize_bbox(
                        self._first_value(
                            raw,
                            "bbox",
                            "bounding_box",
                            "boundingBox",
                        ),
                        raw=raw,
                    ),
                    "coordinate_reference_system": normalize_space(
                        self._first_value(
                            raw,
                            "coordinate_reference_system",
                            "coordinateReferenceSystem",
                            "crs",
                        )
                    ),
                    "geometry_source": normalize_space(
                        self._first_value(
                            raw,
                            "geometry_source",
                            "geometrySource",
                        )
                    ),
                    "geometry_precision": normalize_space(
                        self._first_value(
                            raw,
                            "geometry_precision",
                            "geometryPrecision",
                        )
                    ),
                },
                "habitats": self._normalize_habitats(
                    self._first_value(
                        raw,
                        "habitats",
                        "habitat",
                        "ecosystem_types",
                        "ecosystemTypes",
                    )
                ),
                "threats": self._normalize_threats(
                    self._first_value(
                        raw,
                        "threats",
                        "threat",
                    )
                ),
                "conservation": {
                    "protected_area_overlap_percent": self._optional_float(
                        self._first_value(
                            raw,
                            "protected_area_overlap_percent",
                            "protectedAreaOverlapPercent",
                        )
                    ),
                    "protected_areas": self._normalize_protected_areas(
                        self._first_value(
                            raw,
                            "protected_areas",
                            "protectedAreas",
                        )
                    ),
                    "conservation_actions": self._normalize_actions(
                        self._first_value(
                            raw,
                            "conservation_actions",
                            "conservationActions",
                            "actions",
                        )
                    ),
                    "management_authority": normalize_space(
                        self._first_value(
                            raw,
                            "management_authority",
                            "managementAuthority",
                        )
                    ),
                    "management_plan": normalize_space(
                        self._first_value(
                            raw,
                            "management_plan",
                            "managementPlan",
                        )
                    ),
                    "conservation_notes": normalize_space(
                        self._first_value(
                            raw,
                            "conservation_notes",
                            "conservationNotes",
                        )
                    ),
                },
                "assessment": {
                    "assessment_id": normalize_space(
                        self._first_value(
                            raw,
                            "assessment_id",
                            "assessmentId",
                        )
                    ),
                    "assessment_status": normalize_space(
                        self._first_value(
                            raw,
                            "assessment_status",
                            "assessmentStatus",
                        )
                    ),
                    "assessment_date": normalize_space(
                        self._first_value(
                            raw,
                            "assessment_date",
                            "assessmentDate",
                        )
                    ),
                    "assessed_by": normalize_space(
                        self._first_value(
                            raw,
                            "assessed_by",
                            "assessedBy",
                        )
                    ),
                    "reviewed_by": normalize_space(
                        self._first_value(
                            raw,
                            "reviewed_by",
                            "reviewedBy",
                        )
                    ),
                    "confirmed_date": normalize_space(
                        self._first_value(
                            raw,
                            "confirmed_date",
                            "confirmedDate",
                        )
                    ),
                    "reassessment_due": normalize_space(
                        self._first_value(
                            raw,
                            "reassessment_due",
                            "reassessmentDue",
                        )
                    ),
                    "methodology_version": normalize_space(
                        self._first_value(
                            raw,
                            "methodology_version",
                            "methodologyVersion",
                            "standard_version",
                            "standardVersion",
                        )
                    ),
                    "assessment_history": self._normalize_assessment_history(
                        self._first_value(
                            raw,
                            "assessment_history",
                            "assessmentHistory",
                            "history",
                        )
                    ),
                },
                "organizations": {
                    "proposer": normalize_space(
                        self._first_value(
                            raw,
                            "proposer",
                            "proposing_organization",
                            "proposingOrganization",
                        )
                    ),
                    "national_coordination_group": normalize_space(
                        self._first_value(
                            raw,
                            "national_coordination_group",
                            "nationalCoordinationGroup",
                        )
                    ),
                    "regional_focal_point": normalize_space(
                        self._first_value(
                            raw,
                            "regional_focal_point",
                            "regionalFocalPoint",
                        )
                    ),
                    "data_provider": normalize_space(
                        self._first_value(
                            raw,
                            "data_provider",
                            "dataProvider",
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
                "documents": self._normalize_documents(
                    self._first_value(
                        raw,
                        "documents",
                        "reports",
                        "attachments",
                    )
                ),
                "media": self._normalize_media(
                    self._first_value(
                        raw,
                        "media",
                        "images",
                        "image",
                        "maps",
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
        """Extract optional trigger-taxon classification."""

        lineage = {
            "kingdom": normalize_space(raw.get("kingdom")),
            "phylum": normalize_space(raw.get("phylum")),
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
        """Extract optional trigger-taxon synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
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
    def _normalize_criteria(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize KBA criteria and subcriteria."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "criterion": normalize_space(
                            cls._first_value(
                                item,
                                "criterion",
                                "code",
                                "name",
                            )
                        ),
                        "subcriterion": normalize_space(
                            cls._first_value(
                                item,
                                "subcriterion",
                                "subCriterion",
                                "sub_code",
                                "subCode",
                            )
                        ),
                        "description": normalize_space(
                            cls._first_value(
                                item,
                                "description",
                                "title",
                            )
                        ),
                        "threshold": cls._first_value(
                            item,
                            "threshold",
                        ),
                        "measured_value": cls._first_value(
                            item,
                            "measured_value",
                            "measuredValue",
                            "value",
                        ),
                        "units": normalize_space(
                            cls._first_value(
                                item,
                                "units",
                                "unit",
                            )
                        ),
                        "met": cls._optional_bool(
                            cls._first_value(
                                item,
                                "met",
                                "is_met",
                                "isMet",
                                "qualifies",
                            )
                        ),
                        "trigger_id": normalize_space(
                            cls._first_value(
                                item,
                                "trigger_id",
                                "triggerId",
                                "taxon_id",
                                "taxonId",
                                "ecosystem_id",
                                "ecosystemId",
                            )
                        ),
                        "notes": normalize_space(
                            cls._first_value(
                                item,
                                "notes",
                                "remarks",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                criterion = normalize_space(item)

                if criterion:
                    result.append(
                        {
                            "criterion": criterion,
                            "subcriterion": "",
                            "description": "",
                            "threshold": None,
                            "measured_value": None,
                            "units": "",
                            "met": None,
                            "trigger_id": "",
                            "notes": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_trigger_taxa(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize qualifying trigger taxa."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "scientific_name": normalize_space(
                            cls._first_value(
                                item,
                                "scientific_name",
                                "scientificName",
                                "name",
                                "taxon_name",
                                "taxonName",
                            )
                        ),
                        "taxon_id": normalize_space(
                            cls._first_value(
                                item,
                                "taxon_id",
                                "taxonId",
                                "species_id",
                                "speciesId",
                                "id",
                            )
                        ),
                        "rank": cls._normalize_rank(
                            cls._first_value(
                                item,
                                "rank",
                                "taxon_rank",
                                "taxonRank",
                            )
                        ),
                        "kingdom": normalize_space(
                            cls._first_value(item, "kingdom")
                        ),
                        "criterion": normalize_space(
                            cls._first_value(
                                item,
                                "criterion",
                                "criteria",
                            )
                        ),
                        "global_population_percent": cls._optional_float(
                            cls._first_value(
                                item,
                                "global_population_percent",
                                "globalPopulationPercent",
                            )
                        ),
                        "site_population": cls._first_value(
                            item,
                            "site_population",
                            "sitePopulation",
                        ),
                        "population_units": normalize_space(
                            cls._first_value(
                                item,
                                "population_units",
                                "populationUnits",
                            )
                        ),
                        "iucn_status": normalize_space(
                            cls._first_value(
                                item,
                                "iucn_status",
                                "iucnStatus",
                            )
                        ),
                        "endemic": cls._optional_bool(
                            cls._first_value(
                                item,
                                "endemic",
                                "is_endemic",
                                "isEndemic",
                            )
                        ),
                        "seasonal_status": normalize_space(
                            cls._first_value(
                                item,
                                "seasonal_status",
                                "seasonalStatus",
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
                            "scientific_name": name,
                            "taxon_id": "",
                            "rank": cls._infer_rank(name),
                            "kingdom": "",
                            "criterion": "",
                            "global_population_percent": None,
                            "site_population": None,
                            "population_units": "",
                            "iucn_status": "",
                            "endemic": None,
                            "seasonal_status": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_ecosystems(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize qualifying ecosystem records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "ecosystem_name",
                                "ecosystemName",
                            )
                        ),
                        "ecosystem_id": normalize_space(
                            cls._first_value(
                                item,
                                "ecosystem_id",
                                "ecosystemId",
                                "id",
                            )
                        ),
                        "classification": normalize_space(
                            cls._first_value(
                                item,
                                "classification",
                                "type",
                            )
                        ),
                        "criterion": normalize_space(
                            cls._first_value(
                                item,
                                "criterion",
                                "criteria",
                            )
                        ),
                        "extent_km2": cls._optional_float(
                            cls._first_value(
                                item,
                                "extent_km2",
                                "extentKm2",
                                "area_km2",
                                "areaKm2",
                            )
                        ),
                        "global_extent_percent": cls._optional_float(
                            cls._first_value(
                                item,
                                "global_extent_percent",
                                "globalExtentPercent",
                            )
                        ),
                        "threat_status": normalize_space(
                            cls._first_value(
                                item,
                                "threat_status",
                                "threatStatus",
                                "red_list_status",
                                "redListStatus",
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
                            "ecosystem_id": "",
                            "classification": "",
                            "criterion": "",
                            "extent_km2": None,
                            "global_extent_percent": None,
                            "threat_status": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_regions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize country, region, and administrative-area records."""

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
                            "iso_code",
                            "isoCode",
                        )
                    ),
                    "level": normalize_space(
                        cls._first_value(
                            item,
                            "level",
                            "admin_level",
                            "adminLevel",
                            "type",
                        )
                    ),
                    "geoname_id": normalize_space(
                        cls._first_value(
                            item,
                            "geoname_id",
                            "geonameId",
                        )
                    ),
                    "raw": dict(item),
                }
            else:
                entry = {
                    "name": normalize_space(item),
                    "code": "",
                    "level": "",
                    "geoname_id": "",
                    "raw": item,
                }

            if entry["name"] or entry["code"]:
                result.append(entry)

        return result

    @classmethod
    def _normalize_bbox(
        cls,
        value: Any,
        *,
        raw: Mapping[str, Any],
    ) -> dict[str, float | None]:
        """Normalize a geographic bounding box."""

        if isinstance(value, Mapping):
            north = cls._optional_float(
                cls._first_value(value, "north", "max_lat", "maxLat")
            )
            south = cls._optional_float(
                cls._first_value(value, "south", "min_lat", "minLat")
            )
            east = cls._optional_float(
                cls._first_value(value, "east", "max_lon", "maxLon")
            )
            west = cls._optional_float(
                cls._first_value(value, "west", "min_lon", "minLon")
            )
        else:
            north = cls._optional_float(
                cls._first_value(raw, "north", "bbox_north", "bboxNorth")
            )
            south = cls._optional_float(
                cls._first_value(raw, "south", "bbox_south", "bboxSouth")
            )
            east = cls._optional_float(
                cls._first_value(raw, "east", "bbox_east", "bboxEast")
            )
            west = cls._optional_float(
                cls._first_value(raw, "west", "bbox_west", "bboxWest")
            )

        return {
            "north": north,
            "south": south,
            "east": east,
            "west": west,
        }

    @classmethod
    def _normalize_habitats(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize habitat records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "habitat",
                                "type",
                            )
                        ),
                        "code": normalize_space(
                            cls._first_value(
                                item,
                                "code",
                                "habitat_code",
                                "habitatCode",
                            )
                        ),
                        "extent_km2": cls._optional_float(
                            cls._first_value(
                                item,
                                "extent_km2",
                                "extentKm2",
                                "area_km2",
                                "areaKm2",
                            )
                        ),
                        "condition": normalize_space(
                            cls._first_value(
                                item,
                                "condition",
                                "status",
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
                            "code": "",
                            "extent_km2": None,
                            "condition": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_threats(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize threats to a KBA site or trigger."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "threat",
                                "title",
                            )
                        ),
                        "category": normalize_space(
                            cls._first_value(
                                item,
                                "category",
                                "type",
                                "code",
                            )
                        ),
                        "scope": normalize_space(
                            cls._first_value(item, "scope")
                        ),
                        "severity": normalize_space(
                            cls._first_value(item, "severity")
                        ),
                        "timing": normalize_space(
                            cls._first_value(item, "timing")
                        ),
                        "trend": normalize_space(
                            cls._first_value(item, "trend")
                        ),
                        "description": normalize_space(
                            cls._first_value(
                                item,
                                "description",
                                "notes",
                                "remarks",
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
                            "category": "",
                            "scope": "",
                            "severity": "",
                            "timing": "",
                            "trend": "",
                            "description": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_protected_areas(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize protected-area overlap records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "protected_area_name",
                                "protectedAreaName",
                            )
                        ),
                        "wdpa_id": normalize_space(
                            cls._first_value(
                                item,
                                "wdpa_id",
                                "wdpaId",
                                "id",
                            )
                        ),
                        "designation": normalize_space(
                            cls._first_value(
                                item,
                                "designation",
                                "type",
                            )
                        ),
                        "iucn_category": normalize_space(
                            cls._first_value(
                                item,
                                "iucn_category",
                                "iucnCategory",
                            )
                        ),
                        "overlap_percent": cls._optional_float(
                            cls._first_value(
                                item,
                                "overlap_percent",
                                "overlapPercent",
                            )
                        ),
                        "overlap_km2": cls._optional_float(
                            cls._first_value(
                                item,
                                "overlap_km2",
                                "overlapKm2",
                            )
                        ),
                        "raw": dict(item),
                    }
                )

        return result

    @classmethod
    def _normalize_actions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize conservation actions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "action",
                                "title",
                            )
                        ),
                        "category": normalize_space(
                            cls._first_value(
                                item,
                                "category",
                                "type",
                            )
                        ),
                        "status": normalize_space(
                            cls._first_value(item, "status")
                        ),
                        "responsible_party": normalize_space(
                            cls._first_value(
                                item,
                                "responsible_party",
                                "responsibleParty",
                                "organization",
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
                            "category": "",
                            "status": "",
                            "responsible_party": "",
                            "start_date": "",
                            "end_date": "",
                            "description": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_assessment_history(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize KBA assessment and reassessment history."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "assessment_id": normalize_space(
                            cls._first_value(
                                item,
                                "assessment_id",
                                "assessmentId",
                                "id",
                            )
                        ),
                        "status": normalize_space(
                            cls._first_value(item, "status")
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "assessment_date",
                                "assessmentDate",
                            )
                        ),
                        "assessed_by": normalize_space(
                            cls._first_value(
                                item,
                                "assessed_by",
                                "assessedBy",
                            )
                        ),
                        "reviewed_by": normalize_space(
                            cls._first_value(
                                item,
                                "reviewed_by",
                                "reviewedBy",
                            )
                        ),
                        "methodology_version": normalize_space(
                            cls._first_value(
                                item,
                                "methodology_version",
                                "methodologyVersion",
                            )
                        ),
                        "notes": normalize_space(
                            cls._first_value(
                                item,
                                "notes",
                                "remarks",
                            )
                        ),
                        "raw": dict(item),
                    }
                )

        return result

    @classmethod
    def _normalize_documents(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize supporting documents and reports."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "title": normalize_space(
                            cls._first_value(
                                item,
                                "title",
                                "name",
                            )
                        ),
                        "type": normalize_space(
                            cls._first_value(
                                item,
                                "type",
                                "document_type",
                                "documentType",
                            )
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "publication_date",
                                "publicationDate",
                            )
                        ),
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "href",
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
                )
            else:
                url = normalize_space(item)

                if url:
                    result.append(
                        {
                            "title": "",
                            "type": "",
                            "date": "",
                            "url": url,
                            "license": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_media(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize KBA site imagery and maps."""

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

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
        *,
        raw: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        """Normalize KBA and external identifiers."""

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
            "kba_id": "Key Biodiversity Areas",
            "kbaId": "Key Biodiversity Areas",
            "site_code": "Key Biodiversity Areas",
            "siteCode": "Key Biodiversity Areas",
            "wdpa_id": "Protected Planet WDPA",
            "wdpaId": "Protected Planet WDPA",
            "iba_id": "Important Bird and Biodiversity Areas",
            "ibaId": "Important Bird and Biodiversity Areas",
            "aze_id": "Alliance for Zero Extinction",
            "azeId": "Alliance for Zero Extinction",
            "iucn_id": "IUCN",
            "iucnId": "IUCN",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "birdlife_id": "BirdLife International",
            "birdlifeId": "BirdLife International",
            "geoname_id": "GeoNames",
            "geonameId": "GeoNames",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
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
        """Normalize KBA scientific and assessment references."""

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
        """Normalize trigger-taxon ranks."""

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
            "sub order": "suborder",
            "sub class": "subclass",
            "sub phylum": "subphylum",
            "var.": "variety",
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
    def _normalize_status(
        value: Any,
        *,
        taxonomic: bool,
    ) -> str:
        """Normalize KBA assessment or taxonomic status."""

        status = normalize_space(value).casefold()

        aliases = {
            "confirmed": "accepted",
            "validated": "accepted",
            "approved": "accepted",
            "designated": "accepted",
            "proposed": "provisionally accepted",
            "candidate": "provisionally accepted",
            "in review": "provisionally accepted",
            "reassessment needed": "unknown",
            "deconfirmed": "inactive",
            "retired": "inactive",
            "inactive": "inactive",
            "accepted": "accepted",
            "valid": "valid",
            "current": "accepted",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "doubtful": "unknown",
            "unresolved": "unknown",
            "reference": "reference",
        }

        if status:
            return aliases.get(status, status)

        return "unknown" if taxonomic else "reference"

    @staticmethod
    def _infer_rank(scientific_name: str) -> str:
        """Infer trigger-taxon rank from a scientific name."""

        words = normalize_space(scientific_name).split()
        lowered = {word.casefold() for word in words}

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if "var." in lowered or "variety" in lowered:
            return "variety"

        if "f." in lowered or "forma" in lowered:
            return "form"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "infraspecific"

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
                f"Invalid KBA cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "KBA cursor must be non-negative."
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
            "confirmed",
            "approved",
            "designated",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "absent",
            "inactive",
            "retired",
        }:
            return False

        return None
