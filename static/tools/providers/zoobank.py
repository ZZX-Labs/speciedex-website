#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/zoobank.py

ZooBank provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not depend on a live API during the main
ingestion workflow.

Each source record is normalized into the shared Taxon contract while the
complete ZooBank object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "zoobank",
        "path": "static/data/providers/zoobank/names.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "ZooBank",
        "source_url": "https://zoobank.org/"
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
    """File-backed ZooBank provider."""

    PROVIDER_NAME = "zoobank"

    DEFAULT_SOURCE_NAME = "ZooBank"
    DEFAULT_SOURCE_URL = "https://zoobank.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable ZooBank JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"ZooBank export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"ZooBank path is not a file: {source_path}"
            )

        offset = self._decode_cursor(self.cursor)

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
            for line_number, line in enumerate(handle):
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
                    value = json.loads(stripped)
                except json.JSONDecodeError:
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
        """Resolve the configured ZooBank JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
        )

        if not configured:
            raise ProviderError(
                "ZooBank provider requires a path."
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
        """Normalize one ZooBank nomenclatural record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "lsid",
                "zoobank_lsid",
                "zoobankLsid",
                "name_lsid",
                "nameLsid",
                "taxon_id",
                "taxonId",
                "id",
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

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "name_without_authorship",
                "nameWithoutAuthorship",
                "name",
            )
        ) or scientific_name

        rank = self._normalize_rank(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
                "name_rank",
                "nameRank",
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
                "nomenclatural_status",
                "nomenclaturalStatus",
                "name_status",
                "nameStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_name_lsid",
                "acceptedNameLsid",
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_id",
                "acceptedId",
                "current_name_lsid",
                "currentNameLsid",
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
                + "/"
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
                    "author",
                    "authority",
                )
            ),
            kingdom=lineage.get("kingdom", "Animalia"),
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
                    "registration_date",
                    "registrationDate",
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
                "programme": "zoobank",
                "reference_only": True,
                "lsid": provider_id,
                "accepted_name_lsid": accepted_provider_id,
                "lineage": lineage,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_lsid",
                            "parentLsid",
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
                "registration": {
                    "date": normalize_space(
                        self._first_value(
                            raw,
                            "registration_date",
                            "registrationDate",
                        )
                    ),
                    "status": normalize_space(
                        self._first_value(
                            raw,
                            "registration_status",
                            "registrationStatus",
                        )
                    ),
                    "registered_by": normalize_space(
                        self._first_value(
                            raw,
                            "registered_by",
                            "registeredBy",
                            "registrant",
                        )
                    ),
                },
                "nomenclature": {
                    "original_combination": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination",
                            "originalCombination",
                            "original_name",
                            "originalName",
                        )
                    ),
                    "original_combination_lsid": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination_lsid",
                            "originalCombinationLsid",
                            "original_name_lsid",
                            "originalNameLsid",
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
                    "basionym_lsid": normalize_space(
                        self._first_value(
                            raw,
                            "basionym_lsid",
                            "basionymLsid",
                        )
                    ),
                    "nomenclatural_status": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_status",
                            "nomenclaturalStatus",
                            "name_status",
                            "nameStatus",
                        )
                    ),
                    "nomenclatural_code": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_code",
                            "nomenclaturalCode",
                            "code",
                        )
                    ),
                    "gender": normalize_space(
                        self._first_value(
                            raw,
                            "gender",
                            "grammatical_gender",
                            "grammaticalGender",
                        )
                    ),
                },
                "nomenclatural_acts": self._normalize_acts(
                    self._first_value(
                        raw,
                        "nomenclatural_acts",
                        "nomenclaturalActs",
                        "acts",
                    )
                ),
                "publication": {
                    "lsid": normalize_space(
                        self._first_value(
                            raw,
                            "publication_lsid",
                            "publicationLsid",
                        )
                    ),
                    "title": normalize_space(
                        self._first_value(
                            raw,
                            "publication_title",
                            "publicationTitle",
                            "published_in",
                            "publishedIn",
                        )
                    ),
                    "authors": normalize_space(
                        self._first_value(
                            raw,
                            "publication_authors",
                            "publicationAuthors",
                        )
                    ),
                    "journal": normalize_space(
                        self._first_value(
                            raw,
                            "journal",
                            "publication_journal",
                            "publicationJournal",
                        )
                    ),
                    "year": normalize_space(
                        self._first_value(
                            raw,
                            "publication_year",
                            "publicationYear",
                            "year",
                        )
                    ),
                    "volume": normalize_space(
                        self._first_value(
                            raw,
                            "volume",
                            "publication_volume",
                            "publicationVolume",
                        )
                    ),
                    "issue": normalize_space(
                        self._first_value(
                            raw,
                            "issue",
                            "publication_issue",
                            "publicationIssue",
                        )
                    ),
                    "pages": normalize_space(
                        self._first_value(
                            raw,
                            "pages",
                            "page",
                            "publication_pages",
                            "publicationPages",
                        )
                    ),
                    "doi": normalize_space(
                        self._first_value(
                            raw,
                            "doi",
                        )
                    ),
                    "url": normalize_space(
                        self._first_value(
                            raw,
                            "publication_url",
                            "publicationUrl",
                        )
                    ),
                },
                "type": {
                    "type_species": normalize_space(
                        self._first_value(
                            raw,
                            "type_species",
                            "typeSpecies",
                        )
                    ),
                    "type_species_lsid": normalize_space(
                        self._first_value(
                            raw,
                            "type_species_lsid",
                            "typeSpeciesLsid",
                        )
                    ),
                    "type_specimen": normalize_space(
                        self._first_value(
                            raw,
                            "type_specimen",
                            "typeSpecimen",
                        )
                    ),
                    "type_locality": normalize_space(
                        self._first_value(
                            raw,
                            "type_locality",
                            "typeLocality",
                        )
                    ),
                    "holotype": normalize_space(
                        self._first_value(
                            raw,
                            "holotype",
                        )
                    ),
                    "lectotype": normalize_space(
                        self._first_value(
                            raw,
                            "lectotype",
                        )
                    ),
                    "neotype": normalize_space(
                        self._first_value(
                            raw,
                            "neotype",
                        )
                    ),
                    "repository": normalize_space(
                        self._first_value(
                            raw,
                            "type_repository",
                            "typeRepository",
                            "repository",
                            "institution",
                        )
                    ),
                },
                "authors": self._normalize_authors(
                    self._first_value(
                        raw,
                        "authors",
                        "author_records",
                        "authorRecords",
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
        """Extract zoological lineage from direct or nested fields."""

        lineage = {
            "kingdom": normalize_space(
                raw.get("kingdom")
            ) or "Animalia",
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
        """Extract and deduplicate zoological synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "taxonomic_synonyms",
                "taxonomicSynonyms",
                "alternative_names",
                "alternativeNames",
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

    @classmethod
    def _normalize_acts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize nomenclatural acts and ZooBank registrations."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "lsid": normalize_space(
                            cls._first_value(
                                item,
                                "lsid",
                                "act_lsid",
                                "actLsid",
                                "id",
                            )
                        ),
                        "type": normalize_space(
                            cls._first_value(
                                item,
                                "type",
                                "act",
                                "act_type",
                                "actType",
                            )
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "registration_date",
                                "registrationDate",
                                "published_at",
                                "publishedAt",
                            )
                        ),
                        "subject_name": normalize_space(
                            cls._first_value(
                                item,
                                "subject_name",
                                "subjectName",
                                "name",
                            )
                        ),
                        "subject_lsid": normalize_space(
                            cls._first_value(
                                item,
                                "subject_lsid",
                                "subjectLsid",
                            )
                        ),
                        "publication_lsid": normalize_space(
                            cls._first_value(
                                item,
                                "publication_lsid",
                                "publicationLsid",
                            )
                        ),
                        "notes": normalize_space(
                            cls._first_value(
                                item,
                                "notes",
                                "remarks",
                                "description",
                            )
                        ),
                    }
                )
                result.append(entry)
            else:
                act = normalize_space(item)

                if act:
                    result.append(
                        {
                            "lsid": "",
                            "type": act,
                            "date": "",
                            "subject_name": "",
                            "subject_lsid": "",
                            "publication_lsid": "",
                            "notes": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_authors(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize nomenclatural author records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "lsid": normalize_space(
                            cls._first_value(
                                item,
                                "lsid",
                                "author_lsid",
                                "authorLsid",
                                "id",
                            )
                        ),
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "full_name",
                                "fullName",
                            )
                        ),
                        "abbreviation": normalize_space(
                            cls._first_value(
                                item,
                                "abbreviation",
                                "abbr",
                            )
                        ),
                        "role": normalize_space(
                            cls._first_value(
                                item,
                                "role",
                                "author_role",
                                "authorRole",
                            )
                        ),
                    }
                )

                if entry.get("name") or entry.get("lsid"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "lsid": "",
                            "name": name,
                            "abbreviation": "",
                            "role": "",
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
        """Normalize ZooBank and external taxonomic identifiers."""

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
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "worms_id": "WoRMS",
            "wormsId": "WoRMS",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "eol_id": "Encyclopedia of Life",
            "eolId": "Encyclopedia of Life",
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
        """Normalize ZooBank references and publication citations."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
                        "lsid": normalize_space(
                            cls._first_value(
                                item,
                                "lsid",
                                "publication_lsid",
                                "publicationLsid",
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
                        "year": normalize_space(
                            cls._first_value(
                                item,
                                "year",
                                "publication_year",
                                "publicationYear",
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
                            "lsid": "",
                            "citation": citation,
                            "doi": "",
                            "url": "",
                            "year": "",
                        }
                    )

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize zoological taxonomic ranks."""

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
            "infra species": "infraspecies",
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
    def _normalize_status(value: Any) -> str:
        """Normalize zoological nomenclatural and taxonomic statuses."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "available": "valid",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "objective synonym": "synonym",
            "subjective synonym": "synonym",
            "unavailable": "excluded",
            "nomen nudum": "excluded",
            "nomen dubium": "unknown",
            "nomen oblitum": "inactive",
            "misapplied": "misapplied",
            "rejected": "excluded",
            "unresolved": "unknown",
            "reference": "reference",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _infer_rank(scientific_name: str) -> str:
        """Infer zoological rank from name structure."""

        words = normalize_space(scientific_name).split()

        lowered = {
            word.casefold()
            for word in words
        }

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        """Decode a non-negative JSONL line offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid ZooBank cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "ZooBank cursor must be non-negative."
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
