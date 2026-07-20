#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/plazi_treatmentbank.py

Plazi TreatmentBank provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It is intended for Plazi TreatmentBank taxonomic
treatments, treatment identifiers, source-document metadata, scientific names,
nomenclatural acts, diagnoses, descriptions, materials examined, cited
specimens, collecting events, geographic records, figures, references,
external identifiers, and provenance metadata.

Plazi TreatmentBank is principally a taxonomic-literature and treatment
repository. Records are represented through the shared Speciedex Taxon
contract as reference-oriented taxonomic treatment entities while preserving
taxon identity and accepted-name relationships when available.

The complete source object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "plazi_treatmentbank",
        "path": "static/data/providers/plazi-treatmentbank/treatments.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Plazi TreatmentBank",
        "source_url": "https://treatment.plazi.org/"
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
    """File-backed Plazi TreatmentBank provider."""

    PROVIDER_NAME = "plazi_treatmentbank"

    DEFAULT_SOURCE_NAME = "Plazi TreatmentBank"
    DEFAULT_SOURCE_URL = "https://treatment.plazi.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable Plazi TreatmentBank JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Plazi TreatmentBank export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Plazi TreatmentBank path is not a file: {source_path}"
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
                            f"Invalid Plazi TreatmentBank JSON at "
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
        """Resolve the configured TreatmentBank JSONL source path."""

        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                "Plazi TreatmentBank provider requires a path."
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
        """Normalize one Plazi taxonomic treatment record."""

        treatment_id = normalize_space(
            self._first_value(
                raw,
                "treatment_id",
                "treatmentId",
                "treatment_uuid",
                "treatmentUuid",
                "treatment_lsid",
                "treatmentLsid",
                "uuid",
                "id",
            )
        )

        document_id = normalize_space(
            self._first_value(
                raw,
                "document_id",
                "documentId",
                "source_document_id",
                "sourceDocumentId",
                "article_id",
                "articleId",
            )
        )

        provider_id = treatment_id or document_id

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
            )
        )

        infraspecific_epithet = normalize_space(
            self._first_value(
                raw,
                "infraspecific_epithet",
                "infraspecificEpithet",
                "subspecies",
                "variety",
                "form",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "scientific_name",
                "scientificName",
                "taxon_name",
                "taxonName",
                "treated_taxon_name",
                "treatedTaxonName",
                "name",
            )
        )

        if not scientific_name and genus and specific_epithet:
            scientific_name = f"{genus} {specific_epithet}"

            if infraspecific_epithet:
                scientific_name = (
                    f"{scientific_name} {infraspecific_epithet}"
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
            rank = self._infer_rank(canonical_name)

        status = self._normalize_status(
            self._first_value(
                raw,
                "status",
                "taxonomic_status",
                "taxonomicStatus",
                "nomenclatural_status",
                "nomenclaturalStatus",
                "treatment_status",
                "treatmentStatus",
            )
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "accepted_taxon_id",
                "acceptedTaxonId",
                "accepted_name_id",
                "acceptedNameId",
                "accepted_name_usage_id",
                "acceptedNameUsageId",
            )
        )

        source_url = normalize_space(
            self._first_value(
                raw,
                "url",
                "source_url",
                "sourceUrl",
                "treatment_url",
                "treatmentUrl",
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
            source_url = f"{base}/id/{provider_id}"

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
            kingdom=lineage.get("kingdom", ""),
            phylum=lineage.get("phylum", ""),
            class_name=lineage.get("class", ""),
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
                "programme": "plazi_treatmentbank",
                "reference_only": True,
                "entity_type": "taxonomic_treatment",
                "treatment_id": treatment_id,
                "document_id": document_id,
                "lineage": lineage,
                "name": {
                    "genus": genus,
                    "specific_epithet": specific_epithet,
                    "infraspecific_epithet": infraspecific_epithet,
                    "verbatim_name": normalize_space(
                        self._first_value(
                            raw,
                            "verbatim_name",
                            "verbatimName",
                            "name_verbatim",
                            "nameVerbatim",
                        )
                    ),
                    "original_name": normalize_space(
                        self._first_value(
                            raw,
                            "original_name",
                            "originalName",
                            "original_combination",
                            "originalCombination",
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
                },
                "nomenclature": {
                    "nomenclatural_act": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_act",
                            "nomenclaturalAct",
                            "taxonomic_act",
                            "taxonomicAct",
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
                        )
                    ),
                    "new_species": self._optional_bool(
                        self._first_value(
                            raw,
                            "new_species",
                            "newSpecies",
                            "sp_nov",
                            "spNov",
                        )
                    ),
                    "new_genus": self._optional_bool(
                        self._first_value(
                            raw,
                            "new_genus",
                            "newGenus",
                            "gen_nov",
                            "genNov",
                        )
                    ),
                    "new_combination": self._optional_bool(
                        self._first_value(
                            raw,
                            "new_combination",
                            "newCombination",
                            "comb_nov",
                            "combNov",
                        )
                    ),
                    "replacement_name": normalize_space(
                        self._first_value(
                            raw,
                            "replacement_name",
                            "replacementName",
                            "nomen_novum",
                            "nomenNovum",
                        )
                    ),
                    "publication": normalize_space(
                        self._first_value(
                            raw,
                            "name_published_in",
                            "namePublishedIn",
                            "publication",
                        )
                    ),
                    "publication_year": normalize_space(
                        self._first_value(
                            raw,
                            "publication_year",
                            "publicationYear",
                            "year",
                        )
                    ),
                },
                "treatment": {
                    "title": normalize_space(
                        self._first_value(
                            raw,
                            "treatment_title",
                            "treatmentTitle",
                            "title",
                        )
                    ),
                    "treatment_type": normalize_space(
                        self._first_value(
                            raw,
                            "treatment_type",
                            "treatmentType",
                            "type",
                        )
                    ),
                    "treatment_status": normalize_space(
                        self._first_value(
                            raw,
                            "treatment_status",
                            "treatmentStatus",
                        )
                    ),
                    "diagnosis": self._text_value(
                        self._first_value(
                            raw,
                            "diagnosis",
                            "differential_diagnosis",
                            "differentialDiagnosis",
                        )
                    ),
                    "description": self._text_value(
                        self._first_value(
                            raw,
                            "description",
                            "taxon_description",
                            "taxonDescription",
                        )
                    ),
                    "discussion": self._text_value(
                        self._first_value(
                            raw,
                            "discussion",
                            "remarks",
                        )
                    ),
                    "etymology": self._text_value(
                        self._first_value(
                            raw,
                            "etymology",
                        )
                    ),
                    "biology": self._text_value(
                        self._first_value(
                            raw,
                            "biology",
                            "natural_history",
                            "naturalHistory",
                        )
                    ),
                    "distribution_text": self._text_value(
                        self._first_value(
                            raw,
                            "distribution_text",
                            "distributionText",
                            "distribution",
                        )
                    ),
                    "keys": self._normalize_keys(
                        self._first_value(
                            raw,
                            "identification_keys",
                            "identificationKeys",
                            "keys",
                        )
                    ),
                    "sections": self._normalize_sections(
                        self._first_value(
                            raw,
                            "sections",
                            "treatment_sections",
                            "treatmentSections",
                        )
                    ),
                },
                "source_document": self._normalize_document(raw),
                "materials_examined": self._normalize_materials_examined(
                    self._first_value(
                        raw,
                        "materials_examined",
                        "materialsExamined",
                        "material_citations",
                        "materialCitations",
                        "specimens",
                    )
                ),
                "type_material": self._normalize_type_material(
                    self._first_value(
                        raw,
                        "type_material",
                        "typeMaterial",
                        "types",
                    )
                ),
                "locations": self._normalize_locations(
                    self._first_value(
                        raw,
                        "locations",
                        "localities",
                        "distribution_records",
                        "distributionRecords",
                    )
                ),
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
                "figures": self._normalize_figures(
                    self._first_value(
                        raw,
                        "figures",
                        "figure_citations",
                        "figureCitations",
                        "media",
                        "images",
                    )
                ),
                "citations": self._normalize_citations(
                    self._first_value(
                        raw,
                        "citations",
                        "references",
                        "bibliography",
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
                "links": self._normalize_links(
                    self._first_value(
                        raw,
                        "links",
                        "related_links",
                        "relatedLinks",
                    )
                ),
                "provenance": {
                    "plazi_source": normalize_space(
                        self._first_value(
                            raw,
                            "plazi_source",
                            "plaziSource",
                        )
                    ),
                    "conversion_date": normalize_space(
                        self._first_value(
                            raw,
                            "conversion_date",
                            "conversionDate",
                        )
                    ),
                    "conversion_tool": normalize_space(
                        self._first_value(
                            raw,
                            "conversion_tool",
                            "conversionTool",
                        )
                    ),
                    "data_origin": normalize_space(
                        self._first_value(
                            raw,
                            "data_origin",
                            "dataOrigin",
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
                "notes": self._list_value(
                    self._first_value(
                        raw,
                        "notes",
                        "comments",
                        "remarks",
                    )
                ),
                "bulk_source": source_path.as_posix(),
                "raw": raw,
            },
        )

    @classmethod
    def _normalize_document(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize source publication metadata."""

        authors = cls._first_value(
            raw,
            "document_authors",
            "documentAuthors",
            "authors",
            "author",
        )

        return {
            "document_id": normalize_space(
                cls._first_value(
                    raw,
                    "document_id",
                    "documentId",
                    "source_document_id",
                    "sourceDocumentId",
                    "article_id",
                    "articleId",
                )
            ),
            "title": normalize_space(
                cls._first_value(
                    raw,
                    "document_title",
                    "documentTitle",
                    "article_title",
                    "articleTitle",
                    "publication_title",
                    "publicationTitle",
                )
            ),
            "authors": cls._normalize_people(authors),
            "journal": normalize_space(
                cls._first_value(
                    raw,
                    "journal",
                    "journal_title",
                    "journalTitle",
                    "source_title",
                    "sourceTitle",
                )
            ),
            "publisher": normalize_space(
                cls._first_value(
                    raw,
                    "publisher",
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
            "date": normalize_space(
                cls._first_value(
                    raw,
                    "publication_date",
                    "publicationDate",
                    "date",
                )
            ),
            "volume": normalize_space(raw.get("volume")),
            "issue": normalize_space(raw.get("issue")),
            "pages": normalize_space(
                cls._first_value(
                    raw,
                    "pages",
                    "page_range",
                    "pageRange",
                )
            ),
            "doi": normalize_space(raw.get("doi")).removeprefix(
                "https://doi.org/"
            ),
            "isbn": normalize_space(raw.get("isbn")),
            "issn": cls._list_value(raw.get("issn")),
            "pmid": normalize_space(raw.get("pmid")),
            "pmcid": normalize_space(raw.get("pmcid")),
            "url": normalize_space(
                cls._first_value(
                    raw,
                    "document_url",
                    "documentUrl",
                    "article_url",
                    "articleUrl",
                )
            ),
        }

    @classmethod
    def _extract_lineage(
        cls,
        raw: Mapping[str, Any],
        *,
        genus: str,
    ) -> dict[str, str]:
        """Extract taxonomic classification."""

        lineage = {
            "domain": normalize_space(
                cls._first_value(
                    raw,
                    "domain",
                    "superkingdom",
                )
            ),
            "kingdom": normalize_space(raw.get("kingdom")),
            "phylum": normalize_space(
                cls._first_value(
                    raw,
                    "phylum",
                    "division",
                )
            ),
            "class": normalize_space(raw.get("class")),
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

        if not lineage.get("kingdom") and lineage.get("domain"):
            lineage["kingdom"] = lineage["domain"]

        return lineage

    @classmethod
    def _extract_synonyms(
        cls,
        raw: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """Extract and deduplicate synonyms."""

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
    def _normalize_materials_examined(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize cited specimen and collection-event records."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if not isinstance(item, Mapping):
                text = normalize_space(item)

                if text:
                    result.append(
                        {
                            "material_id": "",
                            "type_status": "",
                            "catalog_number": "",
                            "institution_code": "",
                            "collection_code": "",
                            "scientific_name": "",
                            "collector": "",
                            "event_date": "",
                            "locality": "",
                            "country": "",
                            "latitude": None,
                            "longitude": None,
                            "verbatim": text,
                            "raw": item,
                        }
                    )
                continue

            result.append(
                {
                    "material_id": normalize_space(
                        cls._first_value(
                            item,
                            "material_id",
                            "materialId",
                            "specimen_id",
                            "specimenId",
                            "id",
                        )
                    ),
                    "type_status": normalize_space(
                        cls._first_value(
                            item,
                            "type_status",
                            "typeStatus",
                        )
                    ),
                    "catalog_number": normalize_space(
                        cls._first_value(
                            item,
                            "catalog_number",
                            "catalogNumber",
                        )
                    ),
                    "institution_code": normalize_space(
                        cls._first_value(
                            item,
                            "institution_code",
                            "institutionCode",
                        )
                    ),
                    "collection_code": normalize_space(
                        cls._first_value(
                            item,
                            "collection_code",
                            "collectionCode",
                        )
                    ),
                    "scientific_name": normalize_space(
                        cls._first_value(
                            item,
                            "scientific_name",
                            "scientificName",
                        )
                    ),
                    "collector": normalize_space(
                        cls._first_value(
                            item,
                            "collector",
                            "recorded_by",
                            "recordedBy",
                        )
                    ),
                    "event_date": normalize_space(
                        cls._first_value(
                            item,
                            "event_date",
                            "eventDate",
                            "collection_date",
                            "collectionDate",
                        )
                    ),
                    "locality": normalize_space(
                        cls._first_value(
                            item,
                            "locality",
                            "verbatim_locality",
                            "verbatimLocality",
                        )
                    ),
                    "country": normalize_space(
                        cls._first_value(item, "country")
                    ),
                    "latitude": cls._optional_float(
                        cls._first_value(
                            item,
                            "decimal_latitude",
                            "decimalLatitude",
                            "latitude",
                            "lat",
                        )
                    ),
                    "longitude": cls._optional_float(
                        cls._first_value(
                            item,
                            "decimal_longitude",
                            "decimalLongitude",
                            "longitude",
                            "lon",
                            "lng",
                        )
                    ),
                    "verbatim": normalize_space(
                        cls._first_value(
                            item,
                            "verbatim",
                            "material_citation",
                            "materialCitation",
                        )
                    ),
                    "raw": dict(item),
                }
            )

        return result

    @classmethod
    def _normalize_type_material(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize nomenclatural type material."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "type_status": normalize_space(
                            cls._first_value(
                                item,
                                "type_status",
                                "typeStatus",
                                "status",
                            )
                        ),
                        "specimen_id": normalize_space(
                            cls._first_value(
                                item,
                                "specimen_id",
                                "specimenId",
                                "id",
                            )
                        ),
                        "catalog_number": normalize_space(
                            cls._first_value(
                                item,
                                "catalog_number",
                                "catalogNumber",
                            )
                        ),
                        "repository": normalize_space(
                            cls._first_value(
                                item,
                                "repository",
                                "institution_code",
                                "institutionCode",
                            )
                        ),
                        "locality": normalize_space(
                            cls._first_value(
                                item,
                                "locality",
                                "type_locality",
                                "typeLocality",
                            )
                        ),
                        "sex": normalize_space(item.get("sex")),
                        "life_stage": normalize_space(
                            cls._first_value(
                                item,
                                "life_stage",
                                "lifeStage",
                            )
                        ),
                        "remarks": normalize_space(
                            cls._first_value(
                                item,
                                "remarks",
                                "notes",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                text = normalize_space(item)

                if text:
                    result.append(
                        {
                            "type_status": "",
                            "specimen_id": "",
                            "catalog_number": "",
                            "repository": "",
                            "locality": "",
                            "sex": "",
                            "life_stage": "",
                            "remarks": text,
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_locations(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize geographic records cited by treatments."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "name": normalize_space(
                            cls._first_value(
                                item,
                                "name",
                                "locality",
                                "location",
                            )
                        ),
                        "country": normalize_space(
                            cls._first_value(item, "country")
                        ),
                        "country_code": normalize_space(
                            cls._first_value(
                                item,
                                "country_code",
                                "countryCode",
                            )
                        ),
                        "state_province": normalize_space(
                            cls._first_value(
                                item,
                                "state_province",
                                "stateProvince",
                            )
                        ),
                        "county": normalize_space(
                            cls._first_value(item, "county")
                        ),
                        "latitude": cls._optional_float(
                            cls._first_value(
                                item,
                                "latitude",
                                "lat",
                                "decimal_latitude",
                                "decimalLatitude",
                            )
                        ),
                        "longitude": cls._optional_float(
                            cls._first_value(
                                item,
                                "longitude",
                                "lon",
                                "lng",
                                "decimal_longitude",
                                "decimalLongitude",
                            )
                        ),
                        "elevation_m": cls._optional_float(
                            cls._first_value(
                                item,
                                "elevation_m",
                                "elevationM",
                                "elevation",
                            )
                        ),
                        "geoname_id": normalize_space(
                            cls._first_value(
                                item,
                                "geoname_id",
                                "geonameId",
                            )
                        ),
                        "mrgid": normalize_space(
                            cls._first_value(
                                item,
                                "mrgid",
                                "MRGID",
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
                            "country": "",
                            "country_code": "",
                            "state_province": "",
                            "county": "",
                            "latitude": None,
                            "longitude": None,
                            "elevation_m": None,
                            "geoname_id": "",
                            "mrgid": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_synonym_records(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize synonym and name-usage citations."""

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
                        "id": normalize_space(
                            cls._first_value(
                                item,
                                "id",
                                "taxon_id",
                                "taxonId",
                                "name_id",
                                "nameId",
                            )
                        ),
                        "authorship": normalize_space(
                            cls._first_value(
                                item,
                                "authorship",
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
                        "citation": normalize_space(
                            cls._first_value(
                                item,
                                "citation",
                                "reference",
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
                            "id": "",
                            "authorship": "",
                            "status": "synonym",
                            "relationship": "",
                            "citation": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_figures(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize figures, plates, maps, and image references."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                entry = {
                    "figure_id": normalize_space(
                        cls._first_value(
                            item,
                            "figure_id",
                            "figureId",
                            "id",
                        )
                    ),
                    "label": normalize_space(
                        cls._first_value(
                            item,
                            "label",
                            "figure_label",
                            "figureLabel",
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
                    "url": normalize_space(
                        cls._first_value(
                            item,
                            "url",
                            "image_url",
                            "imageUrl",
                            "media_url",
                            "mediaUrl",
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
                    ),
                    "creator": normalize_space(
                        cls._first_value(
                            item,
                            "creator",
                            "author",
                            "photographer",
                            "illustrator",
                        )
                    ),
                    "license": normalize_space(
                        cls._first_value(
                            item,
                            "license",
                            "rights",
                        )
                    ),
                    "raw": dict(item),
                }
            else:
                entry = {
                    "figure_id": "",
                    "label": "",
                    "caption": "",
                    "url": normalize_space(item),
                    "thumbnail_url": "",
                    "type": "",
                    "creator": "",
                    "license": "",
                    "raw": item,
                }

            if entry["url"] or entry["caption"] or entry["label"]:
                result.append(entry)

        return result

    @classmethod
    def _normalize_citations(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize bibliographic citations."""

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
                        "journal": normalize_space(
                            cls._first_value(
                                item,
                                "journal",
                                "source_title",
                                "sourceTitle",
                            )
                        ),
                        "doi": normalize_space(
                            item.get("doi")
                        ).removeprefix("https://doi.org/"),
                        "url": normalize_space(
                            cls._first_value(
                                item,
                                "url",
                                "source_url",
                                "sourceUrl",
                            )
                        ),
                        "reference_id": normalize_space(
                            cls._first_value(
                                item,
                                "reference_id",
                                "referenceId",
                                "id",
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
                            "journal": "",
                            "doi": "",
                            "url": "",
                            "reference_id": "",
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
        """Normalize Plazi and external identifiers."""

        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

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

            key = (source.casefold(), identifier.casefold())

            if identifier and key not in seen:
                seen.add(key)
                result.append(
                    {
                        "identifier": identifier,
                        "source": source,
                    }
                )

        known_fields = {
            "treatment_id": "Plazi TreatmentBank",
            "treatmentId": "Plazi TreatmentBank",
            "treatment_lsid": "Treatment LSID",
            "treatmentLsid": "Treatment LSID",
            "document_id": "Plazi Document",
            "documentId": "Plazi Document",
            "doi": "DOI",
            "zoobank_lsid": "ZooBank",
            "zoobankLsid": "ZooBank",
            "ipni_id": "IPNI",
            "ipniId": "IPNI",
            "gbif_id": "GBIF",
            "gbifId": "GBIF",
            "worms_id": "WoRMS",
            "wormsId": "WoRMS",
            "itis_tsn": "ITIS",
            "itisTsn": "ITIS",
            "col_id": "Catalogue of Life",
            "colId": "Catalogue of Life",
            "wikidata_id": "Wikidata",
            "wikidataId": "Wikidata",
            "openalex_id": "OpenAlex",
            "openalexId": "OpenAlex",
        }

        for field, source in known_fields.items():
            identifier = normalize_space(raw.get(field))

            if source == "DOI":
                identifier = identifier.removeprefix(
                    "https://doi.org/"
                )

            key = (source.casefold(), identifier.casefold())

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
    def _normalize_links(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize related external links."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                url = normalize_space(
                    cls._first_value(
                        item,
                        "url",
                        "href",
                        "link",
                    )
                )
                relation = normalize_space(
                    cls._first_value(
                        item,
                        "relation",
                        "rel",
                        "type",
                    )
                )
            else:
                url = normalize_space(item)
                relation = ""

            if url:
                result.append(
                    {
                        "url": url,
                        "relation": relation,
                    }
                )

        return result

    @classmethod
    def _normalize_people(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize authors and other people."""

        result: list[dict[str, str]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                name = normalize_space(
                    cls._first_value(
                        item,
                        "name",
                        "display_name",
                        "displayName",
                    )
                )
                orcid = normalize_space(
                    cls._first_value(
                        item,
                        "orcid",
                        "orcid_id",
                        "orcidId",
                    )
                ).removeprefix("https://orcid.org/")
                role = normalize_space(
                    cls._first_value(
                        item,
                        "role",
                        "type",
                    )
                )
            else:
                name = normalize_space(item)
                orcid = ""
                role = ""

            if name:
                result.append(
                    {
                        "name": name,
                        "orcid": orcid,
                        "role": role,
                    }
                )

        return result

    @classmethod
    def _normalize_keys(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize identification key statements."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "key_id": normalize_space(
                            cls._first_value(
                                item,
                                "key_id",
                                "keyId",
                                "id",
                            )
                        ),
                        "couplet": normalize_space(
                            cls._first_value(
                                item,
                                "couplet",
                                "number",
                            )
                        ),
                        "statement": cls._text_value(
                            cls._first_value(
                                item,
                                "statement",
                                "text",
                            )
                        ),
                        "target_taxon": normalize_space(
                            cls._first_value(
                                item,
                                "target_taxon",
                                "targetTaxon",
                            )
                        ),
                        "next_couplet": normalize_space(
                            cls._first_value(
                                item,
                                "next_couplet",
                                "nextCouplet",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                text = cls._text_value(item)

                if text:
                    result.append(
                        {
                            "key_id": "",
                            "couplet": "",
                            "statement": text,
                            "target_taxon": "",
                            "next_couplet": "",
                            "raw": item,
                        }
                    )

        return result

    @classmethod
    def _normalize_sections(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize arbitrary treatment sections."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(value):
            if isinstance(item, Mapping):
                result.append(
                    {
                        "title": normalize_space(
                            cls._first_value(
                                item,
                                "title",
                                "heading",
                                "name",
                            )
                        ),
                        "type": normalize_space(
                            cls._first_value(
                                item,
                                "type",
                                "section_type",
                                "sectionType",
                            )
                        ),
                        "text": cls._text_value(
                            cls._first_value(
                                item,
                                "text",
                                "content",
                                "body",
                            )
                        ),
                        "raw": dict(item),
                    }
                )
            else:
                text = cls._text_value(item)

                if text:
                    result.append(
                        {
                            "title": "",
                            "type": "",
                            "text": text,
                            "raw": item,
                        }
                    )

        return result

    @staticmethod
    def _text_value(value: Any) -> str:
        """Normalize scalar or segmented treatment text."""

        if value is None:
            return ""

        if isinstance(value, str):
            return normalize_space(value)

        if isinstance(value, Mapping):
            for key in ("text", "content", "body", "value"):
                if key in value:
                    return Provider._text_value(value.get(key))

            return normalize_space(
                " ".join(
                    Provider._text_value(item)
                    for item in value.values()
                )
            )

        if isinstance(value, (list, tuple, set)):
            return normalize_space(
                " ".join(
                    Provider._text_value(item)
                    for item in value
                )
            )

        return normalize_space(value)

    @staticmethod
    def _normalize_rank(value: Any) -> str:
        """Normalize cross-code taxonomic ranks."""

        rank = normalize_space(value).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "super kingdom": "domain",
            "superkingdom": "domain",
            "division": "phylum",
            "sub division": "subphylum",
            "subdivision": "subphylum",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
            "sub tribe": "subtribe",
            "sub order": "suborder",
            "sub class": "subclass",
            "var.": "variety",
            "subvar.": "subvariety",
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
        """Normalize treatment, taxonomic, and nomenclatural statuses."""

        status = normalize_space(value).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "accepted",
            "current": "accepted",
            "new species": "accepted",
            "sp. nov.": "accepted",
            "species nova": "accepted",
            "new genus": "accepted",
            "gen. nov.": "accepted",
            "new combination": "accepted",
            "comb. nov.": "accepted",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "objective synonym": "synonym",
            "subjective synonym": "synonym",
            "unaccepted": "synonym",
            "misapplied": "misapplied",
            "homonym": "excluded",
            "nomen nudum": "excluded",
            "unavailable": "excluded",
            "invalid": "excluded",
            "illegitimate": "excluded",
            "doubtful": "unknown",
            "unresolved": "unknown",
            "reference": "reference",
            "treatment": "reference",
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

        if "var." in lowered or "variety" in lowered:
            return "variety"

        if "f." in lowered or "forma" in lowered:
            return "form"

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "infraspecific"

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
                f"Invalid Plazi TreatmentBank cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Plazi TreatmentBank cursor must be non-negative."
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
            "present",
            "active",
            "new",
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
