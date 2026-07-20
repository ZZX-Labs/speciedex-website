#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/antcat.py

AntCat provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for ant taxonomy, nomenclature, and
bibliographic data.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete AntCat source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "antcat",
        "path": "static/data/providers/antcat/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "AntCat",
        "source_url": "https://antcat.org/"
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
    """File-backed AntCat provider."""

    PROVIDER_NAME = "antcat"

    DEFAULT_SOURCE_NAME = "AntCat"
    DEFAULT_SOURCE_URL = "https://antcat.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable AntCat JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"AntCat export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"AntCat path is not a file: {source_path}"
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
                            f"Invalid AntCat JSON at "
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
        """Resolve the configured AntCat JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "AntCat provider requires a path."
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
        """Normalize one AntCat taxon or nomenclatural record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "antcat_id",
                "antcatId",
                "taxon_id",
                "taxonId",
                "name_id",
                "nameId",
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

        species_epithet = normalize_space(
            self._first_value(
                raw,
                "species",
                "specific_epithet",
                "specificEpithet",
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

        if not scientific_name and genus and species_epithet:
            scientific_name = f"{genus} {species_epithet}"

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
                "accepted_name_id",
                "acceptedNameId",
                "accepted_taxon_id",
                "acceptedTaxonId",
                "current_name_id",
                "currentNameId",
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
                + "/catalog/"
                + provider_id
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
            kingdom=lineage.get("kingdom", "Animalia"),
            phylum=lineage.get("phylum", "Arthropoda"),
            class_name=lineage.get("class", "Insecta"),
            order=lineage.get("order", "Hymenoptera"),
            family=lineage.get("family", "Formicidae"),
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
                "programme": "antcat",
                "reference_only": True,
                "antcat_id": provider_id,
                "accepted_name_id": accepted_provider_id,
                "lineage": lineage,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_taxon_id",
                            "parentTaxonId",
                            "parent_name_id",
                            "parentNameId",
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
                    "original_combination": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination",
                            "originalCombination",
                            "original_name",
                            "originalName",
                        )
                    ),
                    "original_combination_id": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination_id",
                            "originalCombinationId",
                            "original_name_id",
                            "originalNameId",
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
                    "basionym_id": normalize_space(
                        self._first_value(
                            raw,
                            "basionym_id",
                            "basionymId",
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
                    ) or "ICZN",
                    "year": normalize_space(
                        self._first_value(
                            raw,
                            "year",
                            "publication_year",
                            "publicationYear",
                        )
                    ),
                    "homonym": self._optional_bool(
                        self._first_value(
                            raw,
                            "homonym",
                            "is_homonym",
                            "isHomonym",
                        )
                    ),
                    "replacement_name": normalize_space(
                        self._first_value(
                            raw,
                            "replacement_name",
                            "replacementName",
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
                    "type_species_id": normalize_space(
                        self._first_value(
                            raw,
                            "type_species_id",
                            "typeSpeciesId",
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
                        self._first_value(raw, "holotype")
                    ),
                    "lectotype": normalize_space(
                        self._first_value(raw, "lectotype")
                    ),
                    "neotype": normalize_space(
                        self._first_value(raw, "neotype")
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
                "taxonomy": {
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "comments",
                        )
                    ),
                    "current_combination": normalize_space(
                        self._first_value(
                            raw,
                            "current_combination",
                            "currentCombination",
                        )
                    ),
                    "tribe": normalize_space(
                        self._first_value(raw, "tribe")
                    ),
                    "subtribe": normalize_space(
                        self._first_value(raw, "subtribe")
                    ),
                    "species_group": normalize_space(
                        self._first_value(
                            raw,
                            "species_group",
                            "speciesGroup",
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
                "nomenclatural_acts": self._normalize_acts(
                    self._first_value(
                        raw,
                        "nomenclatural_acts",
                        "nomenclaturalActs",
                        "acts",
                    )
                ),
                "publication": self._normalize_publication(raw),
                "distribution": {
                    "summary": self._first_value(
                        raw,
                        "distribution",
                        "geographic_distribution",
                        "geographicDistribution",
                        "range",
                    ),
                    "countries": self._normalize_regions(
                        self._first_value(
                            raw,
                            "countries",
                            "country",
                            "range_countries",
                            "rangeCountries",
                        )
                    ),
                    "regions": self._normalize_regions(
                        self._first_value(
                            raw,
                            "regions",
                            "region",
                            "subnational_regions",
                            "subnationalRegions",
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
                },
                "bibliography": self._normalize_references(
                    self._first_value(
                        raw,
                        "bibliography",
                        "references",
                        "reference",
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
        """Extract ant classification from direct or nested fields."""

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
            ) or "Hymenoptera",
            "superfamily": normalize_space(
                raw.get("superfamily")
            ),
            "family": normalize_space(
                raw.get("family")
            ) or "Formicidae",
            "subfamily": normalize_space(
                raw.get("subfamily")
            ),
            "tribe": normalize_space(
                raw.get("tribe")
            ),
            "subtribe": normalize_space(
                raw.get("subtribe")
            ),
            "genus": genus or normalize_space(raw.get("genus")),
            "subgenus": normalize_space(
                raw.get("subgenus")
            ),
            "species": normalize_space(
                raw.get("species")
            ),
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
        """Extract and deduplicate ant taxonomic synonyms."""

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
    def _normalize_synonym_records(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize synonym records with bibliographic context."""

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
                                "scientific_name",
                                "scientificName",
                            )
                        ),
                        "id": normalize_space(
                            cls._first_value(
                                item,
                                "id",
                                "name_id",
                                "nameId",
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
                                "name_status",
                                "nameStatus",
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
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
                                "published_in",
                                "publishedIn",
                            )
                        ),
                    }
                )

                if entry.get("name"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "id": "",
                            "authorship": "",
                            "status": "synonym",
                            "year": "",
                            "reference": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_acts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize nomenclatural and taxonomic acts."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
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
                        "subject_id": normalize_space(
                            cls._first_value(
                                item,
                                "subject_id",
                                "subjectId",
                                "id",
                            )
                        ),
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
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
                            "type": act,
                            "date": "",
                            "subject_name": "",
                            "subject_id": "",
                            "reference": "",
                            "notes": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_publication(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize primary publication metadata."""

        return {
            "title": normalize_space(
                cls._first_value(
                    raw,
                    "publication_title",
                    "publicationTitle",
                    "published_in",
                    "publishedIn",
                )
            ),
            "authors": normalize_space(
                cls._first_value(
                    raw,
                    "publication_authors",
                    "publicationAuthors",
                    "authors",
                )
            ),
            "journal": normalize_space(
                cls._first_value(
                    raw,
                    "journal",
                    "publication_journal",
                    "publicationJournal",
                )
            ),
            "year": normalize_space(
                cls._first_value(
                    raw,
                    "publication_year",
                    "publicationYear",
                    "year",
                )
            ),
            "volume": normalize_space(
                cls._first_value(
                    raw,
                    "volume",
                    "publication_volume",
                    "publicationVolume",
                )
            ),
            "issue": normalize_space(
                cls._first_value(
                    raw,
                    "issue",
                    "publication_issue",
                    "publicationIssue",
                )
            ),
            "pages": normalize_space(
                cls._first_value(
                    raw,
                    "pages",
                    "page",
                    "publication_pages",
                    "publicationPages",
                )
            ),
            "doi": normalize_space(
                cls._first_value(raw, "doi")
            ),
            "url": normalize_space(
                cls._first_value(
                    raw,
                    "publication_url",
                    "publicationUrl",
                )
            ),
        }

    @classmethod
    def _normalize_regions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize distribution regions."""

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
                        "status": normalize_space(
                            cls._first_value(
                                item,
                                "status",
                                "presence",
                                "occurrence_status",
                                "occurrenceStatus",
                            )
                        ),
                    }
                )

                if entry.get("name") or entry.get("code"):
                    result.append(entry)
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "code": "",
                            "status": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize AntCat bibliography records."""

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
                            "url": "",
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
        """Normalize AntCat and external identifiers."""

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
            "antweb_id": "AntWeb",
            "antwebId": "AntWeb",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "zoobank_lsid": "ZooBank",
            "zoobankLsid": "ZooBank",
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

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize ant taxonomic ranks."""

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
        """Normalize AntCat taxonomic and nomenclatural statuses."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "current": "accepted",
            "available": "valid",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "subjective synonym": "synonym",
            "objective synonym": "synonym",
            "unaccepted": "synonym",
            "homonym": "excluded",
            "nomen nudum": "excluded",
            "nomen dubium": "unknown",
            "unavailable": "excluded",
            "suppressed": "excluded",
            "misapplied": "misapplied",
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
        """Infer rank from scientific-name structure."""

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
        """Decode a non-negative JSONL offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid AntCat cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "AntCat cursor must be non-negative."
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
