#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/open_tree_of_life.py

Open Tree of Life provider plug-in.

This provider consumes a normalized or semi-normalized JSONL export configured
through providers.json. It does not assume access to an undocumented or
unstable public API.

Each source record is normalized into the shared Taxon contract while the
complete Open Tree of Life object is preserved under ``Taxon.extra["raw"]``.

Required provider configuration:

    {
        "name": "open_tree_of_life",
        "path": "static/data/providers/open-tree-of-life/taxa.jsonl"
    }

Optional configuration:

    {
        "page_size": 1000,
        "source_name": "Open Tree of Life",
        "source_url": "https://tree.opentreeoflife.org/"
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
    """File-backed Open Tree of Life provider."""

    PROVIDER_NAME = "open_tree_of_life"

    DEFAULT_SOURCE_NAME = "Open Tree of Life"
    DEFAULT_SOURCE_URL = "https://tree.opentreeoflife.org/"

    def fetch(self) -> Batch:
        """Read and normalize one resumable JSONL batch."""

        source_path = self._source_path()

        if not source_path.exists():
            raise ProviderError(
                f"Open Tree of Life export not found: {source_path}"
            )

        if not source_path.is_file():
            raise ProviderError(
                f"Open Tree of Life path is not a file: {source_path}"
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
        """Resolve the configured JSONL source path."""

        configured = normalize_space(
            self.definition.get(
                "path"
            )
        )

        if not configured:
            raise ProviderError(
                "Open Tree of Life provider requires a path."
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
        """Normalize one Open Tree of Life taxonomy record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "ott_id",
                "ottId",
                "ottID",
                "taxon_id",
                "taxonId",
                "taxonID",
                "id",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "name",
                "scientific_name",
                "scientificName",
                "unique_name",
                "uniqueName",
            )
        )

        if not provider_id or not scientific_name:
            return None

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "unique_name",
                "uniqueName",
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
                "accepted_ott_id",
                "acceptedOttId",
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
                "taxon_url",
                "taxonUrl",
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
                + "/opentree/argus/ottol@"
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
                "programme": "open_tree_of_life",
                "reference_only": True,
                "ott_id": provider_id,
                "accepted_ott_id": accepted_provider_id,
                "unique_name": normalize_space(
                    self._first_value(
                        raw,
                        "unique_name",
                        "uniqueName",
                    )
                ),
                "node_id": normalize_space(
                    self._first_value(
                        raw,
                        "node_id",
                        "nodeId",
                        "nodeID",
                    )
                ),
                "parent": {
                    "ott_id": normalize_space(
                        self._first_value(
                            raw,
                            "parent_ott_id",
                            "parentOttId",
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
                    "rank": normalize_space(
                        self._first_value(
                            raw,
                            "parent_rank",
                            "parentRank",
                        )
                    ).casefold(),
                },
                "lineage": lineage,
                "taxonomic_flags": self._normalize_flags(
                    self._first_value(
                        raw,
                        "flags",
                        "taxonomic_flags",
                        "taxonomicFlags",
                    )
                ),
                "source_taxonomies": self._normalize_source_taxonomies(
                    self._first_value(
                        raw,
                        "tax_sources",
                        "taxSources",
                        "source_taxonomies",
                        "sourceTaxonomies",
                    )
                ),
                "is_suppressed": self._optional_bool(
                    self._first_value(
                        raw,
                        "is_suppressed",
                        "isSuppressed",
                        "suppressed",
                    )
                ),
                "is_extinct": self._optional_bool(
                    self._first_value(
                        raw,
                        "is_extinct",
                        "isExtinct",
                        "extinct",
                    )
                ),
                "is_hybrid": self._optional_bool(
                    self._first_value(
                        raw,
                        "is_hybrid",
                        "isHybrid",
                        "hybrid",
                    )
                ),
                "is_dubious": self._optional_bool(
                    self._first_value(
                        raw,
                        "is_dubious",
                        "isDubious",
                        "dubious",
                    )
                ),
                "is_hidden": self._optional_bool(
                    self._first_value(
                        raw,
                        "is_hidden",
                        "isHidden",
                        "hidden",
                    )
                ),
                "nomenclature": {
                    "code": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_code",
                            "nomenclaturalCode",
                            "code",
                        )
                    ),
                    "status": normalize_space(
                        self._first_value(
                            raw,
                            "nomenclatural_status",
                            "nomenclaturalStatus",
                            "name_status",
                            "nameStatus",
                        )
                    ),
                },
                "children": self._normalize_children(
                    self._first_value(
                        raw,
                        "children",
                        "child_taxa",
                        "childTaxa",
                    )
                ),
                "references": self._list_value(
                    self._first_value(
                        raw,
                        "references",
                        "reference",
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
        """Extract major lineage values from direct fields or lineage arrays."""

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

        lineage_value = cls._first_value(
            raw,
            "lineage",
            "ancestors",
            "classification",
        )

        for item in cls._list_value(
            lineage_value
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
                    "unique_name",
                    "uniqueName",
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
                        "unique_name",
                        "uniqueName",
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
    def _normalize_flags(
        cls,
        value: Any,
    ) -> list[str]:
        """Normalize Open Tree taxonomy flags."""

        if isinstance(
            value,
            str,
        ):
            raw_values = [
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
            raw_values = cls._list_value(
                value
            )

        result: list[str] = []
        seen: set[str] = set()

        for item in raw_values:
            normalized = normalize_space(
                item
            ).casefold().replace(
                " ",
                "_",
            )

            if (
                not normalized
                or normalized in seen
            ):
                continue

            seen.add(
                normalized
            )
            result.append(
                normalized
            )

        return result

    @classmethod
    def _normalize_source_taxonomies(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """Normalize source taxonomy mappings such as ncbi:9606 or gbif:..."""

        if isinstance(
            value,
            str,
        ):
            raw_values = [
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
            raw_values = cls._list_value(
                value
            )

        result: list[dict[str, str]] = []

        for item in raw_values:
            if isinstance(
                item,
                Mapping,
            ):
                source = normalize_space(
                    cls._first_value(
                        item,
                        "source",
                        "taxonomy",
                        "namespace",
                    )
                )

                identifier = normalize_space(
                    cls._first_value(
                        item,
                        "id",
                        "identifier",
                        "value",
                    )
                )

            else:
                text = normalize_space(
                    item
                )

                if ":" in text:
                    source, identifier = text.split(
                        ":",
                        1,
                    )
                    source = normalize_space(
                        source
                    )
                    identifier = normalize_space(
                        identifier
                    )
                else:
                    source = ""
                    identifier = text

            if source or identifier:
                result.append(
                    {
                        "source": source,
                        "identifier": identifier,
                    }
                )

        return result

    @classmethod
    def _normalize_children(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize child taxon summaries."""

        result: list[dict[str, Any]] = []

        for item in cls._list_value(
            value
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            result.append(
                {
                    "ott_id": normalize_space(
                        cls._first_value(
                            item,
                            "ott_id",
                            "ottId",
                            "id",
                        )
                    ),
                    "name": normalize_space(
                        cls._first_value(
                            item,
                            "name",
                            "scientific_name",
                            "scientificName",
                        )
                    ),
                    "rank": normalize_space(
                        cls._first_value(
                            item,
                            "rank",
                            "taxon_rank",
                            "taxonRank",
                        )
                    ).casefold(),
                    "raw": dict(
                        item
                    ),
                }
            )

        return result

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize Open Tree taxonomic status terms."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "synonym": "synonym",
            "unaccepted": "synonym",
            "dubious": "unknown",
            "suppressed": "excluded",
            "hidden": "excluded",
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
                f"Invalid Open Tree of Life cursor: {cursor!r}."
            ) from error

        if offset < 0:
            raise ProviderError(
                "Open Tree of Life cursor must be non-negative."
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
