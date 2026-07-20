#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/iucn.py

Composite IUCN provider wrapper.

This provider coordinates multiple specialized IUCN provider modules, such as:

- providers.iucn_red_list
- providers.iucn_taxonomy
- providers.iucn_assessments
- providers.iucn_species

The wrapper does not implement an IUCN API directly. Each child provider owns
its endpoint, authentication, pagination, and record normalization. This module
loads the configured child providers, preserves independent child cursors and
state files, merges their normalized Taxon records, and returns one Batch to
the Speciedex ingestion pipeline.

Configuration example:

    {
        "name": "iucn",
        "providers": [
            "iucn_red_list",
            "iucn_taxonomy",
            "iucn_assessments"
        ],
        "strategy": "round_robin",
        "children_per_fetch": 1,
        "child_definitions": {
            "iucn_red_list": {
                "enabled": true,
                "api_url": "https://api.example.invalid"
            }
        }
    }

Supported strategies:

- round_robin:
    Run a bounded number of non-exhausted child providers per wrapper fetch.

- all:
    Run every non-exhausted child provider per wrapper fetch.

Child modules must remain inside the providers package, must begin with
``iucn_``, and must expose a concrete ``Provider(BaseProvider)`` class.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .common import (
    BaseProvider,
    Batch,
    HTTPClient,
    ProviderError,
    Taxon,
    normalize_space,
    read_json,
    safe_int,
    write_json,
)


IUCN_WRAPPER_SCHEMA_VERSION = 1

DEFAULT_CHILD_PROVIDERS = (
    "iucn_red_list",
    "iucn_taxonomy",
    "iucn_assessments",
)

VALID_CHILD_NAME = re.compile(
    r"^iucn_[a-z0-9_]+$"
)

STRATEGY_ROUND_ROBIN = "round_robin"
STRATEGY_ALL = "all"

VALID_STRATEGIES = {
    STRATEGY_ROUND_ROBIN,
    STRATEGY_ALL,
}


class Provider(BaseProvider):
    """Composite wrapper for specialized IUCN provider modules."""

    PROVIDER_NAME = "iucn"

    def __init__(
        self,
        definition: dict[str, Any],
        http: HTTPClient,
        state_path: Path,
        batch_size: int,
        repo_root: Path,
    ) -> None:
        super().__init__(
            definition,
            http,
            state_path,
            batch_size,
            repo_root,
        )

        self.strategy = normalize_space(
            self.definition.get(
                "strategy",
                STRATEGY_ROUND_ROBIN,
            )
        ).casefold()

        if self.strategy not in VALID_STRATEGIES:
            raise ProviderError(
                "IUCN wrapper strategy must be one of: "
                + ", ".join(
                    sorted(
                        VALID_STRATEGIES
                    )
                )
            )

        self.child_names = self._configured_child_names()

        if not self.child_names:
            raise ProviderError(
                "IUCN wrapper has no enabled child providers."
            )

        configured_children = safe_int(
            self.definition.get(
                "children_per_fetch",
                1,
            ),
            1,
        )

        self.children_per_fetch = max(
            1,
            min(
                configured_children,
                len(
                    self.child_names
                ),
            ),
        )

        self.child_state_root = (
            self.state_path.parent
            / f"{self.state_path.stem}-children"
        )

        self.child_state_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._repair_wrapper_state()

    @property
    def cursor(self) -> str | None:
        """
        Return serialized wrapper scheduling state.

        Child providers retain their own cursors in child-specific state files.
        The wrapper cursor records only scheduling position and exhaustion.
        """

        payload = {
            "schema_version": IUCN_WRAPPER_SCHEMA_VERSION,
            "next_child_index": safe_int(
                self.state.get(
                    "next_child_index",
                    0,
                ),
                0,
            ),
            "exhausted_children": sorted(
                self._exhausted_children()
            ),
        }

        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def fetch(self) -> Batch:
        """Fetch and merge one bounded composite IUCN batch."""

        active_children = [
            name
            for name in self.child_names
            if name not in self._exhausted_children()
        ]

        if not active_children:
            return Batch(
                records=[],
                next_cursor=None,
                exhausted=True,
                requests=0,
                raw=0,
            )

        selected = self._select_children(
            active_children
        )

        merged_records: list[Taxon] = []
        total_requests = 0
        total_raw = 0
        child_results: dict[str, dict[str, Any]] = {}

        for child_name in selected:
            child = self._load_child_provider(
                child_name
            )

            try:
                batch = child.fetch()

                if not isinstance(
                    batch,
                    Batch,
                ):
                    raise ProviderError(
                        f"IUCN child {child_name!r} "
                        "did not return Batch."
                    )

                validated_records = self._validate_child_records(
                    child_name,
                    batch.records,
                )

                merged_records.extend(
                    validated_records
                )

                total_requests += max(
                    0,
                    int(
                        batch.requests
                    ),
                )

                total_raw += max(
                    0,
                    int(
                        batch.raw
                    ),
                )

                child.save_success(
                    batch
                )

                if batch.exhausted:
                    self._mark_child_exhausted(
                        child_name
                    )
                else:
                    self._mark_child_active(
                        child_name
                    )

                child_results[
                    child_name
                ] = {
                    "records": len(
                        validated_records
                    ),
                    "raw": batch.raw,
                    "requests": batch.requests,
                    "exhausted": batch.exhausted,
                    "next_cursor": batch.next_cursor,
                    "error": None,
                }

            except Exception as error:
                try:
                    child.save_failure(
                        error
                    )
                except Exception:
                    pass

                child_results[
                    child_name
                ] = {
                    "records": 0,
                    "raw": 0,
                    "requests": 0,
                    "exhausted": False,
                    "next_cursor": child.cursor,
                    "error": str(
                        error
                    ),
                }

                self._record_child_failure(
                    child_name,
                    error,
                )

                if not bool(
                    self.definition.get(
                        "continue_on_child_error",
                        False,
                    )
                ):
                    self._persist_wrapper_state(
                        child_results
                    )

                    raise ProviderError(
                        f"IUCN child provider "
                        f"{child_name!r} failed: {error}"
                    ) from error

        merged_records = self._deduplicate_records(
            merged_records
        )

        self._advance_schedule(
            selected
        )

        self._persist_wrapper_state(
            child_results
        )

        exhausted = all(
            name in self._exhausted_children()
            for name in self.child_names
        )

        next_cursor = (
            None
            if exhausted
            else self.cursor
        )

        return Batch(
            records=merged_records,
            next_cursor=next_cursor,
            exhausted=exhausted,
            requests=total_requests,
            raw=total_raw,
        )

    def save_success(
        self,
        batch: Batch,
    ) -> None:
        """
        Save wrapper-level success without overwriting child cursor state.

        BaseProvider.save_success remains suitable for ordinary providers, but
        this wrapper stores its scheduling metadata and aggregate batch data.
        """

        self.state.update(
            {
                "provider": self.name,
                "schema_version": IUCN_WRAPPER_SCHEMA_VERSION,
                "cursor": batch.next_cursor,
                "bootstrap_complete": batch.exhausted,
                "last_error": None,
                "last_batch_records": len(
                    batch.records
                ),
                "last_requests": batch.requests,
                "last_raw_records": batch.raw,
                "children": self._child_state_summary(),
            }
        )

        from .common import now

        self.state[
            "last_success"
        ] = now()

        write_json(
            self.state_path,
            self.state,
        )

    def save_failure(
        self,
        error: Exception,
    ) -> None:
        """Persist wrapper-level failure while retaining child state."""

        from .common import now

        self.state.update(
            {
                "provider": self.name,
                "schema_version": IUCN_WRAPPER_SCHEMA_VERSION,
                "last_attempt": now(),
                "last_error": str(
                    error
                ),
                "children": self._child_state_summary(),
            }
        )

        write_json(
            self.state_path,
            self.state,
        )

    def _configured_child_names(
        self,
    ) -> list[str]:
        """Return validated, ordered, enabled child provider names."""

        configured = self.definition.get(
            "providers",
            self.definition.get(
                "children",
                list(
                    DEFAULT_CHILD_PROVIDERS
                ),
            ),
        )

        if isinstance(
            configured,
            str,
        ):
            values = [
                item
                for item in re.split(
                    r"[,\s]+",
                    configured,
                )
                if item
            ]
        elif isinstance(
            configured,
            list,
        ):
            values = configured
        else:
            raise ProviderError(
                "IUCN providers must be a list or "
                "comma-separated string."
            )

        child_definitions = self._child_definitions()
        result: list[str] = []
        seen: set[str] = set()

        for value in values:
            name = normalize_space(
                value
            ).casefold()

            if not VALID_CHILD_NAME.fullmatch(
                name
            ):
                raise ProviderError(
                    f"Invalid IUCN child provider name: "
                    f"{name!r}."
                )

            if name == self.PROVIDER_NAME:
                raise ProviderError(
                    "The IUCN wrapper cannot load itself "
                    "as a child provider."
                )

            child_definition = child_definitions.get(
                name,
                {},
            )

            if (
                isinstance(
                    child_definition,
                    Mapping,
                )
                and child_definition.get(
                    "enabled"
                ) is False
            ):
                continue

            if name not in seen:
                seen.add(
                    name
                )
                result.append(
                    name
                )

        return result

    def _child_definitions(
        self,
    ) -> dict[str, dict[str, Any]]:
        """Return normalized per-child definition overrides."""

        value = self.definition.get(
            "child_definitions",
            {},
        )

        if not isinstance(
            value,
            Mapping,
        ):
            raise ProviderError(
                "IUCN child_definitions must be a mapping."
            )

        result: dict[str, dict[str, Any]] = {}

        for key, child_value in value.items():
            name = normalize_space(
                key
            ).casefold()

            if not VALID_CHILD_NAME.fullmatch(
                name
            ):
                raise ProviderError(
                    f"Invalid IUCN child definition key: "
                    f"{name!r}."
                )

            if not isinstance(
                child_value,
                Mapping,
            ):
                raise ProviderError(
                    f"IUCN child definition {name!r} "
                    "must be a mapping."
                )

            result[
                name
            ] = dict(
                child_value
            )

        return result

    def _child_definition(
        self,
        child_name: str,
    ) -> dict[str, Any]:
        """
        Build one child definition.

        Wrapper-only fields are not inherited. Authentication and generic IUCN
        fields may be inherited unless explicitly overridden.
        """

        wrapper_only = {
            "name",
            "module",
            "providers",
            "children",
            "strategy",
            "children_per_fetch",
            "child_definitions",
            "continue_on_child_error",
        }

        definition = {
            key: value
            for key, value in self.definition.items()
            if key not in wrapper_only
        }

        definition.update(
            self._child_definitions().get(
                child_name,
                {},
            )
        )

        definition[
            "name"
        ] = child_name

        return definition

    def _load_child_provider(
        self,
        child_name: str,
    ) -> BaseProvider:
        """Import, validate, and construct one IUCN child provider."""

        module_name = (
            f"providers.{child_name}"
        )

        try:
            module = importlib.import_module(
                module_name
            )

        except ModuleNotFoundError as error:
            if error.name == module_name:
                raise ProviderError(
                    "IUCN child provider module not found: "
                    f"providers/{child_name}.py"
                ) from error

            raise ProviderError(
                f"IUCN child {child_name!r} has a "
                f"missing dependency: {error.name or error}"
            ) from error

        except ImportError as error:
            raise ProviderError(
                f"Unable to import IUCN child "
                f"{child_name!r}: {error}"
            ) from error

        provider_class = getattr(
            module,
            "Provider",
            None,
        )

        if (
            provider_class is None
            or not inspect.isclass(
                provider_class
            )
            or provider_class is BaseProvider
            or not issubclass(
                provider_class,
                BaseProvider,
            )
        ):
            raise ProviderError(
                f"IUCN child {child_name!r} does not "
                "expose a concrete Provider(BaseProvider)."
            )

        child_provider_name = normalize_space(
            getattr(
                provider_class,
                "PROVIDER_NAME",
                "",
            )
        ).casefold()

        if (
            child_provider_name
            and child_provider_name
            != child_name
        ):
            raise ProviderError(
                "IUCN child provider name mismatch: "
                f"expected={child_name!r}, "
                f"actual={child_provider_name!r}."
            )

        child_state_path = (
            self.child_state_root
            / f"{child_name}.json"
        )

        try:
            provider = provider_class(
                self._child_definition(
                    child_name
                ),
                self.http,
                child_state_path,
                self.batch_size,
                self.repo_root,
            )
        except Exception as error:
            raise ProviderError(
                f"Unable to initialize IUCN child "
                f"{child_name!r}: {error}"
            ) from error

        return provider

    def _select_children(
        self,
        active_children: list[str],
    ) -> list[str]:
        """Select child providers according to the configured strategy."""

        if self.strategy == STRATEGY_ALL:
            return list(
                active_children
            )

        start_index = safe_int(
            self.state.get(
                "next_child_index",
                0,
            ),
            0,
        )

        ordered = [
            self.child_names[
                (
                    start_index
                    + offset
                )
                % len(
                    self.child_names
                )
            ]
            for offset in range(
                len(
                    self.child_names
                )
            )
        ]

        selected = [
            name
            for name in ordered
            if name in active_children
        ]

        return selected[
            :self.children_per_fetch
        ]

    def _advance_schedule(
        self,
        selected: list[str],
    ) -> None:
        """Advance the round-robin scheduling pointer."""

        if (
            self.strategy
            != STRATEGY_ROUND_ROBIN
            or not selected
        ):
            return

        last_name = selected[-1]
        last_index = self.child_names.index(
            last_name
        )

        self.state[
            "next_child_index"
        ] = (
            last_index + 1
        ) % len(
            self.child_names
        )

    def _validate_child_records(
        self,
        child_name: str,
        records: Any,
    ) -> list[Taxon]:
        """Validate and annotate child Taxon records."""

        if not isinstance(
            records,
            list,
        ):
            raise ProviderError(
                f"IUCN child {child_name!r} "
                "returned a non-list records value."
            )

        result: list[Taxon] = []

        for index, record in enumerate(
            records
        ):
            if not isinstance(
                record,
                Taxon,
            ):
                raise ProviderError(
                    f"IUCN child {child_name!r} record "
                    f"{index} is not Taxon."
                )

            if normalize_space(
                record.provider
            ).casefold() != child_name:
                raise ProviderError(
                    f"IUCN child {child_name!r} emitted "
                    f"record provider {record.provider!r}."
                )

            record.extra.setdefault(
                "iucn_wrapper",
                {}
            )

            wrapper_metadata = record.extra[
                "iucn_wrapper"
            ]

            if isinstance(
                wrapper_metadata,
                dict,
            ):
                wrapper_metadata.update(
                    {
                        "wrapper_provider": self.name,
                        "child_provider": child_name,
                    }
                )

            result.append(
                record
            )

        return result

    @staticmethod
    def _deduplicate_records(
        records: list[Taxon],
    ) -> list[Taxon]:
        """
        Suppress exact duplicate child outputs.

        Records from different IUCN child providers are intentionally retained
        as separate assertions. Only repeated records from the same provider
        and provider_id are suppressed.
        """

        result: list[Taxon] = []
        seen: set[tuple[str, str]] = set()

        for record in records:
            key = (
                normalize_space(
                    record.provider
                ).casefold(),
                normalize_space(
                    record.provider_id
                ),
            )

            if key in seen:
                continue

            seen.add(
                key
            )
            result.append(
                record
            )

        return result

    def _repair_wrapper_state(
        self,
    ) -> None:
        """Repair wrapper-specific state defaults."""

        if not isinstance(
            self.state,
            dict,
        ):
            self.state = {}

        self.state.setdefault(
            "provider",
            self.name,
        )

        self.state.setdefault(
            "schema_version",
            IUCN_WRAPPER_SCHEMA_VERSION,
        )

        self.state.setdefault(
            "next_child_index",
            0,
        )

        if not isinstance(
            self.state.get(
                "exhausted_children"
            ),
            list,
        ):
            self.state[
                "exhausted_children"
            ] = []

        if not isinstance(
            self.state.get(
                "child_failures"
            ),
            dict,
        ):
            self.state[
                "child_failures"
            ] = {}

    def _exhausted_children(
        self,
    ) -> set[str]:
        return {
            normalize_space(
                value
            ).casefold()
            for value in self.state.get(
                "exhausted_children",
                [],
            )
            if normalize_space(
                value
            )
        }

    def _mark_child_exhausted(
        self,
        child_name: str,
    ) -> None:
        exhausted = self._exhausted_children()
        exhausted.add(
            child_name
        )

        self.state[
            "exhausted_children"
        ] = sorted(
            exhausted
        )

    def _mark_child_active(
        self,
        child_name: str,
    ) -> None:
        exhausted = self._exhausted_children()
        exhausted.discard(
            child_name
        )

        self.state[
            "exhausted_children"
        ] = sorted(
            exhausted
        )

    def _record_child_failure(
        self,
        child_name: str,
        error: Exception,
    ) -> None:
        from .common import now

        failures = self.state.setdefault(
            "child_failures",
            {},
        )

        if not isinstance(
            failures,
            dict,
        ):
            failures = {}
            self.state[
                "child_failures"
            ] = failures

        previous = failures.get(
            child_name,
            {},
        )

        if not isinstance(
            previous,
            dict,
        ):
            previous = {}

        previous.update(
            {
                "count": safe_int(
                    previous.get(
                        "count",
                        0,
                    ),
                    0,
                ) + 1,
                "last_error": str(
                    error
                ),
                "last_failure": now(),
            }
        )

        failures[
            child_name
        ] = previous

    def _persist_wrapper_state(
        self,
        child_results: Mapping[str, Any],
    ) -> None:
        from .common import now

        self.state.update(
            {
                "provider": self.name,
                "schema_version": IUCN_WRAPPER_SCHEMA_VERSION,
                "cursor": self.cursor,
                "last_attempt": now(),
                "last_child_results": dict(
                    child_results
                ),
                "children": self._child_state_summary(),
            }
        )

        write_json(
            self.state_path,
            self.state,
        )

    def _child_state_summary(
        self,
    ) -> dict[str, Any]:
        """Return non-destructive summaries of child state files."""

        summaries: dict[str, Any] = {}

        for child_name in self.child_names:
            path = (
                self.child_state_root
                / f"{child_name}.json"
            )

            state = read_json(
                path,
                {},
            )

            if not isinstance(
                state,
                dict,
            ):
                state = {}

            summaries[
                child_name
            ] = {
                "state_file": path.as_posix(),
                "cursor": state.get(
                    "cursor"
                ),
                "bootstrap_complete": bool(
                    state.get(
                        "bootstrap_complete",
                        False,
                    )
                ),
                "last_success": state.get(
                    "last_success"
                ),
                "last_error": state.get(
                    "last_error"
                ),
                "last_batch_records": state.get(
                    "last_batch_records",
                    0,
                ),
                "last_requests": state.get(
                    "last_requests",
                    0,
                ),
                "last_raw_records": state.get(
                    "last_raw_records",
                    0,
                ),
            }

        return summaries
