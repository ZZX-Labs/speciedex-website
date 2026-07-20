#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/itis.py

ITIS provider plug-in.

Fetches one complete ITIS taxonomic record with one logical network request
per provider run. The provider accepts JSON or XML responses because ITIS
deployments and intermediary services may return either representation.

The complete decoded provider response is preserved in Taxon.extra["raw"]
while principal taxonomic and nomenclatural fields are normalized for
Speciedex reconciliation.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

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
        Fetch one complete ITIS record with one network request.

        One TSN is processed per scheduled provider execution. A confirmed
        missing or unused TSN advances the cursor. Service errors, malformed
        responses, and HTML error pages do not advance the cursor.
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

        if not (
            base_url.startswith("https://")
            or base_url.startswith("http://")
        ):
            raise ProviderError(
                "ITIS base_url must use HTTP or HTTPS."
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

        if start_tsn < 1:
            raise ProviderError(
                "ITIS start_tsn must be positive."
            )

        if maximum_tsn < start_tsn:
            raise ProviderError(
                "ITIS max_tsn must be greater than or equal to start_tsn."
            )

        cursor = self._decode_cursor(
            self.cursor
        )

        current_tsn = safe_int(
            cursor.get("tsn"),
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

        payload, response_metadata = (
            self._request_record(
                endpoint=endpoint,
                tsn=current_tsn,
            )
        )

        next_tsn = current_tsn + 1
        exhausted = next_tsn > maximum_tsn

        next_cursor = (
            None
            if exhausted
            else self._encode_cursor(
                {
                    "tsn": next_tsn,
                    "endpoint": endpoint,
                }
            )
        )

        if payload is None:
            return Batch(
                records=[],
                next_cursor=next_cursor,
                exhausted=exhausted,
                requests=response_metadata.get("requests", 1),
                raw=1,
            )

        if not isinstance(
            payload,
            dict,
        ):
            raise ProviderError(
                "ITIS returned a decoded response "
                "that is not an object."
            )

        api_error = self._extract_api_error(
            payload
        )

        if api_error:
            if self._is_missing_record_error(
                api_error
            ):
                return Batch(
                    records=[],
                    next_cursor=next_cursor,
                    exhausted=exhausted,
                    requests=response_metadata.get("requests", 1),
                    raw=1,
                )

            raise ProviderError(
                f"ITIS API error for TSN "
                f"{current_tsn}: {api_error}"
            )

        record = self._normalize_record(
            payload=payload,
            tsn=current_tsn,
            endpoint=endpoint,
            response_metadata=response_metadata,
            retrieved_at=now(),
        )

        if record is None:
            if self._looks_like_missing_record(
                payload
            ):
                return Batch(
                    records=[],
                    next_cursor=next_cursor,
                    exhausted=exhausted,
                    requests=response_metadata.get("requests", 1),
                    raw=1,
                )

            raise ProviderError(
                f"ITIS response for TSN {current_tsn} "
                "contained no usable scientific name."
            )

        return Batch(
            records=[record],
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=response_metadata.get("requests", 1),
            raw=1,
        )

    def _request_record(
        self,
        endpoint: str,
        tsn: int,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any],
    ]:
        """
        Request one ITIS record and decode JSON or XML.

        This function performs exactly one call to urlopen. It intentionally
        bypasses HTTPClient.get_json because that helper rejects non-JSON
        responses before this provider can inspect a valid ITIS XML response
        or distinguish an unused TSN from an upstream HTML failure.
        """

        query = urlencode(
            {
                "tsn": tsn,
            }
        )

        url = f"{endpoint}?{query}"

        headers = {
            "Accept": (
                "application/json, "
                "application/xml;q=0.9, "
                "text/xml;q=0.8"
            ),
            "User-Agent": self.http.user_agent,
        }

        request = Request(
            url,
            headers=headers,
            method="GET",
        )

        self.http.requests += 1

        try:
            with urlopen(
                request,
                timeout=self.http.timeout,
            ) as response:
                status = int(
                    getattr(
                        response,
                        "status",
                        200,
                    )
                )

                content_type = normalize_space(
                    response.headers.get(
                        "Content-Type",
                        "",
                    )
                )

                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

                body_bytes = response.read()

        except HTTPError as error:
            status = int(
                getattr(
                    error,
                    "code",
                    0,
                )
            )

            try:
                body_bytes = error.read()
            except OSError:
                body_bytes = b""

            content_type = normalize_space(
                error.headers.get(
                    "Content-Type",
                    "",
                )
                if error.headers
                else ""
            )

            body = body_bytes.decode(
                "utf-8",
                errors="replace",
            )

            if (
                status in {
                    400,
                    404,
                }
                and self._is_missing_record_error(
                    body
                )
            ):
                return (
                    None,
                    {
                        "url": url,
                        "status": status,
                        "requests": 1,
                        "content_type": content_type,
                        "format": "empty",
                    },
                )

            raise ProviderError(
                f"ITIS HTTP {status} for TSN "
                f"{tsn}: "
                f"{self._response_excerpt(body)}"
            ) from error

        except (
            URLError,
            TimeoutError,
            OSError,
        ) as error:
            raise ProviderError(
                f"ITIS request failed for TSN "
                f"{tsn}: {error}"
            ) from error

        body = body_bytes.decode(
            charset,
            errors="replace",
        ).strip()

        metadata = {
            "url": url,
            "status": status,
            "requests": 1,
            "content_type": content_type,
            "content_length": len(
                body_bytes
            ),
            "format": "",
        }

        if not 200 <= status < 300:
            raise ProviderError(
                f"ITIS HTTP {status} for TSN "
                f"{tsn}: "
                f"{self._response_excerpt(body)}"
            )

        if not body:
            metadata["format"] = "empty"
            metadata["empty_success"] = True
            return (None, metadata)

        if self._looks_like_html(
            body,
            content_type,
        ):
            raise ProviderError(
                f"ITIS returned HTML instead of "
                f"taxonomic data for TSN {tsn}: "
                f"{self._response_excerpt(body)}"
            )

        payload = self._decode_response(
            body=body,
            content_type=content_type,
        )

        metadata["format"] = (
            "json"
            if self._looks_like_json(
                body,
                content_type,
            )
            else "xml"
        )

        return (
            payload,
            metadata,
        )

    def _decode_response(
        self,
        body: str,
        content_type: str,
    ) -> dict[str, Any]:
        """Decode an ITIS JSON or XML response."""

        json_error: Exception | None = None
        xml_error: Exception | None = None

        if self._looks_like_json(
            body,
            content_type,
        ):
            try:
                value = json.loads(
                    body
                )

                if isinstance(
                    value,
                    dict,
                ):
                    return value

                raise ProviderError(
                    "ITIS JSON response is not "
                    "an object."
                )

            except (
                json.JSONDecodeError,
                ProviderError,
            ) as error:
                json_error = error

        try:
            root = ElementTree.fromstring(
                body
            )

            converted = self._xml_element_to_value(
                root
            )

            if isinstance(
                converted,
                dict,
            ):
                root_name = self._strip_xml_namespace(
                    root.tag
                )

                if (
                    len(converted) == 1
                    and root_name in converted
                ):
                    unwrapped = converted[
                        root_name
                    ]

                    if isinstance(
                        unwrapped,
                        dict,
                    ):
                        return unwrapped

                return converted

            return {
                self._strip_xml_namespace(
                    root.tag
                ): converted
            }

        except ElementTree.ParseError as error:
            xml_error = error

        if json_error is None:
            try:
                value = json.loads(
                    body
                )

                if isinstance(
                    value,
                    dict,
                ):
                    return value

            except json.JSONDecodeError as error:
                json_error = error

        raise ProviderError(
            "Unable to decode ITIS response as "
            f"JSON or XML. JSON error: "
            f"{json_error}; XML error: {xml_error}. "
            f"Response begins: "
            f"{self._response_excerpt(body)}"
        )

    def _normalize_record(
        self,
        payload: dict[str, Any],
        tsn: int,
        endpoint: str,
        response_metadata: dict[str, Any],
        retrieved_at: str,
    ) -> Taxon | None:
        """Normalize one complete ITIS response."""

        payload = self._unwrap_payload(
            payload
        )

        core_metadata = self._find_dictionary(
            payload,
            (
                "coreMetadata",
                "core_metadata",
            ),
        )

        usage = self._find_dictionary(
            payload,
            (
                "usage",
                "taxonUsage",
            ),
        )

        accepted_name = self._find_dictionary(
            payload,
            (
                "acceptedName",
                "accepted_name",
            ),
        )

        scientific_name_data = (
            self._find_dictionary(
                payload,
                (
                    "scientificName",
                    "scientific_name",
                ),
            )
        )

        taxon_author = self._find_dictionary(
            payload,
            (
                "taxonAuthor",
                "taxon_author",
            ),
        )

        hierarchy_up = self._find_dictionary(
            payload,
            (
                "hierarchyUp",
                "hierarchy_up",
            ),
        )

        hierarchy_down = self._find_dictionary(
            payload,
            (
                "hierarchyDown",
                "hierarchy_down",
            ),
        )

        lineage = self._extract_lineage(
            hierarchy_up
        )

        children = (
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
            or self._find_first_recursive(
                payload,
                (
                    "combinedName",
                    "taxonName",
                    "scientificName",
                ),
            )
        )

        if not scientific_name:
            return None

        canonical_name = (
            self._build_canonical_name(
                scientific_name_data,
                scientific_name,
            )
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
            or self._find_first_recursive(
                payload,
                (
                    "rankName",
                    "taxonRank",
                ),
            )
        ).casefold()

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
            or self._find_first_recursive(
                payload,
                (
                    "usageStatus",
                    "taxonomicStatus",
                ),
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

        if accepted_tsn == str(tsn):
            accepted_tsn = ""

        synonyms = self._extract_synonyms(
            payload=payload,
            accepted_name=accepted_name,
            scientific_name=scientific_name,
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

        source_url = (
            "https://www.itis.gov/servlet/"
            "SingleRpt/SingleRpt?"
            "search_topic=TSN&"
            f"search_value={tsn}"
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
                "endpoint": endpoint,
                "tsn": str(tsn),
                "accepted_tsn": accepted_tsn,
                "lineage": lineage,
                "hierarchy_children": children,
                "common_names": (
                    self._extract_common_names(
                        payload
                    )
                ),
                "publications": (
                    self._extract_named_collection(
                        payload,
                        (
                            "publications",
                            "publication",
                            "referenceLinks",
                        ),
                    )
                ),
                "experts": (
                    self._extract_named_collection(
                        payload,
                        (
                            "experts",
                            "expert",
                            "taxonExperts",
                        ),
                    )
                ),
                "comments": (
                    self._extract_named_collection(
                        payload,
                        (
                            "comments",
                            "comment",
                            "taxonComments",
                        ),
                    )
                ),
                "other_sources": (
                    self._extract_named_collection(
                        payload,
                        (
                            "otherSources",
                            "otherSource",
                            "sourceLinks",
                        ),
                    )
                ),
                "response": response_metadata,
                "raw": payload,
            },
        )

    def _extract_lineage(
        self,
        hierarchy: dict[str, Any],
    ) -> dict[str, str]:
        """Extract every returned parent rank."""

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
            if not isinstance(
                row,
                dict,
            ):
                continue

            rank = normalize_space(
                self._first_value(
                    row,
                    "rankName",
                    "rank",
                    "taxonRank",
                )
            ).casefold()

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
        """Extract returned descendant information."""

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
            if not isinstance(
                row,
                dict,
            ):
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
                    ).casefold(),
                    "raw": row,
                }
            )

        return children

    def _build_canonical_name(
        self,
        scientific_name_data: dict[str, Any],
        fallback: str,
    ) -> str:
        """Construct a canonical name from ITIS unit-name fields."""

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

        canonical_name = " ".join(
            part
            for part in parts
            if part
        )

        return (
            canonical_name
            or normalize_space(
                self._first_value(
                    scientific_name_data,
                    "combinedName",
                    "taxonName",
                    "scientificName",
                )
            )
            or fallback
        )

    def _extract_synonyms(
        self,
        payload: dict[str, Any],
        accepted_name: dict[str, Any],
        scientific_name: str,
    ) -> list[str]:
        """Extract and deduplicate synonym-like names."""

        values: list[str] = []

        for collection_name in (
            "synonymNames",
            "synonyms",
            "taxonomicSynonyms",
        ):
            for item in self._find_list(
                payload,
                (
                    collection_name,
                ),
            ):
                if isinstance(
                    item,
                    dict,
                ):
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
                    value = normalize_space(
                        item
                    )

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
            values.append(
                accepted_value
            )

        unique: list[str] = []
        seen = {
            scientific_name.casefold(),
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
            unique.append(normalized)

        return unique

    def _extract_common_names(
        self,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract vernacular names with language metadata."""

        rows = self._find_list(
            payload,
            (
                "commonNames",
                "vernacularNames",
            ),
        )

        results: list[dict[str, Any]] = []

        for row in rows:
            if isinstance(
                row,
                dict,
            ):
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
                        "language": (
                            normalize_space(
                                self._first_value(
                                    row,
                                    "language",
                                    "languageName",
                                    "lang",
                                )
                            )
                        ),
                        "jurisdiction": (
                            normalize_space(
                                self._first_value(
                                    row,
                                    "jurisdictionValue",
                                    "jurisdiction",
                                )
                            )
                        ),
                        "raw": row,
                    }
                )

            else:
                name = normalize_space(
                    row
                )

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

    def _extract_api_error(
        self,
        payload: dict[str, Any],
    ) -> str:
        """
        Extract explicit ITIS service errors without treating ordinary record
        fields named ``message`` as fatal errors.
        """

        explicit_keys = (
            "error",
            "errorMessage",
            "errorDescription",
            "faultstring",
            "faultString",
        )

        for key in explicit_keys:
            value = self._find_first_recursive(
                payload,
                (key,),
            )

            if isinstance(
                value,
                (str, int, float),
            ):
                normalized = normalize_space(
                    value
                )

                if normalized:
                    return normalized

            if isinstance(
                value,
                dict,
            ):
                for nested_key in (
                    "message",
                    "description",
                    "detail",
                    "#text",
                ):
                    nested = normalize_space(
                        value.get(nested_key)
                    )

                    if nested:
                        return nested

        for wrapper_key in (
            "Fault",
            "fault",
            "serviceError",
            "ServiceError",
        ):
            wrapper = self._find_dictionary(
                payload,
                (wrapper_key,),
            )

            if not wrapper:
                continue

            for nested_key in (
                "faultstring",
                "message",
                "description",
                "detail",
                "#text",
            ):
                nested = self._find_first_recursive(
                    wrapper,
                    (nested_key,),
                )

                normalized = normalize_space(
                    nested
                )

                if normalized:
                    return normalized

        return ""

    @staticmethod
    def _is_missing_record_error(
        error: str,
    ) -> bool:
        """Return True only for a confirmed missing or invalid TSN."""

        normalized = normalize_space(
            error
        ).casefold()

        return any(
            marker in normalized
            for marker in (
                "no record",
                "no records",
                "not found",
                "invalid tsn",
                "tsn does not exist",
                "unable to find",
                "no data found",
                "no result",
            )
        )

    @classmethod
    def _looks_like_missing_record(
        cls,
        payload: dict[str, Any],
    ) -> bool:
        """Identify an otherwise empty missing-record response."""

        if not payload:
            return True

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
        )

        if cls._is_missing_record_error(
            serialized
        ):
            return True

        meaningful_values = [
            value
            for value
            in cls._walk_scalar_values(
                payload
            )
            if normalize_space(value)
        ]

        return not meaningful_values

    @staticmethod
    def _normalize_status(
        value: Any,
    ) -> str:
        """Normalize ITIS usage/status terminology."""

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

        for source, target in (
            aliases.items()
        ):
            if source in status:
                return target

        return status or "unknown"

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        """Infer only species-level ranks from name shape."""

        words = scientific_name.split()

        if len(words) == 2:
            return "species"

        if len(words) >= 3:
            return "subspecies"

        return "unknown"

    @staticmethod
    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> dict[str, Any]:
        """Decode structured state while accepting legacy numeric cursors."""

        if not cursor:
            return {}

        value = cursor.strip()

        if value.isdigit():
            tsn = int(value)

            if tsn < 1:
                raise ProviderError(
                    "ITIS cursor TSN must be positive."
                )

            return {
                "tsn": tsn,
            }

        try:
            decoded = json.loads(
                value
            )
        except json.JSONDecodeError as error:
            raise ProviderError(
                "ITIS cursor is neither a numeric TSN nor valid JSON."
            ) from error

        if not isinstance(
            decoded,
            dict,
        ):
            raise ProviderError(
                "ITIS cursor JSON must decode to an object."
            )

        tsn_value = decoded.get(
            "tsn"
        )

        if tsn_value is not None:
            try:
                tsn = int(
                    tsn_value
                )
            except (
                TypeError,
                ValueError,
            ) as error:
                raise ProviderError(
                    "ITIS cursor contains a non-integer TSN."
                ) from error

            if tsn < 1:
                raise ProviderError(
                    "ITIS cursor TSN must be positive."
                )

            decoded["tsn"] = tsn

        return decoded

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
    @classmethod
    def _unwrap_payload(
        cls,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Unwrap common JSON, SOAP, and XML response envelopes."""

        current = payload
        wrapper_keys = {
            "envelope",
            "body",
            "getfullrecordfromtsnresponse",
            "getfullrecordfromtsnresult",
            "return",
            "result",
        }

        while isinstance(
            current,
            dict,
        ):
            next_value: dict[str, Any] | None = None

            for key, candidate in current.items():
                normalized_key = (
                    cls._strip_xml_namespace(
                        str(key)
                    )
                    .casefold()
                )

                if (
                    normalized_key in wrapper_keys
                    and isinstance(
                        candidate,
                        dict,
                    )
                ):
                    next_value = candidate
                    break

            if next_value is None:
                break

            current = next_value

        return current

    @classmethod
    def _find_dictionary(
        cls,
        value: Any,
        keys: tuple[str, ...],
    ) -> dict[str, Any]:
        """Recursively find the first dictionary under a named key."""

        if isinstance(
            value,
            dict,
        ):
            for key in keys:
                candidate = value.get(
                    key
                )

                if isinstance(
                    candidate,
                    dict,
                ):
                    return candidate

            for child in value.values():
                result = cls._find_dictionary(
                    child,
                    keys,
                )

                if result:
                    return result

        elif isinstance(
            value,
            list,
        ):
            for child in value:
                result = cls._find_dictionary(
                    child,
                    keys,
                )

                if result:
                    return result

        return {}

    @classmethod
    @classmethod
    def _find_list(
        cls,
        value: Any,
        keys: tuple[str, ...],
    ) -> list[Any]:
        """
        Recursively find a provider collection.

        XML decoders frequently represent a one-item collection as a mapping
        rather than a list, so matching singleton mappings are normalized to a
        one-element list.
        """

        normalized_keys = {
            str(key).casefold()
            for key in keys
        }

        if isinstance(
            value,
            dict,
        ):
            for key, candidate in value.items():
                if str(key).casefold() not in normalized_keys:
                    continue

                if isinstance(
                    candidate,
                    list,
                ):
                    return candidate

                if isinstance(
                    candidate,
                    dict,
                ):
                    direct_list = cls._first_list_value(
                        candidate
                    )

                    if direct_list is not None:
                        return direct_list

                    # A single XML collection item is commonly represented
                    # directly as a mapping.
                    if candidate:
                        return [
                            candidate
                        ]

                if candidate not in (
                    None,
                    "",
                ):
                    return [
                        candidate
                    ]

            for child in value.values():
                result = cls._find_list(
                    child,
                    keys,
                )

                if result:
                    return result

        elif isinstance(
            value,
            list,
        ):
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
        """Return the first immediate list value."""

        for child in value.values():
            if isinstance(
                child,
                list,
            ):
                return child

        return None

    @classmethod
    def _find_first_recursive(
        cls,
        value: Any,
        keys: tuple[str, ...],
    ) -> Any:
        """Recursively find the first nonempty value under any named key."""

        if isinstance(
            value,
            dict,
        ):
            for key in keys:
                candidate = value.get(
                    key
                )

                if candidate not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return candidate

            for child in value.values():
                result = (
                    cls._find_first_recursive(
                        child,
                        keys,
                    )
                )

                if result not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return result

        elif isinstance(
            value,
            list,
        ):
            for child in value:
                result = (
                    cls._find_first_recursive(
                        child,
                        keys,
                    )
                )

                if result not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return result

        return None

    @staticmethod
    def _first_value(
        record: dict[str, Any],
        *keys: str,
    ) -> Any:
        """Return the first nonempty direct dictionary value."""

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

    @classmethod
    def _walk_scalar_values(
        cls,
        value: Any,
    ) -> list[Any]:
        """Return every scalar contained in a decoded response."""

        values: list[Any] = []

        if isinstance(
            value,
            dict,
        ):
            for child in value.values():
                values.extend(
                    cls._walk_scalar_values(
                        child
                    )
                )

        elif isinstance(
            value,
            list,
        ):
            for child in value:
                values.extend(
                    cls._walk_scalar_values(
                        child
                    )
                )

        elif value is not None:
            values.append(value)

        return values

    @classmethod
    def _xml_element_to_value(
        cls,
        element: ElementTree.Element,
    ) -> Any:
        """Convert an XML element tree into JSON-compatible data."""

        tag = cls._strip_xml_namespace(
            element.tag
        )

        children = list(
            element
        )

        attributes = {
            f"@{cls._strip_xml_namespace(key)}": value
            for key, value
            in element.attrib.items()
        }

        text = normalize_space(
            element.text
        )

        if not children:
            if attributes:
                if text:
                    attributes["#text"] = text

                return {
                    tag: attributes
                }

            return {
                tag: text
            }

        grouped: dict[str, list[Any]] = {}

        for child in children:
            child_tag = (
                cls._strip_xml_namespace(
                    child.tag
                )
            )

            child_value = (
                cls._xml_element_to_value(
                    child
                )
            )

            if (
                isinstance(
                    child_value,
                    dict,
                )
                and child_tag
                in child_value
            ):
                child_value = child_value[
                    child_tag
                ]

            grouped.setdefault(
                child_tag,
                [],
            ).append(
                child_value
            )

        result: dict[str, Any] = dict(
            attributes
        )

        if text:
            result["#text"] = text

        for child_tag, values in (
            grouped.items()
        ):
            result[child_tag] = (
                values[0]
                if len(values) == 1
                else values
            )

        return {
            tag: result
        }

    @staticmethod
    def _strip_xml_namespace(
        tag: str,
    ) -> str:
        """Remove XML namespaces and prefixes from an element name."""

        if "}" in tag:
            tag = tag.rsplit(
                "}",
                1,
            )[-1]

        if ":" in tag:
            tag = tag.rsplit(
                ":",
                1,
            )[-1]

        return tag

    @staticmethod
    def _looks_like_json(
        body: str,
        content_type: str,
    ) -> bool:
        """Identify a likely JSON response."""

        normalized_type = (
            content_type.casefold()
        )

        stripped = body.lstrip()

        return (
            "json" in normalized_type
            or stripped.startswith("{")
            or stripped.startswith("[")
        )

    @staticmethod
    def _looks_like_html(
        body: str,
        content_type: str,
    ) -> bool:
        """Identify an HTML error or service page."""

        normalized_type = (
            content_type.casefold()
        )

        prefix = body.lstrip()[
            :512
        ].casefold()

        return (
            "text/html" in normalized_type
            or prefix.startswith(
                "<!doctype html"
            )
            or prefix.startswith("<html")
            or "<body" in prefix
        )

    @staticmethod
    def _response_excerpt(
        body: str,
        limit: int = 300,
    ) -> str:
        """Create a compact diagnostic excerpt without logging full pages."""

        excerpt = re.sub(
            r"\s+",
            " ",
            body,
        ).strip()

        if len(excerpt) > limit:
            excerpt = (
                excerpt[:limit]
                + "..."
            )

        return (
            excerpt
            or "<empty response>"
        )

    def _extract_named_collection(
        self,
        payload: dict[str, Any],
        keys: tuple[str, ...],
    ) -> list[Any]:
        """Return the first matching provider collection."""

        return self._find_list(
            payload,
            keys,
        )
