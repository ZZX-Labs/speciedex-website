#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/ioc_world_bird_list.py

IOC World Bird List provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for global avian taxonomy, accepted
names, subspecies, synonymy, English and multilingual common names, IOC
taxonomic sequence, parent relationships, distribution, endemicity, update
history, bibliography, external identifiers, and provenance metadata.

Each source record is normalized into the shared Speciedex Taxon contract while
the complete IOC source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "ioc_world_bird_list",
        "path": "static/data/providers/ioc-world-bird-list/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "IOC World Bird List",
        "source_url": "https://www.worldbirdnames.org/"
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
    """File-backed IOC World Bird List provider."""

    PROVIDER_NAME = "ioc_world_bird_list"

    DEFAULT_SOURCE_NAME = "IOC World Bird List"
    DEFAULT_SOURCE_URL = "https://www.worldbirdnames.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable IOC JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"IOC World Bird List export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"IOC World Bird List path is not a file: {source_path}"
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
                            f"Invalid IOC JSON at "
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
        """Resolve the configured IOC JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "IOC World Bird List provider requires a path."
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
        """Normalize one IOC avian taxon record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "ioc_id",
                "iocId",
                "ioc_taxon_id",
                "iocTaxonId",
                "taxon_id",
                "taxonId",
                "species_code",
                "speciesCode",
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
                "species_epithet",
                "speciesEpithet",
            )
        )

        subspecies_epithet = normalize_space(
            self._first_value(
                raw,
                "subspecies",
                "subspecies_epithet",
                "subspeciesEpithet",
                "infraspecific_epithet",
                "infraspecificEpithet",
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

        if not scientific_name and genus and specific_epithet:
            scientific_name = f"{genus} {specific_epithet}"

            if subspecies_epithet:
                scientific_name = (
                    f"{scientific_name} {subspecies_epithet}"
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
            rank = (
                "subspecies"
                if subspecies_epithet
                else self._infer_rank(canonical_name)
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
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
                "accepted_id",
                "acceptedId",
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

            source_url = f"{base}/browse/{provider_id}"

        lineage = self._extract_lineage(
            raw,
            genus=genus,
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
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "authority",
                    "author",
                )
            ),
            kingdom=lineage.get("kingdom", "Animalia"),
            phylum=lineage.get("phylum", "Chordata"),
            class_name=lineage.get("class", "Aves"),
            order=lineage.get("order", ""),
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
                "programme": "ioc_world_bird_list",
                "reference_only": True,
                "ioc_id": provider_id,
                "accepted_name_id": accepted_provider_id,
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
                "name": {
                    "genus": genus,
                    "specific_epithet": specific_epithet,
                    "subspecies_epithet": subspecies_epithet,
                    "original_combination": normalize_space(
                        self._first_value(
                            raw,
                            "original_combination",
                            "originalCombination",
                            "original_name",
                            "originalName",
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
                    "year": normalize_space(
                        self._first_value(
                            raw,
                            "year",
                            "publication_year",
                            "publicationYear",
                        )
                    ),
                },
                "ioc_sequence": {
                    "ioc_version": normalize_space(
                        self._first_value(
                            raw,
                            "ioc_version",
                            "iocVersion",
                            "version",
                        )
                    ),
                    "order_sequence": self._optional_int(
                        self._first_value(
                            raw,
                            "order_sequence",
                            "orderSequence",
                        )
                    ),
                    "family_sequence": self._optional_int(
                        self._first_value(
                            raw,
                            "family_sequence",
                            "familySequence",
                        )
                    ),
                    "genus_sequence": self._optional_int(
                        self._first_value(
                            raw,
                            "genus_sequence",
                            "genusSequence",
                        )
                    ),
                    "species_sequence": self._optional_int(
                        self._first_value(
                            raw,
                            "species_sequence",
                            "speciesSequence",
                            "sequence",
                        )
                    ),
                    "taxonomic_sort": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_sort",
                            "taxonomicSort",
                        )
                    ),
                },
                "common_names": self._normalize_common_names(
                    self._first_value(
                        raw,
                        "common_names",
                        "commonNames",
                        "vernacular_names",
                        "vernacularNames",
                    ),
                    english_name=normalize_space(
                        self._first_value(
                            raw,
                            "english_name",
                            "englishName",
                            "common_name",
                            "commonName",
                        )
                    ),
                ),
                "taxonomy": {
                    "subfamily": normalize_space(
                        self._first_value(raw, "subfamily")
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
                    "taxonomic_notes": normalize_space(
                        self._first_value(
                            raw,
                            "taxonomic_notes",
                            "taxonomicNotes",
                            "comments",
                        )
                    ),
                    "nomenclatural_code": "ICZN",
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
                "distribution": {
                    "summary": self._first_value(
                        raw,
                        "distribution",
                        "range",
                        "geographic_distribution",
                        "geographicDistribution",
                    ),
                    "breeding_range": self._normalize_regions(
                        self._first_value(
                            raw,
                            "breeding_range",
                            "breedingRange",
                        )
                    ),
                    "nonbreeding_range": self._normalize_regions(
                        self._first_value(
                            raw,
                            "nonbreeding_range",
                            "nonbreedingRange",
                            "wintering_range",
                            "winteringRange",
                        )
                    ),
                    "passage_range": self._normalize_regions(
                        self._first_value(
                            raw,
                            "passage_range",
                            "passageRange",
                            "migration_range",
                            "migrationRange",
                        )
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
                    "endemic_to": self._normalize_regions(
                        self._first_value(
                            raw,
                            "endemic_to",
                            "endemicTo",
                            "endemic_areas",
                            "endemicAreas",
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
                    "extinct": self._optional_bool(
                        self._first_value(
                            raw,
                            "extinct",
                            "is_extinct",
                            "isExtinct",
                        )
                    ),
                },
                "migration": {
                    "migratory": self._optional_bool(
                        self._first_value(
                            raw,
                            "migratory",
                            "is_migratory",
                            "isMigratory",
                        )
                    ),
                    "migration_type": self._list_value(
                        self._first_value(
                            raw,
                            "migration_type",
                            "migrationType",
                        )
                    ),
                    "flyways": self._list_value(
                        self._first_value(
                            raw,
                            "flyways",
                            "migratory_flyways",
                            "migratoryFlyways",
                        )
                    ),
                    "seasonality": normalize_space(
                        self._first_value(
                            raw,
                            "seasonality",
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
                    "birdlife_status": normalize_space(
                        self._first_value(
                            raw,
                            "birdlife_status",
                            "birdlifeStatus",
                        )
                    ),
                    "cites_status": normalize_space(
                        self._first_value(
                            raw,
                            "cites_status",
                            "citesStatus",
                        )
                    ),
                    "cms_status": normalize_space(
                        self._first_value(
                            raw,
                            "cms_status",
                            "cmsStatus",
                        )
                    ),
                },
                "ioc_updates": self._normalize_updates(
                    self._first_value(
                        raw,
                        "ioc_updates",
                        "iocUpdates",
                        "updates",
                        "change_history",
                        "changeHistory",
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
                "media": self._normalize_media(
                    self._first_value(
                        raw,
                        "media",
                        "images",
                        "image",
                        "range_maps",
                        "rangeMaps",
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
        genus: str,
    ) -> dict[str, str]:
        """Extract avian classification."""

        lineage = {
            "kingdom": normalize_space(
                raw.get("kingdom")
            ) or "Animalia",
            "phylum": normalize_space(
                raw.get("phylum")
            ) or "Chordata",
            "class": normalize_space(
                raw.get("class")
            ) or "Aves",
            "order": normalize_space(raw.get("order")),
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
        """Extract and deduplicate avian synonyms."""

        values = cls._list_value(
            cls._first_value(
                raw,
                "synonyms",
                "synonym",
                "synonym_records",
                "synonymRecords",
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
                name = normalize_space(
                    cls._first_value(
                        item,
                        "scientific_name",
                        "scientificName",
                        "name",
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
    def _normalize_common_names(
        cls,
        value: Any,
        *,
        english_name: str,
    ) -> list[dict[str, Any]]:
        """Normalize IOC English and multilingual common names."""

        values = cls._list_value(value)

        if english_name:
            values.insert(
                0,
                {
                    "name": english_name,
                    "language": "en",
                    "preferred": True,
                    "authority": "IOC",
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
                preferred = cls._optional_bool(
                    cls._first_value(
                        item,
                        "preferred",
                        "is_preferred",
                        "isPreferred",
                    )
                )
                authority = normalize_space(
                    cls._first_value(
                        item,
                        "authority",
                        "source",
                    )
                )
                raw_item = dict(item)
            else:
                name = normalize_space(item)
                language = ""
                region = ""
                preferred = None
                authority = ""
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
                    "preferred": preferred,
                    "authority": authority,
                    "raw": raw_item,
                }
            )

        return result

    @classmethod
    def _normalize_synonym_records(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize avian synonym records."""

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
                        "relationship": normalize_space(
                            cls._first_value(
                                item,
                                "relationship",
                                "relation",
                                "type",
                            )
                        ),
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
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
                            "relationship": "",
                            "reference": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_regions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize countries, regions, and seasonal ranges."""

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
                        "season": normalize_space(
                            cls._first_value(
                                item,
                                "season",
                                "seasonal_status",
                                "seasonalStatus",
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
                            "season": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_updates(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize IOC taxonomic change history."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "version": normalize_space(
                            cls._first_value(
                                item,
                                "version",
                                "ioc_version",
                                "iocVersion",
                            )
                        ),
                        "date": normalize_space(
                            cls._first_value(
                                item,
                                "date",
                                "published",
                                "publication_date",
                                "publicationDate",
                            )
                        ),
                        "change_type": normalize_space(
                            cls._first_value(
                                item,
                                "change_type",
                                "changeType",
                                "type",
                            )
                        ),
                        "description": normalize_space(
                            cls._first_value(
                                item,
                                "description",
                                "change",
                                "notes",
                            )
                        ),
                        "previous_name": normalize_space(
                            cls._first_value(
                                item,
                                "previous_name",
                                "previousName",
                            )
                        ),
                        "new_name": normalize_space(
                            cls._first_value(
                                item,
                                "new_name",
                                "newName",
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
                description = normalize_space(item)

                if description:
                    result.append(
                        {
                            "version": "",
                            "date": "",
                            "change_type": "",
                            "description": description,
                            "previous_name": "",
                            "new_name": "",
                            "reference": "",
                            "raw": item,
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
        """Normalize IOC and external avian identifiers."""

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
            "ioc_id": "IOC World Bird List",
            "iocId": "IOC World Bird List",
            "avibase_id": "Avibase",
            "avibaseId": "Avibase",
            "ebird_code": "eBird",
            "ebirdCode": "eBird",
            "clements_code": "Clements",
            "clementsCode": "Clements",
            "birdlife_id": "BirdLife International",
            "birdlifeId": "BirdLife International",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "col_id": "Catalogue of Life",
            "colId": "Catalogue of Life",
            "ncbi_taxid": "NCBI Taxonomy",
            "ncbiTaxid": "NCBI Taxonomy",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "eol_id": "Encyclopedia of Life",
            "eolId": "Encyclopedia of Life",
            "iucn_id": "IUCN",
            "iucnId": "IUCN",
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
        """Normalize avian taxonomic references."""

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

    @classmethod
    def _normalize_media(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize bird images, audio, and range maps."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.update(
                    {
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
                                "recordist",
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
                    "url": normalize_space(item),
                    "thumbnail_url": "",
                    "type": "",
                    "title": "",
                    "creator": "",
                    "license": "",
                }

            if entry.get("url") or entry.get("thumbnail_url"):
                result.append(entry)

        return result

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize avian taxonomic ranks."""

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
            "sub class": "subclass",
            "super family": "superfamily",
            "species group": "species_group",
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
        """Normalize IOC taxonomic and nomenclatural statuses."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "accepted",
            "current": "accepted",
            "recognized": "accepted",
            "provisional": "provisionally accepted",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "unaccepted": "synonym",
            "split": "accepted",
            "lumped": "synonym",
            "misapplied": "misapplied",
            "extinct": "accepted",
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
        """Infer bird taxonomic rank from scientific-name structure."""

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
        """Decode a non-negative JSONL record offset."""

        if not cursor:
            return 0

        try:
            offset = int(cursor)
        except (TypeError, ValueError) as error:
            raise ProviderError(
                f"Invalid IOC World Bird List cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "IOC World Bird List cursor must be non-negative."
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
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "absent",
            "inactive",
        }:
            return False

        return None
