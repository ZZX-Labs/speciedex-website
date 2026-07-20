#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/bacdive.py

BacDive provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for bacterial and archaeal strain-level
taxonomy, phenotype, physiology, culture-collection, ecology, and sequencing
metadata.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete BacDive source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "bacdive",
        "path": "static/data/providers/bacdive/strains.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "BacDive",
        "source_url": "https://bacdive.dsmz.de/"
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
    """File-backed BacDive provider."""

    PROVIDER_NAME = "bacdive"

    DEFAULT_SOURCE_NAME = "BacDive"
    DEFAULT_SOURCE_URL = "https://bacdive.dsmz.de/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable BacDive JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"BacDive export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"BacDive path is not a file: {source_path}"
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
                            f"Invalid BacDive JSON at "
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
        """Resolve the configured BacDive JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "BacDive provider requires a path."
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
        """Normalize one BacDive strain record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "bacdive_id",
                "bacdiveId",
                "bacdive_id_number",
                "bacdiveIdNumber",
                "strain_id",
                "strainId",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "species_name",
                "speciesName",
                "organism_name",
                "organismName",
                "name",
            )
        )

        strain_designation = normalize_space(
            self._first_value(
                raw,
                "strain_designation",
                "strainDesignation",
                "strain",
                "strain_name",
                "strainName",
            )
        )

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "species_name",
                "speciesName",
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
            rank = "species"

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "strain_status",
                "strainStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_species_id",
                "acceptedSpeciesId",
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
                + "/strain/"
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
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "authority",
                )
            ),
            kingdom=lineage.get(
                "kingdom",
                lineage.get("domain", ""),
            ),
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
                "programme": "bacdive",
                "reference_only": True,
                "bacdive_id": provider_id,
                "accepted_taxon_id": accepted_provider_id,
                "lineage": lineage,
                "strain": {
                    "designation": strain_designation,
                    "type_strain": self._optional_bool(
                        self._first_value(
                            raw,
                            "type_strain",
                            "typeStrain",
                            "is_type_strain",
                            "isTypeStrain",
                        )
                    ),
                    "strain_number": normalize_space(
                        self._first_value(
                            raw,
                            "strain_number",
                            "strainNumber",
                        )
                    ),
                    "culture_collection_numbers": self._normalize_collection_numbers(
                        self._first_value(
                            raw,
                            "culture_collection_numbers",
                            "cultureCollectionNumbers",
                            "culture_collections",
                            "cultureCollections",
                            "strain_numbers",
                            "strainNumbers",
                        )
                    ),
                    "depositors": self._normalize_people(
                        self._first_value(
                            raw,
                            "depositors",
                            "depositor",
                        )
                    ),
                    "history": normalize_space(
                        self._first_value(
                            raw,
                            "strain_history",
                            "strainHistory",
                            "history",
                        )
                    ),
                },
                "taxonomy": {
                    "domain": lineage.get("domain", ""),
                    "subspecies": normalize_space(
                        self._first_value(
                            raw,
                            "subspecies",
                            "subspecies_name",
                            "subspeciesName",
                        )
                    ),
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "comments",
                        )
                    ),
                    "lpsn_status": normalize_space(
                        self._first_value(
                            raw,
                            "lpsn_status",
                            "lpsnStatus",
                        )
                    ),
                    "nomenclatural_status": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_status",
                            "nomenclaturalStatus",
                        )
                    ),
                    "nomenclatural_code": "ICNP",
                },
                "isolation": {
                    "source": normalize_space(
                        self._first_value(
                            raw,
                            "isolation_source",
                            "isolationSource",
                            "source_material",
                            "sourceMaterial",
                        )
                    ),
                    "host": normalize_space(
                        self._first_value(
                            raw,
                            "host",
                            "host_name",
                            "hostName",
                        )
                    ),
                    "host_taxon_id": normalize_space(
                        self._first_value(
                            raw,
                            "host_taxon_id",
                            "hostTaxonId",
                        )
                    ),
                    "sample_type": normalize_space(
                        self._first_value(
                            raw,
                            "sample_type",
                            "sampleType",
                        )
                    ),
                    "isolation_date": normalize_space(
                        self._first_value(
                            raw,
                            "isolation_date",
                            "isolationDate",
                        )
                    ),
                    "country": normalize_space(
                        self._first_value(
                            raw,
                            "country",
                            "isolation_country",
                            "isolationCountry",
                        )
                    ),
                    "locality": normalize_space(
                        self._first_value(
                            raw,
                            "locality",
                            "isolation_locality",
                            "isolationLocality",
                        )
                    ),
                    "latitude": self._optional_float(
                        self._first_value(
                            raw,
                            "latitude",
                            "lat",
                        )
                    ),
                    "longitude": self._optional_float(
                        self._first_value(
                            raw,
                            "longitude",
                            "lon",
                            "lng",
                        )
                    ),
                    "depth_m": self._optional_float(
                        self._first_value(
                            raw,
                            "depth_m",
                            "depthM",
                            "depth",
                        )
                    ),
                    "elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "elevation_m",
                            "elevationM",
                            "elevation",
                        )
                    ),
                    "environment": self._list_value(
                        self._first_value(
                            raw,
                            "environment",
                            "environments",
                            "habitat",
                            "habitats",
                        )
                    ),
                },
                "morphology": {
                    "cell_shape": self._list_value(
                        self._first_value(
                            raw,
                            "cell_shape",
                            "cellShape",
                            "morphology",
                        )
                    ),
                    "cell_length_um": self._normalize_range(
                        self._first_value(
                            raw,
                            "cell_length_um",
                            "cellLengthUm",
                            "cell_length",
                            "cellLength",
                        )
                    ),
                    "cell_width_um": self._normalize_range(
                        self._first_value(
                            raw,
                            "cell_width_um",
                            "cellWidthUm",
                            "cell_width",
                            "cellWidth",
                        )
                    ),
                    "gram_stain": normalize_space(
                        self._first_value(
                            raw,
                            "gram_stain",
                            "gramStain",
                            "gram_status",
                            "gramStatus",
                        )
                    ),
                    "motility": self._optional_bool(
                        self._first_value(
                            raw,
                            "motility",
                            "motile",
                            "is_motile",
                            "isMotile",
                        )
                    ),
                    "flagella": normalize_space(
                        self._first_value(
                            raw,
                            "flagella",
                            "flagellation",
                        )
                    ),
                    "spore_formation": self._optional_bool(
                        self._first_value(
                            raw,
                            "spore_formation",
                            "sporeFormation",
                            "spore_forming",
                            "sporeForming",
                        )
                    ),
                    "pigmentation": normalize_space(
                        self._first_value(
                            raw,
                            "pigmentation",
                            "pigment",
                        )
                    ),
                    "colony_morphology": normalize_space(
                        self._first_value(
                            raw,
                            "colony_morphology",
                            "colonyMorphology",
                        )
                    ),
                },
                "growth": {
                    "temperature": self._normalize_condition(
                        self._first_value(
                            raw,
                            "temperature",
                            "growth_temperature",
                            "growthTemperature",
                        ),
                        unit="°C",
                    ),
                    "ph": self._normalize_condition(
                        self._first_value(
                            raw,
                            "ph",
                            "pH",
                            "growth_ph",
                            "growthPh",
                        ),
                        unit="pH",
                    ),
                    "salinity": self._normalize_condition(
                        self._first_value(
                            raw,
                            "salinity",
                            "growth_salinity",
                            "growthSalinity",
                        ),
                        unit="%",
                    ),
                    "oxygen_tolerance": normalize_space(
                        self._first_value(
                            raw,
                            "oxygen_tolerance",
                            "oxygenTolerance",
                            "oxygen_requirement",
                            "oxygenRequirement",
                        )
                    ),
                    "growth_media": self._list_value(
                        self._first_value(
                            raw,
                            "growth_media",
                            "growthMedia",
                            "media",
                        )
                    ),
                    "doubling_time": normalize_space(
                        self._first_value(
                            raw,
                            "doubling_time",
                            "doublingTime",
                        )
                    ),
                },
                "metabolism": {
                    "metabolic_type": self._list_value(
                        self._first_value(
                            raw,
                            "metabolic_type",
                            "metabolicType",
                            "metabolism",
                        )
                    ),
                    "carbon_sources": self._normalize_test_results(
                        self._first_value(
                            raw,
                            "carbon_sources",
                            "carbonSources",
                        )
                    ),
                    "substrate_utilization": self._normalize_test_results(
                        self._first_value(
                            raw,
                            "substrate_utilization",
                            "substrateUtilization",
                        )
                    ),
                    "enzyme_activities": self._normalize_test_results(
                        self._first_value(
                            raw,
                            "enzyme_activities",
                            "enzymeActivities",
                        )
                    ),
                    "fermentation_products": self._list_value(
                        self._first_value(
                            raw,
                            "fermentation_products",
                            "fermentationProducts",
                        )
                    ),
                    "electron_acceptors": self._list_value(
                        self._first_value(
                            raw,
                            "electron_acceptors",
                            "electronAcceptors",
                        )
                    ),
                },
                "chemotaxonomy": {
                    "fatty_acids": self._normalize_composition(
                        self._first_value(
                            raw,
                            "fatty_acids",
                            "fattyAcids",
                        )
                    ),
                    "quinones": self._normalize_composition(
                        self._first_value(
                            raw,
                            "quinones",
                        )
                    ),
                    "polar_lipids": self._normalize_composition(
                        self._first_value(
                            raw,
                            "polar_lipids",
                            "polarLipids",
                        )
                    ),
                    "cell_wall": normalize_space(
                        self._first_value(
                            raw,
                            "cell_wall",
                            "cellWall",
                            "peptidoglycan",
                        )
                    ),
                },
                "pathogenicity": {
                    "pathogenic": self._optional_bool(
                        self._first_value(
                            raw,
                            "pathogenic",
                            "is_pathogenic",
                            "isPathogenic",
                        )
                    ),
                    "biosafety_level": normalize_space(
                        self._first_value(
                            raw,
                            "biosafety_level",
                            "biosafetyLevel",
                            "risk_group",
                            "riskGroup",
                        )
                    ),
                    "hosts": self._list_value(
                        self._first_value(
                            raw,
                            "pathogenic_hosts",
                            "pathogenicHosts",
                            "hosts",
                        )
                    ),
                    "diseases": self._list_value(
                        self._first_value(
                            raw,
                            "diseases",
                            "disease",
                        )
                    ),
                    "virulence_factors": self._list_value(
                        self._first_value(
                            raw,
                            "virulence_factors",
                            "virulenceFactors",
                        )
                    ),
                    "antibiotic_resistance": self._normalize_test_results(
                        self._first_value(
                            raw,
                            "antibiotic_resistance",
                            "antibioticResistance",
                        )
                    ),
                },
                "sequence": {
                    "genome_accessions": self._normalize_identifier_list(
                        self._first_value(
                            raw,
                            "genome_accessions",
                            "genomeAccessions",
                            "genome_accession",
                            "genomeAccession",
                        )
                    ),
                    "sequence_accessions": self._normalize_identifier_list(
                        self._first_value(
                            raw,
                            "sequence_accessions",
                            "sequenceAccessions",
                            "accessions",
                        )
                    ),
                    "sixteen_s_accession": normalize_space(
                        self._first_value(
                            raw,
                            "16s_accession",
                            "16sAccession",
                            "ssu_accession",
                            "ssuAccession",
                        )
                    ),
                    "genome_size_bp": self._optional_int(
                        self._first_value(
                            raw,
                            "genome_size_bp",
                            "genomeSizeBp",
                            "genome_size",
                            "genomeSize",
                        )
                    ),
                    "gc_content_percent": self._optional_float(
                        self._first_value(
                            raw,
                            "gc_content_percent",
                            "gcContentPercent",
                            "gc_content",
                            "gcContent",
                        )
                    ),
                    "plasmids": self._list_value(
                        self._first_value(
                            raw,
                            "plasmids",
                            "plasmid",
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
                        "publications",
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
        """Extract bacterial or archaeal lineage."""

        lineage = {
            "domain": normalize_space(
                cls._first_value(
                    raw,
                    "domain",
                    "superkingdom",
                )
            ),
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

        if not lineage.get("kingdom") and lineage.get("domain"):
            lineage["kingdom"] = lineage["domain"]

        return lineage

    @classmethod
    def _extract_synonyms(
        cls,
        raw: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """Extract and deduplicate microbial name synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
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
                value = normalize_space(
                    cls._first_value(
                        item,
                        "scientific_name",
                        "scientificName",
                        "name",
                    )
                )
            else:
                value = normalize_space(item)

            key = value.casefold()

            if not value or key in seen:
                continue

            seen.add(key)
            result.append(value)

        return result

    @classmethod
    def _normalize_collection_numbers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize culture collection identifiers."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                collection = normalize_space(
                    cls._first_value(
                        item,
                        "collection",
                        "institution",
                        "source",
                    )
                )
                identifier = normalize_space(
                    cls._first_value(
                        item,
                        "identifier",
                        "number",
                        "id",
                        "value",
                    )
                )
            else:
                text = normalize_space(item)
                collection, separator, identifier = text.partition(" ")
                if not separator:
                    collection = ""
                    identifier = text

            if identifier:
                result.append(
                    {
                        "collection": collection,
                        "identifier": identifier,
                    }
                )

        return result

    @classmethod
    def _normalize_people(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize depositor and contributor records."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "full_name",
                        "fullName",
                    )
                )
                institution = normalize_space(
                    cls._first_value(
                        item,
                        "institution",
                        "organization",
                    )
                )
            else:
                name = normalize_space(item)
                institution = ""

            if name:
                result.append(
                    {
                        "name": name,
                        "institution": institution,
                    }
                )

        return result

    @classmethod
    def _normalize_range(
        cls,
        value: Any,
    ) -> dict[str, float | None]:
        """Normalize numeric ranges."""

        if isinstance(value, Mapping):
            minimum = cls._optional_float(
                cls._first_value(
                    value,
                    "min",
                    "minimum",
                    "from",
                )
            )
            maximum = cls._optional_float(
                cls._first_value(
                    value,
                    "max",
                    "maximum",
                    "to",
                )
            )
            optimum = cls._optional_float(
                cls._first_value(
                    value,
                    "optimum",
                    "opt",
                )
            )
        else:
            parsed = cls._optional_float(value)
            minimum = parsed
            maximum = parsed
            optimum = None

        return {
            "minimum": minimum,
            "maximum": maximum,
            "optimum": optimum,
        }

    @classmethod
    def _normalize_condition(
        cls,
        value: Any,
        *,
        unit: str,
    ) -> dict[str, Any]:
        """Normalize growth-condition ranges and optima."""

        result = cls._normalize_range(value)
        result["unit"] = unit
        return result

    @classmethod
    def _normalize_test_results(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize biochemical, substrate, and susceptibility tests."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "test": normalize_space(
                            cls._first_value(
                                item,
                                "test",
                                "name",
                                "compound",
                                "substrate",
                            )
                        ),
                        "result": cls._first_value(
                            item,
                            "result",
                            "value",
                            "status",
                        ),
                        "method": normalize_space(
                            cls._first_value(
                                item,
                                "method",
                                "protocol",
                            )
                        ),
                        "conditions": cls._first_value(
                            item,
                            "conditions",
                            "condition",
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                text = normalize_space(item)

                if text:
                    result.append(
                        {
                            "test": text,
                            "result": None,
                            "method": "",
                            "conditions": None,
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_composition(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize chemotaxonomic composition data."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "compound",
                                "component",
                            )
                        ),
                        "amount": cls._optional_float(
                            cls._first_value(
                                item,
                                "amount",
                                "value",
                                "percentage",
                            )
                        ),
                        "unit": normalize_space(
                            cls._first_value(
                                item,
                                "unit",
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
                            "amount": None,
                            "unit": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_identifier_list(
        cls,
        value: Any,
    ) -> list[str]:
        """Normalize accession lists."""

        values = cls._list_value(value)
        result: list[str] = []
        seen: set[str] = set()

        for item in values:
            if isinstance(item, str) and any(
                separator in item
                for separator in (",", ";")
            ):
                parts = item.replace(";", ",").split(",")
            else:
                parts = [item]

            for part in parts:
                normalized = normalize_space(part)
                key = normalized.casefold()

                if not normalized or key in seen:
                    continue

                seen.add(key)
                result.append(normalized)

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
        *,
        raw: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        """Normalize BacDive and external identifiers."""

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
            "dsm_number": "DSMZ",
            "dsmNumber": "DSMZ",
            "lpsn_id": "LPSN",
            "lpsnId": "LPSN",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "gtdb_id": "GTDB",
            "gtdbId": "GTDB",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
        }

        seen = {
            (
                item["source"].casefold(),
                item["identifier"].casefold(),
            )
            for item in result
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
        """Normalize BacDive references."""

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
                        "pubmed_id": normalize_space(
                            cls._first_value(
                                item,
                                "pubmed_id",
                                "pubmedId",
                                "pmid",
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
                            "authors": "",
                            "year": "",
                            "doi": "",
                            "pubmed_id": "",
                            "url": "",
                        }
                    )

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize microbial taxonomic ranks."""

        rank = normalize_space(value).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "super kingdom": "domain",
            "superkingdom": "domain",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub order": "suborder",
            "sub class": "subclass",
            "sub phylum": "subphylum",
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
        """Normalize BacDive taxonomic and strain status values."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "correct name": "accepted",
            "synonym": "synonym",
            "heterotypic synonym": "synonym",
            "homotypic synonym": "synonym",
            "proposed": "provisionally accepted",
            "candidatus": "provisionally accepted",
            "unclassified": "unknown",
            "uncultured": "reference",
            "type strain": "reference",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        """Decode a non-negative JSONL offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid BacDive cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "BacDive cursor must be non-negative."
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
            "positive",
            "+",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "negative",
            "-",
        }:
            return False

        return None
