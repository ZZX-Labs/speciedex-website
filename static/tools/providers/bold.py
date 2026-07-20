#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/bold.py

Barcode of Life Data System (BOLD) provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete BOLD object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "bold",
        "path": "static/data/providers/bold/records.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Barcode of Life Data System",
        "source_url": "https://www.boldsystems.org/"
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
    """File-backed Barcode of Life Data System provider."""

    PROVIDER_NAME = "bold"

    DEFAULT_SOURCE_NAME = "Barcode of Life Data System"
    DEFAULT_SOURCE_URL = "https://www.boldsystems.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable BOLD JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"BOLD export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"BOLD path is not a file: {source_path}"
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

    def _source_path(
        self,
    ) -> Path:
        """Resolve the configured BOLD JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "BOLD provider requires a path."
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
        """Normalize one BOLD record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "processid",
                "process_id",
                "processId",
                "record_id",
                "recordId",
                "sample_id",
                "sampleId",
                "specimen_id",
                "specimenId",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "species_name",
                "speciesName",
                "scientific_name",
                "scientificName",
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
                "species_name",
                "speciesName",
                "name",
            )
        ) or scientific_name

        rank = normalize_space(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
                "identification_rank",
                "identificationRank",
            )
        ).casefold() or self._infer_rank(
            canonical_name
        )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "identification_status",
                "identificationStatus",
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

        source_url = normalize_space(
            self._first_value(
                raw,
                "url",
                "record_url",
                "recordUrl",
                "source_url",
                "sourceUrl",
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
                + "/index.php/Public_RecordView?processid="
                + provider_id
            )

        lineage = self._extract_lineage(
            raw
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
                    "authority",
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "taxon_author",
                    "taxonAuthor",
                )
            ),
            kingdom=lineage.get(
                "kingdom",
                "",
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
                "programme": "bold",
                "reference_only": True,
                "process_id": provider_id,
                "sample_id": normalize_space(
                    self._first_value(
                        raw,
                        "sample_id",
                        "sampleId",
                    )
                ),
                "specimen_id": normalize_space(
                    self._first_value(
                        raw,
                        "specimen_id",
                        "specimenId",
                    )
                ),
                "record_id": normalize_space(
                    self._first_value(
                        raw,
                        "record_id",
                        "recordId",
                    )
                ),
                "bin": {
                    "uri": normalize_space(
                        self._first_value(
                            raw,
                            "bin_uri",
                            "binUri",
                            "bin",
                            "barcode_index_number",
                            "barcodeIndexNumber",
                        )
                    ),
                    "cluster_id": normalize_space(
                        self._first_value(
                            raw,
                            "bin_cluster_id",
                            "binClusterId",
                        )
                    ),
                },
                "lineage": lineage,
                "identification": {
                    "method": normalize_space(
                        self._first_value(
                            raw,
                            "identification_method",
                            "identificationMethod",
                            "id_method",
                            "idMethod",
                        )
                    ),
                    "identified_by": normalize_space(
                        self._first_value(
                            raw,
                            "identified_by",
                            "identifiedBy",
                            "identifier_name",
                            "identifierName",
                        )
                    ),
                    "date": normalize_space(
                        self._first_value(
                            raw,
                            "identification_date",
                            "identificationDate",
                        )
                    ),
                    "remarks": normalize_space(
                        self._first_value(
                            raw,
                            "identification_remarks",
                            "identificationRemarks",
                        )
                    ),
                    "confidence": self._optional_float(
                        self._first_value(
                            raw,
                            "identification_confidence",
                            "identificationConfidence",
                            "confidence",
                        )
                    ),
                },
                "marker": {
                    "code": normalize_space(
                        self._first_value(
                            raw,
                            "marker_code",
                            "markerCode",
                            "marker",
                            "gene",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            raw,
                            "marker_name",
                            "markerName",
                            "gene_name",
                            "geneName",
                        )
                    ),
                    "sequence": normalize_space(
                        self._first_value(
                            raw,
                            "nucleotides",
                            "sequence",
                            "dna_sequence",
                            "dnaSequence",
                        )
                    ),
                    "sequence_length": self._optional_int(
                        self._first_value(
                            raw,
                            "sequence_length",
                            "sequenceLength",
                        )
                    ),
                    "genbank_accession": normalize_space(
                        self._first_value(
                            raw,
                            "genbank_accession",
                            "genbankAccession",
                            "accession",
                        )
                    ),
                },
                "specimen": {
                    "voucher": normalize_space(
                        self._first_value(
                            raw,
                            "voucher",
                            "voucher_id",
                            "voucherId",
                            "catalognum",
                            "catalog_number",
                            "catalogNumber",
                        )
                    ),
                    "institution": normalize_space(
                        self._first_value(
                            raw,
                            "institution_storing",
                            "institutionStoring",
                            "institution",
                            "museum",
                            "repository",
                        )
                    ),
                    "collection_code": normalize_space(
                        self._first_value(
                            raw,
                            "collection_code",
                            "collectionCode",
                        )
                    ),
                    "tissue_type": normalize_space(
                        self._first_value(
                            raw,
                            "tissue_type",
                            "tissueType",
                        )
                    ),
                    "sex": normalize_space(
                        self._first_value(
                            raw,
                            "sex",
                        )
                    ),
                    "life_stage": normalize_space(
                        self._first_value(
                            raw,
                            "life_stage",
                            "lifeStage",
                            "stage",
                        )
                    ),
                },
                "collection": {
                    "collector": normalize_space(
                        self._first_value(
                            raw,
                            "collectors",
                            "collector",
                            "collected_by",
                            "collectedBy",
                        )
                    ),
                    "date": normalize_space(
                        self._first_value(
                            raw,
                            "collection_date",
                            "collectionDate",
                            "event_date",
                            "eventDate",
                        )
                    ),
                    "country": normalize_space(
                        self._first_value(
                            raw,
                            "country",
                            "country_name",
                            "countryName",
                        )
                    ),
                    "province_state": normalize_space(
                        self._first_value(
                            raw,
                            "province_state",
                            "provinceState",
                            "state_province",
                            "stateProvince",
                        )
                    ),
                    "region": normalize_space(
                        self._first_value(
                            raw,
                            "region",
                            "county",
                        )
                    ),
                    "locality": normalize_space(
                        self._first_value(
                            raw,
                            "locality",
                            "site",
                        )
                    ),
                    "latitude": self._optional_float(
                        self._first_value(
                            raw,
                            "lat",
                            "latitude",
                            "decimal_latitude",
                            "decimalLatitude",
                        )
                    ),
                    "longitude": self._optional_float(
                        self._first_value(
                            raw,
                            "lon",
                            "lng",
                            "longitude",
                            "decimal_longitude",
                            "decimalLongitude",
                        )
                    ),
                    "elevation": self._optional_float(
                        self._first_value(
                            raw,
                            "elev",
                            "elevation",
                            "elevation_m",
                            "elevationM",
                        )
                    ),
                    "depth": self._optional_float(
                        self._first_value(
                            raw,
                            "depth",
                            "depth_m",
                            "depthM",
                        )
                    ),
                },
                "habitat": normalize_space(
                    self._first_value(
                        raw,
                        "habitat",
                        "environment",
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
                "images": self._normalize_images(
                    self._first_value(
                        raw,
                        "images",
                        "image",
                        "media",
                    ),
                    raw,
                ),
                "trace_files": self._list_value(
                    self._first_value(
                        raw,
                        "trace_files",
                        "traceFiles",
                        "chromatograms",
                    )
                ),
                "primers": self._list_value(
                    self._first_value(
                        raw,
                        "primers",
                        "primer",
                    )
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
                        "publication",
                    )
                ),
                "project": {
                    "code": normalize_space(
                        self._first_value(
                            raw,
                            "project_code",
                            "projectCode",
                            "project",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            raw,
                            "project_name",
                            "projectName",
                        )
                    ),
                },
                "dataset": {
                    "code": normalize_space(
                        self._first_value(
                            raw,
                            "dataset_code",
                            "datasetCode",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            raw,
                            "dataset_name",
                            "datasetName",
                        )
                    ),
                },
                "copyright": {
                    "holder": normalize_space(
                        self._first_value(
                            raw,
                            "copyright_holder",
                            "copyrightHolder",
                            "rights_holder",
                            "rightsHolder",
                        )
                    ),
                    "license": normalize_space(
                        self._first_value(
                            raw,
                            "license",
                            "rights",
                        )
                    ),
                },
                "bulk_source": source_path.as_posix(),
                "raw": raw,
            },
        )

    @classmethod
    def _extract_lineage(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, str]:
        """Extract taxonomic lineage from direct BOLD fields."""

        return {
            "kingdom": normalize_space(
                cls._first_value(
                    raw,
                    "kingdom_name",
                    "kingdomName",
                    "kingdom",
                )
            ),
            "phylum": normalize_space(
                cls._first_value(
                    raw,
                    "phylum_name",
                    "phylumName",
                    "phylum",
                )
            ),
            "class": normalize_space(
                cls._first_value(
                    raw,
                    "class_name",
                    "className",
                    "class",
                )
            ),
            "order": normalize_space(
                cls._first_value(
                    raw,
                    "order_name",
                    "orderName",
                    "order",
                )
            ),
            "family": normalize_space(
                cls._first_value(
                    raw,
                    "family_name",
                    "familyName",
                    "family",
                )
            ),
            "genus": normalize_space(
                cls._first_value(
                    raw,
                    "genus_name",
                    "genusName",
                    "genus",
                )
            ),
            "species": normalize_space(
                cls._first_value(
                    raw,
                    "species_name",
                    "speciesName",
                    "species",
                )
            ),
            "subspecies": normalize_space(
                cls._first_value(
                    raw,
                    "subspecies_name",
                    "subspeciesName",
                    "subspecies",
                )
            ),
        }

    @classmethod
    def _extract_synonyms(
        cls,
        raw: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """Extract and deduplicate synonym-like names."""

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
        seen: set[str] = set(
            excluded
        )

        for item in values:
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

    @classmethod
    def _normalize_images(
        cls,
        value: Any,
        raw: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Normalize specimen image metadata."""

        values = cls._list_value(
            value
        )

        direct_image = normalize_space(
            cls._first_value(
                raw,
                "image_url",
                "imageUrl",
                "image",
            )
        )

        if direct_image:
            values.insert(
                0,
                {
                    "url": direct_image,
                    "primary": True,
                },
            )

        result: list[dict[str, Any]] = []

        for item in values:
            if isinstance(
                item,
                Mapping,
            ):
                entry = dict(
                    item
                )

                entry.update(
                    {
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "image_url",
                                "imageUrl",
                                "identifier",
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
                        "caption": normalize_space(
                            cls._first_value(
                                item,
                                "caption",
                                "title",
                                "description",
                            )
                        ),
                        "creator": normalize_space(
                            cls._first_value(
                                item,
                                "creator",
                                "photographer",
                                "author",
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
                    "url": normalize_space(
                        item
                    ),
                    "thumbnail_url": "",
                    "caption": "",
                    "creator": "",
                    "license": "",
                }

            if (
                entry.get(
                    "url"
                )
                or entry.get(
                    "thumbnail_url"
                )
            ):
                result.append(
                    entry
                )

        return result

    @classmethod
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize publication and reference metadata."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if isinstance(
                item,
                Mapping,
            ):
                entry = dict(
                    item
                )

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

                result.append(
                    entry
                )

            else:
                citation = normalize_space(
                    item
                )

                if citation:
                    result.append(
                        {
                            "citation": citation,
                            "doi": "",
                            "url": "",
                        }
                    )

        return result

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize taxonomic or identification status values."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "identified": "accepted",
            "provisional": "provisionally accepted",
            "provisionally accepted": "provisionally accepted",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "uncertain": "unknown",
            "unresolved": "unknown",
            "reference": "reference",
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
            offset = int(
                cursor
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ProviderError(
                f"Invalid BOLD cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "BOLD cursor must be non-negative."
            )

        return offset

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        words = normalize_space(
            scientific_name
        ).split()

        if len(
            words
        ) == 2:
            return "species"

        if len(
            words
        ) >= 3:
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
    def _optional_int(
        value: Any,
    ) -> int | None:
        if value in (
            None,
            "",
        ):
            return None

        try:
            return int(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

    @staticmethod
    def _optional_float(
        value: Any,
    ) -> float | None:
        if value in (
            None,
            "",
        ):
            return None

        try:
            return float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return None
