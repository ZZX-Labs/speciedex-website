#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/inaturalist.py

iNaturalist taxonomy provider plug-in.

Fetches one page from the iNaturalist taxa endpoint per provider run. Records
are normalized into the shared Taxon contract while the complete source object
is preserved under ``Taxon.extra["raw"]``.

The provider uses stable numeric taxon identifiers, deterministic page cursors,
and one logical API request per fetch call.

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
    """iNaturalist taxa API provider."""

    PROVIDER_NAME = "inaturalist"

    DEFAULT_BASE_URL = "https://api.inaturalist.org/v1"
    DEFAULT_SITE_URL = "https://www.inaturalist.org/taxa/"
    DEFAULT_PAGE_SIZE = 200
    MAX_PAGE_SIZE = 200

    def fetch(self) -> Batch:
        """Fetch and normalize one deterministic iNaturalist taxa page."""

        base_url = normalize_space(
            self.definition.get(
                "base_url",
                self.DEFAULT_BASE_URL,
            )
        ).rstrip("/")

        if not base_url:
            raise ProviderError(
                "iNaturalist base_url is empty."
            )

        if not (
            base_url.startswith("https://")
            or base_url.startswith("http://")
        ):
            raise ProviderError(
                "iNaturalist base_url must use HTTP or HTTPS."
            )

        site_url = normalize_space(
            self.definition.get(
                "site_url",
                self.DEFAULT_SITE_URL,
            )
        )

        if not site_url:
            raise ProviderError(
                "iNaturalist site_url is empty."
            )

        cursor = self._decode_cursor(
            self.cursor
        )

        start_page = max(
            1,
            safe_int(
                self.definition.get(
                    "start_page",
                    1,
                ),
                1,
            ),
        )

        page = max(
            1,
            safe_int(
                cursor.get(
                    "page"
                ),
                start_page,
            ),
        )

        configured_page_size = safe_int(
            self.definition.get(
                "page_size",
                self.DEFAULT_PAGE_SIZE,
            ),
            self.DEFAULT_PAGE_SIZE,
        )

        cursor_page_size = safe_int(
            cursor.get(
                "per_page"
            ),
            configured_page_size,
        )

        per_page = max(
            1,
            min(
                configured_page_size,
                cursor_page_size,
                self.batch_size,
                self.MAX_PAGE_SIZE,
            ),
        )

        parameters: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
            "order": normalize_space(
                self.definition.get(
                    "order",
                    "asc",
                )
            ) or "asc",
            "order_by": normalize_space(
                self.definition.get(
                    "order_by",
                    "id",
                )
            ) or "id",
            "is_active": self._boolean_parameter(
                self.definition.get(
                    "is_active",
                    True,
                )
            ),
        }

        optional_parameters = {
            "taxon_id": "taxon_id",
            "parent_id": "parent_id",
            "rank": "rank",
            "rank_level": "rank_level",
            "q": "q",
            "locale": "locale",
        }

        for definition_key, api_key in optional_parameters.items():
            value = self.definition.get(
                definition_key
            )

            if value not in (
                None,
                "",
                [],
                {},
            ):
                parameters[
                    api_key
                ] = value

        requests_before = self.http.requests

        payload = self.http.get_json(
            f"{base_url}/taxa",
            parameters,
        )

        request_count = (
            self.http.requests
            - requests_before
        )

        if request_count < 1:
            raise ProviderError(
                "iNaturalist fetch completed without an HTTP request."
            )

        if not isinstance(
            payload,
            Mapping,
        ):
            raise ProviderError(
                "iNaturalist returned a non-object JSON response."
            )

        self._raise_api_error(
            payload
        )

        rows = payload.get(
            "results",
            [],
        )

        if not isinstance(
            rows,
            list,
        ):
            raise ProviderError(
                "iNaturalist response field results is not a list."
            )

        total_results = self._optional_int(
            payload.get(
                "total_results"
            )
        )

        retrieved_at = now()
        records: list[Taxon] = []

        for item in rows:
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            record = self._normalize_record(
                raw=dict(item),
                site_url=site_url,
                retrieved_at=retrieved_at,
                crawl_metadata={
                    "endpoint": f"{base_url}/taxa",
                    "page": page,
                    "per_page": per_page,
                    "returned": len(
                        rows
                    ),
                    "total_results": total_results,
                    "parameters": dict(
                        parameters
                    ),
                },
            )

            if record is not None:
                records.append(
                    record
                )

        exhausted = self._is_exhausted(
            page=page,
            per_page=per_page,
            returned=len(
                rows
            ),
            total_results=total_results,
        )

        next_cursor = (
            None
            if exhausted
            else self._encode_cursor(
                {
                    "page": page + 1,
                    "per_page": per_page,
                }
            )
        )

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=request_count,
            raw=len(
                rows
            ),
        )

    def _normalize_record(
        self,
        *,
        raw: dict[str, Any],
        site_url: str,
        retrieved_at: str,
        crawl_metadata: Mapping[str, Any],
    ) -> Taxon | None:
        """Normalize one iNaturalist taxon record."""

        provider_id = normalize_space(
            self._first_value(
                raw,
                "id",
                "taxon_id",
                "taxonId",
            )
        )

        scientific_name = normalize_space(
            self._first_value(
                raw,
                "name",
                "scientific_name",
                "scientificName",
            )
        )

        if not provider_id or not scientific_name:
            return None

        rank = normalize_space(
            self._first_value(
                raw,
                "rank",
                "taxon_rank",
                "taxonRank",
            )
        ).casefold() or self._infer_rank(
            scientific_name
        )

        canonical_name = normalize_space(
            self._first_value(
                raw,
                "canonical_name",
                "canonicalName",
                "name",
            )
        ) or scientific_name

        is_active = self._optional_bool(
            raw.get(
                "is_active"
            )
        )

        status = (
            "accepted"
            if is_active is not False
            else "inactive"
        )

        accepted_provider_id = normalize_space(
            self._first_value(
                raw,
                "current_synonymous_taxon_id",
                "currentSynonymousTaxonId",
                "accepted_taxon_id",
                "acceptedTaxonId",
            )
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        lineage = self._extract_lineage(
            raw
        )

        synonyms = self._extract_synonyms(
            raw,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
        )

        source_url = normalize_space(
            self._first_value(
                raw,
                "url",
                "uri",
                "source_url",
                "sourceUrl",
            )
        )

        if not source_url:
            source_url = (
                site_url.rstrip("/")
                + "/"
                + provider_id
            )

        default_photo = self._mapping_or_empty(
            raw.get(
                "default_photo"
            )
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
                    "attribution",
                    "authorship",
                    "authority",
                )
            ),
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
            accepted_provider_id=accepted_provider_id,
            source_url=source_url,
            source_modified=normalize_space(
                self._first_value(
                    raw,
                    "updated_at",
                    "updatedAt",
                    "modified",
                )
            ),
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": "iNaturalist",
                "reference_only": True,
                "taxon_id": provider_id,
                "rank_level": self._optional_float(
                    raw.get(
                        "rank_level"
                    )
                ),
                "iconic_taxon_name": normalize_space(
                    raw.get(
                        "iconic_taxon_name"
                    )
                ),
                "preferred_common_name": normalize_space(
                    raw.get(
                        "preferred_common_name"
                    )
                ),
                "english_common_name": normalize_space(
                    raw.get(
                        "english_common_name"
                    )
                ),
                "ancestry": normalize_space(
                    raw.get(
                        "ancestry"
                    )
                ),
                "ancestor_ids": self._parse_ancestry(
                    raw.get(
                        "ancestry"
                    )
                ),
                "ancestors": self._normalize_ancestors(
                    raw.get(
                        "ancestors"
                    )
                ),
                "parent_id": normalize_space(
                    self._first_value(
                        raw,
                        "parent_id",
                        "parentId",
                    )
                ),
                "is_active": is_active,
                "is_iconic": self._optional_bool(
                    raw.get(
                        "is_iconic"
                    )
                ),
                "observations_count": self._optional_int(
                    raw.get(
                        "observations_count"
                    )
                ),
                "listed_taxa_count": self._optional_int(
                    raw.get(
                        "listed_taxa_count"
                    )
                ),
                "conservation_status": self._mapping_or_empty(
                    raw.get(
                        "conservation_status"
                    )
                ),
                "conservation_statuses": self._list_value(
                    raw.get(
                        "conservation_statuses"
                    )
                ),
                "taxon_schemes_count": self._optional_int(
                    raw.get(
                        "taxon_schemes_count"
                    )
                ),
                "taxon_changes_count": self._optional_int(
                    raw.get(
                        "taxon_changes_count"
                    )
                ),
                "complete_species_count": self._optional_int(
                    raw.get(
                        "complete_species_count"
                    )
                ),
                "default_photo": default_photo,
                "photos": self._normalize_photos(
                    raw.get(
                        "photos"
                    ),
                    default_photo,
                ),
                "wikipedia_url": normalize_space(
                    raw.get(
                        "wikipedia_url"
                    )
                ),
                "iconic_taxon_id": normalize_space(
                    raw.get(
                        "iconic_taxon_id"
                    )
                ),
                "created_at": normalize_space(
                    raw.get(
                        "created_at"
                    )
                ),
                "updated_at": normalize_space(
                    raw.get(
                        "updated_at"
                    )
                ),
                "crawl": dict(
                    crawl_metadata
                ),
                "raw": raw,
            },
        )

    @classmethod
    def _extract_lineage(
        cls,
        raw: Mapping[str, Any],
    ) -> dict[str, str]:
        """Extract major lineage values from ancestors and direct fields."""

        lineage = {
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

        for item in cls._list_value(
            raw.get(
                "ancestors"
            )
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
                "taxon_names",
                "taxonNames",
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
                    )
                )

                lexical_class = normalize_space(
                    cls._first_value(
                        item,
                        "lexicon",
                        "lexical_class",
                        "lexicalClass",
                    )
                ).casefold()

                if lexical_class and lexical_class not in {
                    "scientific names",
                    "scientific name",
                    "synonym",
                }:
                    continue

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
    def _normalize_ancestors(
        cls,
        value: Any,
    ) -> list[dict[str, Any]]:
        """Normalize ancestor taxon summaries."""

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
                    "id": normalize_space(
                        cls._first_value(
                            item,
                            "id",
                            "taxon_id",
                            "taxonId",
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
                    "rank_level": cls._optional_float(
                        item.get(
                            "rank_level"
                        )
                    ),
                    "raw": dict(
                        item
                    ),
                }
            )

        return result

    @classmethod
    def _normalize_photos(
        cls,
        value: Any,
        default_photo: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Normalize available taxon photo metadata."""

        values = cls._list_value(
            value
        )

        if (
            default_photo
            and not values
        ):
            values = [
                default_photo
            ]

        result: list[dict[str, Any]] = []

        for item in values:
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
                    "id": normalize_space(
                        cls._first_value(
                            item,
                            "id",
                            "photo_id",
                            "photoId",
                        )
                    ),
                    "url": normalize_space(
                        cls._first_value(
                            item,
                            "url",
                            "medium_url",
                            "mediumUrl",
                            "square_url",
                            "squareUrl",
                        )
                    ),
                    "attribution": normalize_space(
                        cls._first_value(
                            item,
                            "attribution",
                            "credit",
                        )
                    ),
                    "license_code": normalize_space(
                        cls._first_value(
                            item,
                            "license_code",
                            "licenseCode",
                            "license",
                        )
                    ),
                }
            )

            if entry.get(
                "url"
            ):
                result.append(
                    entry
                )

        return result

    @staticmethod
    def _parse_ancestry(
        value: Any,
    ) -> list[str]:
        ancestry = normalize_space(
            value
        )

        if not ancestry:
            return []

        return [
            item
            for item in ancestry.split(
                "/"
            )
            if item
        ]

    @staticmethod
    def _is_exhausted(
        *,
        page: int,
        per_page: int,
        returned: int,
        total_results: int | None,
    ) -> bool:
        if returned < per_page:
            return True

        if total_results is None:
            return False

        return (
            page * per_page
            >= total_results
        )

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> dict[str, Any]:
        """Decode structured or legacy numeric page state."""

        if not cursor:
            return {}

        value = cursor.strip()

        if value.isdigit():
            page = int(
                value
            )

            if page < 1:
                raise ProviderError(
                    "iNaturalist page cursor must be positive."
                )

            return {
                "page": page
            }

        try:
            decoded = json.loads(
                value
            )
        except json.JSONDecodeError as error:
            raise ProviderError(
                "iNaturalist cursor is neither a positive "
                "page number nor valid JSON."
            ) from error

        if not isinstance(
            decoded,
            dict,
        ):
            raise ProviderError(
                "iNaturalist cursor JSON must decode to an object."
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

    @staticmethod
    def _raise_api_error(
        payload: Mapping[str, Any],
    ) -> None:
        error = payload.get(
            "error"
        )

        if error in (
            None,
            "",
            {},
            [],
        ):
            return

        if isinstance(
            error,
            Mapping,
        ):
            message = normalize_space(
                error.get(
                    "message"
                )
                or error.get(
                    "error"
                )
                or error.get(
                    "detail"
                )
            )
        else:
            message = normalize_space(
                error
            )

        raise ProviderError(
            "iNaturalist API error"
            + (
                f": {message}"
                if message
                else ""
            )
        )

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
            "0",
            "false",
            "no",
            "off",
        }:
            return "false"

        raise ProviderError(
            f"Invalid iNaturalist boolean value: {value!r}."
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
    def _mapping_or_empty(
        value: Any,
    ) -> dict[str, Any]:
        return (
            dict(
                value
            )
            if isinstance(
                value,
                Mapping,
            )
            else {}
        )

    @staticmethod
    def _optional_int(
        value: Any,
    ) -> int | None:
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

    @staticmethod
    def _optional_float(
        value: Any,
    ) -> float | None:
        if value in (
            None,
            "",
        ):
            return None

        try:
            return float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

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

    @staticmethod
    def _infer_rank(
        scientific_name: str,
    ) -> str:
        words = normalize_space(
            scientific_name
        ).split()

        if len(
            words
        ) == 2:
            return "species"

        if len(
            words
        ) >= 3:
            return "subspecies"

        return "unknown"
