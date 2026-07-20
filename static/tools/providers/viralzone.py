#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/viralzone.py

ViralZone provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for ViralZone virus taxonomy, host
range, genome architecture, virion structure, replication biology, disease
associations, transmission, molecular features, protein and proteome links,
references, external identifiers, and provenance metadata.

ViralZone is principally a curated virology knowledge resource rather than the
formal authority for virus nomenclature. ICTV remains the primary authority
for accepted virus taxonomy. ViralZone records are therefore represented as
reference-oriented taxonomic entities while retaining accepted-name and ICTV
relationships when available.

The complete source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "viralzone",
        "path": "static/data/providers/viralzone/records.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "ViralZone",
        "source_url": "https://viralzone.expasy.org/"
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
    """File-backed ViralZone provider."""

    PROVIDER_NAME = "viralzone"

    DEFAULT_SOURCE_NAME = "ViralZone"
    DEFAULT_SOURCE_URL = "https://viralzone.expasy.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable ViralZone JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"ViralZone export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"ViralZone path is not a file: {source_path}"
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
                            f"Invalid ViralZone JSON at "
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
        """Resolve the configured ViralZone JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "ViralZone provider requires a path."
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
        """Normalize one ViralZone virus or virology record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "viralzone_id",
                "viralZoneId",
                "viralzoneId",
                "entry_id",
                "entryId",
                "taxon_id",
                "taxonId",
                "ncbi_taxid",
                "ncbiTaxid",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "virus_name",
                "virusName",
                "taxon_name",
                "taxonName",
                "entry_name",
                "entryName",
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
                "accepted_name",
                "acceptedName",
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
                "ictv_rank",
                "ictvRank",
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
                "ictv_status",
                "ictvStatus",
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
                "ictv_taxon_id",
                "ictvTaxonId",
                "current_taxon_id",
                "currentTaxonId",
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
                "entry_url",
                "entryUrl",
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
            source_url = f"{base}/{provider_id}"

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
                    "authority",
                    "taxon_authority",
                    "taxonAuthority",
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
                "programme": "viralzone",
                "reference_only": True,
                "entity_type": "virus_reference",
                "viralzone_id": provider_id,
                "accepted_taxon_id": accepted_provider_id,
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
                "names": {
                    "scientific_name": scientific_name,
                    "common_name": normalize_space(
                        self._first_value(
                            raw,
                            "common_name",
                            "commonName",
                        )
                    ),
                    "abbreviation": normalize_space(
                        self._first_value(
                            raw,
                            "abbreviation",
                            "abbrev",
                            "short_name",
                            "shortName",
                        )
                    ),
                    "acronyms": self._list_value(
                        self._first_value(
                            raw,
                            "acronyms",
                            "acronym",
                        )
                    ),
                    "alternative_names": self._normalize_names(
                        self._first_value(
                            raw,
                            "alternative_names",
                            "alternativeNames",
                            "other_names",
                            "otherNames",
                            "synonyms",
                        )
                    ),
                },
                "taxonomy": {
                    "realm": lineage.get("realm", ""),
                    "subrealm": lineage.get("subrealm", ""),
                    "kingdom": lineage.get("kingdom", ""),
                    "subkingdom": lineage.get("subkingdom", ""),
                    "phylum": lineage.get("phylum", ""),
                    "subphylum": lineage.get("subphylum", ""),
                    "class": lineage.get("class", ""),
                    "subclass": lineage.get("subclass", ""),
                    "order": lineage.get("order", ""),
                    "suborder": lineage.get("suborder", ""),
                    "family": lineage.get("family", ""),
                    "subfamily": lineage.get("subfamily", ""),
                    "genus": lineage.get("genus", ""),
                    "subgenus": lineage.get("subgenus", ""),
                    "species": lineage.get("species", ""),
                    "ictv_status": normalize_space(
                        self._first_value(
                            raw,
                            "ictv_status",
                            "ictvStatus",
                        )
                    ),
                    "ictv_release": normalize_space(
                        self._first_value(
                            raw,
                            "ictv_release",
                            "ictvRelease",
                            "ictv_version",
                            "ictvVersion",
                        )
                    ),
                    "baltimore_group": normalize_space(
                        self._first_value(
                            raw,
                            "baltimore_group",
                            "baltimoreGroup",
                        )
                    ),
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
                "genome": {
                    "nucleic_acid": normalize_space(
                        self._first_value(
                            raw,
                            "nucleic_acid",
                            "nucleicAcid",
                            "genome_type",
                            "genomeType",
                        )
                    ),
                    "strandedness": normalize_space(
                        self._first_value(
                            raw,
                            "strandedness",
                            "strand",
                        )
                    ),
                    "sense": normalize_space(
                        self._first_value(
                            raw,
                            "sense",
                            "genome_sense",
                            "genomeSense",
                        )
                    ),
                    "segmented": self._optional_bool(
                        self._first_value(
                            raw,
                            "segmented",
                            "is_segmented",
                            "isSegmented",
                        )
                    ),
                    "segment_count": self._optional_int(
                        self._first_value(
                            raw,
                            "segment_count",
                            "segmentCount",
                            "segments",
                        )
                    ),
                    "circular": self._optional_bool(
                        self._first_value(
                            raw,
                            "circular",
                            "is_circular",
                            "isCircular",
                        )
                    ),
                    "linear": self._optional_bool(
                        self._first_value(
                            raw,
                            "linear",
                            "is_linear",
                            "isLinear",
                        )
                    ),
                    "genome_size": self._first_value(
                        raw,
                        "genome_size",
                        "genomeSize",
                    ),
                    "genome_size_unit": normalize_space(
                        self._first_value(
                            raw,
                            "genome_size_unit",
                            "genomeSizeUnit",
                        )
                    ),
                    "genome_accessions": self._normalize_accessions(
                        self._first_value(
                            raw,
                            "genome_accessions",
                            "genomeAccessions",
                            "genbank_accessions",
                            "genbankAccessions",
                        )
                    ),
                    "terminal_repeats": normalize_space(
                        self._first_value(
                            raw,
                            "terminal_repeats",
                            "terminalRepeats",
                        )
                    ),
                    "polyadenylated": self._optional_bool(
                        self._first_value(
                            raw,
                            "polyadenylated",
                            "is_polyadenylated",
                            "isPolyadenylated",
                        )
                    ),
                },
                "virion": {
                    "enveloped": self._optional_bool(
                        self._first_value(
                            raw,
                            "enveloped",
                            "is_enveloped",
                            "isEnveloped",
                        )
                    ),
                    "capsid_symmetry": normalize_space(
                        self._first_value(
                            raw,
                            "capsid_symmetry",
                            "capsidSymmetry",
                            "symmetry",
                        )
                    ),
                    "capsid_shape": normalize_space(
                        self._first_value(
                            raw,
                            "capsid_shape",
                            "capsidShape",
                            "morphology",
                        )
                    ),
                    "diameter_nm": self._optional_float(
                        self._first_value(
                            raw,
                            "diameter_nm",
                            "diameterNm",
                        )
                    ),
                    "length_nm": self._optional_float(
                        self._first_value(
                            raw,
                            "length_nm",
                            "lengthNm",
                        )
                    ),
                    "width_nm": self._optional_float(
                        self._first_value(
                            raw,
                            "width_nm",
                            "widthNm",
                        )
                    ),
                    "tegument": self._optional_bool(
                        self._first_value(
                            raw,
                            "tegument",
                            "has_tegument",
                            "hasTegument",
                        )
                    ),
                    "matrix": self._optional_bool(
                        self._first_value(
                            raw,
                            "matrix",
                            "has_matrix",
                            "hasMatrix",
                        )
                    ),
                },
                "replication": {
                    "site": normalize_space(
                        self._first_value(
                            raw,
                            "replication_site",
                            "replicationSite",
                        )
                    ),
                    "entry_mechanism": self._list_value(
                        self._first_value(
                            raw,
                            "entry_mechanism",
                            "entryMechanism",
                        )
                    ),
                    "uncoating": normalize_space(
                        self._first_value(
                            raw,
                            "uncoating",
                            "uncoating_mechanism",
                            "uncoatingMechanism",
                        )
                    ),
                    "transcription": self._text_value(
                        self._first_value(
                            raw,
                            "transcription",
                            "transcription_strategy",
                            "transcriptionStrategy",
                        )
                    ),
                    "translation": self._text_value(
                        self._first_value(
                            raw,
                            "translation",
                            "translation_strategy",
                            "translationStrategy",
                        )
                    ),
                    "replication_strategy": self._text_value(
                        self._first_value(
                            raw,
                            "replication_strategy",
                            "replicationStrategy",
                        )
                    ),
                    "assembly": self._text_value(
                        self._first_value(
                            raw,
                            "assembly",
                            "virion_assembly",
                            "virionAssembly",
                        )
                    ),
                    "release": self._list_value(
                        self._first_value(
                            raw,
                            "release",
                            "release_mechanism",
                            "releaseMechanism",
                        )
                    ),
                    "latency": self._optional_bool(
                        self._first_value(
                            raw,
                            "latency",
                            "establishes_latency",
                            "establishesLatency",
                        )
                    ),
                    "integration": self._optional_bool(
                        self._first_value(
                            raw,
                            "integration",
                            "genome_integration",
                            "genomeIntegration",
                        )
                    ),
                },
                "hosts": self._normalize_hosts(
                    self._first_value(
                        raw,
                        "hosts",
                        "host_records",
                        "hostRecords",
                        "host_range",
                        "hostRange",
                    )
                ),
                "vectors": self._normalize_vectors(
                    self._first_value(
                        raw,
                        "vectors",
                        "vector_records",
                        "vectorRecords",
                    )
                ),
                "diseases": self._normalize_diseases(
                    self._first_value(
                        raw,
                        "diseases",
                        "disease_records",
                        "diseaseRecords",
                        "associated_diseases",
                        "associatedDiseases",
                    )
                ),
                "transmission": {
                    "routes": self._list_value(
                        self._first_value(
                            raw,
                            "transmission_routes",
                            "transmissionRoutes",
                            "transmission",
                        )
                    ),
                    "reservoirs": self._normalize_hosts(
                        self._first_value(
                            raw,
                            "reservoirs",
                            "reservoir_hosts",
                            "reservoirHosts",
                        )
                    ),
                    "zoonotic": self._optional_bool(
                        self._first_value(
                            raw,
                            "zoonotic",
                            "is_zoonotic",
                            "isZoonotic",
                        )
                    ),
                    "vertical_transmission": self._optional_bool(
                        self._first_value(
                            raw,
                            "vertical_transmission",
                            "verticalTransmission",
                        )
                    ),
                    "horizontal_transmission": self._optional_bool(
                        self._first_value(
                            raw,
                            "horizontal_transmission",
                            "horizontalTransmission",
                        )
                    ),
                },
                "proteins": self._normalize_proteins(
                    self._first_value(
                        raw,
                        "proteins",
                        "protein_records",
                        "proteinRecords",
                    )
                ),
                "proteomes": self._normalize_proteomes(
                    self._first_value(
                        raw,
                        "proteomes",
                        "proteome_records",
                        "proteomeRecords",
                    )
                ),
                "molecular_features": {
                    "receptors": self._normalize_molecules(
                        self._first_value(
                            raw,
                            "receptors",
                            "host_receptors",
                            "hostReceptors",
                        )
                    ),
                    "co_receptors": self._normalize_molecules(
                        self._first_value(
                            raw,
                            "co_receptors",
                            "coReceptors",
                        )
                    ),
                    "polymerases": self._normalize_molecules(
                        self._first_value(
                            raw,
                            "polymerases",
                            "viral_polymerases",
                            "viralPolymerases",
                        )
                    ),
                    "proteases": self._normalize_molecules(
                        self._first_value(
                            raw,
                            "proteases",
                            "viral_proteases",
                            "viralProteases",
                        )
                    ),
                },
                "geography": {
                    "distribution": self._first_value(
                        raw,
                        "distribution",
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
                    "endemic": self._optional_bool(
                        self._first_value(
                            raw,
                            "endemic",
                            "is_endemic",
                            "isEndemic",
                        )
                    ),
                    "emerging": self._optional_bool(
                        self._first_value(
                            raw,
                            "emerging",
                            "is_emerging",
                            "isEmerging",
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
                        "diagrams",
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
        """Extract ICTV-style viral lineage."""

        lineage = {
            "realm": normalize_space(raw.get("realm")),
            "subrealm": normalize_space(raw.get("subrealm")),
            "kingdom": normalize_space(raw.get("kingdom")),
            "subkingdom": normalize_space(raw.get("subkingdom")),
            "phylum": normalize_space(raw.get("phylum")),
            "subphylum": normalize_space(raw.get("subphylum")),
            "class": normalize_space(raw.get("class")),
            "subclass": normalize_space(raw.get("subclass")),
            "order": normalize_space(raw.get("order")),
            "suborder": normalize_space(raw.get("suborder")),
            "family": normalize_space(raw.get("family")),
            "subfamily": normalize_space(raw.get("subfamily")),
            "genus": normalize_space(raw.get("genus")),
            "subgenus": normalize_space(raw.get("subgenus")),
            "species": normalize_space(raw.get("species")),
        }

        lineage_value = cls._first_value(
            raw,
            "lineage",
            "classification",
            "ictv_lineage",
            "ictvLineage",
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
                "alternative_names",
                "alternativeNames",
                "other_names",
                "otherNames",
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
                        "value",
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
    def _normalize_names(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "value",
                        "scientific_name",
                        "scientificName",
                    )
                )
                name_type = normalize_space(
                    cls._first_value(
                        item,
                        "type",
                        "name_type",
                        "nameType",
                    )
                )
                raw_item = dict(item)
            else:
                name = normalize_space(item)
                name_type = ""
                raw_item = item

            key = (name.casefold(), name_type.casefold())

            if not name or key in seen:
                continue

            seen.add(key)
            result.append(
                {
                    "name": name,
                    "type": name_type,
                    "raw": raw_item,
                }
            )

        return result

    @classmethod
    def _normalize_hosts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "taxon_id": normalize_space(
                            cls._first_value(
                                item,
                                "taxon_id",
                                "taxonId",
                                "ncbi_taxid",
                                "ncbiTaxid",
                                "id",
                            )
                        ),
                        "scientific_name": normalize_space(
                            cls._first_value(
                                item,
                                "scientific_name",
                                "scientificName",
                                "name",
                                "host_name",
                                "hostName",
                            )
                        ),
                        "common_name": normalize_space(
                            cls._first_value(
                                item,
                                "common_name",
                                "commonName",
                            )
                        ),
                        "host_group": normalize_space(
                            cls._first_value(
                                item,
                                "host_group",
                                "hostGroup",
                                "group",
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
                        "evidence": normalize_space(
                            cls._first_value(
                                item,
                                "evidence",
                                "evidence_type",
                                "evidenceType",
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
                            "taxon_id": "",
                            "scientific_name": name,
                            "common_name": "",
                            "host_group": "",
                            "relationship": "",
                            "evidence": "",
                            "reference": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_vectors(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "taxon_id": normalize_space(
                            cls._first_value(
                                item,
                                "taxon_id",
                                "taxonId",
                                "id",
                            )
                        ),
                        "scientific_name": normalize_space(
                            cls._first_value(
                                item,
                                "scientific_name",
                                "scientificName",
                                "name",
                            )
                        ),
                        "vector_type": normalize_space(
                            cls._first_value(
                                item,
                                "vector_type",
                                "vectorType",
                                "type",
                            )
                        ),
                        "biological": cls._optional_bool(
                            cls._first_value(
                                item,
                                "biological",
                                "is_biological",
                                "isBiological",
                            )
                        ),
                        "mechanical": cls._optional_bool(
                            cls._first_value(
                                item,
                                "mechanical",
                                "is_mechanical",
                                "isMechanical",
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
                            "taxon_id": "",
                            "scientific_name": name,
                            "vector_type": "",
                            "biological": None,
                            "mechanical": None,
                            "reference": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_diseases(
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
                                "disease_name",
                                "diseaseName",
                            )
                        ),
                        "disease_id": normalize_space(
                            cls._first_value(
                                item,
                                "disease_id",
                                "diseaseId",
                                "id",
                            )
                        ),
                        "ontology": normalize_space(
                            cls._first_value(
                                item,
                                "ontology",
                                "source",
                            )
                        ),
                        "host": normalize_space(
                            cls._first_value(
                                item,
                                "host",
                                "host_name",
                                "hostName",
                            )
                        ),
                        "symptoms": cls._list_value(
                            cls._first_value(
                                item,
                                "symptoms",
                                "clinical_features",
                                "clinicalFeatures",
                            )
                        ),
                        "severity": normalize_space(
                            cls._first_value(
                                item,
                                "severity",
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
                            "disease_id": "",
                            "ontology": "",
                            "host": "",
                            "symptoms": [],
                            "severity": "",
                            "reference": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_proteins(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                continue

            result.append(
                {
                    "name": normalize_space(
                        cls._first_value(
                            item,
                            "name",
                            "protein_name",
                            "proteinName",
                        )
                    ),
                    "gene": normalize_space(
                        cls._first_value(
                            item,
                            "gene",
                            "gene_name",
                            "geneName",
                        )
                    ),
                    "uniprot_accession": normalize_space(
                        cls._first_value(
                            item,
                            "uniprot_accession",
                            "uniprotAccession",
                            "accession",
                        )
                    ),
                    "function": cls._text_value(
                        cls._first_value(
                            item,
                            "function",
                            "description",
                        )
                    ),
                    "location": normalize_space(
                        cls._first_value(
                            item,
                            "location",
                            "subcellular_location",
                            "subcellularLocation",
                        )
                    ),
                    "essential": cls._optional_bool(
                        cls._first_value(
                            item,
                            "essential",
                            "is_essential",
                            "isEssential",
                        )
                    ),
                    "raw": dict(item),
                }
            )

        return result

    @classmethod
    def _normalize_proteomes(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "proteome_id": normalize_space(
                            cls._first_value(
                                item,
                                "proteome_id",
                                "proteomeId",
                                "upid",
                                "id",
                            )
                        ),
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "proteome_name",
                                "proteomeName",
                            )
                        ),
                        "protein_count": cls._optional_int(
                            cls._first_value(
                                item,
                                "protein_count",
                                "proteinCount",
                            )
                        ),
                        "reference_proteome": cls._optional_bool(
                            cls._first_value(
                                item,
                                "reference_proteome",
                                "referenceProteome",
                                "is_reference",
                                "isReference",
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
                        "raw": dict(item),
                    }
                )

        return result

    @classmethod
    def _normalize_molecules(
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
                                "molecule_name",
                                "moleculeName",
                            )
                        ),
                        "id": normalize_space(
                            cls._first_value(
                                item,
                                "id",
                                "identifier",
                                "accession",
                            )
                        ),
                        "source": normalize_space(
                            cls._first_value(
                                item,
                                "source",
                                "database",
                            )
                        ),
                        "role": normalize_space(
                            cls._first_value(
                                item,
                                "role",
                                "function",
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
                            "source": "",
                            "role": "",
                            "raw": item,
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
    def _normalize_accessions(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                accession = normalize_space(
                    cls._first_value(
                        item,
                        "accession",
                        "id",
                        "identifier",
                    )
                )
                source = normalize_space(
                    cls._first_value(
                        item,
                        "source",
                        "database",
                    )
                )
            else:
                accession = normalize_space(item)
                source = ""

            if accession:
                result.append(
                    {
                        "accession": accession,
                        "source": source,
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

        known_fields = {
            "viralzone_id": "ViralZone",
            "viralZoneId": "ViralZone",
            "ictv_taxon_id": "ICTV",
            "ictvTaxonId": "ICTV",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "uniprot_taxonomy_id": "UniProt Taxonomy",
            "uniprotTaxonomyId": "UniProt Taxonomy",
            "proteome_id": "UniProt Proteomes",
            "proteomeId": "UniProt Proteomes",
            "genbank_accession": "GenBank",
            "genbankAccession": "GenBank",
            "ena_accession": "ENA",
            "enaAccession": "ENA",
            "refseq_accession": "RefSeq",
            "refseqAccession": "RefSeq",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "mesh_id": "MeSH",
            "meshId": "MeSH",
            "disease_ontology_id": "Disease Ontology",
            "diseaseOntologyId": "Disease Ontology",
        }

        for field, source in known_fields.items():
            identifier = normalize_space(raw.get(field))
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
                        "pmid": normalize_space(item.get("pmid")),
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
                            "pmid": "",
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
                            "illustrator",
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
    def _text_value(value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return normalize_space(value)

        if isinstance(value, Mapping):
            for key in ("text", "content", "description", "value"):
                if key in value:
                    return Provider._text_value(value.get(key))

            return normalize_space(
                " ".join(
                    Provider._text_value(item)
                    for item in value.values()
                )
            )

        if isinstance(value, (list, tuple, set)):
            return normalize_space(
                " ".join(
                    Provider._text_value(item)
                    for item in value
                )
            )

        return normalize_space(value)

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
            "sub realm": "subrealm",
            "sub kingdom": "subkingdom",
            "sub phylum": "subphylum",
            "sub class": "subclass",
            "sub order": "suborder",
            "sub family": "subfamily",
            "sub genus": "subgenus",
            "virus species": "species",
            "virus genus": "genus",
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
            "approved": "accepted",
            "ratified": "accepted",
            "abolished": "inactive",
            "deleted": "inactive",
            "inactive": "inactive",
            "synonym": "synonym",
            "former name": "synonym",
            "deprecated": "synonym",
            "unassigned": "unknown",
            "unclassified": "unknown",
            "provisional": "provisionally accepted",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _infer_rank(scientific_name: str) -> str:
        text = normalize_space(scientific_name)

        if text.endswith("viridae"):
            return "family"

        if text.endswith("virinae"):
            return "subfamily"

        if text.endswith("virales"):
            return "order"

        if text.endswith("viricetes"):
            return "class"

        if text.endswith("viricota"):
            return "phylum"

        if text.endswith("virae"):
            return "kingdom"

        if text.endswith("viria"):
            return "realm"

        words = text.split()

        if len(words) >= 2:
            return "species"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid ViralZone cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "ViralZone cursor must be non-negative."
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
            "active",
            "enveloped",
            "segmented",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "absent",
            "inactive",
            "non-enveloped",
            "nonsegmented",
        }:
            return False

        return None
