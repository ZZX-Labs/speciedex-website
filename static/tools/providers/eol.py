#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/eol.py

Encyclopedia of Life provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented or
unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete EOL object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "eol",
        "path": "static/data/providers/eol/pages.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Encyclopedia of Life",
        "source_url": "https://eol.org/"
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
    """File-backed Encyclopedia of Life provider."""

    PROVIDER_NAME = "eol"

    DEFAULT_SOURCE_NAME = "Encyclopedia of Life"
    DEFAULT_SOURCE_URL = "https://eol.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable EOL JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Encyclopedia of Life export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Encyclopedia of Life path is not a file: {source_path}"
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
        """Resolve the configured EOL JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "Encyclopedia of Life provider requires a path."
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
        """Normalize one Encyclopedia of Life page or taxon record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "page_id",
                "pageId",
                "pageID",
                "taxon_concept_id",
                "taxonConceptId",
                "taxonConceptID",
                "id",
                "identifier",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "canonical_form",
                "canonicalForm",
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
                "canonical_form",
                "canonicalForm",
                "name",
            )
        ) or scientific_name

        rank = normalize_space(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
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
                "name_status",
                "nameStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_page_id",
                "acceptedPageId",
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
                "page_url",
                "pageUrl",
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
                + "/pages/"
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
                    "scientific_name_authorship",
                    "scientificNameAuthorship",
                    "authority",
                )
            ),
            kingdom=lineage.get(
                "kingdom",
                "",
            ),
            phylum=lineage.get(
                "phylum",
                lineage.get(
                    "division",
                    "",
                ),
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
                "programme": "encyclopedia_of_life",
                "reference_only": True,
                "enrichment_only": True,
                "page_id": normalize_space(
                    self._first_value(
                        raw,
                        "page_id",
                        "pageId",
                        "pageID",
                        "id",
                    )
                ),
                "taxon_concept_id": normalize_space(
                    self._first_value(
                        raw,
                        "taxon_concept_id",
                        "taxonConceptId",
                        "taxonConceptID",
                    )
                ),
                "accepted_provider_id": accepted_provider_id,
                "lineage": lineage,
                "parent": {
                    "id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_id",
                            "parentId",
                            "parent_page_id",
                            "parentPageId",
                            "parent_taxon_id",
                            "parentTaxonId",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            raw,
                            "parent_name",
                            "parentName",
                        )
                    ),
                    "rank": normalize_space(
                        self._first_value(
                            raw,
                            "parent_rank",
                            "parentRank",
                        )
                    ).casefold(),
                },
                "vernacular_names": self._normalize_vernacular_names(
                    self._first_value(
                        raw,
                        "vernacular_names",
                        "vernacularNames",
                        "common_names",
                        "commonNames",
                    )
                ),
                "descriptions": self._normalize_descriptions(
                    self._first_value(
                        raw,
                        "descriptions",
                        "description",
                        "texts",
                        "text_objects",
                        "textObjects",
                    )
                ),
                "media": self._normalize_media(
                    self._first_value(
                        raw,
                        "media",
                        "media_objects",
                        "mediaObjects",
                        "images",
                        "videos",
                        "sounds",
                    )
                ),
                "traits": self._normalize_traits(
                    self._first_value(
                        raw,
                        "traits",
                        "trait_data",
                        "traitData",
                        "data_objects",
                        "dataObjects",
                    )
                ),
                "distribution": self._list_value(
                    self._first_value(
                        raw,
                        "distribution",
                        "distributions",
                        "occurrences",
                    )
                ),
                "habitats": self._list_value(
                    self._first_value(
                        raw,
                        "habitats",
                        "habitat",
                    )
                ),
                "associations": self._list_value(
                    self._first_value(
                        raw,
                        "associations",
                        "species_associations",
                        "speciesAssociations",
                    )
                ),
                "conservation": self._list_value(
                    self._first_value(
                        raw,
                        "conservation",
                        "conservation_status",
                        "conservationStatus",
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
                "attribution": self._normalize_attribution(
                    raw
                ),
                "licenses": self._list_value(
                    self._first_value(
                        raw,
                        "licenses",
                        "license",
                        "rights",
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
                "collections": self._list_value(
                    self._first_value(
                        raw,
                        "collections",
                        "collection",
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
        """Extract major lineage values from direct fields or hierarchy data."""

        lineage: dict[str, str] = {
            "domain": normalize_space(
                raw.get(
                    "domain"
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
            "division": normalize_space(
                raw.get(
                    "division"
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

        hierarchy = cls._first_value(
            raw,
            "lineage",
            "hierarchy",
            "classification",
            "ancestors",
        )

        for item in cls._list_value(
            hierarchy
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            rank = normalize_space(
                cls._first_value(
                    item,
                    "rank",
                    "taxon_rank",
                    "taxonRank",
                )
            ).casefold()

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
                "taxon_concepts",
                "taxonConcepts",
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
                        "canonical_name",
                        "canonicalName",
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
    def _normalize_vernacular_names(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize EOL vernacular-name objects without discarding fields."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if isinstance(
                item,
                Mapping,
            ):
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "vernacular_name",
                        "vernacularName",
                        "common_name",
                        "commonName",
                    )
                )

                if not name:
                    continue

                entry = dict(
                    item
                )

                entry.update(
                    {
                        "name": name,
                        "language": normalize_space(
                            cls._first_value(
                                item,
                                "language",
                                "lang",
                                "language_code",
                                "languageCode",
                            )
                        ),
                        "preferred": cls._optional_bool(
                            cls._first_value(
                                item,
                                "preferred",
                                "is_preferred",
                                "isPreferred",
                            )
                        ),
                    }
                )

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
                            "language": "",
                            "preferred": None,
                        }
                    )

        return result

    @classmethod
    def _normalize_descriptions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize text objects and narrative descriptions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if isinstance(
                item,
                Mapping,
            ):
                text = normalize_space(
                    cls._first_value(
                        item,
                        "text",
                        "description",
                        "body",
                        "value",
                    )
                )

                if not text:
                    continue

                entry = dict(
                    item
                )

                entry.update(
                    {
                        "text": text,
                        "subject": normalize_space(
                            cls._first_value(
                                item,
                                "subject",
                                "chapter",
                                "section",
                            )
                        ),
                        "language": normalize_space(
                            cls._first_value(
                                item,
                                "language",
                                "lang",
                            )
                        ),
                    }
                )

                result.append(
                    entry
                )

            else:
                text = normalize_space(
                    item
                )

                if text:
                    result.append(
                        {
                            "text": text,
                            "subject": "",
                            "language": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_media(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize image, video, audio, and other media objects."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                url = normalize_space(
                    item
                )

                if url:
                    result.append(
                        {
                            "url": url,
                            "media_type": "",
                        }
                    )

                continue

            entry = dict(
                item
            )

            entry.update(
                {
                    "url": normalize_space(
                        cls._first_value(
                            item,
                            "url",
                            "media_url",
                            "mediaUrl",
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
                    "media_type": normalize_space(
                        cls._first_value(
                            item,
                            "media_type",
                            "mediaType",
                            "type",
                        )
                    ).casefold(),
                    "title": normalize_space(
                        cls._first_value(
                            item,
                            "title",
                            "name",
                        )
                    ),
                    "license": normalize_space(
                        cls._first_value(
                            item,
                            "license",
                            "rights",
                        )
                    ),
                    "rights_holder": normalize_space(
                        cls._first_value(
                            item,
                            "rights_holder",
                            "rightsHolder",
                            "owner",
                            "creator",
                        )
                    ),
                }
            )

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
    def _normalize_traits(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize structured trait and measurement records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            entry = dict(
                item
            )

            entry.update(
                {
                    "predicate": normalize_space(
                        cls._first_value(
                            item,
                            "predicate",
                            "trait",
                            "measurement_type",
                            "measurementType",
                            "property",
                        )
                    ),
                    "value": cls._first_value(
                        item,
                        "value",
                        "measurement_value",
                        "measurementValue",
                        "object",
                    ),
                    "unit": normalize_space(
                        cls._first_value(
                            item,
                            "unit",
                            "measurement_unit",
                            "measurementUnit",
                        )
                    ),
                    "source": normalize_space(
                        cls._first_value(
                            item,
                            "source",
                            "dataset",
                        )
                    ),
                }
            )

            if entry.get(
                "predicate"
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
        """Normalize bibliographic and web references."""

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
                        "title": normalize_space(
                            cls._first_value(
                                item,
                                "title",
                                "citation",
                                "reference",
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
                value_text = normalize_space(
                    item
                )

                if value_text:
                    result.append(
                        {
                            "title": value_text,
                            "url": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_identifiers(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize external identifiers and cross-references."""

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

                if not identifier:
                    continue

                result.append(
                    {
                        "identifier": identifier,
                        "source": normalize_space(
                            cls._first_value(
                                item,
                                "source",
                                "database",
                                "namespace",
                            )
                        ),
                    }
                )

            else:
                identifier = normalize_space(
                    item
                )

                if identifier:
                    result.append(
                        {
                            "identifier": identifier,
                            "source": "",
                        }
                    )

        return result

    @classmethod
    def _normalize_attribution(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize record-level attribution metadata."""

        return {
            "creator": normalize_space(
                cls._first_value(
                    raw,
                    "creator",
                    "author",
                    "contributor",
                )
            ),
            "rights_holder": normalize_space(
                cls._first_value(
                    raw,
                    "rights_holder",
                    "rightsHolder",
                    "owner",
                )
            ),
            "license": normalize_space(
                cls._first_value(
                    raw,
                    "license",
                    "rights",
                )
            ),
            "source": normalize_space(
                cls._first_value(
                    raw,
                    "source",
                    "dataset",
                    "resource",
                )
            ),
        }

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize EOL taxonomic status terms."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "ambiguous": "unknown",
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
                f"Invalid Encyclopedia of Life cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Encyclopedia of Life cursor must be non-negative."
            )

        return offset

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        words = normalize_space(
            scientific_name
        ).split()

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
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
