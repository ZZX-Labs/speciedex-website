#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/gbif.py

GBIF provider plug-in.

Fetches one page from the GBIF Species API per provider run. Every returned
record is normalized into providers.common.Taxon while the complete original
GBIF object is retained under ``extra["raw"]``.

The provider supports:

- deterministic structured cursors,
- legacy numeric cursors,
- configurable Species API base URL,
- bounded page sizing,
- optional GBIF search filters,
- strict cursor progress checks,
- stable provider identifiers,
- complete raw-record preservation,
- one logical API request per fetch call.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
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
    """GBIF Species API provider."""

    PROVIDER_NAME = "gbif"

    DEFAULT_BASE_URL = "https://api.gbif.org/v1"
    DEFAULT_PAGE_SIZE = 500
    MAX_PAGE_SIZE = 1000

    FILTER_PARAMETERS: dict[str, str] = {
        "q": "q",
        "rank": "rank",
        "higher_taxon_key": "highertaxon_key",
        "highertaxon_key": "highertaxon_key",
        "status": "status",
        "is_extinct": "is_extinct",
        "habitat": "habitat",
        "name_type": "name_type",
        "nomenclatural_status": "nomenclatural_status",
        "issue": "issue",
        "dataset_key": "dataset_key",
        "origin": "origin",
    }

    def fetch(self) -> Batch:
        """Fetch and normalize one GBIF species-search page."""

        base_url = normalize_space(
            self.definition.get(
                "base_url",
                self.DEFAULT_BASE_URL,
            )
        ).rstrip("/")

        if not base_url:
            raise ProviderError(
                "GBIF base_url is empty."
            )

        if not (
            base_url.startswith("https://")
            or base_url.startswith("http://")
        ):
            raise ProviderError(
                "GBIF base_url must use HTTP or HTTPS."
            )

        endpoint = f"{base_url}/species/search"
        cursor = self._decode_cursor(
            self.cursor
        )

        configured_start = safe_int(
            self.definition.get(
                "start_offset",
                0,
            ),
            0,
        )

        offset = safe_int(
            cursor.get("offset"),
            configured_start,
        )

        configured_page_size = safe_int(
            self.definition.get(
                "page_size",
                self.DEFAULT_PAGE_SIZE,
            ),
            self.DEFAULT_PAGE_SIZE,
        )

        cursor_limit = safe_int(
            cursor.get("limit"),
            configured_page_size,
        )

        limit = max(
            1,
            min(
                configured_page_size,
                cursor_limit,
                self.batch_size,
                self.MAX_PAGE_SIZE,
            ),
        )

        active_filters = self._configured_filters()

        parameters: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
            **active_filters,
        }

        request_count_before = self.http.requests

        payload = self.http.get_json(
            endpoint,
            parameters,
        )

        request_count = (
            self.http.requests
            - request_count_before
        )

        if request_count < 1:
            raise ProviderError(
                "GBIF fetch completed without an HTTP request."
            )

        if not isinstance(
            payload,
            Mapping,
        ):
            raise ProviderError(
                "GBIF returned a non-object JSON response."
            )

        raw_results = payload.get(
            "results",
            [],
        )

        if not isinstance(
            raw_results,
            list,
        ):
            raise ProviderError(
                "GBIF response field 'results' is not a list."
            )

        response_offset = safe_int(
            payload.get("offset"),
            offset,
        )

        response_limit = safe_int(
            payload.get("limit"),
            limit,
        )

        response_count = self._optional_int(
            payload.get("count")
        )

        end_of_records = bool(
            payload.get(
                "endOfRecords",
                len(raw_results) < limit,
            )
        )

        if response_offset < 0:
            raise ProviderError(
                "GBIF returned a negative response offset."
            )

        if response_limit < 1:
            response_limit = limit

        retrieved_at = now()

        crawl_metadata = {
            "endpoint": endpoint,
            "requested_offset": offset,
            "requested_limit": limit,
            "response_offset": response_offset,
            "response_limit": response_limit,
            "returned": len(raw_results),
            "count": response_count,
            "end_of_records": end_of_records,
            "filters": dict(active_filters),
        }

        records: list[Taxon] = []

        for raw_record in raw_results:
            if not isinstance(
                raw_record,
                Mapping,
            ):
                continue

            normalized = self._normalize_record(
                raw_record=dict(raw_record),
                base_url=base_url,
                retrieved_at=retrieved_at,
                crawl_metadata=crawl_metadata,
            )

            if normalized is not None:
                records.append(
                    normalized
                )

        next_offset = (
            response_offset
            + len(raw_results)
        )

        if (
            not end_of_records
            and next_offset <= offset
        ):
            raise ProviderError(
                "GBIF returned no cursor progress while "
                "endOfRecords was false."
            )

        next_cursor = (
            None
            if end_of_records
            else self._encode_cursor(
                {
                    "offset": next_offset,
                    "limit": limit,
                    "endpoint": endpoint,
                    "filters": active_filters,
                }
            )
        )

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=end_of_records,
            requests=request_count,
            raw=len(raw_results),
        )

    def _normalize_record(
        self,
        *,
        raw_record: dict[str, Any],
        base_url: str,
        retrieved_at: str,
        crawl_metadata: Mapping[str, Any],
    ) -> Taxon | None:
        """Normalize one GBIF search result."""

        provider_id = self._first_value(
            raw_record,
            "key",
            "usageKey",
            "taxonKey",
        )

        scientific_name = normalize_space(
            self._first_value(
                raw_record,
                "scientificName",
                "canonicalName",
                "species",
                "name",
            )
        )

        if (
            provider_id in (
                None,
                "",
            )
            or not scientific_name
        ):
            return None

        provider_key = str(
            provider_id
        )

        canonical_name = normalize_space(
            self._first_value(
                raw_record,
                "canonicalName",
                "species",
                "scientificName",
            )
        ) or scientific_name

        accepted_provider_id = normalize_space(
            self._first_value(
                raw_record,
                "acceptedKey",
                "acceptedUsageKey",
            )
        )

        if (
            accepted_provider_id
            == provider_key
        ):
            accepted_provider_id = ""

        source_url = normalize_space(
            self._first_value(
                raw_record,
                "references",
            )
        )

        if not source_url:
            source_url = (
                f"{base_url}/species/"
                f"{provider_key}"
            )

        source_modified = normalize_space(
            self._first_value(
                raw_record,
                "modified",
                "lastInterpreted",
            )
        )

        rank = normalize_space(
            self._first_value(
                raw_record,
                "rank",
                "taxonRank",
            )
        ).casefold() or "unknown"

        status = self._normalize_status(
            self._first_value(
                raw_record,
                "taxonomicStatus",
                "status",
            )
        )

        synonyms = self._extract_synonyms(
            raw_record,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
        )

        return Taxon(
            provider=self.name,
            provider_id=provider_key,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank,
            status=status,
            authorship=normalize_space(
                self._first_value(
                    raw_record,
                    "authorship",
                    "scientificNameAuthorship",
                )
            ),
            kingdom=normalize_space(
                raw_record.get("kingdom")
            ),
            phylum=normalize_space(
                raw_record.get("phylum")
            ),
            class_name=normalize_space(
                raw_record.get("class")
            ),
            order=normalize_space(
                raw_record.get("order")
            ),
            family=normalize_space(
                raw_record.get("family")
            ),
            genus=normalize_space(
                raw_record.get("genus")
            ),
            accepted_provider_id=(
                accepted_provider_id
            ),
            source_url=source_url,
            source_modified=source_modified,
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": "GBIF Species API",
                "endpoint": (
                    f"{base_url}/species/search"
                ),
                "provider_key": provider_key,
                "crawl": dict(
                    crawl_metadata
                ),
                "identifiers": {
                    "key": raw_record.get(
                        "key"
                    ),
                    "nub_key": raw_record.get(
                        "nubKey"
                    ),
                    "taxon_key": raw_record.get(
                        "taxonKey"
                    ),
                    "usage_key": raw_record.get(
                        "usageKey"
                    ),
                    "accepted_key": raw_record.get(
                        "acceptedKey"
                    ),
                    "accepted_usage_key": raw_record.get(
                        "acceptedUsageKey"
                    ),
                    "parent_key": raw_record.get(
                        "parentKey"
                    ),
                    "kingdom_key": raw_record.get(
                        "kingdomKey"
                    ),
                    "phylum_key": raw_record.get(
                        "phylumKey"
                    ),
                    "class_key": raw_record.get(
                        "classKey"
                    ),
                    "order_key": raw_record.get(
                        "orderKey"
                    ),
                    "family_key": raw_record.get(
                        "familyKey"
                    ),
                    "genus_key": raw_record.get(
                        "genusKey"
                    ),
                    "subgenus_key": raw_record.get(
                        "subgenusKey"
                    ),
                    "species_key": raw_record.get(
                        "speciesKey"
                    ),
                    "name_key": raw_record.get(
                        "nameKey"
                    ),
                    "dataset_key": raw_record.get(
                        "datasetKey"
                    ),
                    "constituent_key": raw_record.get(
                        "constituentKey"
                    ),
                },
                "raw": raw_record,
            },
        )

    def _configured_filters(
        self,
    ) -> dict[str, Any]:
        """Return normalized configured GBIF search filters."""

        filters: dict[str, Any] = {}

        for (
            registry_key,
            api_parameter,
        ) in self.FILTER_PARAMETERS.items():
            value = self.definition.get(
                registry_key
            )

            if value in (
                None,
                "",
                [],
                {},
            ):
                continue

            if (
                api_parameter
                in filters
            ):
                continue

            filters[
                api_parameter
            ] = value

        return filters

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize GBIF taxonomic status values."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "doubtful": "unknown",
            "synonym": "synonym",
            "heterotypic synonym": "synonym",
            "homotypic synonym": "synonym",
            "proparte synonym": "synonym",
            "pro parte synonym": "synonym",
            "misapplied": "misapplied",
        }

        return aliases.get(
            status,
            status or "unknown",
        )

    @classmethod
    def _extract_synonyms(
        cls,
        raw_record: Mapping[str, Any],
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        """
        Extract synonym-like names present in the search result.

        No secondary GBIF API request is performed.
        """

        values: list[str] = []

        for key in (
            "synonym",
            "synonyms",
            "accepted",
            "acceptedName",
            "acceptedScientificName",
        ):
            raw_value = raw_record.get(
                key
            )

            if isinstance(
                raw_value,
                str,
            ):
                normalized = normalize_space(
                    raw_value
                )

                if normalized:
                    values.append(
                        normalized
                    )

            elif isinstance(
                raw_value,
                list,
            ):
                for item in raw_value:
                    normalized = ""

                    if isinstance(
                        item,
                        str,
                    ):
                        normalized = normalize_space(
                            item
                        )

                    elif isinstance(
                        item,
                        Mapping,
                    ):
                        normalized = normalize_space(
                            cls._first_value(
                                item,
                                "scientificName",
                                "canonicalName",
                                "acceptedScientificName",
                                "name",
                            )
                        )

                    if normalized:
                        values.append(
                            normalized
                        )

        excluded = {
            scientific_name.casefold(),
            canonical_name.casefold(),
        }

        unique: list[str] = []
        seen: set[str] = set(
            excluded
        )

        for value in values:
            normalized = normalize_space(
                value
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
            unique.append(
                normalized
            )

        return unique

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> dict[str, Any]:
        """Decode a structured or legacy numeric cursor."""

        if not cursor:
            return {}

        stripped = cursor.strip()

        if stripped.isdigit():
            return {
                "offset": int(
                    stripped
                )
            }

        try:
            decoded = json.loads(
                stripped
            )
        except (
            TypeError,
            json.JSONDecodeError,
        ):
            raise ProviderError(
                "GBIF cursor is neither a numeric offset "
                "nor valid JSON."
            )

        if not isinstance(
            decoded,
            dict,
        ):
            raise ProviderError(
                "GBIF cursor JSON must decode to an object."
            )

        offset = decoded.get(
            "offset"
        )

        if (
            offset is not None
            and safe_int(
                offset,
                -1,
            ) < 0
        ):
            raise ProviderError(
                "GBIF cursor contains an invalid offset."
            )

        limit = decoded.get(
            "limit"
        )

        if (
            limit is not None
            and safe_int(
                limit,
                -1,
            ) < 1
        ):
            raise ProviderError(
                "GBIF cursor contains an invalid limit."
            )

        return decoded

    @staticmethod
    def _encode_cursor(
        cursor: Mapping[str, Any],
    ) -> str:
        """Encode provider state as deterministic compact JSON."""

        return json.dumps(
            dict(cursor),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _first_value(
        record: Mapping[str, Any],
        *keys: str,
    ) -> Any:
        """Return the first nonempty requested value."""

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
    def _optional_int(
        value: Any,
    ) -> int | None:
        """Return an integer or None without inventing a value."""

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
