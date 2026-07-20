#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/validation.py

Shared validation framework for the Speciedex ingestion engine.

This module owns:

- provider Taxon validation,
- normalized taxon validation,
- provider batch validation,
- canonical archive-record validation,
- source assertion validation,
- lineage validation,
- synonym validation,
- identifier validation,
- cursor validation,
- provider definition validation,
- manifest-record validation,
- statistics validation,
- structured validation reports,
- configurable validation policies,
- strict and permissive validation modes,
- validation error aggregation,
- rejection-record construction,
- compatibility helpers for provider_manager.py and archive.py.

Validation is intentionally separate from normalization. The taxonomy,
authority, and lineage modules normalize values. This module evaluates whether
the resulting values satisfy Speciedex structural and semantic requirements.

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from providers.common import Batch, Taxon

from .authority import validate_authority
from .hashing import (
    assertion_hash,
    conflict_identifier,
    speciedex_id,
    stable_json_hash,
)
from .lineage import (
    Lineage,
    lineage_from_taxon,
)
from .taxonomy import (
    ACCEPTED_STATUSES,
    PRIMARY_RANKS,
    SYNONYM_STATUSES,
    TERMINAL_RANKS,
    UNKNOWN_RANK,
    UNKNOWN_STATUS,
    canonical_name,
    is_accepted_status,
    is_synonym_status,
    normalize_key,
    normalize_rank,
    normalize_status,
    normalize_taxon,
    normalize_taxon_name,
    validate_taxon,
)


VALIDATION_SCHEMA_VERSION = 1

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

VALID_SEVERITIES = {
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SEVERITY_ERROR,
    SEVERITY_CRITICAL,
}

DEFAULT_MAX_PROVIDER_ID_LENGTH = 512
DEFAULT_MAX_NAME_LENGTH = 1_024
DEFAULT_MAX_AUTHORSHIP_LENGTH = 1_024
DEFAULT_MAX_URL_LENGTH = 4_096
DEFAULT_MAX_CURSOR_LENGTH = 16_384
DEFAULT_MAX_SYNONYMS = 100_000
DEFAULT_MAX_BATCH_RECORDS = 100_000
DEFAULT_MAX_EXTRA_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_RAW_BYTES = 64 * 1024 * 1024

SPECIEDEX_ID_PATTERN = re.compile(
    r"^spx:[a-z0-9_]+:[0-9a-f]+$"
)

ASSERTION_ID_PATTERN = re.compile(
    r"^spx-assertion:[a-z0-9_]+:[0-9a-f]+$"
)

CONFLICT_ID_PATTERN = re.compile(
    r"^spx-conflict:[a-z0-9_]+:[0-9a-f]+$"
)

HTTP_URL_PATTERN = re.compile(
    r"^https?://",
    re.IGNORECASE,
)

LIKELY_PLACEHOLDER_PATTERN = re.compile(
    r"""
    ^(?:
        unknown
        |
        unnamed
        |
        unidentified
        |
        not\ available
        |
        n/?a
        |
        none
        |
        null
        |
        taxon
        |
        species
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ValidationError(ValueError):
    """Raised when strict validation fails."""


class ValidationConfigurationError(ValidationError):
    """Raised when validation policy configuration is invalid."""


@dataclass(slots=True)
class ValidationIssue:
    """One validation issue."""

    code: str
    message: str
    severity: str
    field: str = ""
    value: Any = None
    provider: str = ""
    provider_id: str = ""
    context: dict[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        normalized_severity = normalize_key(
            self.severity
        )

        if (
            normalized_severity
            not in VALID_SEVERITIES
        ):
            raise ValidationConfigurationError(
                "Unsupported validation severity: "
                f"{self.severity!r}."
            )

        self.severity = (
            normalized_severity
        )

        self.code = (
            normalize_key(
                self.code
            )
            .replace(" ", "_")
        )

        self.message = normalize_space(
            self.message
        )

        self.field = normalize_space(
            self.field
        )

        self.provider = normalize_key(
            self.provider
        )

        self.provider_id = normalize_space(
            self.provider_id
        )

    @property
    def blocking(self) -> bool:
        """Return whether the issue blocks ingestion."""

        return self.severity in {
            SEVERITY_ERROR,
            SEVERITY_CRITICAL,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible issue."""

        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "blocking": self.blocking,
            "field": self.field,
            "value": self.value,
            "provider": self.provider,
            "provider_id": (
                self.provider_id
            ),
            "context": dict(
                self.context
            ),
        }


@dataclass(slots=True)
class ValidationResult:
    """Validation result for one object."""

    valid: bool
    issues: list[ValidationIssue] = field(
        default_factory=list
    )
    normalized: Any = None
    object_type: str = ""
    identifier: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    @property
    def errors(self) -> list[ValidationIssue]:
        """Return blocking issues."""

        return [
            issue
            for issue in self.issues
            if issue.blocking
        ]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Return warning issues."""

        return [
            issue
            for issue in self.issues
            if issue.severity
            == SEVERITY_WARNING
        ]

    @property
    def information(self) -> list[ValidationIssue]:
        """Return informational issues."""

        return [
            issue
            for issue in self.issues
            if issue.severity
            == SEVERITY_INFO
        ]

    @property
    def critical(self) -> list[ValidationIssue]:
        """Return critical issues."""

        return [
            issue
            for issue in self.issues
            if issue.severity
            == SEVERITY_CRITICAL
        ]

    @property
    def issue_codes(self) -> list[str]:
        """Return unique issue codes."""

        return sorted(
            {
                issue.code
                for issue in self.issues
            }
        )

    def add(
        self,
        issue: ValidationIssue,
    ) -> None:
        """Append one issue and update validity."""

        self.issues.append(issue)

        if issue.blocking:
            self.valid = False

    def extend(
        self,
        issues: Iterable[
            ValidationIssue
        ],
    ) -> None:
        """Append multiple issues."""

        for issue in issues:
            self.add(issue)

    def raise_for_errors(
        self,
        message: str | None = None,
    ) -> None:
        """Raise ValidationError when blocking issues exist."""

        if self.valid:
            return

        detail = "; ".join(
            issue.message
            for issue in self.errors
        )

        raise ValidationError(
            message
            or detail
            or "Validation failed."
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible validation result."""

        return {
            "schema_version": (
                VALIDATION_SCHEMA_VERSION
            ),
            "valid": self.valid,
            "object_type": (
                self.object_type
            ),
            "identifier": self.identifier,
            "issues": [
                issue.to_dict()
                for issue in self.issues
            ],
            "issue_codes": (
                self.issue_codes
            ),
            "error_count": len(
                self.errors
            ),
            "warning_count": len(
                self.warnings
            ),
            "information_count": len(
                self.information
            ),
            "critical_count": len(
                self.critical
            ),
            "metadata": dict(
                self.metadata
            ),
        }


@dataclass(slots=True)
class BatchValidationResult:
    """Validation result for one provider batch."""

    valid: bool
    provider: str
    record_results: list[
        ValidationResult
    ]
    batch_issues: list[
        ValidationIssue
    ]
    accepted_records: list[Taxon]
    rejected_records: list[Taxon]
    duplicate_source_ids: list[str]
    duplicate_assertions: list[str]

    @property
    def accepted(self) -> int:
        return len(
            self.accepted_records
        )

    @property
    def rejected(self) -> int:
        return len(
            self.rejected_records
        )

    @property
    def warnings(self) -> int:
        return sum(
            len(result.warnings)
            for result
            in self.record_results
        ) + sum(
            1
            for issue
            in self.batch_issues
            if issue.severity
            == SEVERITY_WARNING
        )

    @property
    def errors(self) -> int:
        return sum(
            len(result.errors)
            for result
            in self.record_results
        ) + sum(
            1
            for issue
            in self.batch_issues
            if issue.blocking
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible batch result."""

        return {
            "schema_version": (
                VALIDATION_SCHEMA_VERSION
            ),
            "valid": self.valid,
            "provider": self.provider,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "warnings": self.warnings,
            "errors": self.errors,
            "duplicate_source_ids": list(
                self.duplicate_source_ids
            ),
            "duplicate_assertions": list(
                self.duplicate_assertions
            ),
            "batch_issues": [
                issue.to_dict()
                for issue
                in self.batch_issues
            ],
            "records": [
                result.to_dict()
                for result
                in self.record_results
            ],
        }


@dataclass(slots=True)
class ValidationPolicy:
    """Validation behavior and structural limits."""

    strict: bool = False

    normalize_records: bool = True
    infer_missing_rank: bool = True
    canonicalize_names: bool = True

    require_provider: bool = True
    require_provider_id: bool = True
    require_scientific_name: bool = True
    require_canonical_name: bool = True
    require_rank: bool = False
    require_status: bool = False
    require_retrieved_at: bool = False
    require_source_url: bool = False

    require_lineage_consistency: bool = True
    require_species_genus: bool = False
    require_synonym_target: bool = False
    require_valid_http_url: bool = False

    allow_unknown_rank: bool = True
    allow_unknown_status: bool = True
    allow_empty_lineage: bool = True
    allow_placeholder_names: bool = False
    allow_self_accepted_id: bool = False
    allow_duplicate_synonyms: bool = False

    validate_authorship: bool = True
    validate_source_modified: bool = True
    validate_retrieved_at: bool = True
    validate_extra_serializable: bool = True

    reject_on_warning_codes: set[str] = field(
        default_factory=set
    )

    ignored_issue_codes: set[str] = field(
        default_factory=set
    )

    maximum_provider_id_length: int = (
        DEFAULT_MAX_PROVIDER_ID_LENGTH
    )

    maximum_name_length: int = (
        DEFAULT_MAX_NAME_LENGTH
    )

    maximum_authorship_length: int = (
        DEFAULT_MAX_AUTHORSHIP_LENGTH
    )

    maximum_url_length: int = (
        DEFAULT_MAX_URL_LENGTH
    )

    maximum_cursor_length: int = (
        DEFAULT_MAX_CURSOR_LENGTH
    )

    maximum_synonyms: int = (
        DEFAULT_MAX_SYNONYMS
    )

    maximum_batch_records: int = (
        DEFAULT_MAX_BATCH_RECORDS
    )

    maximum_extra_bytes: int = (
        DEFAULT_MAX_EXTRA_BYTES
    )

    maximum_raw_bytes: int = (
        DEFAULT_MAX_RAW_BYTES
    )

    def __post_init__(self) -> None:
        self.reject_on_warning_codes = {
            normalize_key(code)
            .replace(" ", "_")
            for code
            in self.reject_on_warning_codes
            if normalize_key(code)
        }

        self.ignored_issue_codes = {
            normalize_key(code)
            .replace(" ", "_")
            for code
            in self.ignored_issue_codes
            if normalize_key(code)
        }

        integer_fields = (
            "maximum_provider_id_length",
            "maximum_name_length",
            "maximum_authorship_length",
            "maximum_url_length",
            "maximum_cursor_length",
            "maximum_synonyms",
            "maximum_batch_records",
            "maximum_extra_bytes",
            "maximum_raw_bytes",
        )

        for field_name in integer_fields:
            value = int(
                getattr(
                    self,
                    field_name,
                )
            )

            if value < 1:
                raise (
                    ValidationConfigurationError(
                        f"{field_name} must be "
                        "positive."
                    )
                )

            setattr(
                self,
                field_name,
                value,
            )

    @classmethod
    def permissive(
        cls,
    ) -> ValidationPolicy:
        """Return a permissive ingestion policy."""

        return cls(
            strict=False,
            require_rank=False,
            require_status=False,
            require_retrieved_at=False,
            require_source_url=False,
            allow_unknown_rank=True,
            allow_unknown_status=True,
            allow_empty_lineage=True,
            require_species_genus=False,
            require_synonym_target=False,
        )

    @classmethod
    def strict_archive(
        cls,
    ) -> ValidationPolicy:
        """Return a strict archive policy."""

        return cls(
            strict=True,
            require_rank=True,
            require_status=True,
            require_retrieved_at=True,
            require_source_url=True,
            allow_unknown_rank=False,
            allow_unknown_status=False,
            allow_empty_lineage=False,
            require_species_genus=True,
            require_synonym_target=True,
            require_valid_http_url=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible validation policy."""

        return {
            "strict": self.strict,
            "normalize_records": (
                self.normalize_records
            ),
            "infer_missing_rank": (
                self.infer_missing_rank
            ),
            "canonicalize_names": (
                self.canonicalize_names
            ),
            "require_provider": (
                self.require_provider
            ),
            "require_provider_id": (
                self.require_provider_id
            ),
            "require_scientific_name": (
                self.require_scientific_name
            ),
            "require_canonical_name": (
                self.require_canonical_name
            ),
            "require_rank": (
                self.require_rank
            ),
            "require_status": (
                self.require_status
            ),
            "require_retrieved_at": (
                self.require_retrieved_at
            ),
            "require_source_url": (
                self.require_source_url
            ),
            "require_lineage_consistency": (
                self.require_lineage_consistency
            ),
            "require_species_genus": (
                self.require_species_genus
            ),
            "require_synonym_target": (
                self.require_synonym_target
            ),
            "require_valid_http_url": (
                self.require_valid_http_url
            ),
            "allow_unknown_rank": (
                self.allow_unknown_rank
            ),
            "allow_unknown_status": (
                self.allow_unknown_status
            ),
            "allow_empty_lineage": (
                self.allow_empty_lineage
            ),
            "allow_placeholder_names": (
                self.allow_placeholder_names
            ),
            "allow_self_accepted_id": (
                self.allow_self_accepted_id
            ),
            "allow_duplicate_synonyms": (
                self.allow_duplicate_synonyms
            ),
            "validate_authorship": (
                self.validate_authorship
            ),
            "validate_source_modified": (
                self.validate_source_modified
            ),
            "validate_retrieved_at": (
                self.validate_retrieved_at
            ),
            "validate_extra_serializable": (
                self.validate_extra_serializable
            ),
            "reject_on_warning_codes": sorted(
                self.reject_on_warning_codes
            ),
            "ignored_issue_codes": sorted(
                self.ignored_issue_codes
            ),
            "maximum_provider_id_length": (
                self.maximum_provider_id_length
            ),
            "maximum_name_length": (
                self.maximum_name_length
            ),
            "maximum_authorship_length": (
                self.maximum_authorship_length
            ),
            "maximum_url_length": (
                self.maximum_url_length
            ),
            "maximum_cursor_length": (
                self.maximum_cursor_length
            ),
            "maximum_synonyms": (
                self.maximum_synonyms
            ),
            "maximum_batch_records": (
                self.maximum_batch_records
            ),
            "maximum_extra_bytes": (
                self.maximum_extra_bytes
            ),
            "maximum_raw_bytes": (
                self.maximum_raw_bytes
            ),
        }


@dataclass(slots=True)
class ValidationStatistics:
    """Aggregate validation counters."""

    objects_validated: int = 0
    objects_valid: int = 0
    objects_invalid: int = 0
    warnings: int = 0
    errors: int = 0
    critical: int = 0
    by_code: dict[str, int] = field(
        default_factory=dict
    )
    by_provider: dict[str, int] = field(
        default_factory=dict
    )

    def add(
        self,
        result: ValidationResult,
    ) -> None:
        """Accumulate one validation result."""

        self.objects_validated += 1

        if result.valid:
            self.objects_valid += 1
        else:
            self.objects_invalid += 1

        for issue in result.issues:
            self.by_code[
                issue.code
            ] = (
                self.by_code.get(
                    issue.code,
                    0,
                )
                + 1
            )

            if issue.provider:
                self.by_provider[
                    issue.provider
                ] = (
                    self.by_provider.get(
                        issue.provider,
                        0,
                    )
                    + 1
                )

            if (
                issue.severity
                == SEVERITY_WARNING
            ):
                self.warnings += 1

            elif (
                issue.severity
                == SEVERITY_ERROR
            ):
                self.errors += 1

            elif (
                issue.severity
                == SEVERITY_CRITICAL
            ):
                self.critical += 1

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible validation statistics."""

        return {
            "objects_validated": (
                self.objects_validated
            ),
            "objects_valid": (
                self.objects_valid
            ),
            "objects_invalid": (
                self.objects_invalid
            ),
            "warnings": self.warnings,
            "errors": self.errors,
            "critical": self.critical,
            "by_code": dict(
                sorted(
                    self.by_code.items()
                )
            ),
            "by_provider": dict(
                sorted(
                    self.by_provider.items()
                )
            ),
        }


def utc_now() -> str:
    """Return the current UTC timestamp."""

    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_space(
    value: Any,
) -> str:
    """Collapse surrounding and repeated whitespace."""

    return " ".join(
        str(
            value
            if value is not None
            else ""
        ).strip().split()
    )


def safe_int(
    value: Any,
    default: int = 0,
) -> int:
    """Convert a value to an integer."""

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return int(default)


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    """Convert a value to a finite float."""

    try:
        parsed = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return float(default)

    if not math.isfinite(parsed):
        return float(default)

    return parsed


def parse_timestamp(
    value: Any,
) -> datetime | None:
    """Parse an ISO-8601 timestamp into UTC."""

    normalized = normalize_space(
        value
    )

    if not normalized:
        return None

    try:
        parsed = datetime.fromisoformat(
            normalized.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=UTC
        )

    return parsed.astimezone(
        UTC
    )


def json_size_bytes(
    value: Any,
) -> int:
    """Return compact JSON size or -1 when not serializable."""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    except (
        TypeError,
        ValueError,
    ):
        return -1

    return len(payload)


def validate_cursor(
    cursor: Any,
    *,
    maximum_length: int = (
        DEFAULT_MAX_CURSOR_LENGTH
    ),
) -> ValidationResult:
    """Validate a provider cursor."""

    result = ValidationResult(
        valid=True,
        object_type="cursor",
    )

    if cursor is None:
        result.normalized = None
        return result

    if isinstance(
        cursor,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        normalized = str(cursor)

    elif isinstance(
        cursor,
        (
            Mapping,
            list,
            tuple,
        ),
    ):
        try:
            normalized = json.dumps(
                cursor,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (
            TypeError,
            ValueError,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "cursor_not_serializable"
                    ),
                    message=(
                        "Provider cursor is not "
                        "JSON serializable."
                    ),
                    severity=SEVERITY_ERROR,
                    field="cursor",
                    value=repr(cursor),
                )
            )
            return result

    else:
        result.add(
            ValidationIssue(
                code=(
                    "cursor_unsupported_type"
                ),
                message=(
                    "Provider cursor has an "
                    "unsupported type."
                ),
                severity=SEVERITY_ERROR,
                field="cursor",
                value=type(
                    cursor
                ).__name__,
            )
        )
        return result

    if len(normalized) > maximum_length:
        result.add(
            ValidationIssue(
                code="cursor_too_long",
                message=(
                    "Provider cursor exceeds the "
                    "configured length limit."
                ),
                severity=SEVERITY_ERROR,
                field="cursor",
                value=len(normalized),
                context={
                    "maximum_length": (
                        maximum_length
                    ),
                },
            )
        )

    result.normalized = cursor

    return result


def validate_provider_definition(
    definition: Mapping[str, Any],
) -> ValidationResult:
    """Validate one providers.json definition."""

    result = ValidationResult(
        valid=True,
        object_type=(
            "provider_definition"
        ),
    )

    if not isinstance(
        definition,
        Mapping,
    ):
        result.add(
            ValidationIssue(
                code=(
                    "provider_definition_not_object"
                ),
                message=(
                    "Provider definition must be "
                    "a mapping."
                ),
                severity=SEVERITY_CRITICAL,
            )
        )
        return result

    name = normalize_key(
        definition.get(
            "name",
            definition.get(
                "provider",
                "",
            ),
        )
    )

    module = normalize_space(
        definition.get(
            "module",
            "",
        )
    )

    if not name:
        result.add(
            ValidationIssue(
                code="provider_name_missing",
                message=(
                    "Provider definition has no name."
                ),
                severity=SEVERITY_ERROR,
                field="name",
            )
        )

    if not module:
        result.add(
            ValidationIssue(
                code="provider_module_missing",
                message=(
                    "Provider definition has no "
                    "module."
                ),
                severity=SEVERITY_ERROR,
                field="module",
                provider=name,
            )
        )

    enabled = definition.get(
        "enabled",
        True,
    )

    if not isinstance(
        enabled,
        bool,
    ):
        result.add(
            ValidationIssue(
                code="provider_enabled_not_boolean",
                message=(
                    "Provider enabled value must "
                    "be boolean."
                ),
                severity=SEVERITY_WARNING,
                field="enabled",
                value=enabled,
                provider=name,
            )
        )

    batch_size = definition.get(
        "batch_size"
    )

    if batch_size is not None:
        parsed_batch_size = safe_int(
            batch_size,
            -1,
        )

        if parsed_batch_size < 1:
            result.add(
                ValidationIssue(
                    code=(
                        "provider_batch_size_invalid"
                    ),
                    message=(
                        "Provider batch_size must "
                        "be positive."
                    ),
                    severity=SEVERITY_ERROR,
                    field="batch_size",
                    value=batch_size,
                    provider=name,
                )
            )

    endpoint = normalize_space(
        definition.get(
            "endpoint",
            "",
        )
    )

    if (
        endpoint
        and not HTTP_URL_PATTERN.match(
            endpoint
        )
    ):
        result.add(
            ValidationIssue(
                code=(
                    "provider_endpoint_not_http"
                ),
                message=(
                    "Provider endpoint is not an "
                    "HTTP or HTTPS URL."
                ),
                severity=SEVERITY_WARNING,
                field="endpoint",
                value=endpoint,
                provider=name,
            )
        )

    result.identifier = name
    result.normalized = dict(
        definition
    )

    return result


def validate_speciedex_identifier(
    identifier: Any,
) -> ValidationResult:
    """Validate one canonical Speciedex identifier."""

    normalized = normalize_space(
        identifier
    )

    result = ValidationResult(
        valid=True,
        object_type=(
            "speciedex_identifier"
        ),
        identifier=normalized,
        normalized=normalized,
    )

    if not normalized:
        result.add(
            ValidationIssue(
                code="speciedex_id_missing",
                message=(
                    "Speciedex identifier is empty."
                ),
                severity=SEVERITY_ERROR,
                field="speciedex_id",
            )
        )

    elif not SPECIEDEX_ID_PATTERN.fullmatch(
        normalized
    ):
        result.add(
            ValidationIssue(
                code=(
                    "speciedex_id_invalid_format"
                ),
                message=(
                    "Speciedex identifier format "
                    "is invalid."
                ),
                severity=SEVERITY_ERROR,
                field="speciedex_id",
                value=normalized,
            )
        )

    return result


def validate_assertion_identifier(
    identifier: Any,
) -> ValidationResult:
    """Validate one assertion identifier."""

    normalized = normalize_space(
        identifier
    )

    result = ValidationResult(
        valid=True,
        object_type=(
            "assertion_identifier"
        ),
        identifier=normalized,
        normalized=normalized,
    )

    if not normalized:
        result.add(
            ValidationIssue(
                code="assertion_id_missing",
                message=(
                    "Assertion identifier is empty."
                ),
                severity=SEVERITY_ERROR,
                field="assertion_id",
            )
        )

    elif not ASSERTION_ID_PATTERN.fullmatch(
        normalized
    ):
        result.add(
            ValidationIssue(
                code=(
                    "assertion_id_invalid_format"
                ),
                message=(
                    "Assertion identifier format "
                    "is invalid."
                ),
                severity=SEVERITY_ERROR,
                field="assertion_id",
                value=normalized,
            )
        )

    return result


def validate_conflict_identifier(
    identifier: Any,
) -> ValidationResult:
    """Validate one deterministic conflict identifier."""

    normalized = normalize_space(
        identifier
    )

    result = ValidationResult(
        valid=True,
        object_type=(
            "conflict_identifier"
        ),
        identifier=normalized,
        normalized=normalized,
    )

    if not normalized:
        result.add(
            ValidationIssue(
                code="conflict_id_missing",
                message=(
                    "Conflict identifier is empty."
                ),
                severity=SEVERITY_ERROR,
                field="conflict_id",
            )
        )

    elif not CONFLICT_ID_PATTERN.fullmatch(
        normalized
    ):
        result.add(
            ValidationIssue(
                code=(
                    "conflict_id_invalid_format"
                ),
                message=(
                    "Conflict identifier format "
                    "is invalid."
                ),
                severity=SEVERITY_ERROR,
                field="conflict_id",
                value=normalized,
            )
        )

    return result


class TaxonValidator:
    """
    Validate provider Taxon records under a configurable policy.
    """

    def __init__(
        self,
        policy: ValidationPolicy | None = None,
    ) -> None:
        self.policy = (
            policy
            if policy is not None
            else ValidationPolicy.permissive()
        )

        self.statistics = (
            ValidationStatistics()
        )

    def validate(
        self,
        record: Taxon,
    ) -> ValidationResult:
        """Validate and optionally normalize one Taxon."""

        if not isinstance(
            record,
            Taxon,
        ):
            result = ValidationResult(
                valid=False,
                object_type="taxon",
            )

            result.add(
                ValidationIssue(
                    code="taxon_wrong_type",
                    message=(
                        "Record is not a Taxon "
                        "instance."
                    ),
                    severity=SEVERITY_CRITICAL,
                    value=type(
                        record
                    ).__name__,
                )
            )

            self.statistics.add(
                result
            )

            return result

        normalized = (
            normalize_taxon(
                record,
                infer_missing_rank=(
                    self.policy
                    .infer_missing_rank
                ),
                canonicalize_name=(
                    self.policy
                    .canonicalize_names
                ),
            )
            if self.policy.normalize_records
            else record
        )

        result = ValidationResult(
            valid=True,
            object_type="taxon",
            identifier=(
                f"{normalize_key(normalized.provider)}:"
                f"{normalize_space(normalized.provider_id)}"
            ),
            normalized=normalized,
            metadata={
                "validation_policy": (
                    "strict"
                    if self.policy.strict
                    else "permissive"
                ),
            },
        )

        provider = normalize_key(
            normalized.provider
        )

        provider_id = normalize_space(
            normalized.provider_id
        )

        scientific_name = (
            normalize_taxon_name(
                normalized.scientific_name
            )
        )

        canonical = normalize_taxon_name(
            normalized.canonical_name
        )

        rank = normalize_rank(
            normalized.rank
        )

        status = normalize_status(
            normalized.status
        )

        self._required_field_checks(
            result,
            normalized,
        )

        self._length_checks(
            result,
            normalized,
        )

        self._name_checks(
            result,
            normalized,
        )

        self._rank_checks(
            result,
            normalized,
        )

        self._status_checks(
            result,
            normalized,
        )

        self._authorship_checks(
            result,
            normalized,
        )

        self._lineage_checks(
            result,
            normalized,
        )

        self._synonym_checks(
            result,
            normalized,
        )

        self._source_checks(
            result,
            normalized,
        )

        self._timestamp_checks(
            result,
            normalized,
        )

        self._extra_checks(
            result,
            normalized,
        )

        generic_result = validate_taxon(
            normalized,
            require_status=(
                self.policy.require_status
            ),
            require_rank=(
                self.policy.require_rank
            ),
            require_lineage_consistency=(
                self.policy
                .require_lineage_consistency
            ),
        )

        for message in (
            generic_result.errors
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "taxonomy_validation_error"
                    ),
                    message=message,
                    severity=SEVERITY_ERROR,
                    provider=provider,
                    provider_id=provider_id,
                )
            )

        for message in (
            generic_result.warnings
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "taxonomy_validation_warning"
                    ),
                    message=message,
                    severity=SEVERITY_WARNING,
                    provider=provider,
                    provider_id=provider_id,
                )
            )

        expected_canonical = canonical_name(
            scientific_name,
            authorship=(
                normalized.authorship
            ),
            rank=rank,
        )

        if (
            expected_canonical
            and canonical
            and normalize_key(
                expected_canonical
            )
            != normalize_key(
                canonical
            )
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "canonical_name_mismatch"
                    ),
                    message=(
                        "Stored canonical name differs "
                        "from the normalized canonical "
                        "scientific name."
                    ),
                    severity=SEVERITY_WARNING,
                    field="canonical_name",
                    value=canonical,
                    provider=provider,
                    provider_id=provider_id,
                    context={
                        "expected": (
                            expected_canonical
                        ),
                    },
                )
            )

        expected_identifier = (
            speciedex_id(
                normalized
            )
        )

        expected_assertion_hash = (
            assertion_hash(
                normalized
            )
        )

        result.metadata.update(
            {
                "expected_speciedex_id": (
                    expected_identifier
                ),
                "assertion_hash": (
                    expected_assertion_hash
                ),
                "rank": rank,
                "status": status,
            }
        )

        self._apply_policy_filters(
            result
        )

        self.statistics.add(
            result
        )

        if (
            self.policy.strict
            and not result.valid
        ):
            result.raise_for_errors(
                "Strict taxon validation failed."
            )

        return result

    def validate_many(
        self,
        records: Iterable[Taxon],
    ) -> list[ValidationResult]:
        """Validate multiple Taxon records."""

        return [
            self.validate(record)
            for record in records
        ]

    def _required_field_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate required scalar fields."""

        required = (
            (
                "provider",
                record.provider,
                self.policy.require_provider,
            ),
            (
                "provider_id",
                record.provider_id,
                self.policy.require_provider_id,
            ),
            (
                "scientific_name",
                record.scientific_name,
                self.policy.require_scientific_name,
            ),
            (
                "canonical_name",
                record.canonical_name,
                self.policy.require_canonical_name,
            ),
            (
                "rank",
                record.rank,
                self.policy.require_rank,
            ),
            (
                "status",
                record.status,
                self.policy.require_status,
            ),
            (
                "retrieved_at",
                record.retrieved_at,
                self.policy.require_retrieved_at,
            ),
            (
                "source_url",
                record.source_url,
                self.policy.require_source_url,
            ),
        )

        for (
            field_name,
            value,
            required_field,
        ) in required:
            if (
                required_field
                and not normalize_space(
                    value
                )
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            f"{field_name}_missing"
                        ),
                        message=(
                            f"Required field "
                            f"{field_name} is empty."
                        ),
                        severity=SEVERITY_ERROR,
                        field=field_name,
                        provider=(
                            record.provider
                        ),
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

    def _length_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate configured text length limits."""

        limits = (
            (
                "provider_id",
                record.provider_id,
                self.policy
                .maximum_provider_id_length,
            ),
            (
                "scientific_name",
                record.scientific_name,
                self.policy
                .maximum_name_length,
            ),
            (
                "canonical_name",
                record.canonical_name,
                self.policy
                .maximum_name_length,
            ),
            (
                "authorship",
                record.authorship,
                self.policy
                .maximum_authorship_length,
            ),
            (
                "source_url",
                record.source_url,
                self.policy
                .maximum_url_length,
            ),
        )

        for (
            field_name,
            value,
            maximum,
        ) in limits:
            normalized = normalize_space(
                value
            )

            if len(normalized) > maximum:
                result.add(
                    ValidationIssue(
                        code=(
                            f"{field_name}_too_long"
                        ),
                        message=(
                            f"{field_name} exceeds "
                            "the configured length "
                            "limit."
                        ),
                        severity=SEVERITY_ERROR,
                        field=field_name,
                        value=len(normalized),
                        provider=(
                            record.provider
                        ),
                        provider_id=(
                            record.provider_id
                        ),
                        context={
                            "maximum": maximum,
                        },
                    )
                )

    def _name_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate scientific and canonical names."""

        scientific_name = (
            normalize_taxon_name(
                record.scientific_name
            )
        )

        canonical = normalize_taxon_name(
            record.canonical_name
        )

        provider = normalize_key(
            record.provider
        )

        provider_id = normalize_space(
            record.provider_id
        )

        if (
            scientific_name
            and LIKELY_PLACEHOLDER_PATTERN.fullmatch(
                scientific_name
            )
            and not self.policy
            .allow_placeholder_names
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "scientific_name_placeholder"
                    ),
                    message=(
                        "Scientific name appears to "
                        "be a placeholder."
                    ),
                    severity=SEVERITY_ERROR,
                    field="scientific_name",
                    value=scientific_name,
                    provider=provider,
                    provider_id=provider_id,
                )
            )

        if (
            canonical
            and LIKELY_PLACEHOLDER_PATTERN.fullmatch(
                canonical
            )
            and not self.policy
            .allow_placeholder_names
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "canonical_name_placeholder"
                    ),
                    message=(
                        "Canonical name appears to "
                        "be a placeholder."
                    ),
                    severity=SEVERITY_ERROR,
                    field="canonical_name",
                    value=canonical,
                    provider=provider,
                    provider_id=provider_id,
                )
            )

        if "\x00" in scientific_name:
            result.add(
                ValidationIssue(
                    code=(
                        "scientific_name_null_byte"
                    ),
                    message=(
                        "Scientific name contains a "
                        "null byte."
                    ),
                    severity=SEVERITY_CRITICAL,
                    field="scientific_name",
                    provider=provider,
                    provider_id=provider_id,
                )
            )

        if "\x00" in canonical:
            result.add(
                ValidationIssue(
                    code=(
                        "canonical_name_null_byte"
                    ),
                    message=(
                        "Canonical name contains a "
                        "null byte."
                    ),
                    severity=SEVERITY_CRITICAL,
                    field="canonical_name",
                    provider=provider,
                    provider_id=provider_id,
                )
            )

    def _rank_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate normalized rank policy."""

        rank = normalize_rank(
            record.rank
        )

        if (
            rank == UNKNOWN_RANK
            and not self.policy
            .allow_unknown_rank
        ):
            result.add(
                ValidationIssue(
                    code="rank_unknown",
                    message=(
                        "Taxon rank is unknown or "
                        "unranked."
                    ),
                    severity=SEVERITY_ERROR,
                    field="rank",
                    value=record.rank,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        elif rank == UNKNOWN_RANK:
            result.add(
                ValidationIssue(
                    code="rank_unknown",
                    message=(
                        "Taxon rank is unknown or "
                        "unranked."
                    ),
                    severity=SEVERITY_WARNING,
                    field="rank",
                    value=record.rank,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

    def _status_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate taxonomic status."""

        status = normalize_status(
            record.status
        )

        if (
            status == UNKNOWN_STATUS
            and not self.policy
            .allow_unknown_status
        ):
            result.add(
                ValidationIssue(
                    code="status_unknown",
                    message=(
                        "Taxonomic status is unknown."
                    ),
                    severity=SEVERITY_ERROR,
                    field="status",
                    value=record.status,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        elif status == UNKNOWN_STATUS:
            result.add(
                ValidationIssue(
                    code="status_unknown",
                    message=(
                        "Taxonomic status is unknown."
                    ),
                    severity=SEVERITY_WARNING,
                    field="status",
                    value=record.status,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        accepted_provider_id = (
            normalize_space(
                record.accepted_provider_id
            )
        )

        provider_id = normalize_space(
            record.provider_id
        )

        if (
            accepted_provider_id
            and provider_id
            and accepted_provider_id
            == provider_id
            and not self.policy
            .allow_self_accepted_id
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "accepted_id_self_reference"
                    ),
                    message=(
                        "accepted_provider_id equals "
                        "provider_id."
                    ),
                    severity=SEVERITY_WARNING,
                    field=(
                        "accepted_provider_id"
                    ),
                    value=accepted_provider_id,
                    provider=record.provider,
                    provider_id=provider_id,
                )
            )

        if (
            is_synonym_status(status)
            and self.policy
            .require_synonym_target
            and not accepted_provider_id
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "synonym_target_missing"
                    ),
                    message=(
                        "Synonym-like record has no "
                        "accepted provider identifier."
                    ),
                    severity=SEVERITY_ERROR,
                    field=(
                        "accepted_provider_id"
                    ),
                    provider=record.provider,
                    provider_id=provider_id,
                )
            )

        if (
            is_accepted_status(status)
            and accepted_provider_id
            and accepted_provider_id
            != provider_id
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "accepted_taxon_has_target"
                    ),
                    message=(
                        "Accepted taxon points to a "
                        "different accepted provider "
                        "identifier."
                    ),
                    severity=SEVERITY_WARNING,
                    field=(
                        "accepted_provider_id"
                    ),
                    value=accepted_provider_id,
                    provider=record.provider,
                    provider_id=provider_id,
                )
            )

    def _authorship_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate authorship data."""

        if (
            not self.policy
            .validate_authorship
            or not normalize_space(
                record.authorship
            )
        ):
            return

        authority_result = (
            validate_authority(
                record.authorship
            )
        )

        for message in authority_result.errors:
            result.add(
                ValidationIssue(
                    code=(
                        "authorship_invalid"
                    ),
                    message=message,
                    severity=SEVERITY_ERROR,
                    field="authorship",
                    value=record.authorship,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        for message in (
            authority_result.warnings
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "authorship_warning"
                    ),
                    message=message,
                    severity=SEVERITY_WARNING,
                    field="authorship",
                    value=record.authorship,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

    def _lineage_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate lineage completeness and consistency."""

        lineage = lineage_from_taxon(
            record
        )

        if (
            not lineage
            and not self.policy
            .allow_empty_lineage
        ):
            result.add(
                ValidationIssue(
                    code="lineage_empty",
                    message=(
                        "Taxon lineage is empty."
                    ),
                    severity=SEVERITY_ERROR,
                    field="lineage",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        lineage_result = (
            lineage.validate()
        )

        for message in lineage_result.errors:
            result.add(
                ValidationIssue(
                    code=(
                        "lineage_invalid"
                    ),
                    message=message,
                    severity=SEVERITY_ERROR,
                    field="lineage",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        for message in (
            lineage_result.warnings
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "lineage_warning"
                    ),
                    message=message,
                    severity=SEVERITY_WARNING,
                    field="lineage",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        rank = normalize_rank(
            record.rank
        )

        if (
            rank in TERMINAL_RANKS
            and self.policy
            .require_species_genus
            and not normalize_space(
                record.genus
            )
        ):
            result.add(
                ValidationIssue(
                    code="genus_missing",
                    message=(
                        "Species-level or lower taxon "
                        "has no genus."
                    ),
                    severity=SEVERITY_ERROR,
                    field="genus",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

    def _synonym_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate synonym collection."""

        synonyms = record.synonyms

        if not isinstance(
            synonyms,
            list,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "synonyms_not_list"
                    ),
                    message=(
                        "Taxon synonyms must be a "
                        "list."
                    ),
                    severity=SEVERITY_ERROR,
                    field="synonyms",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )
            return

        if (
            len(synonyms)
            > self.policy
            .maximum_synonyms
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "synonym_count_exceeded"
                    ),
                    message=(
                        "Taxon synonym count exceeds "
                        "the configured limit."
                    ),
                    severity=SEVERITY_ERROR,
                    field="synonyms",
                    value=len(synonyms),
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                    context={
                        "maximum": (
                            self.policy
                            .maximum_synonyms
                        ),
                    },
                )
            )

        seen: set[str] = set()

        canonical_key = normalize_key(
            record.canonical_name
        )

        scientific_key = normalize_key(
            record.scientific_name
        )

        for index, synonym in enumerate(
            synonyms
        ):
            normalized = (
                normalize_taxon_name(
                    synonym
                )
            )

            key = normalize_key(
                normalized
            )

            if not key:
                result.add(
                    ValidationIssue(
                        code="synonym_empty",
                        message=(
                            "Synonym entry is empty."
                        ),
                        severity=SEVERITY_WARNING,
                        field=(
                            f"synonyms[{index}]"
                        ),
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )
                continue

            if (
                key in seen
                and not self.policy
                .allow_duplicate_synonyms
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            "synonym_duplicate"
                        ),
                        message=(
                            "Synonym list contains a "
                            "duplicate normalized name."
                        ),
                        severity=SEVERITY_WARNING,
                        field=(
                            f"synonyms[{index}]"
                        ),
                        value=normalized,
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

            seen.add(key)

            if key in {
                canonical_key,
                scientific_key,
            }:
                result.add(
                    ValidationIssue(
                        code=(
                            "synonym_matches_primary_name"
                        ),
                        message=(
                            "Synonym matches the "
                            "primary scientific or "
                            "canonical name."
                        ),
                        severity=SEVERITY_WARNING,
                        field=(
                            f"synonyms[{index}]"
                        ),
                        value=normalized,
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

    def _source_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate source URL and source metadata."""

        source_url = normalize_space(
            record.source_url
        )

        if (
            source_url
            and self.policy
            .require_valid_http_url
            and not HTTP_URL_PATTERN.match(
                source_url
            )
        ):
            result.add(
                ValidationIssue(
                    code="source_url_invalid",
                    message=(
                        "Source URL is not an HTTP "
                        "or HTTPS URL."
                    ),
                    severity=SEVERITY_ERROR,
                    field="source_url",
                    value=source_url,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

        elif (
            source_url
            and not HTTP_URL_PATTERN.match(
                source_url
            )
        ):
            result.add(
                ValidationIssue(
                    code="source_url_non_http",
                    message=(
                        "Source URL is not an HTTP "
                        "or HTTPS URL."
                    ),
                    severity=SEVERITY_WARNING,
                    field="source_url",
                    value=source_url,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

    def _timestamp_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate retrieved and source-modified timestamps."""

        if (
            self.policy
            .validate_retrieved_at
            and normalize_space(
                record.retrieved_at
            )
        ):
            retrieved = parse_timestamp(
                record.retrieved_at
            )

            if retrieved is None:
                result.add(
                    ValidationIssue(
                        code=(
                            "retrieved_at_invalid"
                        ),
                        message=(
                            "retrieved_at is not a "
                            "valid ISO-8601 timestamp."
                        ),
                        severity=SEVERITY_ERROR,
                        field="retrieved_at",
                        value=record.retrieved_at,
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

            elif retrieved > (
                datetime.now(UTC)
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            "retrieved_at_future"
                        ),
                        message=(
                            "retrieved_at is in the "
                            "future."
                        ),
                        severity=SEVERITY_WARNING,
                        field="retrieved_at",
                        value=record.retrieved_at,
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

        if (
            self.policy
            .validate_source_modified
            and normalize_space(
                record.source_modified
            )
        ):
            modified = parse_timestamp(
                record.source_modified
            )

            if modified is None:
                result.add(
                    ValidationIssue(
                        code=(
                            "source_modified_invalid"
                        ),
                        message=(
                            "source_modified is not a "
                            "valid ISO-8601 timestamp."
                        ),
                        severity=SEVERITY_WARNING,
                        field="source_modified",
                        value=(
                            record.source_modified
                        ),
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

    def _extra_checks(
        self,
        result: ValidationResult,
        record: Taxon,
    ) -> None:
        """Validate provider-specific extra data."""

        if not isinstance(
            record.extra,
            dict,
        ):
            result.add(
                ValidationIssue(
                    code="extra_not_object",
                    message=(
                        "Taxon extra data must be a "
                        "dictionary."
                    ),
                    severity=SEVERITY_ERROR,
                    field="extra",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )
            return

        if (
            not self.policy
            .validate_extra_serializable
        ):
            return

        size = json_size_bytes(
            record.extra
        )

        if size < 0:
            result.add(
                ValidationIssue(
                    code=(
                        "extra_not_serializable"
                    ),
                    message=(
                        "Taxon extra data is not "
                        "JSON serializable."
                    ),
                    severity=SEVERITY_ERROR,
                    field="extra",
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                )
            )

            return

        if (
            size
            > self.policy
            .maximum_extra_bytes
        ):
            result.add(
                ValidationIssue(
                    code="extra_too_large",
                    message=(
                        "Taxon extra data exceeds "
                        "the configured byte limit."
                    ),
                    severity=SEVERITY_ERROR,
                    field="extra",
                    value=size,
                    provider=record.provider,
                    provider_id=(
                        record.provider_id
                    ),
                    context={
                        "maximum_bytes": (
                            self.policy
                            .maximum_extra_bytes
                        ),
                    },
                )
            )

        raw = record.extra.get(
            "raw"
        )

        if raw is not None:
            raw_size = json_size_bytes(
                raw
            )

            if raw_size < 0:
                result.add(
                    ValidationIssue(
                        code=(
                            "raw_not_serializable"
                        ),
                        message=(
                            "Raw provider record is "
                            "not JSON serializable."
                        ),
                        severity=SEVERITY_ERROR,
                        field="extra.raw",
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                    )
                )

            elif (
                raw_size
                > self.policy
                .maximum_raw_bytes
            ):
                result.add(
                    ValidationIssue(
                        code="raw_too_large",
                        message=(
                            "Raw provider record "
                            "exceeds the configured "
                            "byte limit."
                        ),
                        severity=SEVERITY_ERROR,
                        field="extra.raw",
                        value=raw_size,
                        provider=record.provider,
                        provider_id=(
                            record.provider_id
                        ),
                        context={
                            "maximum_bytes": (
                                self.policy
                                .maximum_raw_bytes
                            ),
                        },
                    )
                )

    def _apply_policy_filters(
        self,
        result: ValidationResult,
    ) -> None:
        """Apply ignored and warning-escalation policies."""

        retained: list[
            ValidationIssue
        ] = []

        for issue in result.issues:
            if (
                issue.code
                in self.policy
                .ignored_issue_codes
            ):
                continue

            if (
                issue.severity
                == SEVERITY_WARNING
                and issue.code
                in self.policy
                .reject_on_warning_codes
            ):
                issue = ValidationIssue(
                    code=issue.code,
                    message=issue.message,
                    severity=SEVERITY_ERROR,
                    field=issue.field,
                    value=issue.value,
                    provider=issue.provider,
                    provider_id=(
                        issue.provider_id
                    ),
                    context=issue.context,
                )

            retained.append(issue)

        result.issues = retained

        result.valid = not any(
            issue.blocking
            for issue in retained
        )


class BatchValidator:
    """Validate complete provider batches."""

    def __init__(
        self,
        taxon_validator: TaxonValidator
        | None = None,
        *,
        policy: ValidationPolicy
        | None = None,
    ) -> None:
        self.taxon_validator = (
            taxon_validator
            if taxon_validator
            is not None
            else TaxonValidator(
                policy
            )
        )

        self.policy = (
            self.taxon_validator.policy
        )

    def validate(
        self,
        batch: Batch,
        *,
        expected_provider: str | None = None,
    ) -> BatchValidationResult:
        """Validate a provider Batch."""

        batch_issues: list[
            ValidationIssue
        ] = []

        if not isinstance(
            batch,
            Batch,
        ):
            raise ValidationError(
                "BatchValidator requires a "
                "providers.common.Batch instance."
            )

        records = list(
            batch.records
        )

        if (
            len(records)
            > self.policy
            .maximum_batch_records
        ):
            batch_issues.append(
                ValidationIssue(
                    code=(
                        "batch_record_limit_exceeded"
                    ),
                    message=(
                        "Batch record count exceeds "
                        "the configured limit."
                    ),
                    severity=SEVERITY_ERROR,
                    field="records",
                    value=len(records),
                    context={
                        "maximum": (
                            self.policy
                            .maximum_batch_records
                        ),
                    },
                )
            )

        cursor_result = validate_cursor(
            batch.next_cursor,
            maximum_length=(
                self.policy
                .maximum_cursor_length
            ),
        )

        batch_issues.extend(
            cursor_result.issues
        )

        expected = (
            normalize_key(
                expected_provider
            )
            if expected_provider
            else ""
        )

        record_results: list[
            ValidationResult
        ] = []

        accepted_records: list[
            Taxon
        ] = []

        rejected_records: list[
            Taxon
        ] = []

        source_ids: dict[
            tuple[str, str],
            int,
        ] = {}

        assertion_hashes: dict[
            str,
            int,
        ] = {}

        duplicate_source_ids: list[
            str
        ] = []

        duplicate_assertions: list[
            str
        ] = []

        for index, record in enumerate(
            records
        ):
            result = (
                self.taxon_validator
                .validate(
                    record
                )
            )

            normalized = result.normalized

            if isinstance(
                normalized,
                Taxon,
            ):
                provider = normalize_key(
                    normalized.provider
                )

                provider_id = (
                    normalize_space(
                        normalized.provider_id
                    )
                )

                source_key = (
                    provider,
                    provider_id,
                )

                source_ids[
                    source_key
                ] = (
                    source_ids.get(
                        source_key,
                        0,
                    )
                    + 1
                )

                if (
                    source_ids[
                        source_key
                    ]
                    > 1
                ):
                    label = (
                        f"{provider}:"
                        f"{provider_id}"
                    )

                    duplicate_source_ids.append(
                        label
                    )

                    result.add(
                        ValidationIssue(
                            code=(
                                "batch_duplicate_source_id"
                            ),
                            message=(
                                "Batch contains a "
                                "duplicate provider/"
                                "provider_id pair."
                            ),
                            severity=SEVERITY_ERROR,
                            field=(
                                f"records[{index}]"
                            ),
                            provider=provider,
                            provider_id=provider_id,
                        )
                    )

                digest = assertion_hash(
                    normalized
                )

                assertion_hashes[
                    digest
                ] = (
                    assertion_hashes.get(
                        digest,
                        0,
                    )
                    + 1
                )

                if (
                    assertion_hashes[
                        digest
                    ]
                    > 1
                ):
                    duplicate_assertions.append(
                        digest
                    )

                    result.add(
                        ValidationIssue(
                            code=(
                                "batch_duplicate_assertion"
                            ),
                            message=(
                                "Batch contains a "
                                "duplicate normalized "
                                "provider assertion."
                            ),
                            severity=SEVERITY_WARNING,
                            field=(
                                f"records[{index}]"
                            ),
                            provider=provider,
                            provider_id=provider_id,
                        )
                    )

                if (
                    expected
                    and provider != expected
                ):
                    result.add(
                        ValidationIssue(
                            code=(
                                "batch_provider_mismatch"
                            ),
                            message=(
                                "Taxon provider differs "
                                "from the expected batch "
                                "provider."
                            ),
                            severity=SEVERITY_ERROR,
                            field=(
                                f"records[{index}].provider"
                            ),
                            value=provider,
                            provider=provider,
                            provider_id=provider_id,
                            context={
                                "expected": expected,
                            },
                        )
                    )

                result.valid = not any(
                    issue.blocking
                    for issue
                    in result.issues
                )

                if result.valid:
                    accepted_records.append(
                        normalized
                    )
                else:
                    rejected_records.append(
                        normalized
                    )

            record_results.append(
                result
            )

        raw_count = safe_int(
            batch.raw,
            len(records),
        )

        if raw_count < len(records):
            batch_issues.append(
                ValidationIssue(
                    code=(
                        "batch_raw_count_below_records"
                    ),
                    message=(
                        "Batch raw count is below "
                        "the normalized record count."
                    ),
                    severity=SEVERITY_WARNING,
                    field="raw",
                    value=raw_count,
                    context={
                        "records": len(
                            records
                        ),
                    },
                )
            )

        request_count = safe_int(
            batch.requests,
            0,
        )

        if request_count < 0:
            batch_issues.append(
                ValidationIssue(
                    code=(
                        "batch_request_count_negative"
                    ),
                    message=(
                        "Batch request count is "
                        "negative."
                    ),
                    severity=SEVERITY_ERROR,
                    field="requests",
                    value=request_count,
                )
            )

        valid = (
            not any(
                issue.blocking
                for issue in batch_issues
            )
            and all(
                result.valid
                for result
                in record_results
            )
        )

        provider_name = (
            expected
            or (
                normalize_key(
                    accepted_records[0]
                    .provider
                )
                if accepted_records
                else ""
            )
        )

        return BatchValidationResult(
            valid=valid,
            provider=provider_name,
            record_results=(
                record_results
            ),
            batch_issues=(
                batch_issues
            ),
            accepted_records=(
                accepted_records
            ),
            rejected_records=(
                rejected_records
            ),
            duplicate_source_ids=sorted(
                set(
                    duplicate_source_ids
                )
            ),
            duplicate_assertions=sorted(
                set(
                    duplicate_assertions
                )
            ),
        )


class ArchiveRecordValidator:
    """Validate canonical archive record dictionaries."""

    REQUIRED_FIELDS = (
        "speciedex_id",
        "scientific_name",
        "canonical_name",
        "rank",
        "status",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        *,
        strict: bool = True,
    ) -> None:
        self.strict = bool(
            strict
        )

    def validate(
        self,
        record: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate one canonical archive record."""

        result = ValidationResult(
            valid=True,
            object_type="archive_record",
        )

        if not isinstance(
            record,
            Mapping,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "archive_record_not_object"
                    ),
                    message=(
                        "Archive record must be a "
                        "mapping."
                    ),
                    severity=SEVERITY_CRITICAL,
                )
            )
            return result

        identifier = normalize_space(
            record.get(
                "speciedex_id"
            )
        )

        result.identifier = identifier
        result.normalized = dict(
            record
        )

        for field_name in (
            self.REQUIRED_FIELDS
        ):
            if not normalize_space(
                record.get(
                    field_name
                )
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            f"archive_{field_name}_missing"
                        ),
                        message=(
                            "Archive record required "
                            f"field {field_name} is "
                            "empty."
                        ),
                        severity=SEVERITY_ERROR,
                        field=field_name,
                    )
                )

        identifier_result = (
            validate_speciedex_identifier(
                identifier
            )
        )

        result.extend(
            identifier_result.issues
        )

        created_at = parse_timestamp(
            record.get(
                "created_at"
            )
        )

        updated_at = parse_timestamp(
            record.get(
                "updated_at"
            )
        )

        if created_at is None:
            result.add(
                ValidationIssue(
                    code=(
                        "archive_created_at_invalid"
                    ),
                    message=(
                        "Archive created_at is not "
                        "a valid timestamp."
                    ),
                    severity=SEVERITY_ERROR,
                    field="created_at",
                )
            )

        if updated_at is None:
            result.add(
                ValidationIssue(
                    code=(
                        "archive_updated_at_invalid"
                    ),
                    message=(
                        "Archive updated_at is not "
                        "a valid timestamp."
                    ),
                    severity=SEVERITY_ERROR,
                    field="updated_at",
                )
            )

        if (
            created_at is not None
            and updated_at is not None
            and updated_at < created_at
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "archive_updated_before_created"
                    ),
                    message=(
                        "Archive updated_at precedes "
                        "created_at."
                    ),
                    severity=SEVERITY_ERROR,
                    field="updated_at",
                )
            )

        providers = record.get(
            "providers",
            [],
        )

        if not isinstance(
            providers,
            list,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "archive_providers_not_list"
                    ),
                    message=(
                        "Archive providers must be a "
                        "list."
                    ),
                    severity=SEVERITY_ERROR,
                    field="providers",
                )
            )

        elif len(providers) != len(
            {
                normalize_key(provider)
                for provider
                in providers
            }
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "archive_duplicate_providers"
                    ),
                    message=(
                        "Archive providers contains "
                        "duplicates."
                    ),
                    severity=SEVERITY_WARNING,
                    field="providers",
                )
            )

        taxonomy = record.get(
            "taxonomy",
            {},
        )

        if (
            taxonomy
            and not isinstance(
                taxonomy,
                Mapping,
            )
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "archive_taxonomy_not_object"
                    ),
                    message=(
                        "Archive taxonomy must be a "
                        "mapping."
                    ),
                    severity=SEVERITY_ERROR,
                    field="taxonomy",
                )
            )

        if self.strict:
            record_hash = normalize_space(
                record.get(
                    "record_hash"
                )
            )

            if not record_hash:
                result.add(
                    ValidationIssue(
                        code=(
                            "archive_record_hash_missing"
                        ),
                        message=(
                            "Strict archive record has "
                            "no record_hash."
                        ),
                        severity=SEVERITY_ERROR,
                        field="record_hash",
                    )
                )

        result.valid = not any(
            issue.blocking
            for issue in result.issues
        )

        return result


class SourceAssertionValidator:
    """Validate source assertion dictionaries."""

    def validate(
        self,
        assertion: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate one persisted source assertion."""

        result = ValidationResult(
            valid=True,
            object_type="source_assertion",
        )

        if not isinstance(
            assertion,
            Mapping,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "assertion_not_object"
                    ),
                    message=(
                        "Source assertion must be a "
                        "mapping."
                    ),
                    severity=SEVERITY_CRITICAL,
                )
            )
            return result

        provider = normalize_key(
            assertion.get(
                "provider"
            )
        )

        provider_id = normalize_space(
            assertion.get(
                "provider_id"
            )
        )

        identifier = normalize_space(
            assertion.get(
                "speciedex_id"
            )
        )

        result.identifier = (
            f"{provider}:{provider_id}"
        )

        if not provider:
            result.add(
                ValidationIssue(
                    code=(
                        "assertion_provider_missing"
                    ),
                    message=(
                        "Source assertion provider "
                        "is empty."
                    ),
                    severity=SEVERITY_ERROR,
                    field="provider",
                )
            )

        if not provider_id:
            result.add(
                ValidationIssue(
                    code=(
                        "assertion_provider_id_missing"
                    ),
                    message=(
                        "Source assertion provider_id "
                        "is empty."
                    ),
                    severity=SEVERITY_ERROR,
                    field="provider_id",
                    provider=provider,
                )
            )

        if identifier:
            result.extend(
                validate_speciedex_identifier(
                    identifier
                ).issues
            )

        assertion_digest = normalize_space(
            assertion.get(
                "assertion_hash"
            )
        )

        assertion_json = assertion.get(
            "assertion",
            assertion.get(
                "assertion_json"
            ),
        )

        if (
            assertion_json is not None
            and isinstance(
                assertion_json,
                str,
            )
        ):
            try:
                assertion_json = json.loads(
                    assertion_json
                )
            except json.JSONDecodeError:
                result.add(
                    ValidationIssue(
                        code=(
                            "assertion_json_invalid"
                        ),
                        message=(
                            "Source assertion JSON "
                            "cannot be decoded."
                        ),
                        severity=SEVERITY_ERROR,
                        field="assertion_json",
                        provider=provider,
                        provider_id=provider_id,
                    )
                )

        if (
            assertion_digest
            and assertion_json
            is not None
        ):
            calculated = stable_json_hash(
                assertion_json
            )

            if (
                calculated
                != assertion_digest
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            "assertion_hash_mismatch"
                        ),
                        message=(
                            "Source assertion hash does "
                            "not match assertion data."
                        ),
                        severity=SEVERITY_ERROR,
                        field="assertion_hash",
                        provider=provider,
                        provider_id=provider_id,
                        context={
                            "calculated": (
                                calculated
                            ),
                        },
                    )
                )

        result.valid = not any(
            issue.blocking
            for issue in result.issues
        )

        return result


class StatisticsValidator:
    """Validate generated statistics dictionaries."""

    COUNT_FIELDS = (
        "species",
        "genera",
        "families",
        "orders",
        "classes",
        "phyla",
        "kingdoms",
        "records_archived",
        "source_assertions",
        "source_identifiers",
        "synonyms",
        "unresolved_conflicts",
        "resolved_conflicts",
        "rejected_conflicts",
        "revisions",
        "volumes",
        "sealed_volumes",
    )

    def validate(
        self,
        statistics: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate one generated statistics object."""

        result = ValidationResult(
            valid=True,
            object_type="statistics",
        )

        if not isinstance(
            statistics,
            Mapping,
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "statistics_not_object"
                    ),
                    message=(
                        "Statistics root must be a "
                        "mapping."
                    ),
                    severity=SEVERITY_CRITICAL,
                )
            )
            return result

        for field_name in self.COUNT_FIELDS:
            if field_name not in statistics:
                continue

            value = statistics.get(
                field_name
            )

            try:
                parsed = int(value)

            except (
                TypeError,
                ValueError,
            ):
                result.add(
                    ValidationIssue(
                        code=(
                            "statistics_count_invalid"
                        ),
                        message=(
                            f"Statistics field "
                            f"{field_name} is not an "
                            "integer."
                        ),
                        severity=SEVERITY_ERROR,
                        field=field_name,
                        value=value,
                    )
                )
                continue

            if parsed < 0:
                result.add(
                    ValidationIssue(
                        code=(
                            "statistics_count_negative"
                        ),
                        message=(
                            f"Statistics field "
                            f"{field_name} is negative."
                        ),
                        severity=SEVERITY_ERROR,
                        field=field_name,
                        value=parsed,
                    )
                )

        last_updated = statistics.get(
            "last_updated",
            statistics.get(
                "generated_at",
            ),
        )

        if (
            last_updated
            and parse_timestamp(
                last_updated
            )
            is None
        ):
            result.add(
                ValidationIssue(
                    code=(
                        "statistics_timestamp_invalid"
                    ),
                    message=(
                        "Statistics timestamp is not "
                        "valid ISO-8601."
                    ),
                    severity=SEVERITY_ERROR,
                    field="last_updated",
                    value=last_updated,
                )
            )

        species = safe_int(
            statistics.get(
                "species",
                0,
            )
        )

        records = safe_int(
            statistics.get(
                "records_archived",
                species,
            )
        )

        if species > records:
            result.add(
                ValidationIssue(
                    code=(
                        "statistics_species_exceeds_records"
                    ),
                    message=(
                        "Species count exceeds total "
                        "archived record count."
                    ),
                    severity=SEVERITY_WARNING,
                    field="species",
                    value=species,
                    context={
                        "records_archived": (
                            records
                        ),
                    },
                )
            )

        result.normalized = dict(
            statistics
        )

        result.valid = not any(
            issue.blocking
            for issue in result.issues
        )

        return result


class ValidationManager:
    """
    High-level validation facade for ingestion components.
    """

    def __init__(
        self,
        policy: ValidationPolicy | None = None,
    ) -> None:
        self.policy = (
            policy
            if policy is not None
            else ValidationPolicy.permissive()
        )

        self.taxa = TaxonValidator(
            self.policy
        )

        self.batches = BatchValidator(
            self.taxa
        )

        self.archive_records = (
            ArchiveRecordValidator(
                strict=self.policy.strict
            )
        )

        self.assertions = (
            SourceAssertionValidator()
        )

        self.statistics = (
            StatisticsValidator()
        )

    def validate_taxon(
        self,
        record: Taxon,
    ) -> ValidationResult:
        """Validate one provider taxon."""

        return self.taxa.validate(
            record
        )

    def validate_batch(
        self,
        batch: Batch,
        *,
        expected_provider: str | None = None,
    ) -> BatchValidationResult:
        """Validate one provider batch."""

        return self.batches.validate(
            batch,
            expected_provider=(
                expected_provider
            ),
        )

    def validate_archive_record(
        self,
        record: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate one canonical archive record."""

        return (
            self.archive_records
            .validate(
                record
            )
        )

    def validate_assertion(
        self,
        assertion: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate one source assertion."""

        return self.assertions.validate(
            assertion
        )

    def validate_statistics(
        self,
        statistics: Mapping[str, Any],
    ) -> ValidationResult:
        """Validate generated statistics."""

        return self.statistics.validate(
            statistics
        )

    def summary(self) -> dict[str, Any]:
        """Return aggregate Taxon validation statistics."""

        return (
            self.taxa.statistics
            .to_dict()
        )


def rejection_record(
    record: Taxon,
    result: ValidationResult,
    *,
    rejected_at: str | None = None,
) -> dict[str, Any]:
    """Build a durable rejected-record object."""

    timestamp = (
        normalize_space(
            rejected_at
        )
        or utc_now()
    )

    return {
        "schema_version": (
            VALIDATION_SCHEMA_VERSION
        ),
        "event": "record_rejected",
        "rejected_at": timestamp,
        "provider": normalize_key(
            record.provider
        ),
        "provider_id": normalize_space(
            record.provider_id
        ),
        "scientific_name": (
            normalize_taxon_name(
                record.scientific_name
            )
        ),
        "canonical_name": (
            normalize_taxon_name(
                record.canonical_name
            )
        ),
        "rank": normalize_rank(
            record.rank
        ),
        "status": normalize_status(
            record.status
        ),
        "issues": [
            issue.to_dict()
            for issue in result.issues
        ],
        "record": record.to_dict(),
        "record_hash": assertion_hash(
            record
        ),
    }


def validate_taxon_record(
    record: Taxon,
    *,
    policy: ValidationPolicy | None = None,
) -> ValidationResult:
    """Convenience wrapper for one Taxon."""

    return TaxonValidator(
        policy
    ).validate(
        record
    )


def validate_provider_batch(
    batch: Batch,
    *,
    expected_provider: str | None = None,
    policy: ValidationPolicy | None = None,
) -> BatchValidationResult:
    """Convenience wrapper for one Batch."""

    return BatchValidator(
        policy=policy
    ).validate(
        batch,
        expected_provider=(
            expected_provider
        ),
    )


def validate_archive_record(
    record: Mapping[str, Any],
    *,
    strict: bool = True,
) -> ValidationResult:
    """Convenience wrapper for one archive record."""

    return ArchiveRecordValidator(
        strict=strict
    ).validate(
        record
    )


def validate_statistics(
    statistics: Mapping[str, Any],
) -> ValidationResult:
    """Convenience wrapper for generated statistics."""

    return StatisticsValidator().validate(
        statistics
    )


__all__ = [
    "ASSERTION_ID_PATTERN",
    "ArchiveRecordValidator",
    "BatchValidationResult",
    "BatchValidator",
    "CONFLICT_ID_PATTERN",
    "DEFAULT_MAX_AUTHORSHIP_LENGTH",
    "DEFAULT_MAX_BATCH_RECORDS",
    "DEFAULT_MAX_CURSOR_LENGTH",
    "DEFAULT_MAX_EXTRA_BYTES",
    "DEFAULT_MAX_NAME_LENGTH",
    "DEFAULT_MAX_PROVIDER_ID_LENGTH",
    "DEFAULT_MAX_RAW_BYTES",
    "DEFAULT_MAX_SYNONYMS",
    "DEFAULT_MAX_URL_LENGTH",
    "HTTP_URL_PATTERN",
    "LIKELY_PLACEHOLDER_PATTERN",
    "SEVERITY_CRITICAL",
    "SEVERITY_ERROR",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "SPECIEDEX_ID_PATTERN",
    "SourceAssertionValidator",
    "StatisticsValidator",
    "TaxonValidator",
    "VALIDATION_SCHEMA_VERSION",
    "ValidationConfigurationError",
    "ValidationError",
    "ValidationIssue",
    "ValidationManager",
    "ValidationPolicy",
    "ValidationResult",
    "ValidationStatistics",
    "json_size_bytes",
    "normalize_space",
    "parse_timestamp",
    "rejection_record",
    "safe_float",
    "safe_int",
    "utc_now",
    "validate_archive_record",
    "validate_assertion_identifier",
    "validate_conflict_identifier",
    "validate_cursor",
    "validate_provider_batch",
    "validate_provider_definition",
    "validate_speciedex_identifier",
    "validate_statistics",
    "validate_taxon_record",
]
