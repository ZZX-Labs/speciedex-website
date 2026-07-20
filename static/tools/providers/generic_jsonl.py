#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/generic_jsonl.py

Declarative JSON/JSONL/NDJSON provider adapter.

This module is the universal file-backed adapter for taxonomy and biodiversity
datasets whose records can be mapped into the Speciedex Taxon contract through
providers.json without writing a dedicated Python provider.

Supported capabilities:

- JSONL and NDJSON streams.
- JSON arrays and nested record arrays.
- Dot-path and array-wildcard lookups.
- Multiple fallback paths per target field.
- Defaults and computed string templates.
- Configurable transforms and value maps.
- Include/exclude filters.
- Synonym, lineage, identifier, media, and reference extraction.
- Arbitrary extra-field mappings.
- Resumable cursors.
- Per-record validation and rejection counters.
- Complete raw-record preservation.

Example providers.json entry:

    {
      "name": "example",
      "adapter": "generic_jsonl",
      "path": "static/data/providers/example/taxa.jsonl",
      "mapping": {
        "provider_id": ["taxonID", "id"],
        "scientific_name": ["scientificName", "name"],
        "canonical_name": ["canonicalName"],
        "rank": ["taxonRank"],
        "status": ["taxonomicStatus"],
        "kingdom": ["classification.kingdom", "kingdom"],
        "phylum": ["classification.phylum", "phylum"],
        "class_name": ["classification.class", "class"],
        "order": ["classification.order", "order"],
        "family": ["classification.family", "family"],
        "genus": ["classification.genus", "genus"]
      },
      "defaults": {
        "status": "accepted"
      },
      "computed": {
        "source_url": "https://example.org/taxa/{provider_id}"
      },
      "transforms": {
        "scientific_name": ["normalize_space"],
        "rank": ["strip", "lower", "replace: :_"]
      },
      "filters": [
        {"field": "scientific_name", "not_empty": true}
      ],
      "extra": {
        "common_names": "vernacularNames[*]",
        "traits": "traits",
        "references": "references[*]"
      }
    }

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .common import (
    BaseProvider,
    Batch,
    ProviderError,
    Taxon,
    normalize_space,
    now,
    safe_int,
)


_MISSING = object()
_PATH_TOKEN = re.compile(r"([^[.\]]+)|\[(\*|\d+)\]")
_TEMPLATE_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _deduplicate(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()

    for value in values:
        try:
            key = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except TypeError:
            key = repr(value)

        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def _path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []

    for match in _PATH_TOKEN.finditer(path):
        name, index = match.groups()

        if name is not None:
            tokens.append(name)
        elif index == "*":
            tokens.append("*")
        else:
            tokens.append(int(index))

    return tokens


def extract_path(record: Any, path: str, default: Any = _MISSING) -> Any:
    """
    Extract a value using dot notation and ``[*]`` array wildcards.

    Examples:
        classification.family
        references[*].citation
        items[0].name
    """

    if not path:
        return record

    current: list[Any] = [record]
    wildcard_used = False

    for token in _path_tokens(path):
        next_values: list[Any] = []

        for value in current:
            if token == "*":
                wildcard_used = True

                if isinstance(value, Mapping):
                    next_values.extend(value.values())
                elif isinstance(value, Sequence) and not isinstance(
                    value,
                    (str, bytes, bytearray),
                ):
                    next_values.extend(value)

                continue

            if isinstance(token, int):
                if isinstance(value, Sequence) and not isinstance(
                    value,
                    (str, bytes, bytearray),
                ):
                    if 0 <= token < len(value):
                        next_values.append(value[token])

                continue

            if isinstance(value, Mapping) and token in value:
                next_values.append(value[token])

        if not next_values:
            return default

        current = next_values

    if wildcard_used or len(current) > 1:
        return current

    return current[0] if current else default


def first_path(
    record: Mapping[str, Any],
    paths: str | Sequence[str] | None,
    default: Any = None,
) -> Any:
    """Return the first non-empty value found among candidate paths."""

    if paths is None:
        return default

    candidates = [paths] if isinstance(paths, str) else list(paths)

    for path in candidates:
        value = extract_path(record, str(path), _MISSING)

        if value is not _MISSING and not _is_empty(value):
            return value

    return default


class GenericJSONLProvider(BaseProvider):
    """Configurable file-backed JSON/JSONL provider."""

    PROVIDER_NAME = "generic_jsonl"

    TAXON_FIELDS = {
        "provider_id",
        "scientific_name",
        "canonical_name",
        "rank",
        "status",
        "authorship",
        "kingdom",
        "phylum",
        "class_name",
        "order",
        "family",
        "genus",
        "accepted_provider_id",
        "source_url",
        "source_modified",
        "retrieved_at",
        "synonyms",
    }

    REQUIRED_FIELDS = (
        "provider_id",
        "scientific_name",
    )

    def fetch(self) -> Batch:
        """Read and normalize one resumable batch."""

        source_path = self._source_path()
        cursor = self._decode_cursor(self.cursor)
        page_size = self._page_size()

        records: list[Taxon] = []
        raw_count = 0
        rejected_count = 0
        next_offset = cursor["offset"]
        exhausted = True
        retrieved_at = now()

        for source_index, raw in self._iter_records(
            source_path,
            start_offset=cursor["offset"],
        ):
            if raw_count >= page_size:
                exhausted = False
                break

            next_offset = source_index + 1
            raw_count += 1

            if not isinstance(raw, Mapping):
                rejected_count += 1
                continue

            try:
                record = self.normalize_record(
                    dict(raw),
                    source_path=source_path,
                    retrieved_at=retrieved_at,
                )
            except Exception as error:
                if bool(self.definition.get("strict", False)):
                    raise ProviderError(
                        f"{self.name}: failed to normalize record "
                        f"at offset {source_index}: {error}"
                    ) from error

                rejected_count += 1
                continue

            if record is None:
                rejected_count += 1
                continue

            records.append(record)

        self._last_rejected = rejected_count

        return Batch(
            records=records,
            next_cursor=(
                None
                if exhausted
                else json.dumps(
                    {"offset": next_offset},
                    separators=(",", ":"),
                )
            ),
            exhausted=exhausted,
            requests=0,
            raw=raw_count,
        )

    def normalize_record(
        self,
        raw: dict[str, Any],
        *,
        source_path: Path,
        retrieved_at: str,
    ) -> Taxon | None:
        """Map one arbitrary source record into the shared Taxon contract."""

        mapped = self._map_fields(raw)
        mapped = self._apply_defaults(mapped)
        mapped = self._apply_computed(mapped, raw)
        mapped = self._apply_transforms(mapped)

        if not self._passes_filters(mapped, raw):
            return None

        if not self._validate_required(mapped):
            return None

        synonyms = self._extract_synonyms(raw, mapped)
        lineage = self._extract_lineage(raw, mapped)
        identifiers = self._extract_collection(
            raw,
            self.definition.get("identifiers"),
        )
        media = self._extract_collection(
            raw,
            self.definition.get("media"),
        )
        references = self._extract_collection(
            raw,
            self.definition.get("references"),
        )

        provider_id = normalize_space(mapped.get("provider_id"))
        scientific_name = normalize_space(mapped.get("scientific_name"))
        canonical_name = (
            normalize_space(mapped.get("canonical_name"))
            or scientific_name
        )

        rank = normalize_space(mapped.get("rank")).casefold()
        status = normalize_space(mapped.get("status")).casefold()

        if not rank:
            rank = "unknown"

        if not status:
            status = "unknown"

        accepted_provider_id = normalize_space(
            mapped.get("accepted_provider_id")
        )

        if accepted_provider_id == provider_id:
            accepted_provider_id = ""

        extra = self._map_extra(raw, mapped)
        extra.update(
            {
                "source": normalize_space(
                    self.definition.get(
                        "source_name",
                        self.name,
                    )
                ) or self.name,
                "programme": self.name,
                "reference_only": bool(
                    self.definition.get(
                        "reference_only",
                        True,
                    )
                ),
                "lineage": lineage,
                "identifiers": identifiers,
                "media": media,
                "references": references,
                "bulk_source": source_path.as_posix(),
            }
        )

        if bool(self.definition.get("preserve_raw", True)):
            extra["raw"] = raw

        return Taxon(
            provider=self.name,
            provider_id=provider_id,
            scientific_name=scientific_name,
            canonical_name=canonical_name,
            rank=rank,
            status=status,
            authorship=normalize_space(mapped.get("authorship")),
            kingdom=normalize_space(
                mapped.get("kingdom")
                or lineage.get("kingdom")
            ),
            phylum=normalize_space(
                mapped.get("phylum")
                or lineage.get("phylum")
                or lineage.get("division")
            ),
            class_name=normalize_space(
                mapped.get("class_name")
                or lineage.get("class")
            ),
            order=normalize_space(
                mapped.get("order")
                or lineage.get("order")
            ),
            family=normalize_space(
                mapped.get("family")
                or lineage.get("family")
            ),
            genus=normalize_space(
                mapped.get("genus")
                or lineage.get("genus")
            ),
            accepted_provider_id=accepted_provider_id,
            source_url=normalize_space(mapped.get("source_url")),
            source_modified=normalize_space(
                mapped.get("source_modified")
            ),
            retrieved_at=(
                normalize_space(mapped.get("retrieved_at"))
                or retrieved_at
            ),
            synonyms=synonyms,
            extra=extra,
        )

    def _source_path(self) -> Path:
        configured = normalize_space(
            self.definition.get("path")
            or self.definition.get("file")
            or self.definition.get("source_path")
        )

        if not configured:
            raise ProviderError(
                f"{self.name}: generic JSON provider requires a path."
            )

        path = Path(configured)

        if not path.is_absolute():
            path = self.repo_root / path

        if not path.exists():
            raise ProviderError(
                f"{self.name}: source file not found: {path}"
            )

        if not path.is_file():
            raise ProviderError(
                f"{self.name}: source path is not a file: {path}"
            )

        return path

    def _page_size(self) -> int:
        configured = safe_int(
            self.definition.get(
                "page_size",
                self.batch_size,
            ),
            self.batch_size,
        )

        return max(
            1,
            min(configured, self.batch_size),
        )

    def _iter_records(
        self,
        source_path: Path,
        *,
        start_offset: int,
    ) -> Iterable[tuple[int, Any]]:
        suffix = source_path.suffix.casefold()

        if suffix in {".jsonl", ".ndjson"}:
            yield from self._iter_json_lines(
                source_path,
                start_offset=start_offset,
            )
            return

        if suffix == ".json":
            yield from self._iter_json_document(
                source_path,
                start_offset=start_offset,
            )
            return

        file_format = normalize_space(
            self.definition.get("format")
        ).casefold()

        if file_format in {"jsonl", "ndjson"}:
            yield from self._iter_json_lines(
                source_path,
                start_offset=start_offset,
            )
            return

        if file_format == "json":
            yield from self._iter_json_document(
                source_path,
                start_offset=start_offset,
            )
            return

        raise ProviderError(
            f"{self.name}: unsupported source format for {source_path}."
        )

    def _iter_json_lines(
        self,
        source_path: Path,
        *,
        start_offset: int,
    ) -> Iterable[tuple[int, Any]]:
        encoding = normalize_space(
            self.definition.get("encoding")
        ) or "utf-8"

        with source_path.open(
            "r",
            encoding=encoding,
        ) as handle:
            logical_index = 0

            for physical_line, line in enumerate(handle, start=1):
                stripped = line.strip()

                if not stripped:
                    continue

                if stripped.startswith("#") and bool(
                    self.definition.get(
                        "allow_comments",
                        True,
                    )
                ):
                    continue

                if logical_index < start_offset:
                    logical_index += 1
                    continue

                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError as error:
                    if bool(self.definition.get("strict", False)):
                        raise ProviderError(
                            f"{self.name}: invalid JSON at "
                            f"{source_path}:{physical_line}: {error}"
                        ) from error

                    logical_index += 1
                    continue

                yield logical_index, value
                logical_index += 1

    def _iter_json_document(
        self,
        source_path: Path,
        *,
        start_offset: int,
    ) -> Iterable[tuple[int, Any]]:
        encoding = normalize_space(
            self.definition.get("encoding")
        ) or "utf-8"

        with source_path.open(
            "r",
            encoding=encoding,
        ) as handle:
            document = json.load(handle)

        records_path = normalize_space(
            self.definition.get("records_path")
        )

        if records_path:
            document = extract_path(document, records_path, _MISSING)

            if document is _MISSING:
                raise ProviderError(
                    f"{self.name}: records_path {records_path!r} "
                    "was not found."
                )

        if isinstance(document, Mapping):
            record_key = normalize_space(
                self.definition.get("record_key")
            )

            if record_key:
                document = document.get(record_key, [])

        if not isinstance(document, list):
            if bool(self.definition.get("single_record", False)):
                document = [document]
            else:
                raise ProviderError(
                    f"{self.name}: JSON source must resolve to an array."
                )

        for index in range(start_offset, len(document)):
            yield index, document[index]

    def _map_fields(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        mapping = self.definition.get("mapping", {})

        if not isinstance(mapping, Mapping):
            raise ProviderError(
                f"{self.name}: mapping must be an object."
            )

        mapped: dict[str, Any] = {}

        for field in self.TAXON_FIELDS:
            specification = mapping.get(field)

            if specification is None:
                specification = self._default_paths(field)

            value = self._resolve_specification(
                raw,
                specification,
            )

            if value is not _MISSING:
                mapped[field] = value

        return mapped

    @staticmethod
    def _default_paths(field: str) -> list[str]:
        defaults = {
            "provider_id": [
                "provider_id",
                "providerId",
                "taxonID",
                "taxonId",
                "taxon_id",
                "id",
            ],
            "scientific_name": [
                "scientific_name",
                "scientificName",
                "name",
            ],
            "canonical_name": [
                "canonical_name",
                "canonicalName",
            ],
            "rank": [
                "rank",
                "taxonRank",
                "taxon_rank",
            ],
            "status": [
                "status",
                "taxonomicStatus",
                "taxonomic_status",
            ],
            "authorship": [
                "authorship",
                "scientificNameAuthorship",
                "scientific_name_authorship",
                "authority",
            ],
            "kingdom": ["kingdom"],
            "phylum": ["phylum", "division"],
            "class_name": ["class_name", "class"],
            "order": ["order"],
            "family": ["family"],
            "genus": ["genus"],
            "accepted_provider_id": [
                "accepted_provider_id",
                "acceptedProviderId",
                "acceptedNameUsageID",
                "acceptedTaxonID",
            ],
            "source_url": [
                "source_url",
                "sourceUrl",
                "references",
            ],
            "source_modified": [
                "source_modified",
                "sourceModified",
                "modified",
                "lastModified",
            ],
            "retrieved_at": [
                "retrieved_at",
                "retrievedAt",
            ],
            "synonyms": [
                "synonyms",
                "synonym",
            ],
        }

        return defaults.get(field, [field])

    def _resolve_specification(
        self,
        raw: Mapping[str, Any],
        specification: Any,
    ) -> Any:
        if specification is None:
            return _MISSING

        if isinstance(specification, str):
            return extract_path(raw, specification, _MISSING)

        if isinstance(specification, list):
            for candidate in specification:
                value = self._resolve_specification(raw, candidate)

                if value is not _MISSING and not _is_empty(value):
                    return value

            return _MISSING

        if isinstance(specification, Mapping):
            if "literal" in specification:
                return specification["literal"]

            paths = (
                specification.get("paths")
                or specification.get("path")
            )

            value = self._resolve_specification(raw, paths)

            if value is _MISSING or _is_empty(value):
                if "default" in specification:
                    value = specification["default"]

            if value is not _MISSING:
                transforms = specification.get("transforms", [])
                value = self._transform_value(
                    value,
                    transforms,
                )

            return value

        return specification

    def _apply_defaults(
        self,
        mapped: dict[str, Any],
    ) -> dict[str, Any]:
        defaults = (
            self.definition.get("defaults")
            or self.definition.get("default")
            or {}
        )

        if not isinstance(defaults, Mapping):
            return mapped

        for field, value in defaults.items():
            if field not in mapped or _is_empty(mapped[field]):
                mapped[str(field)] = value

        return mapped

    def _apply_computed(
        self,
        mapped: dict[str, Any],
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        computed = self.definition.get("computed", {})

        if not isinstance(computed, Mapping):
            return mapped

        context = {
            **raw,
            **mapped,
        }

        for field, template in computed.items():
            if isinstance(template, str):
                mapped[str(field)] = self._render_template(
                    template,
                    context,
                )
            elif isinstance(template, Mapping):
                text = normalize_space(template.get("template"))

                if text:
                    mapped[str(field)] = self._render_template(
                        text,
                        context,
                    )

        return mapped

    @staticmethod
    def _render_template(
        template: str,
        context: Mapping[str, Any],
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = extract_path(context, key, "")

            if isinstance(value, (dict, list)):
                return json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                )

            return str(value or "")

        return _TEMPLATE_TOKEN.sub(replace, template)

    def _apply_transforms(
        self,
        mapped: dict[str, Any],
    ) -> dict[str, Any]:
        transforms = self.definition.get("transforms", {})

        if not isinstance(transforms, Mapping):
            return mapped

        for field, operations in transforms.items():
            if field not in mapped:
                continue

            mapped[str(field)] = self._transform_value(
                mapped[str(field)],
                operations,
            )

        return mapped

    def _transform_value(
        self,
        value: Any,
        operations: Any,
    ) -> Any:
        for operation in _coerce_list(operations):
            value = self._apply_transform(value, operation)

        return value

    def _apply_transform(
        self,
        value: Any,
        operation: Any,
    ) -> Any:
        if isinstance(operation, Mapping):
            name = normalize_space(
                operation.get("name")
                or operation.get("op")
                or operation.get("type")
            ).casefold()

            if name == "map":
                mapping = operation.get("values", {})
                default = operation.get("default", value)
                return self._map_value(value, mapping, default)

            if name == "replace":
                return str(value).replace(
                    str(operation.get("old", "")),
                    str(operation.get("new", "")),
                )

            if name == "join":
                separator = str(operation.get("separator", ", "))
                return separator.join(
                    normalize_space(item)
                    for item in _coerce_list(value)
                    if normalize_space(item)
                )

            if name == "split":
                separator = str(operation.get("separator", ","))
                return [
                    normalize_space(item)
                    for item in str(value).split(separator)
                    if normalize_space(item)
                ]

            if name == "prefix":
                return str(operation.get("value", "")) + str(value)

            if name == "suffix":
                return str(value) + str(operation.get("value", ""))

            if name == "default":
                return (
                    operation.get("value")
                    if _is_empty(value)
                    else value
                )

            return value

        text = normalize_space(operation)
        lower = text.casefold()

        if lower in {"strip", "trim", "normalize_space"}:
            return normalize_space(value)

        if lower == "lower":
            return normalize_space(value).lower()

        if lower == "upper":
            return normalize_space(value).upper()

        if lower == "casefold":
            return normalize_space(value).casefold()

        if lower == "title":
            return normalize_space(value).title()

        if lower in {"int", "integer"}:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        if lower in {"float", "number"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if lower in {"bool", "boolean"}:
            return self._optional_bool(value)

        if lower == "list":
            return _coerce_list(value)

        if lower == "first":
            values = _coerce_list(value)
            return values[0] if values else None

        if lower == "dedupe":
            return _deduplicate(_coerce_list(value))

        if lower.startswith("replace:"):
            payload = text[len("replace:"):]
            old, separator, new = payload.partition(":")

            if separator:
                return str(value).replace(old, new)

            return value

        if lower.startswith("split:"):
            separator = text[len("split:"):]
            return [
                normalize_space(item)
                for item in str(value).split(separator)
                if normalize_space(item)
            ]

        if lower.startswith("join:"):
            separator = text[len("join:"):]
            return separator.join(
                normalize_space(item)
                for item in _coerce_list(value)
                if normalize_space(item)
            )

        if lower.startswith("map:"):
            map_name = text[len("map:"):]
            value_maps = self.definition.get("value_maps", {})
            mapping = (
                value_maps.get(map_name, {})
                if isinstance(value_maps, Mapping)
                else {}
            )
            return self._map_value(value, mapping, value)

        return value

    @staticmethod
    def _map_value(
        value: Any,
        mapping: Any,
        default: Any,
    ) -> Any:
        if not isinstance(mapping, Mapping):
            return default

        if isinstance(value, list):
            return [
                mapping.get(
                    item,
                    mapping.get(str(item), item),
                )
                for item in value
            ]

        return mapping.get(
            value,
            mapping.get(str(value), default),
        )

    def _passes_filters(
        self,
        mapped: Mapping[str, Any],
        raw: Mapping[str, Any],
    ) -> bool:
        filters = self.definition.get("filters", [])

        if not isinstance(filters, list):
            return True

        for rule in filters:
            if not isinstance(rule, Mapping):
                continue

            field = normalize_space(rule.get("field"))
            source = normalize_space(
                rule.get("source")
            ).casefold()

            if source == "raw":
                value = extract_path(raw, field, None)
            else:
                value = mapped.get(field)

                if value is None and "." in field:
                    value = extract_path(raw, field, None)

            if bool(rule.get("not_empty", False)) and _is_empty(value):
                return False

            if bool(rule.get("empty", False)) and not _is_empty(value):
                return False

            if "equals" in rule and value != rule["equals"]:
                return False

            if "not_equals" in rule and value == rule["not_equals"]:
                return False

            if "in" in rule and value not in _coerce_list(rule["in"]):
                return False

            if "not_in" in rule and value in _coerce_list(rule["not_in"]):
                return False

            if "contains" in rule:
                needle = str(rule["contains"])

                if isinstance(value, list):
                    if needle not in [str(item) for item in value]:
                        return False
                elif needle not in str(value or ""):
                    return False

            if "regex" in rule:
                if not re.search(
                    str(rule["regex"]),
                    str(value or ""),
                ):
                    return False

            if "min" in rule:
                try:
                    if float(value) < float(rule["min"]):
                        return False
                except (TypeError, ValueError):
                    return False

            if "max" in rule:
                try:
                    if float(value) > float(rule["max"]):
                        return False
                except (TypeError, ValueError):
                    return False

        return True

    def _validate_required(
        self,
        mapped: Mapping[str, Any],
    ) -> bool:
        required = self.definition.get(
            "required_fields",
            list(self.REQUIRED_FIELDS),
        )

        for field in _coerce_list(required):
            if _is_empty(mapped.get(str(field))):
                return False

        return True

    def _extract_synonyms(
        self,
        raw: Mapping[str, Any],
        mapped: Mapping[str, Any],
    ) -> list[str]:
        specification = (
            self.definition.get("synonyms")
            or self.definition.get("synonym_paths")
            or mapped.get("synonyms")
        )

        values: list[Any] = []

        if isinstance(specification, (str, list, Mapping)):
            if isinstance(specification, Mapping):
                paths = (
                    specification.get("paths")
                    or specification.get("path")
                )
                name_path = normalize_space(
                    specification.get("name_path")
                )
            else:
                paths = specification
                name_path = ""

            extracted = self._resolve_specification(raw, paths)

            if extracted is not _MISSING:
                for item in _coerce_list(extracted):
                    if isinstance(item, Mapping) and name_path:
                        item = extract_path(item, name_path, "")
                    elif isinstance(item, Mapping):
                        item = first_path(
                            item,
                            [
                                "scientific_name",
                                "scientificName",
                                "name",
                                "value",
                            ],
                            "",
                        )

                    values.append(item)

        excluded = {
            normalize_space(mapped.get("scientific_name")).casefold(),
            normalize_space(mapped.get("canonical_name")).casefold(),
        }

        result: list[str] = []
        seen: set[str] = set(excluded)

        for value in values:
            text = normalize_space(value)
            key = text.casefold()

            if not text or key in seen:
                continue

            seen.add(key)
            result.append(text)

        return result

    def _extract_lineage(
        self,
        raw: Mapping[str, Any],
        mapped: Mapping[str, Any],
    ) -> dict[str, str]:
        lineage: dict[str, str] = {
            "kingdom": normalize_space(mapped.get("kingdom")),
            "phylum": normalize_space(mapped.get("phylum")),
            "class": normalize_space(mapped.get("class_name")),
            "order": normalize_space(mapped.get("order")),
            "family": normalize_space(mapped.get("family")),
            "genus": normalize_space(mapped.get("genus")),
        }

        configuration = self.definition.get("lineage")

        if not isinstance(configuration, Mapping):
            return lineage

        for rank, specification in configuration.items():
            if rank in {
                "path",
                "paths",
                "rank_path",
                "name_path",
                "separator",
            }:
                continue

            value = self._resolve_specification(
                raw,
                specification,
            )

            if value is not _MISSING:
                lineage[str(rank)] = normalize_space(value)

        path_specification = (
            configuration.get("paths")
            or configuration.get("path")
        )

        if path_specification:
            extracted = self._resolve_specification(
                raw,
                path_specification,
            )

            rank_path = normalize_space(
                configuration.get("rank_path")
            )
            name_path = normalize_space(
                configuration.get("name_path")
            )

            for item in _coerce_list(extracted):
                if not isinstance(item, Mapping):
                    continue

                rank = normalize_space(
                    extract_path(item, rank_path, "")
                    if rank_path
                    else first_path(
                        item,
                        ["rank", "taxonRank"],
                        "",
                    )
                ).casefold().replace(" ", "_")

                name = normalize_space(
                    extract_path(item, name_path, "")
                    if name_path
                    else first_path(
                        item,
                        ["name", "scientificName"],
                        "",
                    )
                )

                if rank and name and not lineage.get(rank):
                    lineage[rank] = name

        return lineage

    def _extract_collection(
        self,
        raw: Mapping[str, Any],
        configuration: Any,
    ) -> list[Any]:
        if configuration is None:
            return []

        if isinstance(configuration, (str, list)):
            extracted = self._resolve_specification(
                raw,
                configuration,
            )
            return (
                []
                if extracted is _MISSING
                else _deduplicate(_coerce_list(extracted))
            )

        if not isinstance(configuration, Mapping):
            return []

        paths = (
            configuration.get("paths")
            or configuration.get("path")
        )
        extracted = self._resolve_specification(raw, paths)

        if extracted is _MISSING:
            return []

        field_mapping = configuration.get("mapping")

        if not isinstance(field_mapping, Mapping):
            return _deduplicate(_coerce_list(extracted))

        result: list[dict[str, Any]] = []

        for item in _coerce_list(extracted):
            if not isinstance(item, Mapping):
                continue

            normalized: dict[str, Any] = {}

            for field, specification in field_mapping.items():
                value = self._resolve_specification(
                    item,
                    specification,
                )

                if value is not _MISSING:
                    normalized[str(field)] = value

            if normalized:
                if bool(
                    configuration.get(
                        "preserve_raw",
                        False,
                    )
                ):
                    normalized["raw"] = dict(item)

                result.append(normalized)

        return _deduplicate(result)

    def _map_extra(
        self,
        raw: Mapping[str, Any],
        mapped: Mapping[str, Any],
    ) -> dict[str, Any]:
        configuration = self.definition.get("extra", {})

        if not isinstance(configuration, Mapping):
            return {}

        extra: dict[str, Any] = {}

        for field, specification in configuration.items():
            value = self._resolve_specification(
                raw,
                specification,
            )

            if value is _MISSING:
                continue

            extra[str(field)] = value

        if bool(
            self.definition.get(
                "include_mapped_in_extra",
                False,
            )
        ):
            extra["mapped"] = dict(mapped)

        return extra

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
    ) -> dict[str, int]:
        if not cursor:
            return {"offset": 0}

        try:
            parsed = json.loads(cursor)

            if isinstance(parsed, Mapping):
                offset = int(parsed.get("offset", 0))
            else:
                offset = int(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            try:
                offset = int(cursor)
            except (TypeError, ValueError) as error:
                raise ProviderError(
                    f"Invalid generic JSON cursor: {cursor!r}."
                ) from error

        if offset < 0:
            raise ProviderError(
                "Generic JSON cursor must be non-negative."
            )

        return {"offset": offset}

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
            "on",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "n",
            "off",
        }:
            return False

        return None


class Provider(GenericJSONLProvider):
    """Default provider class used when adapter is generic_jsonl."""

    PROVIDER_NAME = "generic_jsonl"
