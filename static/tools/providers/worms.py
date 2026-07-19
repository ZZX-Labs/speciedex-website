#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/worms.py

World Register of Marine Species provider plug-in.

Fetches one page of WoRMS Aphia records with one logical API request per
provider execution. Records are retrieved through bounded modification-date
windows so the AphiaRecordsByDate endpoint is not given an invalid or
unreasonably large date range.

The complete provider record is preserved under Taxon.extra["raw"] while
principal taxonomic and nomenclatural fields are normalized for Speciedex.

Legacy numeric cursors are accepted. New cursors are deterministic JSON
objects containing the active date window and WoRMS page offset.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
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
    """World Register of Marine Species provider."""

    PROVIDER_NAME = "worms"

    DEFAULT_BASE_URL = (
        "https://www.marinespecies.org/rest"
    )

    DEFAULT_START_DATE = "2000-01-01"
    DEFAULT_OFFSET = 1
    DEFAULT_WINDOW_DAYS = 31
    MAX_WINDOW_DAYS = 366

    def fetch(self) -> Batch:
        """
        Fetch one WoRMS AphiaRecordsByDate page.

        Only one call to HTTPClient.get_json is made. The HTTP client may
        internally retry failed transport attempts according to its retry
        configuration, but this provider does not issue per-record follow-up
        requests.
        """

        base_url = normalize_space(
            self.definition.get(
                "base_url",
                self.DEFAULT_BASE_URL,
            )
        ).rstrip("/")

        if not base_url:
            raise ProviderError(
                "WoRMS base_url is empty."
            )

        endpoint = (
            f"{base_url}/AphiaRecordsByDate"
        )

        configured_start = self._parse_date(
            self.definition.get(
                "start_date",
                self.DEFAULT_START_DATE,
            ),
            "start_date",
        )

        configured_end = self._configured_end_date()

        if configured_end < configured_start:
            raise ProviderError(
                "WoRMS end_date is earlier than start_date."
            )

        window_days = max(
            1,
            min(
                safe_int(
                    self.definition.get(
                        "window_days",
                        self.DEFAULT_WINDOW_DAYS,
                    ),
                    self.DEFAULT_WINDOW_DAYS,
                ),
                self.MAX_WINDOW_DAYS,
            ),
        )

        initial_offset = max(
            1,
            safe_int(
                self.definition.get(
                    "start_offset",
                    self.DEFAULT_OFFSET,
                ),
                self.DEFAULT_OFFSET,
            ),
        )

        cursor = self._decode_cursor(
            self.cursor
        )

        window_start = self._cursor_date(
            cursor.get("window_start"),
            configured_start,
        )

        maximum_window_end = min(
            configured_end,
            window_start
            + timedelta(
                days=window_days - 1
            ),
        )

        window_end = self._cursor_date(
            cursor.get("window_end"),
            maximum_window_end,
        )

        if window_end < window_start:
            window_end = maximum_window_end

        if window_end > configured_end:
            window_end = configured_end

        offset = max(
            initial_offset,
            safe_int(
                cursor.get("offset"),
                initial_offset,
            ),
        )

        marine_only = self._boolean_parameter(
            self.definition.get(
                "marine_only",
                False,
            )
        )

        parameters: dict[str, Any] = {
            "startdate": self._api_datetime(
                window_start,
                end_of_day=False,
            ),
            "enddate": self._api_datetime(
                window_end,
                end_of_day=True,
            ),
            "marine_only": marine_only,
            "offset": offset,
        }

        request_count_before = (
            self.http.requests
        )

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
                "WoRMS provider completed without "
                "performing an HTTP request."
            )

        raw_records = self._extract_response_records(
            payload
        )

        retrieved_at = now()

        crawl_metadata = {
            "endpoint": endpoint,
            "window_start": (
                window_start.isoformat()
            ),
            "window_end": (
                window_end.isoformat()
            ),
            "offset": offset,
            "returned": len(raw_records),
            "marine_only": (
                marine_only == "true"
            ),
        }

        records: list[Taxon] = []

        for raw_record in raw_records:
            if not isinstance(
                raw_record,
                dict,
            ):
                continue

            record = self._normalize_record(
                raw_record=raw_record,
                base_url=base_url,
                endpoint=endpoint,
                retrieved_at=retrieved_at,
                crawl_metadata=crawl_metadata,
            )

            if record is not None:
                records.append(record)

        if raw_records:
            next_cursor = self._encode_cursor(
                {
                    "window_start": (
                        window_start.isoformat()
                    ),
                    "window_end": (
                        window_end.isoformat()
                    ),
                    "offset": offset + 1,
                }
            )

            exhausted = False

        else:
            next_window_start = (
                window_end
                + timedelta(days=1)
            )

            if next_window_start > configured_end:
                next_cursor = None
                exhausted = True
            else:
                next_window_end = min(
                    configured_end,
                    next_window_start
                    + timedelta(
                        days=window_days - 1
                    ),
                )

                next_cursor = self._encode_cursor(
                    {
                        "window_start": (
                            next_window_start.isoformat()
                        ),
                        "window_end": (
                            next_window_end.isoformat()
                        ),
                        "offset": initial_offset,
                    }
                )

                exhausted = False

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=request_count,
            raw=len(raw_records),
        )

    def _normalize_record(
        self,
        raw_record: dict[str, Any],
        base_url: str,
        endpoint: str,
        retrieved_at: str,
        crawl_metadata: dict[str, Any],
    ) -> Taxon | None:
        """Normalize one Aphia record without discarding source fields."""

        aphia_id = self._first_value(
            raw_record,
            "AphiaID",
            "aphiaID",
            "aphia_id",
            "id",
        )

        scientific_name = normalize_space(
            self._first_value(
                raw_record,
                "scientificname",
                "scientificName",
                "valid_name",
                "validName",
                "name",
            )
        )

        if (
            aphia_id in (
                None,
                "",
            )
            or not scientific_name
        ):
            return None

        provider_id = str(
            aphia_id
        )

        canonical_name = normalize_space(
            self._first_value(
                raw_record,
                "scientificname",
                "scientificName",
                "valid_name",
                "validName",
            )
        )

        if not canonical_name:
            canonical_name = scientific_name

        valid_aphia_id = normalize_space(
            self._first_value(
                raw_record,
                "valid_AphiaID",
                "validAphiaID",
                "accepted_AphiaID",
                "acceptedAphiaID",
            )
        )

        if valid_aphia_id == provider_id:
            valid_aphia_id = ""

        valid_name = normalize_space(
            self._first_value(
                raw_record,
                "valid_name",
                "validName",
                "accepted_name",
                "acceptedName",
            )
        )

        authority = normalize_space(
            self._first_value(
                raw_record,
                "authority",
                "authorship",
                "scientificNameAuthorship",
            )
        )

        rank = normalize_space(
            self._first_value(
                raw_record,
                "rank",
                "taxonRank",
            )
        ).casefold()

        status = self._normalize_status(
            self._first_value(
                raw_record,
                "status",
                "taxonomicStatus",
            )
        )

        source_url = normalize_space(
            raw_record.get("url")
        )

        if not source_url:
            source_url = (
                "https://www.marinespecies.org/"
                "aphia.php?p=taxdetails&id="
                f"{provider_id}"
            )

        source_modified = normalize_space(
            self._first_value(
                raw_record,
                "modified",
                "lastModified",
                "updated",
            )
        )

        synonyms = self._extract_synonyms(
            raw_record=raw_record,
            scientific_name=scientific_name,
            valid_name=valid_name,
        )

        taxonomy = {
            "kingdom": normalize_space(
                raw_record.get("kingdom")
            ),
            "phylum": normalize_space(
                raw_record.get("phylum")
            ),
            "class": normalize_space(
                raw_record.get("class")
            ),
            "order": normalize_space(
                raw_record.get("order")
            ),
            "family": normalize_space(
                raw_record.get("family")
            ),
            "genus": normalize_space(
                raw_record.get("genus")
            ),
        }

        return Taxon(
            provider=self.name,
            provider_id=provider_id,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank or "unknown",
            status=status,
            authorship=authority,
            kingdom=taxonomy["kingdom"],
            phylum=taxonomy["phylum"],
            class_name=taxonomy["class"],
            order=taxonomy["order"],
            family=taxonomy["family"],
            genus=taxonomy["genus"],
            accepted_provider_id=valid_aphia_id,
            source_url=source_url,
            source_modified=source_modified,
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": (
                    "World Register of Marine Species"
                ),
                "endpoint": endpoint,
                "aphia_id": provider_id,
                "valid_aphia_id": valid_aphia_id,
                "valid_name": valid_name,
                "taxonomy": taxonomy,
                "environment": {
                    "marine": (
                        self._boolean_value(
                            self._first_value(
                                raw_record,
                                "isMarine",
                                "is_marine",
                            )
                        )
                    ),
                    "brackish": (
                        self._boolean_value(
                            self._first_value(
                                raw_record,
                                "isBrackish",
                                "is_brackish",
                            )
                        )
                    ),
                    "freshwater": (
                        self._boolean_value(
                            self._first_value(
                                raw_record,
                                "isFreshwater",
                                "is_freshwater",
                            )
                        )
                    ),
                    "terrestrial": (
                        self._boolean_value(
                            self._first_value(
                                raw_record,
                                "isTerrestrial",
                                "is_terrestrial",
                            )
                        )
                    ),
                },
                "is_extinct": (
                    self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isExtinct",
                            "is_extinct",
                        )
                    )
                ),
                "is_fossil": (
                    self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isFossil",
                            "is_fossil",
                        )
                    )
                ),
                "parent_aphia_id": normalize_space(
                    self._first_value(
                        raw_record,
                        "parent_AphiaID",
                        "parentAphiaID",
                    )
                ),
                "parent_name_usage": (
                    normalize_space(
                        self._first_value(
                            raw_record,
                            "parent_name_usage",
                            "parentNameUsage",
                        )
                    )
                ),
                "lsid": normalize_space(
                    raw_record.get("lsid")
                ),
                "citation": normalize_space(
                    raw_record.get("citation")
                ),
                "unaccept_reason": normalize_space(
                    self._first_value(
                        raw_record,
                        "unacceptreason",
                        "unaccept_reason",
                        "unacceptedReason",
                    )
                ),
                "crawl": dict(
                    crawl_metadata
                ),
                "raw": raw_record,
            },
        )

    def _extract_response_records(
        self,
        payload: Any,
    ) -> list[Any]:
        """Validate and extract records from a WoRMS response."""

        if isinstance(
            payload,
            list,
        ):
            return payload

        if not isinstance(
            payload,
            dict,
        ):
            raise ProviderError(
                "WoRMS returned an unsupported "
                "JSON response type."
            )

        api_error = self._extract_api_error(
            payload
        )

        if api_error:
            raise ProviderError(
                f"WoRMS API error: {api_error}"
            )

        for key in (
            "records",
            "results",
            "data",
            "taxa",
        ):
            value = payload.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return value

        if not payload:
            return []

        raise ProviderError(
            "WoRMS returned an object response "
            "without a recognized records list."
        )

    @classmethod
    def _extract_synonyms(
        cls,
        raw_record: dict[str, Any],
        scientific_name: str,
        valid_name: str,
    ) -> list[str]:
        """Extract and deduplicate synonym-like names in the response."""

        values: list[str] = []

        if (
            valid_name
            and valid_name.casefold()
            != scientific_name.casefold()
        ):
            values.append(
                valid_name
            )

        for key in (
            "synonym",
            "synonyms",
            "unaccepted_names",
            "unacceptedNames",
        ):
            value = raw_record.get(
                key
            )

            if isinstance(
                value,
                str,
            ):
                normalized = normalize_space(
                    value
                )

                if normalized:
                    values.append(
                        normalized
                    )

            elif isinstance(
                value,
                list,
            ):
                for item in value:
                    if isinstance(
                        item,
                        str,
                    ):
                        normalized = (
                            normalize_space(item)
                        )

                    elif isinstance(
                        item,
                        dict,
                    ):
                        normalized = (
                            normalize_space(
                                cls._first_value(
                                    item,
                                    "scientificname",
                                    "scientificName",
                                    "valid_name",
                                    "validName",
                                    "name",
                                )
                            )
                        )

                    else:
                        normalized = ""

                    if normalized:
                        values.append(
                            normalized
                        )

        unique: list[str] = []
        seen = {
            scientific_name.casefold()
        }

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

            seen.add(key)
            unique.append(
                normalized
            )

        return unique

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize WoRMS status terminology."""

        status = normalize_space(
            value
        ).casefold()

        aliases = {
            "accepted": "accepted",
            "unaccepted": "synonym",
            "alternate representation": "accepted",
            "temporary name": "provisionally accepted",
            "uncertain": "unknown",
            "quarantined": "unknown",
        }

        if status in aliases:
            return aliases[status]

        for source, target in (
            aliases.items()
        ):
            if source in status:
                return target

        return status or "unknown"

    @staticmethod
    def _extract_api_error(
        payload: dict[str, Any],
    ) -> str:
        """Extract a readable error from an object response."""

        for key in (
            "error",
            "message",
            "detail",
            "description",
        ):
            value = payload.get(
                key
            )

            if isinstance(
                value,
                str,
            ):
                normalized = normalize_space(
                    value
                )

                if normalized:
                    return normalized

            elif isinstance(
                value,
                dict,
            ):
                for child_key in (
                    "message",
                    "detail",
                    "description",
                    "error",
                ):
                    normalized = normalize_space(
                        value.get(
                            child_key
                        )
                    )

                    if normalized:
                        return normalized

        return ""

    def _configured_end_date(
        self,
    ) -> date:
        """
        Return the configured end date or today's UTC date.

        Values such as "today", "now", and an empty value are treated as the
        current UTC date.
        """

        value = self.definition.get(
            "end_date"
        )

        normalized = normalize_space(
            value
        ).casefold()

        if normalized in {
            "",
            "today",
            "now",
            "current",
        }:
            return datetime.now(
                UTC
            ).date()

        return self._parse_date(
            value,
            "end_date",
        )

    @classmethod
    def _decode_cursor(
        cls,
        cursor: str | None,
    ) -> dict[str, Any]:
        """
        Decode structured state while accepting the legacy numeric cursor.

        A legacy numeric cursor is treated as the page offset for the first
        configured date window.
        """

        if not cursor:
            return {}

        value = cursor.strip()

        if value.isdigit():
            return {
                "offset": int(value),
            }

        try:
            decoded = json.loads(
                value
            )
        except json.JSONDecodeError:
            return {}

        return (
            decoded
            if isinstance(
                decoded,
                dict,
            )
            else {}
        )

    @staticmethod
    def _encode_cursor(
        cursor: dict[str, Any],
    ) -> str:
        """Encode deterministic provider state."""

        return json.dumps(
            cursor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _parse_date(
        cls,
        value: Any,
        field_name: str,
    ) -> date:
        """Parse an ISO date or datetime configuration value."""

        normalized = normalize_space(
            value
        )

        if not normalized:
            raise ProviderError(
                f"WoRMS {field_name} is empty."
            )

        normalized = normalized.replace(
            "Z",
            "+00:00",
        )

        try:
            return date.fromisoformat(
                normalized[:10]
            )
        except ValueError as error:
            raise ProviderError(
                f"Invalid WoRMS {field_name}: "
                f"{value!r}. Expected YYYY-MM-DD "
                "or an ISO-8601 datetime."
            ) from error

    @classmethod
    def _cursor_date(
        cls,
        value: Any,
        fallback: date,
    ) -> date:
        """Parse a cursor date or return its deterministic fallback."""

        normalized = normalize_space(
            value
        )

        if not normalized:
            return fallback

        try:
            return date.fromisoformat(
                normalized[:10]
            )
        except ValueError:
            return fallback

    @staticmethod
    def _api_datetime(
        value: date,
        end_of_day: bool,
    ) -> str:
        """Format the date for the WoRMS API."""

        suffix = (
            "T23:59:59"
            if end_of_day
            else "T00:00:00"
        )

        return (
            value.isoformat()
            + suffix
        )

    @staticmethod
    def _first_value(
        record: dict[str, Any],
        *keys: str,
    ) -> Any:
        """Return the first nonempty value under the requested keys."""

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
    def _boolean_parameter(
        value: Any,
    ) -> str:
        """Convert a configured boolean to the WoRMS query form."""

        if isinstance(
            value,
            bool,
        ):
            return (
                "true"
                if value
                else "false"
            )

        normalized = normalize_space(
            value
        ).casefold()

        return (
            "true"
            if normalized in {
                "1",
                "true",
                "yes",
                "on",
            }
            else "false"
        )

    @staticmethod
    def _boolean_value(
        value: Any,
    ) -> bool | None:
        """Normalize a WoRMS boolean-like value."""

        if isinstance(
            value,
            bool,
        ):
            return value

        if isinstance(
            value,
            int,
        ):
            return bool(value)

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
