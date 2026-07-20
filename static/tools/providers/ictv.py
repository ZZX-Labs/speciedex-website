#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/ictv.py

International Committee on Taxonomy of Viruses (ICTV) provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete ICTV object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "ictv",
        "path": "static/data/providers/ictv/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "International Committee on Taxonomy of Viruses",
        "source_url": "https://ictv.global/"
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
    """File-backed ICTV provider."""

    PROVIDER_NAME = "ictv"

    DEFAULT_SOURCE_NAME = (
        "International Committee on Taxonomy of Viruses"
    )
    DEFAULT_SOURCE_URL = "https://ictv.global/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable ICTV JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"ICTV export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"ICTV path is not a file: {source_path}"
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
        """Resolve the configured ICTV JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "ICTV provider requires a path."
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
        """Normalize one ICTV taxonomy record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "taxon_id",
                "taxonId",
                "taxonID",
                "ictv_id",
                "ictvId",
                "ictvID",
                "species_sort",
                "speciesSort",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "taxon_name",
                "taxonName",
                "species",
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
                "taxon_name",
                "taxonName",
                "name",
            )
        ) or scientific_name

        rank = self._normalize_rank(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
                "level",
            )
        )

        if rank == "unknown":
            rank = self._infer_rank(
                raw,
                canonical_name,
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "current_status",
                "currentStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_id",
                "acceptedId",
                "current_taxon_id",
                "currentTaxonId",
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
                "taxon_url",
                "taxonUrl",
            )
        ) or normalize_space(
            self.definition.get(
                "source_url",
                self.DEFAULT_SOURCE_URL,
            )
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
                    "author",
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
                "programme": "ictv",
                "reference_only": True,
                "ictv_taxon_id": provider_id,
                "accepted_taxon_id": accepted_provider_id,
                "lineage": lineage,
                "realm": lineage.get(
                    "realm",
                    "",
                ),
                "subrealm": lineage.get(
                    "subrealm",
                    "",
                ),
                "kingdom": lineage.get(
                    "kingdom",
                    "",
                ),
                "subkingdom": lineage.get(
                    "subkingdom",
                    "",
                ),
                "phylum": lineage.get(
                    "phylum",
                    "",
                ),
                "subphylum": lineage.get(
                    "subphylum",
                    "",
                ),
                "class": lineage.get(
                    "class",
                    "",
                ),
                "subclass": lineage.get(
                    "subclass",
                    "",
                ),
                "order": lineage.get(
                    "order",
                    "",
                ),
                "suborder": lineage.get(
                    "suborder",
                    "",
                ),
                "family": lineage.get(
                    "family",
                    "",
                ),
                "subfamily": lineage.get(
                    "subfamily",
                    "",
                ),
                "genus": lineage.get(
                    "genus",
                    "",
                ),
                "subgenus": lineage.get(
                    "subgenus",
                    "",
                ),
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
                "nomenclature": {
                    "current_name": scientific_name,
                    "former_names": self._list_value(
                        self._first_value(
                            raw,
                            "former_names",
                            "formerNames",
                            "previous_names",
                            "previousNames",
                        )
                    ),
                    "abbreviation": normalize_space(
                        self._first_value(
                            raw,
                            "abbreviation",
                            "abbr",
                        )
                    ),
                    "isolate": normalize_space(
                        self._first_value(
                            raw,
                            "isolate",
                            "exemplar_isolate",
                            "exemplarIsolate",
                        )
                    ),
                    "type_species": normalize_space(
                        self._first_value(
                            raw,
                            "type_species",
                            "typeSpecies",
                        )
                    ),
                },
                "genome": {
                    "type": normalize_space(
                        self._first_value(
                            raw,
                            "genome_type",
                            "genomeType",
                            "genome",
                        )
                    ),
                    "composition": normalize_space(
                        self._first_value(
                            raw,
                            "genome_composition",
                            "genomeComposition",
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
                    "segments": self._optional_int(
                        self._first_value(
                            raw,
                            "segments",
                            "segment_count",
                            "segmentCount",
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
                    "strandedness": normalize_space(
                        self._first_value(
                            raw,
                            "strandedness",
                            "strand",
                        )
                    ),
                    "length": self._optional_int(
                        self._first_value(
                            raw,
                            "genome_length",
                            "genomeLength",
                            "length",
                        )
                    ),
                },
                "hosts": self._normalize_hosts(
                    self._first_value(
                        raw,
                        "hosts",
                        "host",
                        "host_range",
                        "hostRange",
                    )
                ),
                "exemplar": {
                    "isolate": normalize_space(
                        self._first_value(
                            raw,
                            "exemplar_isolate",
                            "exemplarIsolate",
                            "isolate",
                        )
                    ),
                    "accession": normalize_space(
                        self._first_value(
                            raw,
                            "exemplar_accession",
                            "exemplarAccession",
                            "genbank_accession",
                            "genbankAccession",
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
                },
                "proposal": {
                    "number": normalize_space(
                        self._first_value(
                            raw,
                            "proposal_number",
                            "proposalNumber",
                        )
                    ),
                    "title": normalize_space(
                        self._first_value(
                            raw,
                            "proposal_title",
                            "proposalTitle",
                        )
                    ),
                    "year": normalize_space(
                        self._first_value(
                            raw,
                            "proposal_year",
                            "proposalYear",
                        )
                    ),
                    "status": normalize_space(
                        self._first_value(
                            raw,
                            "proposal_status",
                            "proposalStatus",
                        )
                    ),
                },
                "release": {
                    "version": normalize_space(
                        self._first_value(
                            raw,
                            "release",
                            "release_version",
                            "releaseVersion",
                        )
                    ),
                    "date": normalize_space(
                        self._first_value(
                            raw,
                            "release_date",
                            "releaseDate",
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
                        "citations",
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
        """Extract ICTV hierarchy from direct fields or lineage arrays."""

        ranks = (
            "realm",
            "subrealm",
            "kingdom",
            "subkingdom",
            "phylum",
            "subphylum",
            "class",
            "subclass",
            "order",
            "suborder",
            "family",
            "subfamily",
            "genus",
            "subgenus",
            "species",
        )

        lineage = {
            rank: normalize_space(
                raw.get(
                    rank
                )
            )
            for rank in ranks
        }

        lineage_value = cls._first_value(
            raw,
            "lineage",
            "classification",
            "hierarchy",
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
                    "level",
                )
            )

            name = normalize_space(
                cls._first_value(
                    item,
                    "name",
                    "taxon_name",
                    "taxonName",
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
        """Extract and deduplicate former and alternate virus names."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "former_names",
                "formerNames",
                "previous_names",
                "previousNames",
                "alternative_names",
                "alternativeNames",
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
                        "name",
                        "scientific_name",
                        "scientificName",
                        "taxon_name",
                        "taxonName",
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
    def _normalize_hosts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize host names and host taxonomy metadata."""

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
                        "group": normalize_space(
                            cls._first_value(
                                item,
                                "group",
                                "host_group",
                                "hostGroup",
                            )
                        ),
                    }
                )

                if entry.get(
                    "name"
                ) or entry.get(
                    "taxon_id"
                ):
                    result.append(
                        entry
                    )

            else:
                name = normalize_space(
                    item
                )

                if name:
                    result.append(
                        {
                            "name": name,
                            "taxon_id": "",
                            "group": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize external identifiers and accession mappings."""

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

    @classmethod
    def _normalize_identifier_list(
        cls,
        value: Any,
    ) -> list[str]:
        """Normalize accession lists from arrays or delimited strings."""

        if isinstance(
            value,
            str,
        ):
            values = [
                item
                for item in value.replace(
                    ";",
                    ",",
                ).split(
                    ","
                )
                if item
            ]
        else:
            values = cls._list_value(
                value
            )

        result: list[str] = []
        seen: set[str] = set()

        for item in values:
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
        """Normalize ICTV references, reports, and proposal citations."""

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
    def _normalize_rank(
        value: Any,
    ) -> str:
        """Normalize ICTV rank labels."""

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
            "species group": "species_group",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub order": "suborder",
            "sub class": "subclass",
            "sub phylum": "subphylum",
            "sub kingdom": "subkingdom",
            "sub realm": "subrealm",
        }

        if not rank:
            return "unknown"

        return aliases.get(
            rank,
            rank.replace(
                " ",
                "",
            )
            if rank.startswith(
                "sub "
            )
            else rank.replace(
                " ",
                "_",
            ),
        )

    @staticmethod
    def _infer_rank(
        raw: Mapping[str, Any],
        scientific_name: str,
    ) -> str:
        """Infer the record rank from populated hierarchy fields."""

        for rank in (
            "species",
            "subgenus",
            "genus",
            "subfamily",
            "family",
            "suborder",
            "order",
            "subclass",
            "class",
            "subphylum",
            "phylum",
            "subkingdom",
            "kingdom",
            "subrealm",
            "realm",
        ):
            value = normalize_space(
                raw.get(
                    rank
                )
            )

            if (
                value
                and value.casefold()
                == scientific_name.casefold()
            ):
                return rank

        return "species"

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize ICTV taxonomic status values."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "current": "accepted",
            "approved": "accepted",
            "valid": "valid",
            "abolished": "obsolete",
            "deleted": "obsolete",
            "obsolete": "obsolete",
            "renamed": "synonym",
            "synonym": "synonym",
            "pending": "provisionally accepted",
            "proposed": "provisionally accepted",
        }

        return aliases.get(
            status,
            status or "accepted",
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
                f"Invalid ICTV cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "ICTV cursor must be non-negative."
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
