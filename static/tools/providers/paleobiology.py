#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/paleobiology.py

Paleobiology Database provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete Paleobiology Database object is preserved under
``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "paleobiology",
        "path": "static/data/providers/paleobiology/records.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Paleobiology Database",
        "source_url": "https://paleobiodb.org/"
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
    """File-backed Paleobiology Database provider."""

    PROVIDER_NAME = "paleobiology"

    DEFAULT_SOURCE_NAME = "Paleobiology Database"
    DEFAULT_SOURCE_URL = "https://paleobiodb.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable Paleobiology JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Paleobiology Database export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Paleobiology Database path is not a file: {source_path}"
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
        """Resolve the configured JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "Paleobiology provider requires a path."
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
        """Normalize one Paleobiology taxon or occurrence record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "taxon_no",
                "taxonNo",
                "taxon_id",
                "taxonId",
                "taxonID",
                "orig_no",
                "origNo",
                "accepted_no",
                "acceptedNo",
                "id",
            )
        )

        occurrence_id = normalize_space(
            self._first_value(
                raw,
                "occurrence_no",
                "occurrenceNo",
                "occurrence_id",
                "occurrenceId",
                "occurrenceID",
            )
        )

        if not provider_id:
            provider_id = occurrence_id

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "taxon_name",
                "taxonName",
                "scientific_name",
                "scientificName",
                "accepted_name",
                "acceptedName",
                "name",
            )
        )

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "accepted_name",
                "acceptedName",
                "canonical_name",
                "canonicalName",
                "taxon_name",
                "taxonName",
            )
        ) or scientific_name

        rank = self._normalize_rank(
            self._first_value(
                raw,
                "taxon_rank",
                "taxonRank",
                "rank",
            )
        )

        if rank == "unknown":
            rank = self._infer_rank(
                canonical_name
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "taxonomic_status",
                "taxonomicStatus",
                "status",
                "difference",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_no",
                "acceptedNo",
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

            source_url = (
                f"{base}/classic/checkTaxonInfo?"
                f"taxon_no={provider_id}"
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
                    "taxon_attr",
                    "taxonAttr",
                    "authorship",
                    "author",
                    "authority",
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
                "programme": "paleobiology_database",
                "reference_only": True,
                "taxon_no": provider_id,
                "occurrence_no": occurrence_id,
                "accepted_no": accepted_provider_id,
                "original_no": normalize_space(
                    self._first_value(
                        raw,
                        "orig_no",
                        "origNo",
                        "original_no",
                        "originalNo",
                    )
                ),
                "lineage": lineage,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_no",
                            "parentNo",
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
                "occurrence": {
                    "occurrence_no": occurrence_id,
                    "collection_no": normalize_space(
                        self._first_value(
                            raw,
                            "collection_no",
                            "collectionNo",
                            "collection_id",
                            "collectionId",
                        )
                    ),
                    "identified_name": normalize_space(
                        self._first_value(
                            raw,
                            "identified_name",
                            "identifiedName",
                        )
                    ),
                    "identified_rank": self._normalize_rank(
                        self._first_value(
                            raw,
                            "identified_rank",
                            "identifiedRank",
                        )
                    ),
                    "identified_by": normalize_space(
                        self._first_value(
                            raw,
                            "identified_by",
                            "identifiedBy",
                        )
                    ),
                    "reidentified": self._optional_bool(
                        self._first_value(
                            raw,
                            "reidentified",
                            "is_reidentified",
                            "isReidentified",
                        )
                    ),
                    "abundance": self._optional_float(
                        self._first_value(
                            raw,
                            "abundance",
                            "abund_value",
                            "abundValue",
                        )
                    ),
                    "abundance_unit": normalize_space(
                        self._first_value(
                            raw,
                            "abundance_unit",
                            "abundanceUnit",
                            "abund_unit",
                            "abundUnit",
                        )
                    ),
                    "comments": normalize_space(
                        self._first_value(
                            raw,
                            "occurrence_comments",
                            "occurrenceComments",
                        )
                    ),
                },
                "geologic_time": {
                    "early_interval": normalize_space(
                        self._first_value(
                            raw,
                            "early_interval",
                            "earlyInterval",
                            "interval_name",
                            "intervalName",
                        )
                    ),
                    "late_interval": normalize_space(
                        self._first_value(
                            raw,
                            "late_interval",
                            "lateInterval",
                        )
                    ),
                    "max_ma": self._optional_float(
                        self._first_value(
                            raw,
                            "max_ma",
                            "maxMa",
                            "max_age",
                            "maxAge",
                        )
                    ),
                    "min_ma": self._optional_float(
                        self._first_value(
                            raw,
                            "min_ma",
                            "minMa",
                            "min_age",
                            "minAge",
                        )
                    ),
                    "stage": normalize_space(
                        self._first_value(
                            raw,
                            "stage",
                        )
                    ),
                    "epoch": normalize_space(
                        self._first_value(
                            raw,
                            "epoch",
                        )
                    ),
                    "period": normalize_space(
                        self._first_value(
                            raw,
                            "period",
                        )
                    ),
                    "era": normalize_space(
                        self._first_value(
                            raw,
                            "era",
                        )
                    ),
                },
                "stratigraphy": {
                    "formation": normalize_space(
                        self._first_value(
                            raw,
                            "formation",
                        )
                    ),
                    "member": normalize_space(
                        self._first_value(
                            raw,
                            "member",
                        )
                    ),
                    "group": normalize_space(
                        self._first_value(
                            raw,
                            "stratgroup",
                            "stratGroup",
                            "group",
                        )
                    ),
                    "bed": normalize_space(
                        self._first_value(
                            raw,
                            "stratigraphic_bed",
                            "stratigraphicBed",
                            "bed",
                        )
                    ),
                    "zone": normalize_space(
                        self._first_value(
                            raw,
                            "zone",
                            "biozone",
                        )
                    ),
                    "lithology": self._list_value(
                        self._first_value(
                            raw,
                            "lithology",
                            "lithologies",
                            "lithology1",
                        )
                    ),
                },
                "paleoenvironment": {
                    "environment": normalize_space(
                        self._first_value(
                            raw,
                            "environment",
                            "paleoenvironment",
                        )
                    ),
                    "environment_basis": normalize_space(
                        self._first_value(
                            raw,
                            "environment_basis",
                            "environmentBasis",
                        )
                    ),
                    "lithology": self._list_value(
                        self._first_value(
                            raw,
                            "lithology",
                            "lithologies",
                        )
                    ),
                    "preservation_mode": self._list_value(
                        self._first_value(
                            raw,
                            "preservation_mode",
                            "preservationMode",
                            "preservation",
                        )
                    ),
                },
                "collection": {
                    "collection_no": normalize_space(
                        self._first_value(
                            raw,
                            "collection_no",
                            "collectionNo",
                        )
                    ),
                    "collection_name": normalize_space(
                        self._first_value(
                            raw,
                            "collection_name",
                            "collectionName",
                        )
                    ),
                    "country": normalize_space(
                        self._first_value(
                            raw,
                            "country",
                        )
                    ),
                    "state": normalize_space(
                        self._first_value(
                            raw,
                            "state",
                            "state_province",
                            "stateProvince",
                        )
                    ),
                    "county": normalize_space(
                        self._first_value(
                            raw,
                            "county",
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
                    "paleolatitude": self._optional_float(
                        self._first_value(
                            raw,
                            "paleolatitude",
                            "paleolat",
                            "paleolatitude_deg",
                            "paleolatitudeDeg",
                        )
                    ),
                    "paleolongitude": self._optional_float(
                        self._first_value(
                            raw,
                            "paleolongitude",
                            "paleolng",
                            "paleolongitude_deg",
                            "paleolongitudeDeg",
                        )
                    ),
                    "geoplate": normalize_space(
                        self._first_value(
                            raw,
                            "geoplate",
                            "plate",
                        )
                    ),
                    "collectors": self._list_value(
                        self._first_value(
                            raw,
                            "collectors",
                            "collected_by",
                            "collectedBy",
                        )
                    ),
                    "collection_date": normalize_space(
                        self._first_value(
                            raw,
                            "collection_date",
                            "collectionDate",
                        )
                    ),
                },
                "taxonomy": {
                    "difference": normalize_space(
                        self._first_value(
                            raw,
                            "difference",
                        )
                    ),
                    "type_taxon": self._optional_bool(
                        self._first_value(
                            raw,
                            "type_taxon",
                            "typeTaxon",
                            "is_type_taxon",
                            "isTypeTaxon",
                        )
                    ),
                    "extant": self._optional_bool(
                        self._first_value(
                            raw,
                            "extant",
                            "is_extant",
                            "isExtant",
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
                },
                "measurements": self._list_value(
                    self._first_value(
                        raw,
                        "measurements",
                        "measurement",
                    )
                ),
                "preservation": self._list_value(
                    self._first_value(
                        raw,
                        "preservation",
                        "preservation_modes",
                        "preservationModes",
                    )
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
                        "primary_reference",
                        "primaryReference",
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
            "kingdom": normalize_space(
                raw.get(
                    "kingdom"
                )
            ),
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

        lineage_value = cls._first_value(
            raw,
            "lineage",
            "classification",
            "higher_taxa",
            "higherTaxa",
        )

        for item in cls._list_value(
            lineage_value
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
                lineage[
                    rank
                ] = name

        return lineage

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
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize publication and collection references."""

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
                        "reference_no": normalize_space(
                            cls._first_value(
                                item,
                                "reference_no",
                                "referenceNo",
                                "ref_no",
                                "refNo",
                                "id",
                            )
                        ),
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
                            "reference_no": "",
                            "citation": citation,
                            "doi": "",
                            "url": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize external taxonomy and collection identifiers."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(
            value
        ):
            if isinstance(
                item,
                Mapping,
            ):
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
                identifier = normalize_space(
                    item
                )
                source = ""

            if identifier:
                result.append(
                    {
                        "identifier": identifier,
                        "source": source,
                    }
                )

        return result

    @staticmethod
    def _normalize_rank(
        value: Any,
    ) -> str:
        """Normalize paleontological taxonomic rank labels."""

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
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub order": "suborder",
            "sub class": "subclass",
            "sub phylum": "subphylum",
            "informal": "unranked",
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
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize Paleobiology taxonomic statuses."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "belongs to": "accepted",
            "recombined as": "synonym",
            "synonym of": "synonym",
            "subjective synonym of": "synonym",
            "objective synonym of": "synonym",
            "misspelling of": "synonym",
            "nomen dubium": "unknown",
            "nomen nudum": "excluded",
            "invalid subgroup of": "excluded",
            "misapplied": "misapplied",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        """Infer rank from scientific-name structure."""

        words = normalize_space(
            scientific_name
        ).split()

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

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
                f"Invalid Paleobiology cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Paleobiology cursor must be non-negative."
            )

        return offset

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
