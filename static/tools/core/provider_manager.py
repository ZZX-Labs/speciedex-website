#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/provider_manager.py

Provider registry, availability, loading, execution, and run-summary manager.

This module is responsible for:

- validating provider registry entries,
- checking provider module availability,
- checking required environment variables,
- loading provider plug-ins,
- executing provider fetch operations,
- validating returned Batch and Taxon objects,
- reconciling records against the canonical archive,
- saving provider success or failure state,
- updating scheduler health state,
- recording rejected records,
- producing stable provider-run summaries.

Provider implementations remain responsible only for fetching and normalizing
source records. They must not write directly to the Speciedex archive.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Iterable, Mapping, Sequence

from providers.common import Batch, HTTPClient, Taxon
from providers.loader import load_provider

from .archive import Archive, normalize_key, normalize_space, now
from .reconciler import Reconciler, ReconciliationResult
from .scheduler import Scheduler


PROVIDER_NAME_PATTERN = re.compile(
    r"^[a-z0-9_]+$"
)

RANK_ALIASES = {
    "sp": "species", "sp.": "species", "species": "species",
    "subsp": "subspecies", "subsp.": "subspecies",
    "ssp": "subspecies", "ssp.": "subspecies",
    "gen": "genus", "gen.": "genus", "genus": "genus",
    "fam": "family", "fam.": "family", "family": "family",
    "ord": "order", "ord.": "order", "order": "order",
    "classis": "class", "class": "class",
    "division": "phylum", "phylum": "phylum",
    "regnum": "kingdom", "kingdom": "kingdom",
}

STATUS_ALIASES = {
    "accepted name": "accepted",
    "current": "accepted",
    "valid name": "valid",
    "provisional": "provisionally accepted",
    "provisionally accepted": "provisionally accepted",
}


@dataclass(slots=True)
class ProviderAvailability:
    """Availability result for one provider definition."""

    provider: str
    available: bool
    reason: str = ""
    module_path: str = ""
    missing_environment: list[str] = field(
        default_factory=list
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible availability object."""

        return {
            "provider": self.provider,
            "available": self.available,
            "reason": self.reason,
            "module_path": self.module_path,
            "missing_environment": list(
                self.missing_environment
            ),
        }


@dataclass(slots=True)
class ProviderRunSummary:
    """Stable summary for one provider execution."""

    provider: str
    started_at: str
    completed_at: str = ""
    duration_seconds: float = 0.0
    fetched: int = 0
    raw: int = 0
    created: int = 0
    matched: int = 0
    revised: int = 0
    conflicted: int = 0
    rejected: int = 0
    requests: int = 0
    exhausted: bool = False
    next_cursor: str | None = None
    error: str | None = None

    @property
    def succeeded(
        self,
    ) -> bool:
        """Return whether the provider completed successfully."""

        return self.error is None

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible provider summary."""

        return {
            "provider": self.provider,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(
                self.duration_seconds,
                6,
            ),
            "fetched": self.fetched,
            "raw": self.raw,
            "created": self.created,
            "matched": self.matched,
            "revised": self.revised,
            "conflicted": self.conflicted,
            "rejected": self.rejected,
            "requests": self.requests,
            "exhausted": self.exhausted,
            "next_cursor": self.next_cursor,
            "error": self.error,
        }


@dataclass(slots=True)
class ProviderRegistryValidation:
    """Validation result for a provider registry."""

    valid: bool
    definitions: list[dict[str, Any]]
    errors: list[str]
    warnings: list[str]

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """Return a JSON-compatible validation result."""

        return {
            "valid": self.valid,
            "providers": [
                definition.get(
                    "name",
                    "",
                )
                for definition in self.definitions
            ],
            "errors": list(
                self.errors
            ),
            "warnings": list(
                self.warnings
            ),
        }


class ProviderManager:
    """
    Manage provider registry entries and provider execution.

    The manager coordinates providers with Archive, Reconciler, and Scheduler
    without placing persistence or reconciliation logic inside provider
    modules.
    """

    def __init__(
        self,
        *,
        tools_root: Path,
        repo_root: Path,
        archive: Archive,
        http: HTTPClient,
        batch_size: int,
        reconciler: Reconciler | None = None,
        scheduler: Scheduler | None = None,
        reject_invalid_records: bool = True,
    ) -> None:
        if batch_size < 1:
            raise ValueError(
                "batch_size must be positive."
            )

        self.tools_root = Path(
            tools_root
        )

        self.repo_root = Path(
            repo_root
        )

        self.providers_root = (
            self.tools_root
            / "providers"
        )

        self.archive = archive
        self.http = http
        self.batch_size = int(
            batch_size
        )

        self.reconciler = (
            reconciler
            if reconciler is not None
            else Reconciler()
        )

        self.scheduler = scheduler

        self.reject_invalid_records = bool(
            reject_invalid_records
        )

    def validate_registry(
        self,
        definitions: Any,
    ) -> ProviderRegistryValidation:
        """Validate and normalize provider registry definitions."""

        errors: list[str] = []
        warnings: list[str] = []
        normalized: list[
            dict[str, Any]
        ] = []

        if not isinstance(
            definitions,
            list,
        ):
            return ProviderRegistryValidation(
                valid=False,
                definitions=[],
                errors=[
                    "Provider registry does not contain "
                    "a providers list."
                ],
                warnings=[],
            )

        seen_names: set[str] = set()

        for index, definition in enumerate(
            definitions,
        ):
            location = (
                f"providers[{index}]"
            )

            if not isinstance(
                definition,
                Mapping,
            ):
                errors.append(
                    f"{location} is not an object."
                )
                continue

            item = dict(
                definition
            )

            name = normalize_key(
                item.get(
                    "name"
                )
            )

            if not name:
                errors.append(
                    f"{location} has no provider name."
                )
                continue

            if not PROVIDER_NAME_PATTERN.fullmatch(
                name
            ):
                errors.append(
                    f"{location} has invalid provider "
                    f"name: {name!r}."
                )
                continue

            if name in seen_names:
                errors.append(
                    f"Duplicate provider name: {name}."
                )
                continue

            seen_names.add(
                name
            )

            item["name"] = name

            required_env = item.get(
                "required_env",
                [],
            )

            if required_env is None:
                required_env = []

            if not isinstance(
                required_env,
                list,
            ):
                errors.append(
                    f"{location}.required_env must "
                    "be a list."
                )
                required_env = []

            item["required_env"] = [
                normalize_space(
                    value
                )
                for value in required_env
                if normalize_space(value)
            ]

            if "enabled" not in item:
                item["enabled"] = True

            if not isinstance(
                item["enabled"],
                bool,
            ):
                item["enabled"] = (
                    normalize_key(
                        item["enabled"]
                    )
                    not in {
                        "0",
                        "false",
                        "no",
                        "off",
                        "disabled",
                    }
                )

            module_path = (
                self.providers_root
                / f"{name}.py"
            )

            if not module_path.is_file():
                warnings.append(
                    f"Provider module is missing: "
                    f"{module_path.as_posix()}."
                )

            normalized.append(
                item
            )

        return ProviderRegistryValidation(
            valid=not errors,
            definitions=normalized,
            errors=errors,
            warnings=warnings,
        )

    def provider_availability(
        self,
        definition: Mapping[str, Any],
    ) -> ProviderAvailability:
        """Check whether one provider is currently runnable."""

        name = normalize_key(
            definition.get(
                "name"
            )
        )

        if not name:
            return ProviderAvailability(
                provider="",
                available=False,
                reason=(
                    "provider definition has no name"
                ),
            )

        module_path = (
            self.providers_root
            / f"{name}.py"
        )

        if not self._enabled(
            definition
        ):
            return ProviderAvailability(
                provider=name,
                available=False,
                reason="disabled",
                module_path=(
                    module_path.as_posix()
                ),
            )

        required_env = (
            definition.get(
                "required_env",
                [],
            )
        )

        if not isinstance(
            required_env,
            list,
        ):
            required_env = []

        missing_environment = [
            normalize_space(
                variable
            )
            for variable in required_env
            if (
                normalize_space(variable)
                and not os.getenv(
                    normalize_space(
                        variable
                    )
                )
            )
        ]

        if missing_environment:
            return ProviderAvailability(
                provider=name,
                available=False,
                reason=(
                    "missing environment: "
                    + ", ".join(
                        missing_environment
                    )
                ),
                module_path=(
                    module_path.as_posix()
                ),
                missing_environment=(
                    missing_environment
                ),
            )

        if not module_path.is_file():
            return ProviderAvailability(
                provider=name,
                available=False,
                reason=(
                    f"missing module: "
                    f"{module_path.name}"
                ),
                module_path=(
                    module_path.as_posix()
                ),
            )

        try:
            source = module_path.read_text(
                encoding="utf-8",
            )
        except OSError as error:
            return ProviderAvailability(
                provider=name,
                available=False,
                reason=(
                    "unable to read module: "
                    f"{error}"
                ),
                module_path=(
                    module_path.as_posix()
                ),
            )

        if "class Provider" not in source:
            return ProviderAvailability(
                provider=name,
                available=False,
                reason=(
                    "module does not declare "
                    "`class Provider`"
                ),
                module_path=(
                    module_path.as_posix()
                ),
            )

        return ProviderAvailability(
            provider=name,
            available=True,
            reason="",
            module_path=(
                module_path.as_posix()
            ),
        )

    def partition_definitions(
        self,
        definitions: Sequence[
            Mapping[str, Any]
        ],
        *,
        requested: Iterable[str] | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, str]],
    ]:
        """
        Partition provider definitions into eligible and skipped groups.
        """

        requested_names = {
            normalize_key(
                name
            )
            for name in (
                requested or []
            )
            if normalize_key(name)
        }

        eligible: list[
            dict[str, Any]
        ] = []

        skipped: list[
            dict[str, str]
        ] = []

        for definition in definitions:
            name = normalize_key(
                definition.get(
                    "name"
                )
            )

            if (
                requested_names
                and name not in requested_names
            ):
                continue

            availability = (
                self.provider_availability(
                    definition
                )
            )

            if availability.available:
                eligible.append(
                    dict(definition)
                )
            else:
                skipped.append(
                    {
                        "provider": name,
                        "reason": (
                            availability.reason
                        ),
                    }
                )

        if requested_names:
            known_names = {
                normalize_key(
                    definition.get(
                        "name"
                    )
                )
                for definition in definitions
            }

            for missing_name in sorted(
                requested_names
                - known_names
            ):
                skipped.append(
                    {
                        "provider": missing_name,
                        "reason": (
                            "requested provider is not "
                            "registered"
                        ),
                    }
                )

        return (
            eligible,
            skipped,
        )

    def load(
        self,
        definition: Mapping[str, Any],
    ) -> Any:
        """Load one provider plug-in through providers.loader."""

        name = normalize_key(
            definition.get(
                "name"
            )
        )

        if not name:
            raise ValueError(
                "Provider definition has no name."
            )

        availability = (
            self.provider_availability(
                definition
            )
        )

        if not availability.available:
            raise RuntimeError(
                f"Provider {name} is unavailable: "
                f"{availability.reason}"
            )

        state_path = (
            self.archive.provider_states
            / f"{name}.json"
        )

        return load_provider(
            dict(definition),
            self.http,
            state_path,
            self.batch_size,
            self.repo_root,
        )

    def run(
        self,
        definition: Mapping[str, Any],
    ) -> ProviderRunSummary:
        """
        Execute one provider and apply its returned records to the archive.
        """

        name = normalize_key(
            definition.get(
                "name"
            )
        )

        if not name:
            raise ValueError(
                "Provider definition has no name."
            )

        started_timestamp = now()
        started_monotonic = monotonic()

        summary = ProviderRunSummary(
            provider=name,
            started_at=started_timestamp,
        )

        provider: Any | None = None

        try:
            provider = self.load(
                definition
            )

            batch = provider.fetch()

            self._validate_batch(
                batch,
                provider_name=name,
            )

            summary.fetched = len(
                batch.records
            )

            summary.raw = self._safe_nonnegative_int(
                batch.raw
            )

            summary.requests = (
                self._safe_nonnegative_int(
                    batch.requests
                )
            )

            summary.exhausted = bool(
                batch.exhausted
            )

            summary.next_cursor = (
                None
                if batch.next_cursor is None
                else str(
                    batch.next_cursor
                )
            )

            for record in batch.records:
                outcome = self._process_record(
                    record=record,
                    expected_provider=name,
                )

                if outcome == "created":
                    summary.created += 1
                elif outcome == "matched":
                    summary.matched += 1
                elif outcome == "revised":
                    summary.matched += 1
                    summary.revised += 1
                elif outcome == "conflicted":
                    summary.conflicted += 1
                elif outcome == "rejected":
                    summary.rejected += 1

            provider.save_success(
                batch
            )

            summary.completed_at = now()
            summary.duration_seconds = max(
                0.0,
                monotonic()
                - started_monotonic,
            )

            if self.scheduler is not None:
                self.scheduler.record_success(
                    definition,
                    fetched=summary.fetched,
                    requests=summary.requests,
                    duration_seconds=(
                        summary.duration_seconds
                    ),
                )

            return summary

        except Exception as error:
            summary.error = normalize_space(
                error
            ) or error.__class__.__name__

            summary.completed_at = now()

            summary.duration_seconds = max(
                0.0,
                monotonic()
                - started_monotonic,
            )

            if provider is not None:
                try:
                    provider.save_failure(
                        error
                    )
                except Exception:
                    pass

            if self.scheduler is not None:
                try:
                    self.scheduler.record_failure(
                        definition,
                        error,
                        requests=summary.requests,
                        duration_seconds=(
                            summary.duration_seconds
                        ),
                    )
                except Exception:
                    pass

            return summary

    def run_many(
        self,
        definitions: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[
        ProviderRunSummary
    ]:
        """Execute provider definitions sequentially."""

        return [
            self.run(
                definition
            )
            for definition in definitions
        ]

    def _process_record(
        self,
        *,
        record: Any,
        expected_provider: str,
    ) -> str:
        """
        Validate, reconcile, and apply one returned provider record.

        Returns one of:

            created
            matched
            revised
            conflicted
            rejected
        """

        validation_error = (
            self._record_validation_error(
                record,
                expected_provider=(
                    expected_provider
                ),
            )
        )

        if validation_error:
            if (
                self.reject_invalid_records
                and isinstance(
                    record,
                    Taxon,
                )
            ):
                self.archive.add_rejected(
                    record,
                    validation_error,
                )

            return "rejected"

        assert isinstance(
            record,
            Taxon,
        )

        self._normalize_record(record)

        result = self.reconciler.resolve(
            self.archive,
            record,
        )

        return self._apply_reconciliation(
            record,
            result,
        )

    def _apply_reconciliation(
        self,
        record: Taxon,
        result: ReconciliationResult,
    ) -> str:
        """Apply one reconciliation result to the archive."""

        if result.action == "match":
            identifier = (
                result.identifier
                or ""
            )

            changed = (
                self.archive.attach_assertion(
                    identifier,
                    record,
                )
            )

            return (
                "revised"
                if changed
                else "matched"
            )

        if result.action == "create":
            self.archive.add_primary(
                record
            )

            return "created"

        if result.action == "conflict":
            self.archive.add_conflict(
                record,
                result.candidates,
                result.reason,
            )

            return "conflicted"

        raise RuntimeError(
            "Unsupported reconciliation action: "
            f"{result.action}"
        )

    @staticmethod
    def _validate_batch(
        batch: Any,
        *,
        provider_name: str,
    ) -> None:
        """Validate the object returned by Provider.fetch."""

        if not isinstance(
            batch,
            Batch,
        ):
            raise TypeError(
                f"Provider {provider_name} returned "
                f"{type(batch).__name__}; expected Batch."
            )

        if not isinstance(
            batch.records,
            list,
        ):
            raise TypeError(
                f"Provider {provider_name} returned a "
                "Batch whose records field is not a list."
            )

        if batch.requests is None:
            raise ValueError(
                f"Provider {provider_name} returned a "
                "Batch without a requests value."
            )

        try:
            requests = int(
                batch.requests
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                f"Provider {provider_name} returned an "
                "invalid requests value."
            ) from error

        if requests < 0:
            raise ValueError(
                f"Provider {provider_name} returned a "
                "negative requests value."
            )

        try:
            raw = int(
                batch.raw
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                f"Provider {provider_name} returned an "
                "invalid raw-record count."
            ) from error

        if raw < 0:
            raise ValueError(
                f"Provider {provider_name} returned a "
                "negative raw-record count."
            )

    @staticmethod
    def _record_validation_error(
        record: Any,
        *,
        expected_provider: str,
    ) -> str:
        """Return a record validation error or an empty string."""

        if not isinstance(
            record,
            Taxon,
        ):
            return (
                "provider returned a record that is "
                "not a Taxon object"
            )

        provider = normalize_key(
            record.provider
        )

        if not provider:
            return (
                "record has no provider"
            )

        if provider != expected_provider:
            return (
                "record provider does not match "
                f"loaded provider: {provider!r} != "
                f"{expected_provider!r}"
            )

        if not normalize_space(
            record.provider_id
        ):
            return (
                "record has no provider_id"
            )

        if not normalize_space(
            record.scientific_name
        ):
            return (
                "record has no scientific_name"
            )

        if not normalize_space(
            record.canonical_name
        ):
            return (
                "record has no canonical_name"
            )

        if not normalize_space(
            record.rank
        ):
            return (
                "record has no rank"
            )

        if not normalize_space(
            record.status
        ):
            return (
                "record has no status"
            )

        if not isinstance(
            record.synonyms,
            list,
        ):
            return (
                "record synonyms field is not a list"
            )

        if not isinstance(
            record.extra,
            dict,
        ):
            return (
                "record extra field is not an object"
            )

        return ""

    @staticmethod
    def _normalize_record(record: Taxon) -> None:
        """Normalize identity, rank, status, and lineage fields in place."""
        record.provider = normalize_key(record.provider)
        record.provider_id = normalize_space(record.provider_id)
        record.scientific_name = normalize_space(record.scientific_name)
        record.canonical_name = (
            normalize_space(record.canonical_name)
            or record.scientific_name
        )
        raw_rank = normalize_key(record.rank).replace("_", " ").replace("-", " ")
        raw_rank = " ".join(raw_rank.split())
        record.rank = RANK_ALIASES.get(
            raw_rank,
            raw_rank.replace(" ", "_") or "unknown",
        )
        raw_status = normalize_key(record.status).replace("_", " ")
        raw_status = " ".join(raw_status.split())
        record.status = STATUS_ALIASES.get(
            raw_status,
            raw_status or "unknown",
        )
        record.authorship = normalize_space(record.authorship)
        record.kingdom = normalize_space(record.kingdom)
        record.phylum = normalize_space(record.phylum)
        record.class_name = normalize_space(record.class_name)
        record.order = normalize_space(record.order)
        record.family = normalize_space(record.family)
        record.genus = normalize_space(record.genus)
        record.synonyms = list(dict.fromkeys(
            normalize_space(value)
            for value in record.synonyms
            if normalize_space(value)
        ))

    @staticmethod
    def _enabled(
        definition: Mapping[str, Any],
    ) -> bool:
        """Return whether a provider definition is enabled."""

        value = definition.get(
            "enabled",
            True,
        )

        if isinstance(
            value,
            bool,
        ):
            return value

        return normalize_key(
            value
        ) not in {
            "0",
            "false",
            "no",
            "off",
            "disabled",
        }

    @staticmethod
    def _safe_nonnegative_int(
        value: Any,
    ) -> int:
        """Convert a value to a nonnegative integer."""

        try:
            parsed = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return 0

        return max(
            0,
            parsed,
        )


def provider_available(
    definition: Mapping[str, Any],
    *,
    tools_root: Path,
) -> tuple[
    bool,
    str,
]:
    """
    Compatibility helper matching the original stat-grabber.py function.
    """

    name = normalize_key(
        definition.get(
            "name"
        )
    )

    if not name:
        return (
            False,
            "provider definition has no name",
        )

    enabled = definition.get(
        "enabled",
        True,
    )

    if isinstance(
        enabled,
        bool,
    ):
        is_enabled = enabled
    else:
        is_enabled = normalize_key(
            enabled
        ) not in {
            "0",
            "false",
            "no",
            "off",
            "disabled",
        }

    if not is_enabled:
        return (
            False,
            "disabled",
        )

    required_env = definition.get(
        "required_env",
        [],
    )

    if not isinstance(
        required_env,
        list,
    ):
        required_env = []

    missing = [
        normalize_space(
            variable
        )
        for variable in required_env
        if (
            normalize_space(variable)
            and not os.getenv(
                normalize_space(
                    variable
                )
            )
        )
    ]

    if missing:
        return (
            False,
            "missing environment: "
            + ", ".join(missing),
        )

    module_path = (
        Path(tools_root)
        / "providers"
        / f"{name}.py"
    )

    if not module_path.is_file():
        return (
            False,
            f"missing module: {module_path.name}",
        )

    return (
        True,
        "",
    )
