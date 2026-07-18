#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/itis.py

ITIS provider plug-in.

Fetches one complete ITIS taxonomic record with exactly one API request per
provider run. The entire provider response is preserved in Taxon.extra["raw"]
while principal taxonomic and nomenclatural fields are normalized for
Speciedex reconciliation.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

from typing import Any

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
    """Integrated Taxonomic Information System provider."""

    PROVIDER_NAME = "itis"

    DEFAULT_BASE_URL = (
        "https://www.itis.gov/ITISWebService/jsonservice"
    )

    DEFAULT_START_TSN = 1
    DEFAULT_MAX_TSN = 9_999_999

    def fetch(self) -> Batch:
        """
        Fetch one complete ITIS record using one API request.

        Each scheduled execution processes one TSN. Missing or invalid TSNs
        produce an empty batch and advance the cursor so the provider cannot
        become permanently stuck on an unused identifier.
        """

        base_url = normalize_space(
            self.definition.get(
                "base_url",
                self.DEFAULT_BASE_URL,
            )
        ).rstrip("/")

        if not base_url:
            raise ProviderError(
                "ITIS base_url is empty."
            )

        start_tsn = safe_int(
            self.definition.get(
                "start_tsn",
                self.DEFAULT_START_TSN,
            ),
            self.DEFAULT_START_TSN,
        )

        maximum_tsn = safe_int(
            self.definition.get(
                "max_tsn",
                self.DEFAULT_MAX_TSN,
            ),
            self.DEFAULT_MAX_TSN,
        )

        current_tsn = safe_int(
            self.cursor,
            start_tsn,
        )

        if current_tsn < start_tsn:
            current_tsn = start_tsn

        if current_tsn > maximum_tsn:
            return Batch(
                records=[],
                next_cursor=None,
                exhausted=True,
                requests=0,
                raw=0,
            )

        endpoint = (
            f"{base_url}/getFullRecordFromTSN"
        )

        request_count_before = self.http.requests

        payload = self.http.get_json(
            endpoint,
            {
                "tsn": current_tsn,
            },
        )

        request_count = (
            self.http.requests
            - request_count_before
        )

        if request_count != 1:
            raise ProviderError(
                "ITIS provider expected exactly one API "
                f"request but performed {request_count}."
            )

        if not isinstance(payload, dict):
            raise ProviderError(
                "ITIS returned a non-object JSON response."
            )

        api_error = self._extract_api_error(
            payload
        )

        next_tsn = current_tsn + 1
        exhausted = next_tsn > maximum_tsn
        next_cursor = (
            None
            if exhausted
            else str(next_tsn)
        )

        if api_error:
            if self._is_missing_record_error(
                api_error
            ):
                return Batch(
                    records=[],
                    next_cursor=next_cursor,
                    exhausted=exhausted,
                    requests=request_count,
                    raw=1,
                )

            raise ProviderError(
                f"ITIS API error for TSN "
                f"{current_tsn}: {api_error}"
            )

        record = self._normalize_record(
            payload=payload,
            tsn=current_tsn,
            base_url=base_url,
            retrieved_at=now(),
        )

        return Batch(
            records=(
                [record]
                if record is not None
                else []
            ),
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=request_count,
            raw=1,
        )

    def _normalize_record(
        self,
        payload: dict[str, Any],
        tsn: int,
        base_url: str,
        retrieved_at: str,
    ) -> Taxon | None:
        """Normalize one complete ITIS response."""

        core_metadata = self._dictionary(
            payload.get("coreMetadata")
        )

        usage = self._dictionary(
            payload.get("usage")
        )

        accepted_name = self._dictionary(
            payload.get("acceptedName")
        )

        scientific_name_data = self._dictionary(
            payload.get("scientificName")
        )

        taxon_author = self._dictionary(
            payload.get("taxonAuthor")
        )

        credibility_rating = self._dictionary(
            payload.get("credibilityRating")
        )

        jurisdiction = self._dictionary(
            payload.get("jurisdiction")
        )

        geographic_division = self._dictionary(
            payload.get("geographicDivision")
        )

        hierarchy_up = self._dictionary(
            payload.get("hierarchyUp")
        )

        hierarchy_down = self._dictionary(
            payload.get("hierarchyDown")
        )

        lineage = self._extract_lineage(
            hierarchy_up
        )

        hierarchy_children = (
            self._extract_hierarchy_children(
                hierarchy_down
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                usage,
                "taxonName",
                "scientificName",
                "combinedName",
            )
            or self._first_value(
                scientific_name_data,
                "combinedName",
                "scientificName",
                "taxonName",
                "unitName1",
            )
            or self._first_value(
                accepted_name,
                "acceptedName",
                "combinedName",
                "taxonName",
            )
            or self._first_value(
                core_metadata,
                "taxonName",
                "scientificName",
                "combinedName",
            )
        )

        if not scientific_name:
            return None

        canonical_name = self._build_canonical_name(
            scientific_name_data,
            scientific_name,
        )

        rank = normalize_space(
            self._first_value(
                core_metadata,
                "rankName",
                "rank",
                "taxonRank",
            )
            or self._first_value(
                usage,
                "rankName",
                "rank",
            )
            or self._first_value(
                scientific_name_data,
                "rankName",
                "rank",
            )
        ).lower()

        if not rank:
            rank = self._infer_rank(
                canonical_name
            )

        status = self._normalize_status(
            self._first_value(
                usage,
                "usage",
                "taxonUsageRating",
                "usageStatus",
            )
            or self._first_value(
                core_metadata,
                "usage",
                "status",
                "taxonomicStatus",
            )
        )

        authorship = normalize_space(
            self._first_value(
                taxon_author,
                "taxonAuthor",
                "author",
                "authorship",
            )
            or self._first_value(
                core_metadata,
                "author",
                "taxonAuthor",
                "authorship",
            )
            or self._first_value(
                scientific_name_data,
                "author",
                "authorship",
            )
        )

        accepted_tsn = normalize_space(
            self._first_value(
                accepted_name,
                "acceptedTsn",
                "acceptedTSN",
                "tsn",
            )
            or self._first_value(
                usage,
                "acceptedTsn",
                "acceptedTSN",
            )
        )

        synonyms = self._extract_synonyms(
            payload=payload,
            accepted_name=accepted_name,
            scientific_name=scientific_name,
        )

        source_url = (
            "https://www.itis.gov/servlet/"
            "SingleRpt/SingleRpt?"
            "search_topic=TSN&"
            f"search_value={tsn}"
        )

        source_modified = normalize_space(
            self._first_value(
                core_metadata,
                "updateDate",
                "modified",
                "lastModified",
                "recordUpdateDate",
            )
            or self._find_first_recursive(
                payload,
                (
                    "updateDate",
                    "modified",
                    "lastModified",
                    "recordUpdateDate",
                ),
            )
        )

        return Taxon(
            provider=self.name,
            provider_id=str(tsn),
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank or "unknown",
            status=status,
            authorship=authorship,
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
            accepted_provider_id=accepted_tsn,
            source_url=source_url,
            source_modified=source_modified,
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": (
                    "Integrated Taxonomic "
                    "Information System"
                ),
                "endpoint": (
                    f"{base_url}/"
                    "getFullRecordFromTSN"
                ),
                "tsn": str(tsn),
                "accepted_tsn": accepted_tsn,
                "core_metadata": core_metadata,
                "usage": usage,
                "accepted_name": accepted_name,
                "scientific_name_data": (
                    scientific_name_data
                ),
                "taxon_author": taxon_author,
                "credibility_rating": (
                    credibility_rating
                ),
                "jurisdiction": jurisdiction,
                "geographic_division": (
                    geographic_division
                ),
                "lineage": lineage,
                "hierarchy_up": hierarchy_up,
                "hierarchy_down": hierarchy_down,
                "hierarchy_children": (
                    hierarchy_children
                ),
                "common_names": (
                    self._extract_common_names(
                        payload
                    )
                ),
                "publications": (
                    self._extract_publications(
                        payload
                    )
                ),
                "experts": self._extract_experts(
                    payload
                ),
                "comments": self._extract_comments(
                    payload
                ),
                "other_sources": (
                    self._extract_other_sources(
                        payload
                    )
                ),
                "vernacular_names": (
                    self._extract_named_collection(
                        payload,
                        (
                            "vernacularNames",
                            "commonNames",
                        ),
                    )
                ),
                "raw": payload,
            },
        )

    def _extract_lineage(
        self,
        hierarchy: dict[str, Any],
    ) -> dict[str, str]:
        """Extract all available parent ranks from an ITIS hierarchy."""

        rows = self._find_list(
            hierarchy,
            (
                "hierarchyList",
                "hierarchy",
                "parentTaxa",
            ),
        )

        lineage: dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue

            rank = normalize_space(
                self._first_value(
                    row,
                    "rankName",
                    "rank",
                    "taxonRank",
                )
            ).lower()

            name = normalize_space(
                self._first_value(
                    row,
                    "taxonName",
                    "scientificName",
                    "name",
                )
            )

            if rank and name:
                lineage[rank] = name

        return lineage

    def _extract_hierarchy_children(
        self,
        hierarchy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract immediate or returned descendant information."""

        rows = self._find_list(
            hierarchy,
            (
                "hierarchyList",
                "hierarchy",
                "children",
                "childTaxa",
            ),
        )

        children: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            children.append(
                {
                    "tsn": normalize_space(
                        self._first_value(
                            row,
                            "tsn",
                            "TSN",
                        )
                    ),
                    "name": normalize_space(
                        self._first_value(
                            row,
                            "taxonName",
                            "scientificName",
                            "name",
                        )
                    ),
                    "rank": normalize_space(
                        self._first_value(
                            row,
                            "rankName",
                            "rank",
                        )
                    ).lower(),
                    "raw": row,
                }
            )

        return children

    def _build_canonical_name(
        self,
        scientific_name_data: dict[str, Any],
        fallback: str,
    ) -> str:
        """Construct a canonical name from ITIS unit-name components."""

        parts = [
            normalize_space(
                scientific_name_data.get(
                    "unitName1"
                )
            ),
            normalize_space(
                scientific_name_data.get(
                    "unitName2"
                )
            ),
            normalize_space(
                scientific_name_data.get(
                    "unitName3"
                )
            ),
            normalize_space(
                scientific_name_data.get(
                    "unitName4"
                )
            ),
        ]

        canonical = " ".join(
            part
            for part in parts
            if part
        )

        if canonical:
            return canonical

        combined = normalize_space(
            self._first_value(
                scientific_name_data,
                "combinedName",
                "taxonName",
                "scientificName",
            )
        )

        return combined or fallback

    def _extract_synonyms(
        self,
        payload: dict[str, Any],
        accepted_name: dict[str, Any],
        scientific_name: str,
    ) -> list[str]:
        """Extract synonym and accepted-name values from the full response."""

        values: list[str] = []

        for collection_name in (
            "synonymNames",
            "synonyms",
            "taxonomicSynonyms",
        ):
            collection = self._find_list(
                payload,
                (collection_name,),
            )

            for item in collection:
                if isinstance(item, dict):
                    value = normalize_space(
                        self._first_value(
                            item,
                            "combinedName",
                            "taxonName",
                            "scientificName",
                            "name",
                        )
                    )
                else:
                    value = normalize_space(item)

                if value:
                    values.append(value)

        accepted_value = normalize_space(
            self._first_value(
                accepted_name,
                "acceptedName",
                "combinedName",
                "taxonName",
                "scientificName",
            )
        )

        if accepted_value:
            values.append(accepted_value)

        unique: list[str] = []
        seen: set[str] = {
            scientific_name.casefold(),
        }

        for value in values:
            key = value.casefold()

            if not value or key in seen:
                continue

            seen.add(key)
            unique.append(value)

        return unique

    def _extract_common_names(
        self,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract vernacular names without discarding language metadata."""

        rows = self._find_list(
            payload,
            (
                "commonNames",
                "vernacularNames",
            ),
        )

        results: list[dict[str, Any]] = []

        for row in rows:
            if isinstance(row, dict):
                name = normalize_space(
                    self._first_value(
                        row,
                        "commonName",
                        "vernacularName",
                        "name",
                    )
                )

                if not name:
                    continue

                results.append(
                    {
                        "name": name,
                        "language": normalize_space(
                            self._first_value(
                                row,
                                "language",
                                "languageName",
                                "lang",
                            )
                        ),
                        "jurisdiction": normalize_space(
                            self._first_value(
                                row,
                                "jurisdictionValue",
                                "jurisdiction",
                            )
                        ),
                        "raw": row,
                    }
                )

            else:
                name = normalize_space(row)

                if name:
                    results.append(
                        {
                            "name": name,
                            "language": "",
                            "jurisdiction": "",
                            "raw": row,
                        }
                    )

        return results

    def _extract_publications(
        self,
        payload: dict[str, Any],
    ) -> list[Any]:
        return self._extract_named_collection(
            payload,
            (
                "publications",
                "publication",
                "referenceLinks",
            ),
        )

    def _extract_experts(
        self,
        payload: dict[str, Any],
    ) -> list[Any]:
        return self._extract_named_collection(
            payload,
            (
                "experts",
                "expert",
                "taxonExperts",
            ),
        )

    def _extract_comments(
        self,
        payload: dict[str, Any],
    ) -> list[Any]:
        return self._extract_named_collection(
            payload,
            (
                "comments",
                "comment",
                "taxonComments",
            ),
        )

    def _extract_other_sources(
        self,
        payload: dict[str, Any],
    ) -> list[Any]:
        return self._extract_named_collection(
            payload,
            (
                "otherSources",
                "otherSource",
                "sourceLinks",
            ),
        )

    def _extract_named_collection(
        self,
        payload: dict[str, Any],
        keys: tuple[str, ...],
    ) -> list[Any]:
        """Return the first matching list found recursively."""

        return self._find_list(
            payload,
            keys,
        )

    def _extract_api_error(
        self,
        payload: dict[str, Any],
    ) -> str:
        """Extract ITIS error descriptions from known response shapes."""

        for key in (
            "error",
            "errorMessage",
            "errorDescription",
            "message",
        ):
            value = payload.get(key)

            if isinstance(value, str):
                normalized = normalize_space(value)

                if normalized:
                    return normalized

            if isinstance(value, dict):
                normalized = normalize_space(
                    self._first_value(
                        value,
                        "message",
                        "description",
                        "error",
                        "errorMessage",
                    )
                )

                if normalized:
                    return normalized

        return ""

    @staticmethod
    def _is_missing_record_error(
        error: str,
    ) -> bool:
        normalized = error.casefold()

        return any(
            marker in normalized
            for marker in (
                "no record",
                "no records",
                "not found",
                "invalid tsn",
                "tsn does not exist",
                "unable to find",
            )
        )

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "valid": "valid",
            "accepted name": "accepted",
            "valid name": "valid",
            "not accepted": "synonym",
            "invalid": "synonym",
            "synonym": "synonym",
            "junior synonym": "synonym",
            "senior synonym": "accepted",
            "original name": "reference",
        }

        if status in aliases:
            return aliases[status]

        for source, target in aliases.items():
            if source in status:
                return target

        return status or "unknown"

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        words = scientific_name.split()

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    def _dictionary(
        value: Any,
    ) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _first_value(
        record: dict[str, Any],
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

    @classmethod
    def _find_list(
        cls,
        value: Any,
        keys: tuple[str, ...],
    ) -> list[Any]:
        """Recursively find the first list stored under one of the keys."""

        if isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)

                if isinstance(candidate, list):
                    return candidate

                if isinstance(candidate, dict):
                    nested = cls._first_list_value(
                        candidate
                    )

                    if nested is not None:
                        return nested

            for child in value.values():
                result = cls._find_list(
                    child,
                    keys,
                )

                if result:
                    return result

        elif isinstance(value, list):
            for child in value:
                result = cls._find_list(
                    child,
                    keys,
                )

                if result:
                    return result

        return []

    @staticmethod
    def _first_list_value(
        value: dict[str, Any],
    ) -> list[Any] | None:
        for child in value.values():
            if isinstance(child, list):
                return child

        return None

    @classmethod
    def _find_first_recursive(
        cls,
        value: Any,
        keys: tuple[str, ...],
    ) -> Any:
        """Recursively find the first nonempty value under any key."""

        if isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)

                if candidate not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return candidate

            for child in value.values():
                result = cls._find_first_recursive(
                    child,
                    keys,
                )

                if result not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return result

        elif isinstance(value, list):
            for child in value:
                result = cls._find_first_recursive(
                    child,
                    keys,
                )

                if result not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return result

        return None
