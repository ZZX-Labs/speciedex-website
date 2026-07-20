#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/lpsn.py

List of Prokaryotic names with Standing in Nomenclature (LPSN) provider.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented,
unlicensed, or unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete LPSN object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "lpsn",
        "path": "static/data/providers/lpsn/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "List of Prokaryotic names with Standing in Nomenclature",
        "source_url": "https://lpsn.dsmz.de/"
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
    """File-backed LPSN provider."""

    PROVIDER_NAME = "lpsn"

    DEFAULT_SOURCE_NAME = (
        "List of Prokaryotic names with Standing in Nomenclature"
    )
    DEFAULT_SOURCE_URL = "https://lpsn.dsmz.de/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable LPSN JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"LPSN export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"LPSN path is not a file: {source_path}"
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
        """Resolve the configured LPSN JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "LPSN provider requires a path."
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
        """Normalize one LPSN taxon or nomenclatural record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "lpsn_id",
                "lpsnId",
                "taxon_id",
                "taxonId",
                "name_id",
                "nameId",
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
            )
        )

        if rank == "unknown":
            rank = self._infer_rank(
                canonical_name
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "nomenclatural_status",
                "nomenclaturalStatus",
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
                + "/species/"
                + canonical_name.replace(
                    " ",
                    "-",
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
                    "author",
                    "authority",
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                )
            ),
            kingdom=lineage.get(
                "kingdom",
                lineage.get(
                    "domain",
                    "",
                ),
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
                "programme": "lpsn",
                "reference_only": True,
                "lpsn_id": provider_id,
                "accepted_name_id": accepted_provider_id,
                "lineage": lineage,
                "domain": lineage.get(
                    "domain",
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
                    "standing": normalize_space(
                        self._first_value(
                            raw,
                            "standing",
                            "nomenclatural_standing",
                            "nomenclaturalStanding",
                        )
                    ),
                    "validly_published": self._optional_bool(
                        self._first_value(
                            raw,
                            "validly_published",
                            "validlyPublished",
                            "is_validly_published",
                            "isValidlyPublished",
                        )
                    ),
                    "correct_name": self._optional_bool(
                        self._first_value(
                            raw,
                            "correct_name",
                            "correctName",
                            "is_correct_name",
                            "isCorrectName",
                        )
                    ),
                    "legitimate": self._optional_bool(
                        self._first_value(
                            raw,
                            "legitimate",
                            "is_legitimate",
                            "isLegitimate",
                        )
                    ),
                    "approved_lists": self._optional_bool(
                        self._first_value(
                            raw,
                            "approved_lists",
                            "approvedLists",
                            "on_approved_lists",
                            "onApprovedLists",
                        )
                    ),
                    "basonym": normalize_space(
                        self._first_value(
                            raw,
                            "basonym",
                            "basionym",
                            "basonym_name",
                            "basonymName",
                            "basionym_name",
                            "basionymName",
                        )
                    ),
                    "basonym_id": normalize_space(
                        self._first_value(
                            raw,
                            "basonym_id",
                            "basonymId",
                            "basionym_id",
                            "basionymId",
                        )
                    ),
                    "nomenclatural_status": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_status",
                            "nomenclaturalStatus",
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
                },
                "type": {
                    "type_strain": self._normalize_type_strains(
                        self._first_value(
                            raw,
                            "type_strains",
                            "typeStrains",
                            "type_strain",
                            "typeStrain",
                        )
                    ),
                    "type_species": normalize_space(
                        self._first_value(
                            raw,
                            "type_species",
                            "typeSpecies",
                        )
                    ),
                    "type_genus": normalize_space(
                        self._first_value(
                            raw,
                            "type_genus",
                            "typeGenus",
                        )
                    ),
                },
                "publication": {
                    "effective_publication": normalize_space(
                        self._first_value(
                            raw,
                            "effective_publication",
                            "effectivePublication",
                        )
                    ),
                    "valid_publication": normalize_space(
                        self._first_value(
                            raw,
                            "valid_publication",
                            "validPublication",
                        )
                    ),
                    "authors": normalize_space(
                        self._first_value(
                            raw,
                            "publication_authors",
                            "publicationAuthors",
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
                    "page": normalize_space(
                        self._first_value(
                            raw,
                            "page",
                            "pages",
                            "publication_page",
                            "publicationPage",
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
                "nomenclatural_acts": self._normalize_acts(
                    self._first_value(
                        raw,
                        "nomenclatural_acts",
                        "nomenclaturalActs",
                        "acts",
                    )
                ),
                "etymology": normalize_space(
                    self._first_value(
                        raw,
                        "etymology",
                    )
                ),
                "authors": self._normalize_authors(
                    self._first_value(
                        raw,
                        "author_records",
                        "authorRecords",
                        "authors",
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
        """Extract major prokaryotic lineage values."""

        lineage = {
            "domain": normalize_space(
                cls._first_value(
                    raw,
                    "domain",
                    "superkingdom",
                )
            ),
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
        """Extract and deduplicate prokaryotic synonyms."""

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
    def _normalize_type_strains(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize type-strain designations and culture collections."""

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
                        "designation": normalize_space(
                            cls._first_value(
                                item,
                                "designation",
                                "strain",
                                "name",
                            )
                        ),
                        "collection": normalize_space(
                            cls._first_value(
                                item,
                                "collection",
                                "culture_collection",
                                "cultureCollection",
                            )
                        ),
                        "accession": normalize_space(
                            cls._first_value(
                                item,
                                "accession",
                                "collection_number",
                                "collectionNumber",
                                "id",
                            )
                        ),
                    }
                )

                if (
                    entry.get(
                        "designation"
                    )
                    or entry.get(
                        "accession"
                    )
                ):
                    result.append(
                        entry
                    )
            else:
                designation = normalize_space(
                    item
                )

                if designation:
                    result.append(
                        {
                            "designation": designation,
                            "collection": "",
                            "accession": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_acts(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize nomenclatural acts and proposal history."""

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
                        "act": normalize_space(
                            cls._first_value(
                                item,
                                "act",
                                "type",
                                "name",
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
                        "reference": normalize_space(
                            cls._first_value(
                                item,
                                "reference",
                                "citation",
                            )
                        ),
                    }
                )

                result.append(
                    entry
                )
            else:
                act = normalize_space(
                    item
                )

                if act:
                    result.append(
                        {
                            "act": act,
                            "date": "",
                            "reference": "",
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

                if entry.get(
                    "name"
                ) or entry.get(
                    "abbreviation"
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
                            "abbreviation": "",
                            "role": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize external taxonomy and strain identifiers."""

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

    @classmethod
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize LPSN references and citations."""

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
        """Normalize prokaryotic taxonomic ranks."""

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
            "super kingdom": "domain",
            "superkingdom": "domain",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub order": "suborder",
            "sub class": "subclass",
            "sub phylum": "subphylum",
            "var.": "variety",
            "forma": "form",
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
        """Normalize LPSN taxonomic and nomenclatural statuses."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "correct name": "accepted",
            "validly published": "valid",
            "valid": "valid",
            "synonym": "synonym",
            "heterotypic synonym": "synonym",
            "homotypic synonym": "synonym",
            "basonym": "reference",
            "basionym": "reference",
            "not validly published": "provisionally accepted",
            "candidatus": "provisionally accepted",
            "illegitimate": "excluded",
            "rejected": "excluded",
            "uncertain": "unknown",
            "unresolved": "unknown",
        }

        return aliases.get(
            status,
            status or "reference",
        )

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        """Infer rank from prokaryotic name structure."""

        words = normalize_space(
            scientific_name
        ).split()

        if len(words) == 1:
            return "genus"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            lowered = {
                word.casefold()
                for word in words
            }

            if "subsp." in lowered or "subspecies" in lowered:
                return "subspecies"

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
                f"Invalid LPSN cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "LPSN cursor must be non-negative."
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
