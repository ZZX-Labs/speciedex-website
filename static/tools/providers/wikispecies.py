#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/wikispecies.py

Wikispecies provider plug-in.

Wikispecies is used as a taxonomic reference and discovery source. It is kept
separate from the general Wikipedia enrichment provider.

The provider requests one MediaWiki API page per run, preserves the complete
page payload in ``Taxon.extra["raw"]``, and emits normalized Taxon objects for
the core validation and reconciliation pipeline.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import quote

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
    """Wikispecies MediaWiki API provider."""

    PROVIDER_NAME = "wikispecies"

    DEFAULT_API_URL = "https://species.wikimedia.org/w/api.php"
    DEFAULT_SITE_URL = "https://species.wikimedia.org/wiki/"

    DEFAULT_PAGE_SIZE = 100
    MAX_PAGE_SIZE = 500

    TAXONOMIC_RANKS = {
        "domain",
        "superkingdom",
        "kingdom",
        "subkingdom",
        "infrakingdom",
        "superphylum",
        "phylum",
        "subphylum",
        "infraphylum",
        "superclass",
        "class",
        "subclass",
        "infraclass",
        "superorder",
        "order",
        "suborder",
        "infraorder",
        "parvorder",
        "superfamily",
        "family",
        "subfamily",
        "tribe",
        "subtribe",
        "genus",
        "subgenus",
        "section",
        "subsection",
        "series",
        "subseries",
        "species",
        "subspecies",
        "variety",
        "subvariety",
        "form",
        "subform",
        "strain",
        "cultivar",
        "pathovar",
        "serovar",
        "biovar",
        "isolate",
        "hybrid",
        "virus",
        "clade",
        "unranked",
    }

    EXCLUDED_TITLE_PREFIXES = {
        "author:",
        "category:",
        "file:",
        "help:",
        "mediawiki:",
        "module:",
        "portal:",
        "special:",
        "talk:",
        "template:",
        "user:",
        "wikispecies:",
    }

    EXCLUDED_PAGE_TITLES = {
        "main page",
        "wikispecies",
    }

    _TAG_PATTERN = re.compile(r"<[^>]+>")
    _COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
    _LINK_PATTERN = re.compile(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]")
    _TEMPLATE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")

    def fetch(self) -> Batch:
        """Fetch and normalize one resumable Wikispecies page batch."""

        api_url = normalize_space(
            self.definition.get(
                "api_url",
                self.DEFAULT_API_URL,
            )
        )

        if not api_url:
            raise ProviderError(
                "Wikispecies api_url is empty."
            )

        if not (
            api_url.startswith("https://")
            or api_url.startswith("http://")
        ):
            raise ProviderError(
                "Wikispecies api_url must use HTTP or HTTPS."
            )

        site_url = normalize_space(
            self.definition.get(
                "site_url",
                self.DEFAULT_SITE_URL,
            )
        )

        if not site_url:
            raise ProviderError(
                "Wikispecies site_url is empty."
            )

        namespace = safe_int(
            self.definition.get(
                "namespace",
                0,
            ),
            0,
        )

        configured_page_size = safe_int(
            self.definition.get(
                "page_size",
                self.DEFAULT_PAGE_SIZE,
            ),
            self.DEFAULT_PAGE_SIZE,
        )

        page_size = max(
            1,
            min(
                configured_page_size,
                self.batch_size,
                self.MAX_PAGE_SIZE,
            ),
        )

        parameters: dict[str, Any] = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "allpages",
            "gapnamespace": namespace,
            "gaplimit": page_size,
            "gapfilterredir": "nonredirects",
            "prop": "|".join(
                (
                    "info",
                    "pageprops",
                    "revisions",
                    "categories",
                    "langlinks",
                    "links",
                    "templates",
                    "images",
                    "extlinks",
                )
            ),
            "inprop": "url|displaytitle",
            "rvlimit": 1,
            "rvslots": "main",
            "rvprop": "ids|timestamp|user|userid|comment|flags|size|sha1|contentmodel|content",
            "cllimit": "max",
            "lllimit": "max",
            "pllimit": "max",
            "tllimit": "max",
            "imlimit": "max",
            "ellimit": "max",
        }

        continuation = self._decode_cursor(
            self.cursor
        )

        protected = set(parameters)

        for key, value in continuation.items():
            if key not in protected:
                parameters[key] = value

        requests_before = self.http.requests
        payload = self.http.get_json(
            api_url,
            parameters,
        )
        request_count = (
            self.http.requests
            - requests_before
        )

        if request_count < 1:
            raise ProviderError(
                "Wikispecies fetch completed without an API request."
            )

        if not isinstance(
            payload,
            Mapping,
        ):
            raise ProviderError(
                "Wikispecies returned a non-object JSON response."
            )

        self._raise_api_error(
            payload
        )
        self._remember_api_warnings(
            payload
        )

        query = payload.get(
            "query",
            {},
        )

        if not isinstance(
            query,
            Mapping,
        ):
            query = {}

        raw_pages = query.get(
            "pages",
            [],
        )

        if isinstance(
            raw_pages,
            Mapping,
        ):
            raw_pages = list(
                raw_pages.values()
            )

        if not isinstance(
            raw_pages,
            list,
        ):
            raise ProviderError(
                "Wikispecies response field query.pages is not a list."
            )

        retrieved_at = now()
        records: list[Taxon] = []

        for raw_page in raw_pages:
            if not isinstance(
                raw_page,
                Mapping,
            ):
                continue

            record = self._normalize_page(
                raw_page=dict(raw_page),
                api_url=api_url,
                site_url=site_url,
                retrieved_at=retrieved_at,
            )

            if record is not None:
                records.append(
                    record
                )

        raw_continuation = payload.get(
            "continue"
        )

        if isinstance(
            raw_continuation,
            Mapping,
        ) and raw_continuation:
            next_cursor = self._encode_cursor(
                raw_continuation
            )
            exhausted = False
        else:
            next_cursor = None
            exhausted = True

        if (
            not exhausted
            and self.cursor
            and next_cursor == self.cursor
        ):
            raise ProviderError(
                "Wikispecies returned an unchanged continuation cursor."
            )

        return Batch(
            records=records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=request_count,
            raw=len(raw_pages),
        )

    def _normalize_page(
        self,
        *,
        raw_page: dict[str, Any],
        api_url: str,
        site_url: str,
        retrieved_at: str,
    ) -> Taxon | None:
        """Normalize one Wikispecies page."""

        page_id = raw_page.get(
            "pageid"
        )

        title = normalize_space(
            raw_page.get("title")
        )

        if (
            page_id in (
                None,
                "",
            )
            or not title
        ):
            return None

        if not self._is_candidate_page(
            raw_page,
            title,
        ):
            return None

        page_properties = raw_page.get(
            "pageprops",
            {},
        )

        if not isinstance(
            page_properties,
            Mapping,
        ):
            page_properties = {}

        revisions = self._list_value(
            raw_page.get("revisions")
        )

        latest_revision = (
            revisions[0]
            if (
                revisions
                and isinstance(
                    revisions[0],
                    Mapping,
                )
            )
            else {}
        )

        slots = latest_revision.get(
            "slots",
            {},
        )

        if not isinstance(
            slots,
            Mapping,
        ):
            slots = {}

        main_slot = slots.get(
            "main",
            {},
        )

        if not isinstance(
            main_slot,
            Mapping,
        ):
            main_slot = {}

        revision_content = self._first_value(
            main_slot,
            "content",
            "*",
        )

        if revision_content is None:
            revision_content = self._first_value(
                latest_revision,
                "content",
                "*",
            )

        content = (
            str(revision_content)
            if revision_content is not None
            else ""
        )

        categories = self._extract_titles(
            raw_page.get("categories")
        )

        canonical_name = self._canonical_name(
            title=title,
            page_properties=page_properties,
        )

        scientific_name = self._strip_markup(
            normalize_space(
                self._first_value(
                    page_properties,
                    "wikibase-title",
                    "displaytitle",
                )
            )
        ) or canonical_name

        rank = self._infer_rank(
            title=title,
            page_properties=page_properties,
            categories=categories,
            content=content,
        )

        taxonomy = self._extract_taxonomy(
            content
        )

        status = self._infer_status(
            categories=categories,
            page_properties=page_properties,
            content=content,
        )

        authorship = self._extract_authorship(
            content
        )

        synonyms = self._extract_synonyms(
            content,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
        )

        full_url = normalize_space(
            raw_page.get("fullurl")
        ) or (
            site_url.rstrip("/")
            + "/"
            + self._encode_wiki_title(
                title
            )
        )

        revision_timestamp = normalize_space(
            latest_revision.get(
                "timestamp"
            )
        )

        return Taxon(
            provider=self.name,
            provider_id=str(page_id),
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank,
            status=status,
            authorship=authorship,
            kingdom=taxonomy.get(
                "kingdom",
                "",
            ),
            phylum=taxonomy.get(
                "phylum",
                "",
            ),
            class_name=taxonomy.get(
                "class",
                "",
            ),
            order=taxonomy.get(
                "order",
                "",
            ),
            family=taxonomy.get(
                "family",
                "",
            ),
            genus=taxonomy.get(
                "genus",
                "",
            ),
            accepted_provider_id="",
            source_url=full_url,
            source_modified=revision_timestamp,
            retrieved_at=retrieved_at,
            synonyms=synonyms,
            extra={
                "source": "Wikispecies",
                "reference_only": True,
                "endpoint": api_url,
                "page_id": page_id,
                "namespace": raw_page.get("ns"),
                "title": title,
                "display_title": normalize_space(
                    raw_page.get(
                        "displaytitle"
                    )
                ),
                "canonical_url": normalize_space(
                    raw_page.get(
                        "canonicalurl"
                    )
                ),
                "full_url": full_url,
                "edit_url": normalize_space(
                    raw_page.get(
                        "editurl"
                    )
                ),
                "content_model": normalize_space(
                    raw_page.get(
                        "contentmodel"
                    )
                ),
                "page_language": normalize_space(
                    raw_page.get(
                        "pagelanguage"
                    )
                ),
                "page_properties": dict(
                    page_properties
                ),
                "categories": categories,
                "language_links": self._extract_language_links(
                    raw_page.get("langlinks")
                ),
                "internal_links": self._extract_titles(
                    raw_page.get("links")
                ),
                "templates": self._extract_titles(
                    raw_page.get("templates")
                ),
                "images": self._extract_titles(
                    raw_page.get("images")
                ),
                "external_links": self._extract_external_links(
                    raw_page.get("extlinks")
                ),
                "latest_revision": dict(
                    latest_revision
                ),
                "revision_id": latest_revision.get(
                    "revid"
                ),
                "parent_revision_id": latest_revision.get(
                    "parentid"
                ),
                "revision_timestamp": revision_timestamp,
                "revision_user": normalize_space(
                    latest_revision.get(
                        "user"
                    )
                ),
                "revision_user_id": latest_revision.get(
                    "userid"
                ),
                "revision_comment": normalize_space(
                    latest_revision.get(
                        "comment"
                    )
                ),
                "revision_size": latest_revision.get(
                    "size"
                ),
                "revision_sha1": latest_revision.get(
                    "sha1"
                ),
                "revision_content": content,
                "inferred_taxonomy": taxonomy,
                "raw": raw_page,
            },
        )

    def _is_candidate_page(
        self,
        page: Mapping[str, Any],
        title: str,
    ) -> bool:
        namespace = safe_int(
            page.get("ns"),
            0,
        )

        configured_namespace = safe_int(
            self.definition.get(
                "namespace",
                0,
            ),
            0,
        )

        if namespace != configured_namespace:
            return False

        normalized = title.casefold()

        if normalized in self.EXCLUDED_PAGE_TITLES:
            return False

        return not any(
            normalized.startswith(
                prefix
            )
            for prefix in self.EXCLUDED_TITLE_PREFIXES
        )

    def _canonical_name(
        self,
        *,
        title: str,
        page_properties: Mapping[str, Any],
    ) -> str:
        for candidate in (
            page_properties.get(
                "wikibase-title"
            ),
            page_properties.get(
                "displaytitle"
            ),
            title,
        ):
            value = self._strip_markup(
                normalize_space(
                    candidate
                )
            )

            if value:
                return value

        return title

    def _infer_rank(
        self,
        *,
        title: str,
        page_properties: Mapping[str, Any],
        categories: list[str],
        content: str,
    ) -> str:
        for candidate in (
            page_properties.get(
                "taxonrank"
            ),
            page_properties.get(
                "taxon_rank"
            ),
            page_properties.get(
                "rank"
            ),
        ):
            rank = self._normalize_rank(
                candidate
            )

            if rank:
                return rank

        for category in categories:
            normalized = normalize_space(
                category
            ).casefold()

            if normalized.startswith(
                "category:"
            ):
                normalized = normalized[
                    len("category:"):
                ].strip()

            for rank in sorted(
                self.TAXONOMIC_RANKS,
                key=len,
                reverse=True,
            ):
                if (
                    normalized == rank
                    or normalized == f"{rank}s"
                    or normalized.endswith(
                        f" {rank}"
                    )
                    or normalized.endswith(
                        f" {rank}s"
                    )
                ):
                    return rank

        rank_value = self._extract_named_field(
            content,
            {
                "rank",
                "taxon rank",
            },
        )

        normalized_rank = self._normalize_rank(
            rank_value
        )

        if normalized_rank:
            return normalized_rank

        words = title.split()

        if len(words) == 2:
            return "species"

        if len(words) == 3:
            return "subspecies"

        return "unranked"

    def _normalize_rank(
        self,
        value: Any,
    ) -> str:
        rank = normalize_space(
            value
        ).casefold().replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        )

        aliases = {
            "regnum": "kingdom",
            "divisio": "phylum",
            "division": "phylum",
            "classis": "class",
            "ordo": "order",
            "familia": "family",
            "tribus": "tribe",
            "varietas": "variety",
            "forma": "form",
            "sub species": "subspecies",
            "sub genus": "subgenus",
            "sub family": "subfamily",
        }

        compact = rank.replace(
            " ",
            "",
        )

        rank = aliases.get(
            rank,
            compact
            if compact in self.TAXONOMIC_RANKS
            else rank,
        )

        return (
            rank
            if rank in self.TAXONOMIC_RANKS
            else ""
        )

    def _infer_status(
        self,
        *,
        categories: list[str],
        page_properties: Mapping[str, Any],
        content: str,
    ) -> str:
        values = [
            *categories,
            *[
                normalize_space(value)
                for value in page_properties.values()
                if isinstance(
                    value,
                    (
                        str,
                        int,
                        float,
                    ),
                )
            ],
            content[:10000],
        ]

        combined = " ".join(
            values
        ).casefold()

        if any(
            marker in combined
            for marker in (
                "taxonomic synonym",
                "synonym of",
                "{{synonym",
                "invalid name",
                "nomen nudum",
                "nomen dubium",
                "junior homonym",
                "senior homonym",
            )
        ):
            return "synonym"

        if any(
            marker in combined
            for marker in (
                "accepted taxon",
                "accepted name",
                "{{taxon",
                "{{species",
            )
        ):
            return "accepted"

        return "reference"

    def _extract_taxonomy(
        self,
        content: str,
    ) -> dict[str, str]:
        aliases = {
            "kingdom": {
                "kingdom",
                "regnum",
            },
            "phylum": {
                "phylum",
                "division",
                "divisio",
            },
            "class": {
                "class",
                "classis",
            },
            "order": {
                "order",
                "ordo",
            },
            "family": {
                "family",
                "familia",
            },
            "genus": {
                "genus",
            },
        }

        return {
            target: self._extract_named_field(
                content,
                names,
            )
            for target, names in aliases.items()
        }

    def _extract_authorship(
        self,
        content: str,
    ) -> str:
        return self._extract_named_field(
            content,
            {
                "authority",
                "authorship",
                "author",
                "taxon authority",
                "binomial authority",
                "trinomial authority",
            },
        )

    def _extract_synonyms(
        self,
        content: str,
        *,
        scientific_name: str,
        canonical_name: str,
    ) -> list[str]:
        synonyms: list[str] = []

        for name in (
            "synonym",
            "synonyms",
            "basionym",
            "protonym",
            "original combination",
            "original name",
        ):
            value = self._extract_named_field(
                content,
                {name},
            )

            if value:
                synonyms.extend(
                    self._split_names(
                        value
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

        for synonym in synonyms:
            normalized = normalize_space(
                synonym
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
    def _extract_named_field(
        cls,
        content: str,
        aliases: set[str],
    ) -> str:
        normalized_aliases = {
            alias.casefold()
            for alias in aliases
        }

        for line in content.splitlines():
            stripped = line.strip()

            if "=" not in stripped:
                continue

            left, right = stripped.split(
                "=",
                1,
            )

            field_name = (
                left.strip()
                .lstrip("|")
                .strip()
                .casefold()
                .replace("_", " ")
            )

            if field_name not in normalized_aliases:
                continue

            value = cls._clean_wikitext_value(
                right
            )

            if value:
                return value

        return ""

    @staticmethod
    def _extract_titles(
        value: Any,
    ) -> list[str]:
        result: list[str] = []

        for item in Provider._list_value(
            value
        ):
            title = normalize_space(
                item.get("title")
                if isinstance(
                    item,
                    Mapping,
                )
                else item
            )

            if title:
                result.append(
                    title
                )

        return result

    @staticmethod
    def _extract_language_links(
        value: Any,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []

        for item in Provider._list_value(
            value
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            entry = {
                "language": normalize_space(
                    item.get("lang")
                ),
                "title": normalize_space(
                    item.get("title")
                    or item.get("*")
                ),
                "url": normalize_space(
                    item.get("url")
                ),
            }

            if any(
                entry.values()
            ):
                result.append(
                    entry
                )

        return result

    @staticmethod
    def _extract_external_links(
        value: Any,
    ) -> list[str]:
        result: list[str] = []

        for item in Provider._list_value(
            value
        ):
            link = normalize_space(
                (
                    item.get("url")
                    or item.get("*")
                )
                if isinstance(
                    item,
                    Mapping,
                )
                else item
            )

            if link:
                result.append(
                    link
                )

        return result

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> dict[str, Any]:
        if not cursor:
            return {}

        try:
            value = json.loads(
                cursor
            )
        except json.JSONDecodeError as error:
            raise ProviderError(
                "Wikispecies cursor is not valid JSON."
            ) from error

        if not isinstance(
            value,
            dict,
        ):
            raise ProviderError(
                "Wikispecies cursor JSON must decode to an object."
            )

        return {
            str(key): item
            for key, item in value.items()
            if item is not None
        }

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
    def _encode_wiki_title(
        title: str,
    ) -> str:
        return quote(
            title.replace(
                " ",
                "_",
            ),
            safe="()_,-.'",
        )

    @classmethod
    def _strip_markup(
        cls,
        value: str,
    ) -> str:
        result = cls._COMMENT_PATTERN.sub(
            "",
            value,
        )
        result = cls._TAG_PATTERN.sub(
            " ",
            result,
        )
        result = result.replace(
            "'''",
            "",
        ).replace(
            "''",
            "",
        ).replace(
            "&nbsp;",
            " ",
        )

        result = cls._LINK_PATTERN.sub(
            lambda match: match.group(1),
            result,
        )

        return normalize_space(
            result
        )

    @classmethod
    def _clean_wikitext_value(
        cls,
        value: Any,
    ) -> str:
        result = normalize_space(
            value
        )

        if not result:
            return ""

        result = cls._COMMENT_PATTERN.sub(
            "",
            result,
        )
        result = result.rstrip(
            "|}"
        ).strip()

        match = cls._TEMPLATE_PATTERN.fullmatch(
            result
        )

        if match is not None:
            parts = [
                normalize_space(
                    part
                )
                for part in match.group(1).split(
                    "|"
                )
            ]

            positional = [
                part
                for part in parts[1:]
                if (
                    part
                    and "=" not in part
                )
            ]

            result = (
                positional[-1]
                if positional
                else (
                    parts[0]
                    if parts
                    else ""
                )
            )

        return cls._strip_markup(
            result
        )

    @staticmethod
    def _split_names(
        value: str,
    ) -> list[str]:
        normalized = (
            value.replace(
                "<br />",
                "\n",
            )
            .replace(
                "<br/>",
                "\n",
            )
            .replace(
                "<br>",
                "\n",
            )
        )

        result: list[str] = []

        for line in normalized.splitlines():
            line = normalize_space(
                line.lstrip(
                    "*#;:"
                )
            )

            if not line:
                continue

            if ";" in line:
                result.extend(
                    normalize_space(
                        item
                    )
                    for item in line.split(
                        ";"
                    )
                    if normalize_space(
                        item
                    )
                )
            else:
                result.append(
                    line
                )

        return result

    @staticmethod
    def _raise_api_error(
        payload: Mapping[str, Any],
    ) -> None:
        error = payload.get(
            "error"
        )

        if not isinstance(
            error,
            Mapping,
        ):
            return

        code = normalize_space(
            error.get("code")
        )
        information = normalize_space(
            error.get("info")
        )

        raise ProviderError(
            "Wikispecies API error"
            + (
                f" {code}"
                if code
                else ""
            )
            + (
                f": {information}"
                if information
                else ""
            )
        )

    def _remember_api_warnings(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        warnings = payload.get(
            "warnings"
        )

        if not isinstance(
            warnings,
            Mapping,
        ):
            self.state.pop(
                "last_api_warnings",
                None,
            )
            return

        messages: list[str] = []

        for module_name, warning in warnings.items():
            if isinstance(
                warning,
                Mapping,
            ):
                message = normalize_space(
                    warning.get("*")
                    or warning.get("warnings")
                    or warning.get("html")
                )
            else:
                message = normalize_space(
                    warning
                )

            if message:
                messages.append(
                    f"{module_name}: {message}"
                )

        if messages:
            self.state[
                "last_api_warnings"
            ] = messages
        else:
            self.state.pop(
                "last_api_warnings",
                None,
            )
