#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/unite.py

UNITE fungal ITS taxonomy provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete UNITE object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "unite",
        "path": "static/data/providers/unite/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "UNITE",
        "source_url": "https://unite.ut.ee/"
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
    """File-backed UNITE provider."""

    PROVIDER_NAME = "unite"

    DEFAULT_SOURCE_NAME = "UNITE"
    DEFAULT_SOURCE_URL = "https://unite.ut.ee/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable UNITE JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"UNITE export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"UNITE path is not a file: {source_path}"
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

                record = self._normalize_record(
                    dict(value),
                    source_path=source_path,
                    retrieved_at=retrieved_at,
                )

                if record is not None:
                    records.append(
                        record
                    )

        return Batch(
            records=records,
            next_cursor=(
                None
                if exhausted
                else str(
                    next_offset
                )
            ),
            exhausted=exhausted,
            requests=0,
            raw=raw_count,
        )

    def _source_path(self) -> Path:
        """Resolve the configured UNITE JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "UNITE provider requires a path."
            )

        path = Path(
            configured
        )

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
        """Normalize one UNITE species-hypothesis or sequence record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "sh_id",
                "shId",
                "species_hypothesis_id",
                "speciesHypothesisId",
                "doi",
                "unite_id",
                "uniteId",
                "taxon_id",
                "taxonId",
                "id",
            )
        )

        sequence_id = normalize_space(
            self._first_value(
                raw,
                "sequence_id",
                "sequenceId",
                "accession",
                "representative_sequence_id",
                "representativeSequenceId",
            )
        )

        if not provider_id:
            provider_id = sequence_id

        lineage_string = normalize_space(
            self._first_value(
                raw,
                "lineage",
                "taxonomy",
                "taxonomic_path",
                "taxonomicPath",
                "classification",
            )
        )

        lineage = self._extract_lineage(
            raw,
            lineage_string=lineage_string,
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "taxon_name",
                "taxonName",
                "species_name",
                "speciesName",
                "name",
            )
        )

        if not scientific_name:
            for candidate in (
                "species",
                "genus",
                "family",
                "order",
                "class",
                "phylum",
                "kingdom",
            ):
                if lineage.get(candidate):
                    scientific_name = lineage[candidate]
                    break

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "species_name",
                "speciesName",
                "name",
            )
        ) or scientific_name

        rank = self._determine_rank(
            raw,
            lineage,
            scientific_name,
        )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "curation_status",
                "curationStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_sh_id",
                "acceptedShId",
                "current_sh_id",
                "currentShId",
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
            source_url = normalize_space(
                self.definition.get(
                    "source_url",
                    self.DEFAULT_SOURCE_URL,
                )
            )

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
                )
            ),
            kingdom=lineage.get(
                "kingdom",
                "Fungi",
            ),
            phylum=lineage.get(
                "phylum",
                "",
            ),
            class_name=lineage.get(
                "class",
                "",
            ),
            order=lineage.get(
                "order",
                "",
            ),
            family=lineage.get(
                "family",
                "",
            ),
            genus=lineage.get(
                "genus",
                "",
            ),
            accepted_provider_id=accepted_provider_id,
            source_url=source_url,
            source_modified=normalize_space(
                self._first_value(
                    raw,
                    "modified",
                    "last_modified",
                    "lastModified",
                    "updated",
                    "release",
                    "release_date",
                    "releaseDate",
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
                "programme": "unite",
                "reference_only": True,
                "species_hypothesis": {
                    "id": provider_id,
                    "doi": normalize_space(
                        self._first_value(
                            raw,
                            "doi",
                            "sh_doi",
                            "shDoi",
                        )
                    ),
                    "version": normalize_space(
                        self._first_value(
                            raw,
                            "version",
                            "sh_version",
                            "shVersion",
                        )
                    ),
                    "release": normalize_space(
                        self._first_value(
                            raw,
                            "release",
                            "unite_release",
                            "uniteRelease",
                        )
                    ),
                    "threshold": self._optional_float(
                        self._first_value(
                            raw,
                            "threshold",
                            "clustering_threshold",
                            "clusteringThreshold",
                            "similarity_threshold",
                            "similarityThreshold",
                        )
                    ),
                    "sequence_count": self._optional_int(
                        self._first_value(
                            raw,
                            "sequence_count",
                            "sequenceCount",
                            "cluster_size",
                            "clusterSize",
                        )
                    ),
                    "representative_sequence_id": normalize_space(
                        self._first_value(
                            raw,
                            "representative_sequence_id",
                            "representativeSequenceId",
                            "representative_id",
                            "representativeId",
                        )
                    ),
                    "is_representative": self._optional_bool(
                        self._first_value(
                            raw,
                            "is_representative",
                            "isRepresentative",
                            "representative",
                        )
                    ),
                },
                "accepted_taxon_id": accepted_provider_id,
                "lineage": lineage,
                "lineage_string": lineage_string,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_taxon_id",
                            "parentTaxonId",
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
                "sequence": {
                    "id": sequence_id,
                    "accession": normalize_space(
                        self._first_value(
                            raw,
                            "accession",
                            "primary_accession",
                            "primaryAccession",
                        )
                    ),
                    "secondary_accessions": self._normalize_identifier_list(
                        self._first_value(
                            raw,
                            "secondary_accessions",
                            "secondaryAccessions",
                            "accessions",
                        )
                    ),
                    "sequence": normalize_space(
                        self._first_value(
                            raw,
                            "sequence",
                            "nucleotides",
                            "its_sequence",
                            "itsSequence",
                        )
                    ),
                    "length": self._optional_int(
                        self._first_value(
                            raw,
                            "sequence_length",
                            "sequenceLength",
                            "length",
                        )
                    ),
                    "marker": normalize_space(
                        self._first_value(
                            raw,
                            "marker",
                            "gene",
                            "region",
                            "its_region",
                            "itsRegion",
                        )
                    ),
                    "its1": normalize_space(
                        self._first_value(
                            raw,
                            "its1",
                            "its1_sequence",
                            "its1Sequence",
                        )
                    ),
                    "its2": normalize_space(
                        self._first_value(
                            raw,
                            "its2",
                            "its2_sequence",
                            "its2Sequence",
                        )
                    ),
                    "lsu": normalize_space(
                        self._first_value(
                            raw,
                            "lsu",
                            "lsu_sequence",
                            "lsuSequence",
                        )
                    ),
                    "ssu": normalize_space(
                        self._first_value(
                            raw,
                            "ssu",
                            "ssu_sequence",
                            "ssuSequence",
                        )
                    ),
                },
                "organism": {
                    "name": scientific_name,
                    "strain": normalize_space(
                        self._first_value(
                            raw,
                            "strain",
                            "strain_name",
                            "strainName",
                        )
                    ),
                    "isolate": normalize_space(
                        self._first_value(
                            raw,
                            "isolate",
                            "isolate_name",
                            "isolateName",
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
                    "type_material": self._optional_bool(
                        self._first_value(
                            raw,
                            "type_material",
                            "typeMaterial",
                            "is_type_material",
                            "isTypeMaterial",
                        )
                    ),
                },
                "ecology": {
                    "guild": self._list_value(
                        self._first_value(
                            raw,
                            "guild",
                            "guilds",
                            "ecological_guild",
                            "ecologicalGuild",
                        )
                    ),
                    "trophic_mode": self._list_value(
                        self._first_value(
                            raw,
                            "trophic_mode",
                            "trophicMode",
                        )
                    ),
                    "growth_form": self._list_value(
                        self._first_value(
                            raw,
                            "growth_form",
                            "growthForm",
                        )
                    ),
                    "trait": self._list_value(
                        self._first_value(
                            raw,
                            "traits",
                            "trait",
                        )
                    ),
                    "host": self._normalize_hosts(
                        self._first_value(
                            raw,
                            "hosts",
                            "host",
                        )
                    ),
                    "substrate": self._list_value(
                        self._first_value(
                            raw,
                            "substrates",
                            "substrate",
                        )
                    ),
                    "habitat": self._list_value(
                        self._first_value(
                            raw,
                            "habitats",
                            "habitat",
                            "environment",
                        )
                    ),
                },
                "geography": {
                    "country": normalize_space(
                        self._first_value(
                            raw,
                            "country",
                            "geographic_location",
                            "geographicLocation",
                        )
                    ),
                    "locality": normalize_space(
                        self._first_value(
                            raw,
                            "locality",
                            "location",
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
                    "elevation_m": self._optional_float(
                        self._first_value(
                            raw,
                            "elevation",
                            "elevation_m",
                            "elevationM",
                        )
                    ),
                    "depth_m": self._optional_float(
                        self._first_value(
                            raw,
                            "depth",
                            "depth_m",
                            "depthM",
                        )
                    ),
                    "regions": self._list_value(
                        self._first_value(
                            raw,
                            "regions",
                            "region",
                        )
                    ),
                },
                "environment": {
                    "environmental_sample": self._optional_bool(
                        self._first_value(
                            raw,
                            "environmental_sample",
                            "environmentalSample",
                            "is_environmental",
                            "isEnvironmental",
                        )
                    ),
                    "uncultured": self._optional_bool(
                        self._first_value(
                            raw,
                            "uncultured",
                            "is_uncultured",
                            "isUncultured",
                        )
                    ),
                    "metagenome": self._optional_bool(
                        self._first_value(
                            raw,
                            "metagenome",
                            "is_metagenome",
                            "isMetagenome",
                        )
                    ),
                    "isolation_source": normalize_space(
                        self._first_value(
                            raw,
                            "isolation_source",
                            "isolationSource",
                        )
                    ),
                },
                "quality": {
                    "quality_score": self._optional_float(
                        self._first_value(
                            raw,
                            "quality_score",
                            "qualityScore",
                        )
                    ),
                    "sequence_quality": normalize_space(
                        self._first_value(
                            raw,
                            "sequence_quality",
                            "sequenceQuality",
                            "quality",
                        )
                    ),
                    "chimera": self._optional_bool(
                        self._first_value(
                            raw,
                            "chimera",
                            "chimeric",
                            "is_chimera",
                            "isChimera",
                        )
                    ),
                    "ambiguities": self._optional_int(
                        self._first_value(
                            raw,
                            "ambiguities",
                            "ambiguous_bases",
                            "ambiguousBases",
                        )
                    ),
                    "coverage": self._optional_float(
                        self._first_value(
                            raw,
                            "coverage",
                            "sequence_coverage",
                            "sequenceCoverage",
                        )
                    ),
                },
                "curation": {
                    "curated": self._optional_bool(
                        self._first_value(
                            raw,
                            "curated",
                            "is_curated",
                            "isCurated",
                        )
                    ),
                    "reference": self._optional_bool(
                        self._first_value(
                            raw,
                            "reference",
                            "is_reference",
                            "isReference",
                        )
                    ),
                    "taxon_expert": normalize_space(
                        self._first_value(
                            raw,
                            "taxon_expert",
                            "taxonExpert",
                            "curator",
                        )
                    ),
                    "classification_method": normalize_space(
                        self._first_value(
                            raw,
                            "classification_method",
                            "classificationMethod",
                        )
                    ),
                    "confidence": self._optional_float(
                        self._first_value(
                            raw,
                            "classification_confidence",
                            "classificationConfidence",
                            "confidence",
                        )
                    ),
                },
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
        *,
        lineage_string: str,
    ) -> dict[str, str]:
        """Extract fungal lineage from direct, ranked, or prefixed paths."""

        lineage = {
            "kingdom": normalize_space(
                raw.get(
                    "kingdom"
                )
            ) or "Fungi",
            "phylum": normalize_space(
                raw.get(
                    "phylum"
                )
            ),
            "class": normalize_space(
                raw.get(
                    "class"
                )
            ),
            "order": normalize_space(
                raw.get(
                    "order"
                )
            ),
            "family": normalize_space(
                raw.get(
                    "family"
                )
            ),
            "genus": normalize_space(
                raw.get(
                    "genus"
                )
            ),
            "species": normalize_space(
                raw.get(
                    "species"
                )
            ),
        }

        ranked_lineage = cls._first_value(
            raw,
            "ranked_lineage",
            "rankedLineage",
            "lineage_records",
            "lineageRecords",
        )

        for item in cls._list_value(
            ranked_lineage
        ):
            if not isinstance(
                item,
                Mapping,
            ):
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

            if rank and name and not lineage.get(
                rank
            ):
                lineage[rank] = name

        prefix_map = {
            "k__": "kingdom",
            "p__": "phylum",
            "c__": "class",
            "o__": "order",
            "f__": "family",
            "g__": "genus",
            "s__": "species",
        }

        if lineage_string:
            parts = [
                normalize_space(item)
                for item in lineage_string.replace(
                    "|",
                    ";",
                ).split(";")
                if normalize_space(item)
            ]

            positional_ranks = (
                "kingdom",
                "phylum",
                "class",
                "order",
                "family",
                "genus",
                "species",
            )

            used_prefixes = False

            for part in parts:
                for prefix, rank in prefix_map.items():
                    if part.startswith(prefix):
                        used_prefixes = True
                        name = normalize_space(
                            part[len(prefix):]
                        )

                        if name and not lineage.get(rank):
                            lineage[rank] = name

                        break

            if not used_prefixes:
                for rank, name in zip(
                    positional_ranks,
                    parts,
                ):
                    if not lineage.get(rank):
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
        """Extract and deduplicate fungal synonym-like names."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "alternative_names",
                "alternativeNames",
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

    @staticmethod
    def _determine_rank(
        raw: Mapping[str, Any],
        lineage: Mapping[str, str],
        scientific_name: str,
    ) -> str:
        """Determine rank from explicit data or lineage position."""

        rank = Provider._normalize_rank(
            Provider._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
            )
        )

        if rank != "unknown":
            return rank

        for candidate in (
            "species",
            "genus",
            "family",
            "order",
            "class",
            "phylum",
            "kingdom",
        ):
            value = normalize_space(
                lineage.get(candidate)
            )

            if (
                value
                and value.casefold()
                == scientific_name.casefold()
            ):
                return candidate

        words = normalize_space(
            scientific_name
        ).split()

        if len(words) >= 2:
            return "species"

        return "unknown"

    @classmethod
    def _normalize_hosts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize fungal host metadata."""

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
                                "host_name",
                                "hostName",
                                "scientific_name",
                                "scientificName",
                            )
                        ),
                        "taxon_id": normalize_space(
                            cls._first_value(
                                item,
                                "taxon_id",
                                "taxonId",
                                "id",
                            )
                        ),
                        "part": normalize_space(
                            cls._first_value(
                                item,
                                "part",
                                "host_part",
                                "hostPart",
                            )
                        ),
                    }
                )

                if entry.get("name") or entry.get("taxon_id"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "taxon_id": "",
                            "part": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_identifier_list(
        cls,
        value: Any,
    ) -> list[str]:
        """Normalize arrays or delimited accession lists."""

        if isinstance(value, str):
            values: Iterable[Any] = (
                item
                for item in value.replace(
                    ";",
                    ",",
                ).split(",")
            )
        else:
            values = cls._list_value(value)

        result: list[str] = []
        seen: set[str] = set()

        for item in values:
            normalized = normalize_space(item)
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
    ) -> list[dict[str, str]]:
        """Normalize sequence, taxonomy, and repository identifiers."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                identifier = normalize_space(
                    cls._first_value(
                        item,
                        "identifier",
                        "id",
                        "value",
                        "accession",
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
        """Normalize UNITE references and source publications."""

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
                        "doi": normalize_space(
                            cls._first_value(
                                item,
                                "doi",
                            )
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
                            "doi": "",
                            "pubmed_id": "",
                            "url": "",
                        }
                    )

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize fungal taxonomic rank labels."""

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
            "no rank": "unranked",
        }

        if not rank:
            return "unknown"

        return aliases.get(
            rank,
            rank.replace(
                " ",
                "_",
            ),
        )

    @staticmethod
    def _normalize_status(value: Any) -> str:
        """Normalize UNITE curation and taxonomy status labels."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "curated": "accepted",
            "reference": "reference",
            "representative": "reference",
            "species hypothesis": "reference",
            "synonym": "synonym",
            "uncultured": "reference",
            "environmental sample": "reference",
            "unclassified": "unknown",
            "unresolved": "unknown",
            "provisional": "provisionally accepted",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> int:
        """Decode a non-negative JSONL line offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid UNITE cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "UNITE cursor must be non-negative."
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
