#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/worms.py

World Register of Marine Species provider plug-in.

Fetches one page of WoRMS Aphia records through bounded modification-date
windows. Each provider run performs one logical API request and preserves every
source field under ``Taxon.extra["raw"]``.

Legacy numeric cursors are accepted as page offsets for the first configured
date window. New cursors are deterministic JSON objects containing the active
window and page offset.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
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
    """World Register of Marine Species provider."""

    PROVIDER_NAME = "worms"

    DEFAULT_BASE_URL = "https://www.marinespecies.org/rest"
    DEFAULT_START_DATE = "2000-01-01"
    DEFAULT_OFFSET = 1
    DEFAULT_WINDOW_DAYS = 31
    MAX_WINDOW_DAYS = 366

    def fetch(self) -> Batch:
        """Fetch one AphiaRecordsByDate page."""

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

        if not (
            base_url.startswith("https://")
            or base_url.startswith("http://")
        ):
            raise ProviderError(
                "WoRMS base_url must use HTTP or HTTPS."
            )

        endpoint = f"{base_url}/AphiaRecordsByDate"

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

        window_days = self._positive_bounded_int(
            self.definition.get(
                "window_days",
                self.DEFAULT_WINDOW_DAYS,
            ),
            default=self.DEFAULT_WINDOW_DAYS,
            maximum=self.MAX_WINDOW_DAYS,
            field_name="window_days",
        )

        initial_offset = self._positive_bounded_int(
            self.definition.get(
                "start_offset",
                self.DEFAULT_OFFSET,
            ),
            default=self.DEFAULT_OFFSET,
            maximum=2_147_483_647,
            field_name="start_offset",
        )

        cursor = self._decode_cursor(
            self.cursor
        )

        window_start = self._cursor_date(
            cursor.get("window_start"),
            configured_start,
            field_name="window_start",
        )

        if window_start < configured_start:
            raise ProviderError(
                "WoRMS cursor window_start precedes configured start_date."
            )

        if window_start > configured_end:
            return Batch(
                records=[],
                next_cursor=None,
                exhausted=True,
                requests=0,
                raw=0,
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
            field_name="window_end",
        )

        if window_end < window_start:
            raise ProviderError(
                "WoRMS cursor window_end is earlier than window_start."
            )

        if window_end > maximum_window_end:
            raise ProviderError(
                "WoRMS cursor window exceeds configured window_days."
            )

        offset = self._cursor_offset(
            cursor.get("offset"),
            initial_offset,
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
                "WoRMS fetch completed without an HTTP request."
            )

        raw_records = self._extract_response_records(
            payload
        )

        retrieved_at = now()

        crawl_metadata = {
            "endpoint": endpoint,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "offset": offset,
            "returned": len(raw_records),
            "marine_only": marine_only == "true",
        }

        records: list[Taxon] = []

        for raw_record in raw_records:
            if not isinstance(
                raw_record,
                Mapping,
            ):
                continue

            normalized = self._normalize_record(
                raw_record=dict(raw_record),
                endpoint=endpoint,
                retrieved_at=retrieved_at,
                crawl_metadata=crawl_metadata,
            )

            if normalized is not None:
                records.append(
                    normalized
                )

        next_cursor, exhausted = self._next_cursor(
            raw_records=raw_records,
            offset=offset,
            initial_offset=initial_offset,
            window_start=window_start,
            window_end=window_end,
            configured_end=configured_end,
            window_days=window_days,
        )

        if (
            not exhausted
            and self.cursor
            and next_cursor == self.cursor
        ):
            raise ProviderError(
                "WoRMS returned an unchanged cursor state."
            )

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=request_count,
            raw=len(raw_records),
        )

    def _next_cursor(
        self,
        *,
        raw_records: list[Any],
        offset: int,
        initial_offset: int,
        window_start: date,
        window_end: date,
        configured_end: date,
        window_days: int,
    ) -> tuple[str | None, bool]:
        """Advance within the page sequence or to the next date window."""

        if raw_records:
            return (
                self._encode_cursor(
                    {
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        "offset": offset + 1,
                    }
                ),
                False,
            )

        next_window_start = (
            window_end
            + timedelta(days=1)
        )

        if next_window_start > configured_end:
            return None, True

        next_window_end = min(
            configured_end,
            next_window_start
            + timedelta(
                days=window_days - 1
            ),
        )

        return (
            self._encode_cursor(
                {
                    "window_start": next_window_start.isoformat(),
                    "window_end": next_window_end.isoformat(),
                    "offset": initial_offset,
                }
            ),
            False,
        )

    def _normalize_record(
        self,
        *,
        raw_record: dict[str, Any],
        endpoint: str,
        retrieved_at: str,
        crawl_metadata: Mapping[str, Any],
    ) -> Taxon | None:
        """Normalize one Aphia record."""

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
        ) or scientific_name

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
                "status",
                "taxonomicStatus",
            )
        )

        source_url = normalize_space(
            raw_record.get(
                "url"
            )
        )

        if not source_url:
            source_url = (
                "https://www.marinespecies.org/"
                "aphia.php?p=taxdetails&id="
                f"{provider_id}"
            )

        taxonomy = {
            "kingdom": normalize_space(
                raw_record.get(
                    "kingdom"
                )
            ),
            "phylum": normalize_space(
                raw_record.get(
                    "phylum"
                )
            ),
            "class": normalize_space(
                raw_record.get(
                    "class"
                )
            ),
            "order": normalize_space(
                raw_record.get(
                    "order"
                )
            ),
            "family": normalize_space(
                raw_record.get(
                    "family"
                )
            ),
            "genus": normalize_space(
                raw_record.get(
                    "genus"
                )
            ),
        }

        return Taxon(
            provider=self.name,
            provider_id=provider_id,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank,
            status=status,
            authorship=normalize_space(
                self._first_value(
                    raw_record,
                    "authority",
                    "authorship",
                    "scientificNameAuthorship",
                )
            ),
            kingdom=taxonomy["kingdom"],
            phylum=taxonomy["phylum"],
            class_name=taxonomy["class"],
            order=taxonomy["order"],
            family=taxonomy["family"],
            genus=taxonomy["genus"],
            accepted_provider_id=valid_aphia_id,
            source_url=source_url,
            source_modified=normalize_space(
                self._first_value(
                    raw_record,
                    "modified",
                    "lastModified",
                    "updated",
                )
            ),
            retrieved_at=retrieved_at,
            synonyms=self._extract_synonyms(
                raw_record=raw_record,
                scientific_name=scientific_name,
                valid_name=valid_name,
            ),
            extra={
                "source": "World Register of Marine Species",
                "endpoint": endpoint,
                "aphia_id": provider_id,
                "valid_aphia_id": valid_aphia_id,
                "valid_name": valid_name,
                "taxonomy": taxonomy,
                "environment": {
                    "marine": self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isMarine",
                            "is_marine",
                        )
                    ),
                    "brackish": self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isBrackish",
                            "is_brackish",
                        )
                    ),
                    "freshwater": self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isFreshwater",
                            "is_freshwater",
                        )
                    ),
                    "terrestrial": self._boolean_value(
                        self._first_value(
                            raw_record,
                            "isTerrestrial",
                            "is_terrestrial",
                        )
                    ),
                },
                "is_extinct": self._boolean_value(
                    self._first_value(
                        raw_record,
                        "isExtinct",
                        "is_extinct",
                    )
                ),
                "is_fossil": self._boolean_value(
                    self._first_value(
                        raw_record,
                        "isFossil",
                        "is_fossil",
                    )
                ),
                "parent_aphia_id": normalize_space(
                    self._first_value(
                        raw_record,
                        "parent_AphiaID",
                        "parentAphiaID",
                    )
                ),
                "parent_name_usage": normalize_space(
                    self._first_value(
                        raw_record,
                        "parent_name_usage",
                        "parentNameUsage",
                    )
                ),
                "lsid": normalize_space(
                    raw_record.get(
                        "lsid"
                    )
                ),
                "citation": normalize_space(
                    raw_record.get(
                        "citation"
                    )
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
            Mapping,
        ):
            raise ProviderError(
                "WoRMS returned an unsupported JSON response type."
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

        # Some gateways return one record object rather than a list.
        if self._first_value(
            dict(payload),
            "AphiaID",
            "aphiaID",
            "scientificname",
            "scientificName",
        ) not in (
            None,
            "",
        ):
            return [
                dict(payload)
            ]

        raise ProviderError(
            "WoRMS returned an object response without "
            "a recognized records list."
        )

    @classmethod
    def _extract_synonyms(
        cls,
        *,
        raw_record: Mapping[str, Any],
        scientific_name: str,
        valid_name: str,
    ) -> list[str]:
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
                        normalized = normalize_space(
                            item
                        )
                    elif isinstance(
                        item,
                        Mapping,
                    ):
                        normalized = normalize_space(
                            cls._first_value(
                                dict(item),
                                "scientificname",
                                "scientificName",
                                "valid_name",
                                "validName",
                                "name",
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

            seen.add(
                key
            )
            unique.append(
                normalized
            )

        return unique

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
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

        for source, target in aliases.items():
            if source in status:
                return target

        return status or "unknown"

    @staticmethod
    def _extract_api_error(
        payload: Mapping[str, Any],
    ) -> str:
        """Extract explicit API errors without treating metadata as errors."""

        explicit_error = payload.get(
            "error"
        )

        if isinstance(
            explicit_error,
            str,
        ):
            return normalize_space(
                explicit_error
            )

        if isinstance(
            explicit_error,
            Mapping,
        ):
            for key in (
                "message",
                "detail",
                "description",
                "error",
            ):
                value = normalize_space(
                    explicit_error.get(
                        key
                    )
                )

                if value:
                    return value

        if (
            "status" in payload
            and safe_int(
                payload.get(
                    "status"
                ),
                200,
            ) >= 400
        ):
            for key in (
                "message",
                "detail",
                "description",
            ):
                value = normalize_space(
                    payload.get(
                        key
                    )
                )

                if value:
                    return value

        return ""

    def _configured_end_date(
        self,
    ) -> date:
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
        if not cursor:
            return {}

        value = cursor.strip()

        if value.isdigit():
            offset = int(
                value
            )

            if offset < 1:
                raise ProviderError(
                    "WoRMS legacy cursor offset must be positive."
                )

            return {
                "offset": offset,
            }

        try:
            decoded = json.loads(
                value
            )
        except json.JSONDecodeError as error:
            raise ProviderError(
                "WoRMS cursor is neither a positive integer "
                "nor valid JSON."
            ) from error

        if not isinstance(
            decoded,
            dict,
        ):
            raise ProviderError(
                "WoRMS cursor JSON must decode to an object."
            )

        return decoded

    @staticmethod
    def _encode_cursor(
        cursor: Mapping[str, Any],
    ) -> str:
        return json.dumps(
            dict(cursor),
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
        normalized = normalize_space(
            value
        )

        if not normalized:
            raise ProviderError(
                f"WoRMS {field_name} is empty."
            )

        try:
            return date.fromisoformat(
                normalized.replace(
                    "Z",
                    "+00:00",
                )[:10]
            )
        except ValueError as error:
            raise ProviderError(
                f"Invalid WoRMS {field_name}: {value!r}. "
                "Expected YYYY-MM-DD or ISO-8601 datetime."
            ) from error

    @classmethod
    def _cursor_date(
        cls,
        value: Any,
        fallback: date,
        *,
        field_name: str,
    ) -> date:
        normalized = normalize_space(
            value
        )

        if not normalized:
            return fallback

        try:
            return date.fromisoformat(
                normalized[:10]
            )
        except ValueError as error:
            raise ProviderError(
                f"Invalid WoRMS cursor {field_name}: {value!r}."
            ) from error

    @staticmethod
    def _cursor_offset(
        value: Any,
        fallback: int,
    ) -> int:
        if value in (
            None,
            "",
        ):
            return fallback

        try:
            offset = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ProviderError(
                f"Invalid WoRMS cursor offset: {value!r}."
            ) from error

        if offset < 1:
            raise ProviderError(
                "WoRMS cursor offset must be positive."
            )

        return offset

    @staticmethod
    def _positive_bounded_int(
        value: Any,
        *,
        default: int,
        maximum: int,
        field_name: str,
    ) -> int:
        try:
            parsed = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            parsed = default

        if parsed < 1:
            raise ProviderError(
                f"WoRMS {field_name} must be positive."
            )

        return min(
            parsed,
            maximum,
        )

    @staticmethod
    def _api_datetime(
        value: date,
        *,
        end_of_day: bool,
    ) -> str:
        return (
            value.isoformat()
            + (
                "T23:59:59"
                if end_of_day
                else "T00:00:00"
            )
        )

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
    def _boolean_parameter(
        value: Any,
    ) -> str:
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

        if normalized in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return "true"

        if normalized in {
            "",
            "0",
            "false",
            "no",
            "off",
        }:
            return "false"

        raise ProviderError(
            f"Invalid WoRMS marine_only value: {value!r}."
        )

    @staticmethod
    def _boolean_value(
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
