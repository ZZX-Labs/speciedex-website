#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/crossref.py

Crossref provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for scholarly metadata, DOI records,
titles, authors, containers, publishers, publication dates, licenses,
references, funders, subjects, biodiversity-related taxonomic mentions,
external identifiers, and provenance data.

Each source record is normalized into the shared Speciedex Taxon contract.
Crossref records are bibliographic rather than taxonomic authorities, so they
are stored as reference-oriented records while preserving any taxonomic names
or concepts resolved from the source.

Required provider configuration:

    {
        "name": "crossref",
        "path": "static/data/providers/crossref/works.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Crossref",
        "source_url": "https://api.crossref.org/",
        "allow_reference_only_records": true
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
    """File-backed Crossref provider."""

    PROVIDER_NAME = "crossref"

    DEFAULT_SOURCE_NAME = "Crossref"
    DEFAULT_SOURCE_URL = "https://api.crossref.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable Crossref JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Crossref export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Crossref path is not a file: {source_path}"
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
                            f"Invalid Crossref JSON at "
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
        """Resolve the configured Crossref JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "Crossref provider requires a path."
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
        """Normalize one Crossref work record."""

        doi = normalize_space(
            self._first_value(
                raw,
                "DOI",
                "doi",
                "crossref_doi",
                "crossrefDoi",
            )
        )

        provider_id = doi or normalize_space(
            self._first_value(
                raw,
                "crossref_id",
                "crossrefId",
                "work_id",
                "workId",
                "id",
            )
        )

        taxon_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "taxon_name",
                "taxonName",
                "name",
            )
        )

        title = self._first_text(
            self._first_value(
                raw,
                "title",
                "titles",
                "work_title",
                "workTitle",
            )
        )

        allow_reference_only = bool(
            self.definition.get(
                "allow_reference_only_records",
                True,
            )
        )

        scientific_name = taxon_name

        if not scientific_name and allow_reference_only:
            scientific_name = title or (
                f"Crossref work {provider_id}"
                if provider_id
                else ""
            )

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
            )
        ) or scientific_name

        is_taxonomic = bool(taxon_name)

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
                self._infer_rank(canonical_name)
                if is_taxonomic
                else "reference"
            )

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "work_status",
                "workStatus",
            ),
            taxonomic=is_taxonomic,
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_name_id",
                "acceptedNameId",
            )
        )

        source_url = normalize_space(
            self._first_value(
                raw,
                "URL",
                "url",
                "source_url",
                "sourceUrl",
                "resource.primary.URL",
            )
        )

        if not source_url and doi:
            source_url = f"https://doi.org/{doi}"

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
            authorship=self._author_string(raw),
            kingdom=lineage.get("kingdom", ""),
            phylum=lineage.get("phylum", ""),
            class_name=lineage.get("class", ""),
            order=lineage.get("order", ""),
            family=lineage.get("family", ""),
            genus=lineage.get("genus", ""),
            accepted_provider_id=accepted_provider_id,
            source_url=source_url,
            source_modified=self._date_string(
                self._first_value(
                    raw,
                    "indexed",
                    "deposited",
                    "updated",
                    "modified",
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
                "programme": "crossref",
                "reference_only": not is_taxonomic,
                "doi": doi,
                "lineage": lineage,
                "work": {
                    "type": normalize_space(
                        self._first_value(
                            raw,
                            "type",
                            "work_type",
                            "workType",
                        )
                    ),
                    "title": title,
                    "subtitle": self._first_text(
                        self._first_value(
                            raw,
                            "subtitle",
                            "subtitles",
                        )
                    ),
                    "short_title": self._first_text(
                        self._first_value(
                            raw,
                            "short-title",
                            "short_title",
                            "shortTitle",
                        )
                    ),
                    "original_title": self._first_text(
                        self._first_value(
                            raw,
                            "original-title",
                            "original_title",
                            "originalTitle",
                        )
                    ),
                    "abstract": normalize_space(
                        self._first_value(
                            raw,
                            "abstract",
                        )
                    ),
                    "language": normalize_space(
                        self._first_value(
                            raw,
                            "language",
                        )
                    ),
                    "publisher": normalize_space(
                        self._first_value(
                            raw,
                            "publisher",
                        )
                    ),
                    "publisher_location": normalize_space(
                        self._first_value(
                            raw,
                            "publisher-location",
                            "publisher_location",
                            "publisherLocation",
                        )
                    ),
                    "container_title": self._first_text(
                        self._first_value(
                            raw,
                            "container-title",
                            "container_title",
                            "containerTitle",
                        )
                    ),
                    "short_container_title": self._first_text(
                        self._first_value(
                            raw,
                            "short-container-title",
                            "short_container_title",
                            "shortContainerTitle",
                        )
                    ),
                    "volume": normalize_space(
                        self._first_value(raw, "volume")
                    ),
                    "issue": normalize_space(
                        self._first_value(raw, "issue")
                    ),
                    "page": normalize_space(
                        self._first_value(
                            raw,
                            "page",
                            "pages",
                        )
                    ),
                    "article_number": normalize_space(
                        self._first_value(
                            raw,
                            "article-number",
                            "article_number",
                            "articleNumber",
                        )
                    ),
                    "edition_number": normalize_space(
                        self._first_value(
                            raw,
                            "edition-number",
                            "edition_number",
                            "editionNumber",
                        )
                    ),
                },
                "dates": {
                    "published": self._date_string(
                        self._first_value(
                            raw,
                            "published",
                            "published-print",
                            "published-online",
                        )
                    ),
                    "published_print": self._date_string(
                        self._first_value(
                            raw,
                            "published-print",
                            "published_print",
                            "publishedPrint",
                        )
                    ),
                    "published_online": self._date_string(
                        self._first_value(
                            raw,
                            "published-online",
                            "published_online",
                            "publishedOnline",
                        )
                    ),
                    "issued": self._date_string(
                        self._first_value(raw, "issued")
                    ),
                    "created": self._date_string(
                        self._first_value(raw, "created")
                    ),
                    "deposited": self._date_string(
                        self._first_value(raw, "deposited")
                    ),
                    "indexed": self._date_string(
                        self._first_value(raw, "indexed")
                    ),
                },
                "authors": self._normalize_people(
                    self._first_value(
                        raw,
                        "author",
                        "authors",
                    )
                ),
                "editors": self._normalize_people(
                    self._first_value(
                        raw,
                        "editor",
                        "editors",
                    )
                ),
                "translators": self._normalize_people(
                    self._first_value(
                        raw,
                        "translator",
                        "translators",
                    )
                ),
                "chairs": self._normalize_people(
                    self._first_value(
                        raw,
                        "chair",
                        "chairs",
                    )
                ),
                "funders": self._normalize_funders(
                    self._first_value(
                        raw,
                        "funder",
                        "funders",
                    )
                ),
                "subjects": self._normalize_subjects(
                    self._first_value(
                        raw,
                        "subject",
                        "subjects",
                        "keywords",
                    )
                ),
                "licenses": self._normalize_licenses(
                    self._first_value(
                        raw,
                        "license",
                        "licenses",
                    )
                ),
                "links": self._normalize_links(
                    self._first_value(
                        raw,
                        "link",
                        "links",
                    )
                ),
                "references": self._normalize_references(
                    self._first_value(
                        raw,
                        "reference",
                        "references",
                    )
                ),
                "relation": self._normalize_relations(
                    self._first_value(
                        raw,
                        "relation",
                        "relations",
                    )
                ),
                "assertions": self._normalize_assertions(
                    self._first_value(
                        raw,
                        "assertion",
                        "assertions",
                    )
                ),
                "clinical_trials": self._list_value(
                    self._first_value(
                        raw,
                        "clinical-trial-number",
                        "clinical_trial_number",
                        "clinicalTrialNumber",
                    )
                ),
                "standards": self._list_value(
                    self._first_value(
                        raw,
                        "standards-body",
                        "standards_body",
                        "standardsBody",
                    )
                ),
                "taxonomic_mentions": self._normalize_taxonomic_mentions(
                    self._first_value(
                        raw,
                        "taxonomic_mentions",
                        "taxonomicMentions",
                        "species_mentions",
                        "speciesMentions",
                        "names",
                    )
                ),
                "identifiers": self._normalize_identifiers(
                    self._first_value(
                        raw,
                        "identifiers",
                        "alternative-id",
                        "alternative_id",
                        "alternativeId",
                    ),
                    raw=raw,
                ),
                "metrics": {
                    "reference_count": self._optional_int(
                        self._first_value(
                            raw,
                            "reference-count",
                            "reference_count",
                            "referenceCount",
                        )
                    ),
                    "is_referenced_by_count": self._optional_int(
                        self._first_value(
                            raw,
                            "is-referenced-by-count",
                            "is_referenced_by_count",
                            "isReferencedByCount",
                        )
                    ),
                    "score": self._optional_float(
                        self._first_value(raw, "score")
                    ),
                },
                "member": {
                    "member_id": normalize_space(
                        self._first_value(
                            raw,
                            "member",
                            "member_id",
                            "memberId",
                        )
                    ),
                    "prefix": normalize_space(
                        self._first_value(raw, "prefix")
                    ),
                    "source": normalize_space(
                        self._first_value(raw, "source")
                    ),
                },
                "resource": self._first_value(
                    raw,
                    "resource",
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
        """Extract optional taxonomic lineage from enriched records."""

        lineage = {
            "kingdom": normalize_space(raw.get("kingdom")),
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
        """Extract optional taxonomic synonyms from enriched work records."""

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
    def _normalize_people(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize Crossref contributor records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                family = normalize_space(
                    cls._first_value(
                        item,
                        "family",
                        "family_name",
                        "familyName",
                        "surname",
                    )
                )
                given = normalize_space(
                    cls._first_value(
                        item,
                        "given",
                        "given_name",
                        "givenName",
                    )
                )
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "literal",
                    )
                ) or normalize_space(f"{given} {family}")

                affiliations = []

                for affiliation in cls._list_value(
                    item.get("affiliation")
                ):
                    if isinstance(affiliation, Mapping):
                        affiliation_name = normalize_space(
                            cls._first_value(
                                affiliation,
                                "name",
                                "institution",
                            )
                        )
                    else:
                        affiliation_name = normalize_space(
                            affiliation
                        )

                    if affiliation_name:
                        affiliations.append(affiliation_name)

                result.append(
                    {
                        "name": name,
                        "given": given,
                        "family": family,
                        "sequence": normalize_space(
                            cls._first_value(
                                item,
                                "sequence",
                            )
                        ),
                        "orcid": normalize_space(
                            cls._first_value(
                                item,
                                "ORCID",
                                "orcid",
                            )
                        ),
                        "authenticated_orcid": cls._optional_bool(
                            cls._first_value(
                                item,
                                "authenticated-orcid",
                                "authenticated_orcid",
                                "authenticatedOrcid",
                            )
                        ),
                        "affiliations": affiliations,
                        "raw": dict(item),
                    }
                )
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "given": "",
                            "family": "",
                            "sequence": "",
                            "orcid": "",
                            "authenticated_orcid": None,
                            "affiliations": [],
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_funders(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize Crossref funder metadata."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                award = [
                    normalize_space(entry)
                    for entry in cls._list_value(
                        cls._first_value(
                            item,
                            "award",
                            "awards",
                        )
                    )
                    if normalize_space(entry)
                ]

                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "funder_name",
                                "funderName",
                            )
                        ),
                        "doi": normalize_space(
                            cls._first_value(
                                item,
                                "DOI",
                                "doi",
                            )
                        ),
                        "doi_asserted_by": normalize_space(
                            cls._first_value(
                                item,
                                "doi-asserted-by",
                                "doi_asserted_by",
                                "doiAssertedBy",
                            )
                        ),
                        "awards": award,
                        "raw": dict(item),
                    }
                )
            else:
                name = normalize_space(item)

                if name:
                    result.append(
                        {
                            "name": name,
                            "doi": "",
                            "doi_asserted_by": "",
                            "awards": [],
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_subjects(
        cls,
        value: Any,
    ) -> list[str]:
        """Normalize subject and keyword terms."""

        result: list[str] = []
        seen: set[str] = set()

        for item in cls._list_value(value):
            text = normalize_space(
                cls._first_value(
                    item,
                    "name",
                    "subject",
                    "value",
                )
                if isinstance(item, Mapping)
                else item
            )
            key = text.casefold()

            if not text or key in seen:
                continue

            seen.add(key)
            result.append(text)

        return result

    @classmethod
    def _normalize_licenses(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize license records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "URL",
                                "url",
                            )
                        ),
                        "start": cls._date_string(
                            cls._first_value(
                                item,
                                "start",
                                "start_date",
                                "startDate",
                            )
                        ),
                        "delay_days": cls._optional_int(
                            cls._first_value(
                                item,
                                "delay-in-days",
                                "delay_in_days",
                                "delayInDays",
                            )
                        ),
                        "content_version": normalize_space(
                            cls._first_value(
                                item,
                                "content-version",
                                "content_version",
                                "contentVersion",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                url = normalize_space(item)

                if url:
                    result.append(
                        {
                            "url": url,
                            "start": "",
                            "delay_days": None,
                            "content_version": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_links(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize resource links."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                url = normalize_space(item)

                if url:
                    result.append(
                        {
                            "url": url,
                            "content_type": "",
                            "content_version": "",
                            "intended_application": "",
                            "raw": item,
                        }
                    )
                continue

            result.append(
                {
                    "url": normalize_space(
                        cls._first_value(
                            item,
                            "URL",
                            "url",
                        )
                    ),
                    "content_type": normalize_space(
                        cls._first_value(
                            item,
                            "content-type",
                            "content_type",
                            "contentType",
                        )
                    ),
                    "content_version": normalize_space(
                        cls._first_value(
                            item,
                            "content-version",
                            "content_version",
                            "contentVersion",
                        )
                    ),
                    "intended_application": normalize_space(
                        cls._first_value(
                            item,
                            "intended-application",
                            "intended_application",
                            "intendedApplication",
                        )
                    ),
                    "raw": dict(item),
                }
            )

        return result

    @classmethod
    def _normalize_references(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize cited-reference metadata."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                citation = normalize_space(item)

                if citation:
                    result.append(
                        {
                            "key": "",
                            "doi": "",
                            "article_title": "",
                            "journal_title": "",
                            "author": "",
                            "year": "",
                            "volume": "",
                            "issue": "",
                            "first_page": "",
                            "unstructured": citation,
                            "raw": item,
                        }
                    )
                continue

            result.append(
                {
                    "key": normalize_space(
                        cls._first_value(item, "key")
                    ),
                    "doi": normalize_space(
                        cls._first_value(
                            item,
                            "DOI",
                            "doi",
                        )
                    ),
                    "article_title": normalize_space(
                        cls._first_value(
                            item,
                            "article-title",
                            "article_title",
                            "articleTitle",
                        )
                    ),
                    "journal_title": normalize_space(
                        cls._first_value(
                            item,
                            "journal-title",
                            "journal_title",
                            "journalTitle",
                        )
                    ),
                    "author": normalize_space(
                        cls._first_value(item, "author")
                    ),
                    "year": normalize_space(
                        cls._first_value(item, "year")
                    ),
                    "volume": normalize_space(
                        cls._first_value(item, "volume")
                    ),
                    "issue": normalize_space(
                        cls._first_value(item, "issue")
                    ),
                    "first_page": normalize_space(
                        cls._first_value(
                            item,
                            "first-page",
                            "first_page",
                            "firstPage",
                        )
                    ),
                    "unstructured": normalize_space(
                        cls._first_value(
                            item,
                            "unstructured",
                        )
                    ),
                    "raw": dict(item),
                }
            )

        return result

    @classmethod
    def _normalize_relations(
        cls,
        value: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """Normalize Crossref work relations."""

        if not isinstance(value, Mapping):
            return {}

        result: dict[str, list[dict[str, Any]]] = {}

        for relation_type, entries in value.items():
            normalized_entries: list[dict[str, Any]] = []

            for entry in cls._list_value(entries):
                if isinstance(entry, Mapping):
                    normalized_entries.append(
                        {
                            "id": normalize_space(
                                cls._first_value(
                                    entry,
                                    "id",
                                    "identifier",
                                )
                            ),
                            "id_type": normalize_space(
                                cls._first_value(
                                    entry,
                                    "id-type",
                                    "id_type",
                                    "idType",
                                )
                            ),
                            "asserted_by": normalize_space(
                                cls._first_value(
                                    entry,
                                    "asserted-by",
                                    "asserted_by",
                                    "assertedBy",
                                )
                            ),
                            "raw": dict(entry),
                        }
                    )
                else:
                    normalized_entries.append(
                        {
                            "id": normalize_space(entry),
                            "id_type": "",
                            "asserted_by": "",
                            "raw": entry,
                        }
                    )

            result[str(relation_type)] = normalized_entries

        return result

    @classmethod
    def _normalize_assertions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize publisher assertions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                            )
                        ),
                        "label": normalize_space(
                            cls._first_value(
                                item,
                                "label",
                            )
                        ),
                        "value": cls._first_value(
                            item,
                            "value",
                        ),
                        "group": normalize_space(
                            cls._first_value(
                                item,
                                "group",
                            )
                        ),
                        "order": cls._optional_int(
                            cls._first_value(
                                item,
                                "order",
                            )
                        ),
                        "raw": dict(item),
                    }
                )

        return result

    @classmethod
    def _normalize_taxonomic_mentions(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize extracted or enriched taxonomic-name mentions."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "scientific_name",
                                "scientificName",
                            )
                        ),
                        "canonical_name": normalize_space(
                            cls._first_value(
                                item,
                                "canonical_name",
                                "canonicalName",
                            )
                        ),
                        "rank": cls._normalize_rank(
                            cls._first_value(
                                item,
                                "rank",
                                "taxon_rank",
                                "taxonRank",
                            )
                        ),
                        "identifier": normalize_space(
                            cls._first_value(
                                item,
                                "identifier",
                                "taxon_id",
                                "taxonId",
                            )
                        ),
                        "source": normalize_space(
                            cls._first_value(
                                item,
                                "source",
                                "database",
                            )
                        ),
                        "context": normalize_space(
                            cls._first_value(
                                item,
                                "context",
                                "snippet",
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
                            "canonical_name": name,
                            "rank": cls._infer_rank(name),
                            "identifier": "",
                            "source": "",
                            "context": "",
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
        """Normalize Crossref and external identifiers."""

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
                        "type",
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
            "DOI": "DOI",
            "doi": "DOI",
            "ISBN": "ISBN",
            "isbn": "ISBN",
            "ISSN": "ISSN",
            "issn": "ISSN",
            "archive": "Archive",
            "PMID": "PubMed",
            "pmid": "PubMed",
            "pmcid": "PubMed Central",
            "arxiv": "arXiv",
            "openalex_id": "OpenAlex",
            "openalexId": "OpenAlex",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "bhl_id": "Biodiversity Heritage Library",
            "bhlId": "Biodiversity Heritage Library",
        }

        seen = {
            (
                entry["source"].casefold(),
                entry["identifier"].casefold(),
            )
            for entry in result
        }

        for field, source in known_fields.items():
            raw_value = raw.get(field)

            for identifier in cls._list_value(raw_value):
                normalized = normalize_space(identifier)
                key = (
                    source.casefold(),
                    normalized.casefold(),
                )

                if not normalized or key in seen:
                    continue

                seen.add(key)
                result.append(
                    {
                        "identifier": normalized,
                        "source": source,
                    }
                )

        return result

    @classmethod
    def _author_string(
        cls,
        raw: Mapping[str, Any],
    ) -> str:
        """Create a compact author string from Crossref contributors."""

        authors = cls._normalize_people(
            cls._first_value(
                raw,
                "author",
                "authors",
            )
        )

        names = [
            normalize_space(author.get("name"))
            for author in authors
            if normalize_space(author.get("name"))
        ]

        return "; ".join(names)

    @classmethod
    def _date_string(
        cls,
        value: Any,
    ) -> str:
        """Normalize Crossref date structures."""

        if value is None:
            return ""

        if isinstance(value, str):
            return normalize_space(value)

        if isinstance(value, Mapping):
            if value.get("date-time"):
                return normalize_space(value.get("date-time"))

            if value.get("timestamp"):
                return normalize_space(value.get("timestamp"))

            date_parts = value.get("date-parts")

            if date_parts:
                parts = cls._list_value(date_parts)

                if parts and isinstance(parts[0], list):
                    parts = parts[0]

                normalized = [
                    safe_int(part, 0)
                    for part in parts
                ]

                normalized = [
                    part
                    for part in normalized
                    if part > 0
                ]

                if normalized:
                    year = normalized[0]
                    month = (
                        f"-{normalized[1]:02d}"
                        if len(normalized) > 1
                        else ""
                    )
                    day = (
                        f"-{normalized[2]:02d}"
                        if len(normalized) > 2
                        else ""
                    )
                    return f"{year}{month}{day}"

        if isinstance(value, list):
            parts = value[0] if value and isinstance(value[0], list) else value

            normalized = [
                safe_int(part, 0)
                for part in parts
            ]

            normalized = [
                part
                for part in normalized
                if part > 0
            ]

            if normalized:
                year = normalized[0]
                month = (
                    f"-{normalized[1]:02d}"
                    if len(normalized) > 1
                    else ""
                )
                day = (
                    f"-{normalized[2]:02d}"
                    if len(normalized) > 2
                    else ""
                )
                return f"{year}{month}{day}"

        return normalize_space(value)

    @staticmethod
    def _first_text(value: Any) -> str:
        """Return the first useful string from a scalar or sequence."""

        if isinstance(value, list):
            for item in value:
                text = normalize_space(item)

                if text:
                    return text

            return ""

        return normalize_space(value)

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize optional taxonomic ranks."""

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
    def _normalize_status(
        value: Any,
        *,
        taxonomic: bool,
    ) -> str:
        """Normalize Crossref work or taxonomic status."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "current": "accepted",
            "published": "reference",
            "registered": "reference",
            "posted": "reference",
            "reference": "reference",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "withdrawn": "inactive",
            "retracted": "inactive",
            "removed": "inactive",
            "misapplied": "misapplied",
            "doubtful": "unknown",
            "unresolved": "unknown",
        }

        if status:
            return aliases.get(status, status)

        return "unknown" if taxonomic else "reference"

    @staticmethod
    def _infer_rank(scientific_name: str) -> str:
        """Infer taxonomic rank from a scientific-name string."""

        words = normalize_space(scientific_name).split()
        lowered = {word.casefold() for word in words}

        if "subsp." in lowered or "subspecies" in lowered:
            return "subspecies"

        if "var." in lowered or "variety" in lowered:
            return "variety"

        if "f." in lowered or "forma" in lowered:
            return "form"

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
                f"Invalid Crossref cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Crossref cursor must be non-negative."
            )

        return offset

    @staticmethod
    def _first_value(
        record: Mapping[str, Any],
        *keys: str,
    ) -> Any:
        for key in keys:
            if "." in key:
                current: Any = record

                for part in key.split("."):
                    if not isinstance(current, Mapping):
                        current = None
                        break

                    current = current.get(part)

                value = current
            else:
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
