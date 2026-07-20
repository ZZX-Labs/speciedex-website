#!/usr/bin/env python3
"""
Speciedex.org
static/tools/core/cache.py

Unified cache infrastructure for the Speciedex taxonomic ingestion system.

This module provides:

- thread-safe in-memory LRU caching,
- TTL expiration,
- persistent disk caching,
- compressed cache payloads,
- deterministic cache keys,
- atomic writes,
- namespace isolation,
- cache metadata,
- cache statistics,
- cache verification,
- cache pruning,
- provider-response caching,
- SQLite lookup caching,
- reconciliation lookup caching.

This file is delivered in four contiguous parts. Concatenate all four parts
in order into:

    static/tools/core/cache.py

Copyright (c) 2026 ZZX-Laboratories

Licensed under the MIT License.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import (
    Any,
    Callable,
    Generic,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
    TypeVar,
)


CACHE_SCHEMA_VERSION = 1

DEFAULT_MEMORY_MAX_ENTRIES = 10_000
DEFAULT_MEMORY_MAX_BYTES = 256 * 1024 * 1024

DEFAULT_DISK_MAX_ENTRIES = 250_000
DEFAULT_DISK_MAX_BYTES = 8 * 1024 * 1024 * 1024

DEFAULT_TTL_SECONDS = 60 * 60
DEFAULT_STALE_TTL_SECONDS = 24 * 60 * 60

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_LOCK_POLL_SECONDS = 0.05
DEFAULT_STALE_LOCK_SECONDS = 15 * 60

DEFAULT_PRUNE_INTERVAL_SECONDS = 15 * 60
DEFAULT_METADATA_FILENAME = "cache-metadata.json"

DEFAULT_COMPRESSION_THRESHOLD_BYTES = 4 * 1024
DEFAULT_FILE_MODE = 0o600
DEFAULT_DIRECTORY_MODE = 0o700

DEFAULT_NAMESPACE = "default"

CACHE_KEY_PREFIX = "spx-cache"
CACHE_FILE_SUFFIX = ".cache"
CACHE_COMPRESSED_SUFFIX = ".cache.gz"
CACHE_LOCK_SUFFIX = ".lock"

SERIALIZER_JSON = "json"
SERIALIZER_PICKLE = "pickle"
SERIALIZER_RAW = "raw"

SUPPORTED_SERIALIZERS = {
    SERIALIZER_JSON,
    SERIALIZER_PICKLE,
    SERIALIZER_RAW,
}

COMPRESSION_NONE = "none"
COMPRESSION_GZIP = "gzip"

SUPPORTED_COMPRESSION = {
    COMPRESSION_NONE,
    COMPRESSION_GZIP,
}


KeyType = TypeVar("KeyType")
ValueType = TypeVar("ValueType")


class CacheError(RuntimeError):
    """Base exception for cache failures."""


class CacheKeyError(CacheError):
    """Raised when a cache key cannot be normalized."""


class CacheSerializationError(CacheError):
    """Raised when cached data cannot be serialized or decoded."""


class CacheIntegrityError(CacheError):
    """Raised when a persistent cache entry fails verification."""


class CacheLockError(CacheError):
    """Raised when an exclusive cache lock cannot be acquired."""


class CacheConfigurationError(CacheError):
    """Raised when cache configuration is invalid."""


@dataclass(slots=True)
class CachePolicy:
    """
    Cache behavior for one namespace or operation.

    ttl_seconds:
        Maximum age before an entry expires.

    stale_ttl_seconds:
        Additional age during which an expired entry may be returned when
        allow_stale=True.

    memory:
        Store the entry in the in-memory LRU cache.

    disk:
        Store the entry in the persistent disk cache.

    serializer:
        json, pickle, or raw.

    compression:
        none or gzip.

    compress_above_bytes:
        Compression is enabled only when the serialized payload meets this
        threshold.

    refresh_on_access:
        Refresh the memory-entry access timestamp whenever it is read.

    cache_none:
        Permit caching None values.

    verify_hash:
        Verify persistent payload hashes when reading.
    """

    ttl_seconds: int = DEFAULT_TTL_SECONDS
    stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS
    memory: bool = True
    disk: bool = True
    serializer: str = SERIALIZER_PICKLE
    compression: str = COMPRESSION_GZIP
    compress_above_bytes: int = DEFAULT_COMPRESSION_THRESHOLD_BYTES
    refresh_on_access: bool = True
    cache_none: bool = False
    verify_hash: bool = True

    def __post_init__(self) -> None:
        self.ttl_seconds = max(
            0,
            int(self.ttl_seconds),
        )

        self.stale_ttl_seconds = max(
            0,
            int(self.stale_ttl_seconds),
        )

        self.compress_above_bytes = max(
            0,
            int(self.compress_above_bytes),
        )

        self.serializer = normalize_key(
            self.serializer
        )

        self.compression = normalize_key(
            self.compression
        )

        if self.serializer not in SUPPORTED_SERIALIZERS:
            raise CacheConfigurationError(
                f"Unsupported cache serializer: "
                f"{self.serializer!r}."
            )

        if self.compression not in SUPPORTED_COMPRESSION:
            raise CacheConfigurationError(
                f"Unsupported cache compression: "
                f"{self.compression!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible cache policy."""

        return {
            "ttl_seconds": self.ttl_seconds,
            "stale_ttl_seconds": (
                self.stale_ttl_seconds
            ),
            "memory": self.memory,
            "disk": self.disk,
            "serializer": self.serializer,
            "compression": self.compression,
            "compress_above_bytes": (
                self.compress_above_bytes
            ),
            "refresh_on_access": (
                self.refresh_on_access
            ),
            "cache_none": self.cache_none,
            "verify_hash": self.verify_hash,
        }


@dataclass(slots=True)
class CacheEntryMetadata:
    """Metadata stored beside one cached payload."""

    schema_version: int
    namespace: str
    key: str
    key_hash: str
    created_at: float
    updated_at: float
    accessed_at: float
    expires_at: float | None
    stale_until: float | None
    serializer: str
    compression: str
    payload_hash: str
    payload_bytes: int
    stored_bytes: int
    hits: int = 0
    tags: list[str] = field(
        default_factory=list
    )
    source: str = ""
    content_type: str = ""
    etag: str = ""
    last_modified: str = ""
    status_code: int | None = None
    extra: dict[str, Any] = field(
        default_factory=dict
    )

    @property
    def expired(self) -> bool:
        """Return whether the entry is currently expired."""

        if self.expires_at is None:
            return False

        return time.time() >= self.expires_at

    @property
    def stale(self) -> bool:
        """Return whether the entry is expired but still within stale TTL."""

        current = time.time()

        if self.expires_at is None:
            return False

        if current < self.expires_at:
            return False

        if self.stale_until is None:
            return False

        return current < self.stale_until

    @property
    def dead(self) -> bool:
        """Return whether the entry is beyond both TTL and stale TTL."""

        current = time.time()

        if self.expires_at is None:
            return False

        if current < self.expires_at:
            return False

        if self.stale_until is None:
            return True

        return current >= self.stale_until

    @property
    def age_seconds(self) -> float:
        """Return age since creation."""

        return max(
            0.0,
            time.time() - self.created_at,
        )

    @property
    def idle_seconds(self) -> float:
        """Return age since last access."""

        return max(
            0.0,
            time.time() - self.accessed_at,
        )

    def touch(self) -> None:
        """Update access metadata."""

        self.accessed_at = time.time()
        self.hits += 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible metadata object."""

        return {
            "schema_version": (
                self.schema_version
            ),
            "namespace": self.namespace,
            "key": self.key,
            "key_hash": self.key_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "accessed_at": self.accessed_at,
            "expires_at": self.expires_at,
            "stale_until": self.stale_until,
            "serializer": self.serializer,
            "compression": self.compression,
            "payload_hash": self.payload_hash,
            "payload_bytes": self.payload_bytes,
            "stored_bytes": self.stored_bytes,
            "hits": self.hits,
            "tags": list(self.tags),
            "source": self.source,
            "content_type": self.content_type,
            "etag": self.etag,
            "last_modified": (
                self.last_modified
            ),
            "status_code": self.status_code,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> CacheEntryMetadata:
        """Build metadata from a decoded mapping."""

        return cls(
            schema_version=safe_int(
                value.get(
                    "schema_version",
                    CACHE_SCHEMA_VERSION,
                )
            ),
            namespace=normalize_namespace(
                value.get(
                    "namespace",
                    DEFAULT_NAMESPACE,
                )
            ),
            key=normalize_space(
                value.get("key")
            ),
            key_hash=normalize_key(
                value.get("key_hash")
            ),
            created_at=safe_float(
                value.get(
                    "created_at",
                    time.time(),
                )
            ),
            updated_at=safe_float(
                value.get(
                    "updated_at",
                    time.time(),
                )
            ),
            accessed_at=safe_float(
                value.get(
                    "accessed_at",
                    time.time(),
                )
            ),
            expires_at=optional_float(
                value.get("expires_at")
            ),
            stale_until=optional_float(
                value.get("stale_until")
            ),
            serializer=normalize_key(
                value.get(
                    "serializer",
                    SERIALIZER_PICKLE,
                )
            ),
            compression=normalize_key(
                value.get(
                    "compression",
                    COMPRESSION_NONE,
                )
            ),
            payload_hash=normalize_key(
                value.get("payload_hash")
            ),
            payload_bytes=safe_int(
                value.get("payload_bytes")
            ),
            stored_bytes=safe_int(
                value.get("stored_bytes")
            ),
            hits=safe_int(
                value.get("hits")
            ),
            tags=sorted(
                {
                    normalize_key(tag)
                    for tag in value.get(
                        "tags",
                        [],
                    )
                    if normalize_key(tag)
                }
            ),
            source=normalize_space(
                value.get("source")
            ),
            content_type=normalize_space(
                value.get("content_type")
            ),
            etag=normalize_space(
                value.get("etag")
            ),
            last_modified=normalize_space(
                value.get("last_modified")
            ),
            status_code=optional_int(
                value.get("status_code")
            ),
            extra=(
                dict(
                    value.get(
                        "extra",
                        {},
                    )
                )
                if isinstance(
                    value.get(
                        "extra",
                        {},
                    ),
                    Mapping,
                )
                else {}
            ),
        )


@dataclass(slots=True)
class CacheEntry(Generic[ValueType]):
    """One decoded cached value and its metadata."""

    value: ValueType
    metadata: CacheEntryMetadata
    layer: str

    @property
    def expired(self) -> bool:
        return self.metadata.expired

    @property
    def stale(self) -> bool:
        return self.metadata.stale

    @property
    def dead(self) -> bool:
        return self.metadata.dead

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible cache entry description."""

        return {
            "metadata": (
                self.metadata.to_dict()
            ),
            "layer": self.layer,
            "expired": self.expired,
            "stale": self.stale,
            "dead": self.dead,
        }


@dataclass(slots=True)
class CacheStatistics:
    """Mutable cache statistics."""

    memory_hits: int = 0
    memory_misses: int = 0
    disk_hits: int = 0
    disk_misses: int = 0
    stale_hits: int = 0
    writes: int = 0
    deletes: int = 0
    evictions: int = 0
    expirations: int = 0
    integrity_failures: int = 0
    serialization_failures: int = 0
    lock_failures: int = 0
    prunes: int = 0
    bytes_read: int = 0
    bytes_written: int = 0

    @property
    def hits(self) -> int:
        return (
            self.memory_hits
            + self.disk_hits
        )

    @property
    def misses(self) -> int:
        return (
            self.memory_misses
            + self.disk_misses
        )

    @property
    def requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        if self.requests <= 0:
            return 0.0

        return self.hits / self.requests

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible statistics object."""

        return {
            "memory_hits": self.memory_hits,
            "memory_misses": (
                self.memory_misses
            ),
            "disk_hits": self.disk_hits,
            "disk_misses": self.disk_misses,
            "stale_hits": self.stale_hits,
            "writes": self.writes,
            "deletes": self.deletes,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "integrity_failures": (
                self.integrity_failures
            ),
            "serialization_failures": (
                self.serialization_failures
            ),
            "lock_failures": (
                self.lock_failures
            ),
            "prunes": self.prunes,
            "bytes_read": self.bytes_read,
            "bytes_written": (
                self.bytes_written
            ),
            "hits": self.hits,
            "misses": self.misses,
            "requests": self.requests,
            "hit_rate": round(
                self.hit_rate,
                6,
            ),
        }


@dataclass(slots=True)
class MemoryCacheItem(Generic[ValueType]):
    """Internal in-memory LRU entry."""

    value: ValueType
    metadata: CacheEntryMetadata
    estimated_bytes: int


@dataclass(slots=True)
class CachePaths:
    """Filesystem paths for one persistent cache entry."""

    namespace_root: Path
    entry_root: Path
    payload_path: Path
    metadata_path: Path
    lock_path: Path


@dataclass(slots=True)
class CacheVerification:
    """Verification result for one cache tree."""

    valid: bool
    entries_checked: int
    valid_entries: int
    expired_entries: int
    corrupt_entries: int
    orphaned_payloads: int
    orphaned_metadata: int
    errors: list[str] = field(
        default_factory=list
    )
    warnings: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible verification result."""

        return {
            "valid": self.valid,
            "entries_checked": (
                self.entries_checked
            ),
            "valid_entries": (
                self.valid_entries
            ),
            "expired_entries": (
                self.expired_entries
            ),
            "corrupt_entries": (
                self.corrupt_entries
            ),
            "orphaned_payloads": (
                self.orphaned_payloads
            ),
            "orphaned_metadata": (
                self.orphaned_metadata
            ),
            "errors": list(self.errors),
            "warnings": list(
                self.warnings
            ),
        }


@dataclass(slots=True)
class CachePruneResult:
    """Result of a cache-pruning operation."""

    scanned: int
    deleted: int
    expired_deleted: int
    oversized_deleted: int
    orphaned_deleted: int
    bytes_freed: int
    errors: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible prune result."""

        return {
            "scanned": self.scanned,
            "deleted": self.deleted,
            "expired_deleted": (
                self.expired_deleted
            ),
            "oversized_deleted": (
                self.oversized_deleted
            ),
            "orphaned_deleted": (
                self.orphaned_deleted
            ),
            "bytes_freed": (
                self.bytes_freed
            ),
            "errors": list(self.errors),
        }


def utc_now() -> str:
    """Return the current UTC timestamp."""

    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_space(value: Any) -> str:
    """Normalize whitespace in arbitrary text."""

    return " ".join(
        str(
            value
            if value is not None
            else ""
        )
        .strip()
        .split()
    )


def normalize_key(value: Any) -> str:
    """Normalize text for deterministic comparisons."""

    return normalize_space(
        value
    ).casefold()


def normalize_namespace(value: Any) -> str:
    """Normalize a cache namespace for filesystem use."""

    text = normalize_key(
        value
    )

    text = "".join(
        character
        if (
            character.isalnum()
            or character in {
                "-",
                "_",
                ".",
            }
        )
        else "-"
        for character in text
    )

    text = text.strip(
        "-."
    )

    return text or DEFAULT_NAMESPACE


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


def optional_int(
    value: Any,
) -> int | None:
    """Convert a value to an optional integer."""

    if value is None:
        return None

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    """Convert a value to a float."""

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return float(default)


def optional_float(
    value: Any,
) -> float | None:
    """Convert a value to an optional float."""

    if value is None:
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def canonicalize_value(
    value: Any,
) -> Any:
    """
    Convert supported values into deterministic JSON-compatible data.

    This is used only for cache-key generation. Cached payload serialization
    is handled separately.
    """

    if value is None:
        return None

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if isinstance(
        value,
        bytes,
    ):
        return {
            "__type__": "bytes",
            "hex": value.hex(),
        }

    if isinstance(
        value,
        Path,
    ):
        return value.as_posix()

    if is_dataclass(value):
        return canonicalize_value(
            asdict(value)
        )

    if hasattr(
        value,
        "to_dict",
    ) and callable(
        value.to_dict
    ):
        return canonicalize_value(
            value.to_dict()
        )

    if isinstance(
        value,
        Mapping,
    ):
        return {
            normalize_space(key): (
                canonicalize_value(item)
            )
            for key, item in sorted(
                value.items(),
                key=lambda pair: (
                    normalize_space(
                        pair[0]
                    )
                ),
            )
        }

    if isinstance(
        value,
        (
            set,
            frozenset,
        ),
    ):
        normalized = [
            canonicalize_value(item)
            for item in value
        ]

        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    if isinstance(
        value,
        Sequence,
    ) and not isinstance(
        value,
        (
            str,
            bytes,
            bytearray,
        ),
    ):
        return [
            canonicalize_value(item)
            for item in value
        ]

    if isinstance(
        value,
        Iterable,
    ):
        return [
            canonicalize_value(item)
            for item in value
        ]

    return normalize_space(value)


def stable_json_bytes(
    value: Any,
) -> bytes:
    """Return deterministic UTF-8 JSON bytes."""

    return json.dumps(
        canonicalize_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def stable_hash(
    value: Any,
) -> str:
    """Return a SHA-256 digest of deterministic JSON data."""

    return hashlib.sha256(
        stable_json_bytes(value)
    ).hexdigest()


def payload_hash(
    payload: bytes,
) -> str:
    """Return a SHA-256 digest for serialized payload bytes."""

    return hashlib.sha256(
        payload
    ).hexdigest()


def verify_hash(
    payload: bytes,
    expected: str,
) -> bool:
    """Verify serialized payload bytes against a digest."""

    actual = payload_hash(payload)

    return hashlib.compare_digest(
        actual,
        normalize_key(expected),
    )


def cache_key(
    key: Any,
    *,
    namespace: str = DEFAULT_NAMESPACE,
) -> tuple[str, str]:
    """
    Return the canonical cache key and SHA-256 key digest.

    The canonical key remains human-readable where possible. The digest is
    used for persistent file paths.
    """

    normalized_namespace = (
        normalize_namespace(namespace)
    )

    if isinstance(key, str):
        canonical_key = normalize_space(
            key
        )

    else:
        canonical_key = json.dumps(
            canonicalize_value(key),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    if not canonical_key:
        raise CacheKeyError(
            "Cache key cannot be empty."
        )

    digest = stable_hash(
        {
            "schema_version": (
                CACHE_SCHEMA_VERSION
            ),
            "prefix": CACHE_KEY_PREFIX,
            "namespace": (
                normalized_namespace
            ),
            "key": canonical_key,
        }
    )

    return (
        canonical_key,
        digest,
    )


def estimate_size_bytes(
    value: Any,
) -> int:
    """
    Estimate the memory cost of a cached value.

    Pickle is used only for estimating size. Failures fall back to the object's
    textual representation.
    """

    try:
        return len(
            pickle.dumps(
                value,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        )
    except Exception:
        return len(
            repr(value).encode(
                "utf-8",
                errors="replace",
            )
        )


def serialize_value(
    value: Any,
    *,
    serializer: str,
) -> bytes:
    """Serialize one cached value."""

    normalized = normalize_key(
        serializer
    )

    try:
        if normalized == SERIALIZER_JSON:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")

        if normalized == SERIALIZER_PICKLE:
            return pickle.dumps(
                value,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

        if normalized == SERIALIZER_RAW:
            if isinstance(value, bytes):
                return value

            if isinstance(value, bytearray):
                return bytes(value)

            if isinstance(value, memoryview):
                return value.tobytes()

            raise CacheSerializationError(
                "The raw serializer requires bytes, "
                "bytearray, or memoryview."
            )

    except CacheSerializationError:
        raise

    except Exception as error:
        raise CacheSerializationError(
            f"Unable to serialize cache value "
            f"using {normalized}: {error}"
        ) from error

    raise CacheSerializationError(
        f"Unsupported serializer: "
        f"{serializer!r}."
    )


def deserialize_value(
    payload: bytes,
    *,
    serializer: str,
) -> Any:
    """Decode one cached value."""

    normalized = normalize_key(
        serializer
    )

    try:
        if normalized == SERIALIZER_JSON:
            return json.loads(
                payload.decode("utf-8")
            )

        if normalized == SERIALIZER_PICKLE:
            return pickle.loads(
                payload
            )

        if normalized == SERIALIZER_RAW:
            return payload

    except Exception as error:
        raise CacheSerializationError(
            f"Unable to decode cache payload "
            f"using {normalized}: {error}"
        ) from error

    raise CacheSerializationError(
        f"Unsupported serializer: "
        f"{serializer!r}."
    )


def compress_payload(
    payload: bytes,
    *,
    compression: str,
    threshold: int,
) -> tuple[bytes, str]:
    """Compress payload bytes when configured and large enough."""

    normalized = normalize_key(
        compression
    )

    if (
        normalized == COMPRESSION_NONE
        or len(payload) < threshold
    ):
        return (
            payload,
            COMPRESSION_NONE,
        )

    if normalized == COMPRESSION_GZIP:
        return (
            gzip.compress(
                payload,
                compresslevel=6,
                mtime=0,
            ),
            COMPRESSION_GZIP,
        )

    raise CacheSerializationError(
        f"Unsupported compression: "
        f"{compression!r}."
    )


def decompress_payload(
    payload: bytes,
    *,
    compression: str,
) -> bytes:
    """Decompress persistent payload bytes."""

    normalized = normalize_key(
        compression
    )

    if normalized == COMPRESSION_NONE:
        return payload

    if normalized == COMPRESSION_GZIP:
        try:
            return gzip.decompress(
                payload
            )
        except OSError as error:
            raise CacheSerializationError(
                f"Unable to decompress gzip cache "
                f"payload: {error}"
            ) from error

    raise CacheSerializationError(
        f"Unsupported compression: "
        f"{compression!r}."
    )


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = DEFAULT_FILE_MODE,
) -> None:
    """Atomically write binary data."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=DEFAULT_DIRECTORY_MODE,
    )

    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(
                handle.name
            )

        try:
            os.chmod(
                temporary,
                mode,
            )
        except OSError:
            pass

        temporary.replace(path)

    finally:
        if (
            temporary is not None
            and temporary.exists()
        ):
            temporary.unlink(
                missing_ok=True
            )


def atomic_write_json(
    path: Path,
    value: Any,
) -> None:
    """Atomically write formatted UTF-8 JSON."""

    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    atomic_write_bytes(
        path,
        payload,
    )


def read_json(
    path: Path,
) -> dict[str, Any]:
    """Read a JSON object from disk."""

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except OSError as error:
        raise CacheError(
            f"Unable to read cache metadata "
            f"{path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise CacheIntegrityError(
            f"Invalid cache metadata JSON in "
            f"{path}: {error}"
        ) from error

    if not isinstance(value, dict):
        raise CacheIntegrityError(
            f"Cache metadata root is not an "
            f"object: {path}"
        )

    return value


class MemoryLRUCache(Generic[ValueType]):
    """
    Thread-safe in-memory LRU cache with entry and byte limits.
    """

    def __init__(
        self,
        *,
        maximum_entries: int = (
            DEFAULT_MEMORY_MAX_ENTRIES
        ),
        maximum_bytes: int = (
            DEFAULT_MEMORY_MAX_BYTES
        ),
        statistics: CacheStatistics | None = None,
    ) -> None:
        self.maximum_entries = max(
            0,
            int(maximum_entries),
        )

        self.maximum_bytes = max(
            0,
            int(maximum_bytes),
        )

        self.statistics = (
            statistics
            if statistics is not None
            else CacheStatistics()
        )

        self._items: OrderedDict[
            tuple[str, str],
            MemoryCacheItem[ValueType],
        ] = OrderedDict()

        self._bytes = 0
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(
                self._items
            )

    @property
    def size_bytes(self) -> int:
        """Return estimated in-memory cache size."""

        with self._lock:
            return self._bytes

    def get(
        self,
        namespace: str,
        key_hash_value: str,
        *,
        allow_stale: bool = False,
        refresh_on_access: bool = True,
    ) -> CacheEntry[ValueType] | None:
        """Read one in-memory entry."""

        cache_identity = (
            normalize_namespace(
                namespace
            ),
            normalize_key(
                key_hash_value
            ),
        )

        with self._lock:
            item = self._items.get(
                cache_identity
            )

            if item is None:
                self.statistics.memory_misses += 1
                return None

            if item.metadata.dead:
                self._delete_unlocked(
                    cache_identity
                )

                self.statistics.memory_misses += 1
                self.statistics.expirations += 1

                return None

            if (
                item.metadata.expired
                and not allow_stale
            ):
                self.statistics.memory_misses += 1
                return None

            if refresh_on_access:
                item.metadata.touch()

            self._items.move_to_end(
                cache_identity
            )

            self.statistics.memory_hits += 1

            if item.metadata.expired:
                self.statistics.stale_hits += 1

            return CacheEntry(
                value=item.value,
                metadata=item.metadata,
                layer="memory",
            )

    def set(
        self,
        namespace: str,
        key_hash_value: str,
        value: ValueType,
        metadata: CacheEntryMetadata,
    ) -> None:
        """Store one in-memory entry."""

        if (
            self.maximum_entries == 0
            or self.maximum_bytes == 0
        ):
            return

        cache_identity = (
            normalize_namespace(
                namespace
            ),
            normalize_key(
                key_hash_value
            ),
        )

        estimated_bytes = (
            estimate_size_bytes(value)
            + estimate_size_bytes(
                metadata.to_dict()
            )
        )

        with self._lock:
            existing = self._items.pop(
                cache_identity,
                None,
            )

            if existing is not None:
                self._bytes -= (
                    existing.estimated_bytes
                )

            self._items[
                cache_identity
            ] = MemoryCacheItem(
                value=value,
                metadata=metadata,
                estimated_bytes=(
                    estimated_bytes
                ),
            )

            self._bytes += estimated_bytes

            self._evict_unlocked()

    def delete(
        self,
        namespace: str,
        key_hash_value: str,
    ) -> bool:
        """Delete one in-memory entry."""

        cache_identity = (
            normalize_namespace(
                namespace
            ),
            normalize_key(
                key_hash_value
            ),
        )

        with self._lock:
            return self._delete_unlocked(
                cache_identity
            )

    def clear(
        self,
        *,
        namespace: str | None = None,
    ) -> int:
        """Clear all entries or one namespace."""

        with self._lock:
            if namespace is None:
                count = len(
                    self._items
                )

                self._items.clear()
                self._bytes = 0

                return count

            normalized_namespace = (
                normalize_namespace(
                    namespace
                )
            )

            identities = [
                identity
                for identity
                in self._items
                if identity[0]
                == normalized_namespace
            ]

            for identity in identities:
                self._delete_unlocked(
                    identity
                )

            return len(identities)

    def prune(self) -> int:
        """Remove expired entries."""

        removed = 0

        with self._lock:
            for identity in list(
                self._items
            ):
                item = self._items[
                    identity
                ]

                if item.metadata.dead:
                    self._delete_unlocked(
                        identity
                    )

                    self.statistics.expirations += 1
                    removed += 1

        return removed

    def keys(
        self,
        *,
        namespace: str | None = None,
    ) -> list[tuple[str, str]]:
        """Return cached namespace/hash identities."""

        with self._lock:
            if namespace is None:
                return list(
                    self._items.keys()
                )

            normalized_namespace = (
                normalize_namespace(
                    namespace
                )
            )

            return [
                identity
                for identity
                in self._items.keys()
                if identity[0]
                == normalized_namespace
            ]

    def metadata(
        self,
        *,
        namespace: str | None = None,
    ) -> list[CacheEntryMetadata]:
        """Return metadata for cached entries."""

        with self._lock:
            values = []

            for (
                entry_namespace,
                _entry_hash,
            ), item in self._items.items():
                if (
                    namespace is not None
                    and entry_namespace
                    != normalize_namespace(
                        namespace
                    )
                ):
                    continue

                values.append(
                    item.metadata
                )

            return values

    def _delete_unlocked(
        self,
        identity: tuple[str, str],
    ) -> bool:
        """Delete one entry while the cache lock is held."""

        item = self._items.pop(
            identity,
            None,
        )

        if item is None:
            return False

        self._bytes = max(
            0,
            self._bytes
            - item.estimated_bytes,
        )

        self.statistics.deletes += 1

        return True

    def _evict_unlocked(self) -> None:
        """Evict least-recently-used entries until limits are met."""

        while self._items and (
            len(self._items)
            > self.maximum_entries
            or self._bytes
            > self.maximum_bytes
        ):
            _identity, item = (
                self._items.popitem(
                    last=False
                )
            )

            self._bytes = max(
                0,
                self._bytes
                - item.estimated_bytes,
            )

            self.statistics.evictions += 1


class CacheFileLock:
    """
    Exclusive filesystem lock used for one persistent cache entry.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = (
            DEFAULT_LOCK_TIMEOUT_SECONDS
        ),
        poll_seconds: float = (
            DEFAULT_LOCK_POLL_SECONDS
        ),
        stale_seconds: float = (
            DEFAULT_STALE_LOCK_SECONDS
        ),
        statistics: CacheStatistics | None = None,
    ) -> None:
        self.path = Path(path)

        self.timeout_seconds = max(
            0.0,
            float(timeout_seconds),
        )

        self.poll_seconds = max(
            0.01,
            float(poll_seconds),
        )

        self.stale_seconds = max(
            1.0,
            float(stale_seconds),
        )

        self.statistics = statistics

        self._file_descriptor: int | None = None
        self._depth = 0
        self._thread_lock = threading.RLock()

    def __enter__(
        self,
    ) -> CacheFileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.release()

    def acquire(self) -> None:
        """Acquire the lock."""

        with self._thread_lock:
            if self._depth > 0:
                self._depth += 1
                return

            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
                mode=DEFAULT_DIRECTORY_MODE,
            )

            deadline = (
                time.monotonic()
                + self.timeout_seconds
            )

            while True:
                self._remove_stale_lock()

                try:
                    file_descriptor = os.open(
                        self.path,
                        (
                            os.O_CREAT
                            | os.O_EXCL
                            | os.O_WRONLY
                        ),
                        DEFAULT_FILE_MODE,
                    )

                except FileExistsError:
                    if (
                        time.monotonic()
                        >= deadline
                    ):
                        if self.statistics:
                            self.statistics.lock_failures += 1

                        raise CacheLockError(
                            "Timed out waiting for "
                            f"cache lock: {self.path}"
                        )

                    time.sleep(
                        self.poll_seconds
                    )

                    continue

                except OSError as error:
                    if self.statistics:
                        self.statistics.lock_failures += 1

                    raise CacheLockError(
                        "Unable to acquire cache "
                        f"lock {self.path}: {error}"
                    ) from error

                lock_payload = {
                    "pid": os.getpid(),
                    "thread_id": (
                        threading.get_ident()
                    ),
                    "created_at": utc_now(),
                    "created_epoch": time.time(),
                }

                os.write(
                    file_descriptor,
                    json.dumps(
                        lock_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )

                os.fsync(
                    file_descriptor
                )

                self._file_descriptor = (
                    file_descriptor
                )

                self._depth = 1
                return

    def release(self) -> None:
        """Release the lock."""

        with self._thread_lock:
            if self._depth == 0:
                return

            self._depth -= 1

            if self._depth > 0:
                return

            try:
                if (
                    self._file_descriptor
                    is not None
                ):
                    os.close(
                        self._file_descriptor
                    )

            finally:
                self._file_descriptor = None

                try:
                    self.path.unlink(
                        missing_ok=True
                    )
                except OSError:
                    pass

    def _remove_stale_lock(self) -> None:
        """Remove a stale cache lock."""

        if not self.path.exists():
            return

        try:
            age = (
                time.time()
                - self.path.stat().st_mtime
            )
        except OSError:
            return

        if age < self.stale_seconds:
            return

        try:
            self.path.unlink(
                missing_ok=True
            )
        except OSError:
            return

      class PersistentDiskCache:
    """
    Persistent namespace-aware disk cache.

    Each entry is stored beneath:

        <root>/<namespace>/<aa>/<bb>/<key_hash>/

    containing:

        metadata.json
        payload.cache

    or:

        payload.cache.gz

    The first four hash characters distribute entries across subdirectories.
    """

    def __init__(
        self,
        root: Path,
        *,
        maximum_entries: int = (
            DEFAULT_DISK_MAX_ENTRIES
        ),
        maximum_bytes: int = (
            DEFAULT_DISK_MAX_BYTES
        ),
        lock_timeout_seconds: float = (
            DEFAULT_LOCK_TIMEOUT_SECONDS
        ),
        lock_poll_seconds: float = (
            DEFAULT_LOCK_POLL_SECONDS
        ),
        stale_lock_seconds: float = (
            DEFAULT_STALE_LOCK_SECONDS
        ),
        statistics: CacheStatistics | None = None,
    ) -> None:
        self.root = Path(root)

        self.maximum_entries = max(
            0,
            int(maximum_entries),
        )

        self.maximum_bytes = max(
            0,
            int(maximum_bytes),
        )

        self.lock_timeout_seconds = max(
            0.0,
            float(lock_timeout_seconds),
        )

        self.lock_poll_seconds = max(
            0.01,
            float(lock_poll_seconds),
        )

        self.stale_lock_seconds = max(
            1.0,
            float(stale_lock_seconds),
        )

        self.statistics = (
            statistics
            if statistics is not None
            else CacheStatistics()
        )

        self.root.mkdir(
            parents=True,
            exist_ok=True,
            mode=DEFAULT_DIRECTORY_MODE,
        )

        self._thread_lock = (
            threading.RLock()
        )

    def paths(
        self,
        namespace: str,
        key_hash_value: str,
        *,
        compression: str = (
            COMPRESSION_NONE
        ),
    ) -> CachePaths:
        """Return persistent paths for one cache entry."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        normalized_hash = normalize_key(
            key_hash_value
        )

        if (
            len(normalized_hash) < 4
            or any(
                character
                not in "0123456789abcdef"
                for character
                in normalized_hash
            )
        ):
            raise CacheKeyError(
                "Persistent cache key hash must be "
                "a lowercase hexadecimal digest."
            )

        namespace_root = (
            self.root
            / normalized_namespace
        )

        entry_root = (
            namespace_root
            / normalized_hash[:2]
            / normalized_hash[2:4]
            / normalized_hash
        )

        normalized_compression = (
            normalize_key(
                compression
            )
        )

        if (
            normalized_compression
            == COMPRESSION_GZIP
        ):
            payload_name = (
                "payload"
                + CACHE_COMPRESSED_SUFFIX
            )
        else:
            payload_name = (
                "payload"
                + CACHE_FILE_SUFFIX
            )

        return CachePaths(
            namespace_root=namespace_root,
            entry_root=entry_root,
            payload_path=(
                entry_root
                / payload_name
            ),
            metadata_path=(
                entry_root
                / "metadata.json"
            ),
            lock_path=(
                entry_root
                / (
                    normalized_hash
                    + CACHE_LOCK_SUFFIX
                )
            ),
        )

    def set(
        self,
        *,
        namespace: str,
        key: str,
        key_hash_value: str,
        value: Any,
        policy: CachePolicy,
        tags: Iterable[str] | None = None,
        source: str = "",
        content_type: str = "",
        etag: str = "",
        last_modified: str = "",
        status_code: int | None = None,
        extra: Mapping[str, Any] | None = None,
        created_at: float | None = None,
    ) -> CacheEntryMetadata:
        """Serialize and persist one cache entry."""

        if not policy.disk:
            raise CacheConfigurationError(
                "Persistent cache write requested "
                "with disk caching disabled."
            )

        if (
            value is None
            and not policy.cache_none
        ):
            raise CacheSerializationError(
                "None values are disabled by "
                "the active cache policy."
            )

        serialized = serialize_value(
            value,
            serializer=policy.serializer,
        )

        digest = payload_hash(
            serialized
        )

        stored_payload, compression = (
            compress_payload(
                serialized,
                compression=(
                    policy.compression
                ),
                threshold=(
                    policy
                    .compress_above_bytes
                ),
            )
        )

        current_time = time.time()

        creation_time = (
            float(created_at)
            if created_at is not None
            else current_time
        )

        expires_at = (
            creation_time
            + policy.ttl_seconds
            if policy.ttl_seconds > 0
            else None
        )

        stale_until = (
            (
                expires_at
                + policy.stale_ttl_seconds
            )
            if (
                expires_at is not None
                and policy.stale_ttl_seconds
                > 0
            )
            else expires_at
        )

        metadata = CacheEntryMetadata(
            schema_version=(
                CACHE_SCHEMA_VERSION
            ),
            namespace=normalize_namespace(
                namespace
            ),
            key=key,
            key_hash=normalize_key(
                key_hash_value
            ),
            created_at=creation_time,
            updated_at=current_time,
            accessed_at=current_time,
            expires_at=expires_at,
            stale_until=stale_until,
            serializer=policy.serializer,
            compression=compression,
            payload_hash=digest,
            payload_bytes=len(
                serialized
            ),
            stored_bytes=len(
                stored_payload
            ),
            hits=0,
            tags=sorted(
                {
                    normalize_key(tag)
                    for tag in (
                        tags or []
                    )
                    if normalize_key(tag)
                }
            ),
            source=normalize_space(
                source
            ),
            content_type=normalize_space(
                content_type
            ),
            etag=normalize_space(
                etag
            ),
            last_modified=normalize_space(
                last_modified
            ),
            status_code=(
                int(status_code)
                if status_code is not None
                else None
            ),
            extra=dict(
                extra or {}
            ),
        )

        paths = self.paths(
            metadata.namespace,
            metadata.key_hash,
            compression=(
                metadata.compression
            ),
        )

        alternate_paths = self.paths(
            metadata.namespace,
            metadata.key_hash,
            compression=(
                COMPRESSION_GZIP
                if metadata.compression
                == COMPRESSION_NONE
                else COMPRESSION_NONE
            ),
        )

        lock = CacheFileLock(
            paths.lock_path,
            timeout_seconds=(
                self.lock_timeout_seconds
            ),
            poll_seconds=(
                self.lock_poll_seconds
            ),
            stale_seconds=(
                self.stale_lock_seconds
            ),
            statistics=self.statistics,
        )

        with self._thread_lock:
            with lock:
                paths.entry_root.mkdir(
                    parents=True,
                    exist_ok=True,
                    mode=(
                        DEFAULT_DIRECTORY_MODE
                    ),
                )

                atomic_write_bytes(
                    paths.payload_path,
                    stored_payload,
                )

                atomic_write_json(
                    paths.metadata_path,
                    metadata.to_dict(),
                )

                if (
                    alternate_paths.payload_path
                    != paths.payload_path
                ):
                    alternate_paths.payload_path.unlink(
                        missing_ok=True
                    )

        self.statistics.writes += 1
        self.statistics.bytes_written += len(
            stored_payload
        )

        return metadata

    def get(
        self,
        namespace: str,
        key_hash_value: str,
        *,
        allow_stale: bool = False,
        verify_payload: bool = True,
        touch: bool = True,
    ) -> CacheEntry[Any] | None:
        """Read and decode one persistent cache entry."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        normalized_hash = normalize_key(
            key_hash_value
        )

        metadata_path = (
            self._find_metadata_path(
                normalized_namespace,
                normalized_hash,
            )
        )

        if metadata_path is None:
            self.statistics.disk_misses += 1
            return None

        try:
            metadata = (
                CacheEntryMetadata
                .from_dict(
                    read_json(
                        metadata_path
                    )
                )
            )

        except (
            CacheError,
            ValueError,
        ):
            self.statistics.disk_misses += 1
            self.statistics.integrity_failures += 1
            return None

        if (
            metadata.namespace
            != normalized_namespace
            or metadata.key_hash
            != normalized_hash
        ):
            self.statistics.disk_misses += 1
            self.statistics.integrity_failures += 1
            return None

        if metadata.dead:
            self.delete(
                normalized_namespace,
                normalized_hash,
            )

            self.statistics.disk_misses += 1
            self.statistics.expirations += 1

            return None

        if (
            metadata.expired
            and not allow_stale
        ):
            self.statistics.disk_misses += 1
            return None

        paths = self.paths(
            normalized_namespace,
            normalized_hash,
            compression=(
                metadata.compression
            ),
        )

        if not paths.payload_path.is_file():
            self.statistics.disk_misses += 1
            self.statistics.integrity_failures += 1
            return None

        try:
            stored_payload = (
                paths.payload_path.read_bytes()
            )

        except OSError:
            self.statistics.disk_misses += 1
            return None

        self.statistics.bytes_read += len(
            stored_payload
        )

        try:
            serialized = decompress_payload(
                stored_payload,
                compression=(
                    metadata.compression
                ),
            )

            if (
                verify_payload
                and metadata.payload_hash
                and not verify_hash(
                    serialized,
                    metadata.payload_hash,
                )
            ):
                raise CacheIntegrityError(
                    "Persistent cache payload hash "
                    "does not match metadata."
                )

            value = deserialize_value(
                serialized,
                serializer=(
                    metadata.serializer
                ),
            )

        except (
            CacheSerializationError,
            CacheIntegrityError,
        ):
            self.statistics.disk_misses += 1
            self.statistics.integrity_failures += 1

            return None

        if touch:
            metadata.touch()

            try:
                atomic_write_json(
                    paths.metadata_path,
                    metadata.to_dict(),
                )
            except OSError:
                pass

        self.statistics.disk_hits += 1

        if metadata.expired:
            self.statistics.stale_hits += 1

        return CacheEntry(
            value=value,
            metadata=metadata,
            layer="disk",
        )

    def metadata(
        self,
        namespace: str,
        key_hash_value: str,
    ) -> CacheEntryMetadata | None:
        """Read metadata without decoding the payload."""

        metadata_path = (
            self._find_metadata_path(
                normalize_namespace(
                    namespace
                ),
                normalize_key(
                    key_hash_value
                ),
            )
        )

        if metadata_path is None:
            return None

        try:
            return (
                CacheEntryMetadata
                .from_dict(
                    read_json(
                        metadata_path
                    )
                )
            )

        except (
            CacheError,
            ValueError,
        ):
            return None

    def exists(
        self,
        namespace: str,
        key_hash_value: str,
        *,
        include_expired: bool = False,
        include_stale: bool = True,
    ) -> bool:
        """Return whether a persistent entry exists."""

        metadata = self.metadata(
            namespace,
            key_hash_value,
        )

        if metadata is None:
            return False

        if include_expired:
            return True

        if metadata.dead:
            return False

        if metadata.expired:
            return include_stale

        return True

    def delete(
        self,
        namespace: str,
        key_hash_value: str,
    ) -> bool:
        """Delete one persistent cache entry."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        normalized_hash = normalize_key(
            key_hash_value
        )

        uncompressed = self.paths(
            normalized_namespace,
            normalized_hash,
            compression=COMPRESSION_NONE,
        )

        compressed = self.paths(
            normalized_namespace,
            normalized_hash,
            compression=COMPRESSION_GZIP,
        )

        entry_root = (
            uncompressed.entry_root
        )

        if not entry_root.exists():
            return False

        lock = CacheFileLock(
            uncompressed.lock_path,
            timeout_seconds=(
                self.lock_timeout_seconds
            ),
            poll_seconds=(
                self.lock_poll_seconds
            ),
            stale_seconds=(
                self.stale_lock_seconds
            ),
            statistics=self.statistics,
        )

        deleted = False

        with self._thread_lock:
            try:
                with lock:
                    for path in (
                        uncompressed.payload_path,
                        compressed.payload_path,
                        uncompressed.metadata_path,
                    ):
                        if path.exists():
                            path.unlink(
                                missing_ok=True
                            )
                            deleted = True

                    try:
                        entry_root.rmdir()
                    except OSError:
                        pass

                    self._remove_empty_parents(
                        entry_root.parent,
                        stop_at=(
                            self.root
                            / normalized_namespace
                        ),
                    )

            except CacheLockError:
                return False

        if deleted:
            self.statistics.deletes += 1

        return deleted

    def clear(
        self,
        *,
        namespace: str | None = None,
    ) -> int:
        """Delete all persistent entries or one namespace."""

        target = (
            self.root
            / normalize_namespace(
                namespace
            )
            if namespace is not None
            else self.root
        )

        if not target.exists():
            return 0

        count = sum(
            1
            for _metadata_path
            in target.rglob(
                "metadata.json"
            )
        )

        if namespace is None:
            for child in list(
                self.root.iterdir()
            ):
                if child.is_dir():
                    shutil.rmtree(
                        child,
                        ignore_errors=True,
                    )
                else:
                    child.unlink(
                        missing_ok=True
                    )

        else:
            shutil.rmtree(
                target,
                ignore_errors=True,
            )

        self.statistics.deletes += count

        return count

    def iter_metadata(
        self,
        *,
        namespace: str | None = None,
    ) -> Iterator[
        CacheEntryMetadata
    ]:
        """Iterate persistent cache metadata."""

        search_root = (
            self.root
            / normalize_namespace(
                namespace
            )
            if namespace is not None
            else self.root
        )

        if not search_root.exists():
            return

        for metadata_path in sorted(
            search_root.rglob(
                "metadata.json"
            )
        ):
            try:
                yield (
                    CacheEntryMetadata
                    .from_dict(
                        read_json(
                            metadata_path
                        )
                    )
                )

            except (
                CacheError,
                ValueError,
            ):
                continue

    def iter_entries(
        self,
        *,
        namespace: str | None = None,
        allow_stale: bool = True,
        verify_payload: bool = True,
    ) -> Iterator[CacheEntry[Any]]:
        """Iterate decoded persistent cache entries."""

        for metadata in self.iter_metadata(
            namespace=namespace
        ):
            entry = self.get(
                metadata.namespace,
                metadata.key_hash,
                allow_stale=allow_stale,
                verify_payload=(
                    verify_payload
                ),
                touch=False,
            )

            if entry is not None:
                yield entry

    def find_by_tag(
        self,
        tag: str,
        *,
        namespace: str | None = None,
    ) -> list[CacheEntryMetadata]:
        """Return entries carrying a normalized tag."""

        normalized_tag = normalize_key(
            tag
        )

        if not normalized_tag:
            return []

        return [
            metadata
            for metadata
            in self.iter_metadata(
                namespace=namespace
            )
            if normalized_tag
            in metadata.tags
        ]

    def find_by_source(
        self,
        source: str,
        *,
        namespace: str | None = None,
    ) -> list[CacheEntryMetadata]:
        """Return entries matching a source string."""

        normalized_source = (
            normalize_space(source)
        )

        if not normalized_source:
            return []

        return [
            metadata
            for metadata
            in self.iter_metadata(
                namespace=namespace
            )
            if metadata.source
            == normalized_source
        ]

    def usage(
        self,
        *,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Return persistent cache usage information."""

        search_root = (
            self.root
            / normalize_namespace(
                namespace
            )
            if namespace is not None
            else self.root
        )

        if not search_root.exists():
            return {
                "entries": 0,
                "payload_bytes": 0,
                "stored_bytes": 0,
                "filesystem_bytes": 0,
                "expired": 0,
                "stale": 0,
                "namespaces": 0,
            }

        entries = 0
        payload_bytes = 0
        stored_bytes = 0
        filesystem_bytes = 0
        expired = 0
        stale = 0
        namespaces: set[str] = set()

        for path in search_root.rglob("*"):
            if not path.is_file():
                continue

            try:
                filesystem_bytes += (
                    path.stat().st_size
                )
            except OSError:
                continue

        for metadata in self.iter_metadata(
            namespace=namespace
        ):
            entries += 1
            payload_bytes += (
                metadata.payload_bytes
            )
            stored_bytes += (
                metadata.stored_bytes
            )
            namespaces.add(
                metadata.namespace
            )

            if metadata.dead:
                expired += 1

            elif metadata.expired:
                stale += 1

        return {
            "entries": entries,
            "payload_bytes": (
                payload_bytes
            ),
            "stored_bytes": (
                stored_bytes
            ),
            "filesystem_bytes": (
                filesystem_bytes
            ),
            "expired": expired,
            "stale": stale,
            "namespaces": len(
                namespaces
            ),
        }

    def prune(
        self,
        *,
        namespace: str | None = None,
        delete_stale: bool = False,
        maximum_entries: int | None = None,
        maximum_bytes: int | None = None,
    ) -> CachePruneResult:
        """
        Remove expired, orphaned, and least-recently-used entries.

        Entries are first removed when dead. When delete_stale=True, entries
        inside the stale grace period are removed as well. Remaining entries
        are ordered by accessed_at and removed until configured limits are met.
        """

        maximum_entry_count = (
            self.maximum_entries
            if maximum_entries is None
            else max(
                0,
                int(maximum_entries),
            )
        )

        maximum_size_bytes = (
            self.maximum_bytes
            if maximum_bytes is None
            else max(
                0,
                int(maximum_bytes),
            )
        )

        result = CachePruneResult(
            scanned=0,
            deleted=0,
            expired_deleted=0,
            oversized_deleted=0,
            orphaned_deleted=0,
            bytes_freed=0,
        )

        records: list[
            tuple[
                CacheEntryMetadata,
                Path,
                int,
            ]
        ] = []

        search_root = (
            self.root
            / normalize_namespace(
                namespace
            )
            if namespace is not None
            else self.root
        )

        if not search_root.exists():
            return result

        metadata_paths = sorted(
            search_root.rglob(
                "metadata.json"
            )
        )

        for metadata_path in metadata_paths:
            result.scanned += 1

            entry_root = (
                metadata_path.parent
            )

            try:
                metadata = (
                    CacheEntryMetadata
                    .from_dict(
                        read_json(
                            metadata_path
                        )
                    )
                )

            except (
                CacheError,
                ValueError,
            ) as error:
                size = (
                    self._directory_size(
                        entry_root
                    )
                )

                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted += 1
                result.orphaned_deleted += 1
                result.bytes_freed += size
                result.errors.append(
                    str(error)
                )

                continue

            paths = self.paths(
                metadata.namespace,
                metadata.key_hash,
                compression=(
                    metadata.compression
                ),
            )

            if not paths.payload_path.exists():
                size = (
                    self._directory_size(
                        entry_root
                    )
                )

                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted += 1
                result.orphaned_deleted += 1
                result.bytes_freed += size

                continue

            size = self._directory_size(
                entry_root
            )

            should_expire = (
                metadata.dead
                or (
                    delete_stale
                    and metadata.expired
                )
            )

            if should_expire:
                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted += 1
                result.expired_deleted += 1
                result.bytes_freed += size
                self.statistics.expirations += 1

                continue

            records.append(
                (
                    metadata,
                    entry_root,
                    size,
                )
            )

        orphan_payloads = self._find_orphan_payloads(
            search_root
        )

        for payload_path in orphan_payloads:
            try:
                size = (
                    payload_path.stat().st_size
                )
            except OSError:
                size = 0

            payload_path.unlink(
                missing_ok=True
            )

            result.deleted += 1
            result.orphaned_deleted += 1
            result.bytes_freed += size

        current_entries = len(
            records
        )

        current_bytes = sum(
            size
            for _metadata, _path, size
            in records
        )

        records.sort(
            key=lambda item: (
                item[0].accessed_at,
                item[0].created_at,
                item[0].key_hash,
            )
        )

        while records and (
            (
                maximum_entry_count > 0
                and current_entries
                > maximum_entry_count
            )
            or (
                maximum_size_bytes > 0
                and current_bytes
                > maximum_size_bytes
            )
        ):
            (
                _metadata,
                entry_root,
                size,
            ) = records.pop(0)

            shutil.rmtree(
                entry_root,
                ignore_errors=True,
            )

            current_entries -= 1
            current_bytes = max(
                0,
                current_bytes - size,
            )

            result.deleted += 1
            result.oversized_deleted += 1
            result.bytes_freed += size
            self.statistics.evictions += 1

        self.statistics.prunes += 1
        self.statistics.deletes += (
            result.deleted
        )

        return result

    def verify(
        self,
        *,
        namespace: str | None = None,
        verify_payloads: bool = True,
    ) -> CacheVerification:
        """Verify the persistent cache tree."""

        result = CacheVerification(
            valid=True,
            entries_checked=0,
            valid_entries=0,
            expired_entries=0,
            corrupt_entries=0,
            orphaned_payloads=0,
            orphaned_metadata=0,
        )

        search_root = (
            self.root
            / normalize_namespace(
                namespace
            )
            if namespace is not None
            else self.root
        )

        if not search_root.exists():
            return result

        metadata_paths = sorted(
            search_root.rglob(
                "metadata.json"
            )
        )

        known_payload_paths: set[
            Path
        ] = set()

        for metadata_path in metadata_paths:
            result.entries_checked += 1

            try:
                metadata = (
                    CacheEntryMetadata
                    .from_dict(
                        read_json(
                            metadata_path
                        )
                    )
                )

            except (
                CacheError,
                ValueError,
            ) as error:
                result.corrupt_entries += 1
                result.errors.append(
                    str(error)
                )
                continue

            if (
                metadata.schema_version
                != CACHE_SCHEMA_VERSION
            ):
                result.corrupt_entries += 1
                result.errors.append(
                    "Cache schema version mismatch "
                    f"for {metadata.key_hash}."
                )
                continue

            if not metadata.key_hash:
                result.corrupt_entries += 1
                result.errors.append(
                    "Cache metadata contains an "
                    "empty key hash."
                )
                continue

            paths = self.paths(
                metadata.namespace,
                metadata.key_hash,
                compression=(
                    metadata.compression
                ),
            )

            known_payload_paths.add(
                paths.payload_path
            )

            if not paths.payload_path.is_file():
                result.orphaned_metadata += 1
                result.errors.append(
                    "Cache metadata has no payload: "
                    f"{metadata.key_hash}."
                )
                continue

            if metadata.dead:
                result.expired_entries += 1

            if verify_payloads:
                try:
                    stored_payload = (
                        paths.payload_path
                        .read_bytes()
                    )

                    serialized = (
                        decompress_payload(
                            stored_payload,
                            compression=(
                                metadata.compression
                            ),
                        )
                    )

                    if (
                        metadata.payload_hash
                        and not verify_hash(
                            serialized,
                            metadata.payload_hash,
                        )
                    ):
                        raise (
                            CacheIntegrityError(
                                "payload hash mismatch"
                            )
                        )

                    deserialize_value(
                        serialized,
                        serializer=(
                            metadata.serializer
                        ),
                    )

                except (
                    OSError,
                    CacheError,
                ) as error:
                    result.corrupt_entries += 1
                    result.errors.append(
                        "Invalid cache payload "
                        f"{metadata.key_hash}: "
                        f"{error}"
                    )
                    continue

            result.valid_entries += 1

        for payload_path in (
            self._all_payload_paths(
                search_root
            )
        ):
            if payload_path not in (
                known_payload_paths
            ):
                result.orphaned_payloads += 1
                result.warnings.append(
                    "Orphaned cache payload: "
                    f"{payload_path.as_posix()}."
                )

        result.valid = not result.errors

        return result

    def touch(
        self,
        namespace: str,
        key_hash_value: str,
        *,
        extend_ttl_seconds: int | None = None,
        extend_stale_seconds: int | None = None,
    ) -> bool:
        """Update access time and optionally extend expiration."""

        metadata = self.metadata(
            namespace,
            key_hash_value,
        )

        if metadata is None:
            return False

        metadata.touch()
        metadata.updated_at = time.time()

        if extend_ttl_seconds is not None:
            ttl = max(
                0,
                int(
                    extend_ttl_seconds
                ),
            )

            metadata.expires_at = (
                metadata.updated_at + ttl
                if ttl > 0
                else None
            )

        if (
            metadata.expires_at
            is not None
            and extend_stale_seconds
            is not None
        ):
            metadata.stale_until = (
                metadata.expires_at
                + max(
                    0,
                    int(
                        extend_stale_seconds
                    ),
                )
            )

        paths = self.paths(
            metadata.namespace,
            metadata.key_hash,
            compression=(
                metadata.compression
            ),
        )

        try:
            atomic_write_json(
                paths.metadata_path,
                metadata.to_dict(),
            )
        except OSError:
            return False

        return True

    def update_metadata(
        self,
        namespace: str,
        key_hash_value: str,
        updates: Mapping[str, Any],
    ) -> CacheEntryMetadata | None:
        """Apply supported metadata updates to one entry."""

        metadata = self.metadata(
            namespace,
            key_hash_value,
        )

        if metadata is None:
            return None

        allowed_fields = {
            "expires_at",
            "stale_until",
            "tags",
            "source",
            "content_type",
            "etag",
            "last_modified",
            "status_code",
            "extra",
        }

        for field_name, value in (
            updates.items()
        ):
            if field_name not in (
                allowed_fields
            ):
                continue

            if field_name == "tags":
                metadata.tags = sorted(
                    {
                        normalize_key(tag)
                        for tag in (
                            value or []
                        )
                        if normalize_key(tag)
                    }
                )

            elif field_name == "extra":
                metadata.extra = (
                    dict(value)
                    if isinstance(
                        value,
                        Mapping,
                    )
                    else {}
                )

            elif field_name in {
                "expires_at",
                "stale_until",
            }:
                setattr(
                    metadata,
                    field_name,
                    optional_float(value),
                )

            elif field_name == "status_code":
                metadata.status_code = (
                    optional_int(value)
                )

            else:
                setattr(
                    metadata,
                    field_name,
                    normalize_space(value),
                )

        metadata.updated_at = time.time()

        paths = self.paths(
            metadata.namespace,
            metadata.key_hash,
            compression=(
                metadata.compression
            ),
        )

        atomic_write_json(
            paths.metadata_path,
            metadata.to_dict(),
        )

        return metadata

    def _find_metadata_path(
        self,
        namespace: str,
        key_hash_value: str,
    ) -> Path | None:
        """Return the metadata path when an entry exists."""

        paths = self.paths(
            namespace,
            key_hash_value,
            compression=COMPRESSION_NONE,
        )

        if paths.metadata_path.is_file():
            return paths.metadata_path

        return None

    @staticmethod
    def _directory_size(
        path: Path,
    ) -> int:
        """Return total bytes beneath a directory."""

        total = 0

        if not path.exists():
            return total

        for child in path.rglob("*"):
            if not child.is_file():
                continue

            try:
                total += child.stat().st_size
            except OSError:
                continue

        return total

    @staticmethod
    def _remove_empty_parents(
        path: Path,
        *,
        stop_at: Path,
    ) -> None:
        """Remove empty cache shard directories."""

        current = path

        while (
            current != stop_at
            and current.parent != current
        ):
            try:
                current.rmdir()
            except OSError:
                break

            current = current.parent

        try:
            stop_at.rmdir()
        except OSError:
            pass

    @staticmethod
    def _all_payload_paths(
        root: Path,
    ) -> Iterator[Path]:
        """Iterate every persistent payload file."""

        if not root.exists():
            return

        for path in root.rglob(
            "payload*"
        ):
            if (
                path.is_file()
                and path.name
                in {
                    (
                        "payload"
                        + CACHE_FILE_SUFFIX
                    ),
                    (
                        "payload"
                        + CACHE_COMPRESSED_SUFFIX
                    ),
                }
            ):
                yield path

    @classmethod
    def _find_orphan_payloads(
        cls,
        root: Path,
    ) -> list[Path]:
        """Return payload files without metadata."""

        orphans: list[Path] = []

        for payload_path in (
            cls._all_payload_paths(
                root
            )
        ):
            metadata_path = (
                payload_path.parent
                / "metadata.json"
            )

            if not metadata_path.is_file():
                orphans.append(
                    payload_path
                )

        return orphans


class NamespaceCache(Generic[ValueType]):
    """
    Namespace-bound facade over CacheManager.

    The complete CacheManager implementation appears in Part 3.
    """

    def __init__(
        self,
        manager: CacheManager,
        namespace: str,
        *,
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager

        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else manager.policy_for(
                self.namespace
            )
        )

    def get(
        self,
        key: Any,
        default: ValueType | None = None,
        *,
        allow_stale: bool = False,
    ) -> ValueType | None:
        """Read a value from this namespace."""

        entry = self.manager.get_entry(
            key,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

        if entry is None:
            return default

        return entry.value

    def get_entry(
        self,
        key: Any,
        *,
        allow_stale: bool = False,
    ) -> CacheEntry[ValueType] | None:
        """Read a complete cache entry."""

        return self.manager.get_entry(
            key,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

    def set(
        self,
        key: Any,
        value: ValueType,
        *,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        tags: Iterable[str] | None = None,
        source: str = "",
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        """Write a value into this namespace."""

        return self.manager.set(
            key,
            value,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
            tags=tags,
            source=source,
            extra=extra,
        )

    def delete(
        self,
        key: Any,
    ) -> bool:
        """Delete one namespace entry."""

        return self.manager.delete(
            key,
            namespace=self.namespace,
        )

    def contains(
        self,
        key: Any,
        *,
        allow_stale: bool = False,
    ) -> bool:
        """Return whether one namespace entry exists."""

        return self.manager.contains(
            key,
            namespace=self.namespace,
            allow_stale=allow_stale,
        )

    def clear(self) -> int:
        """Clear this namespace."""

        return self.manager.clear(
            namespace=self.namespace
        )

    def get_or_set(
        self,
        key: Any,
        factory: Callable[
            [],
            ValueType,
        ],
        *,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        tags: Iterable[str] | None = None,
        source: str = "",
        extra: Mapping[str, Any] | None = None,
    ) -> ValueType:
        """Return a cached value or compute and store it."""

        return self.manager.get_or_set(
            key,
            factory,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
            tags=tags,
            source=source,
            extra=extra,
        )

class CacheManager:
    """
    Unified two-layer cache manager.

    Read order:

        memory
        disk
        factory/network/database fallback

    Successful disk reads may be promoted into memory. Writes may target
    either or both layers according to the active CachePolicy.
    """

    def __init__(
        self,
        root: Path,
        *,
        default_policy: CachePolicy | None = None,
        namespace_policies: Mapping[
            str,
            CachePolicy
            | Mapping[str, Any]
        ] | None = None,
        memory_maximum_entries: int = (
            DEFAULT_MEMORY_MAX_ENTRIES
        ),
        memory_maximum_bytes: int = (
            DEFAULT_MEMORY_MAX_BYTES
        ),
        disk_maximum_entries: int = (
            DEFAULT_DISK_MAX_ENTRIES
        ),
        disk_maximum_bytes: int = (
            DEFAULT_DISK_MAX_BYTES
        ),
        prune_interval_seconds: int = (
            DEFAULT_PRUNE_INTERVAL_SECONDS
        ),
        lock_timeout_seconds: float = (
            DEFAULT_LOCK_TIMEOUT_SECONDS
        ),
        lock_poll_seconds: float = (
            DEFAULT_LOCK_POLL_SECONDS
        ),
        stale_lock_seconds: float = (
            DEFAULT_STALE_LOCK_SECONDS
        ),
        auto_prune: bool = True,
    ) -> None:
        self.root = Path(root)

        self.root.mkdir(
            parents=True,
            exist_ok=True,
            mode=DEFAULT_DIRECTORY_MODE,
        )

        self.default_policy = (
            default_policy
            if default_policy is not None
            else CachePolicy()
        )

        self.namespace_policies: dict[
            str,
            CachePolicy,
        ] = {}

        self.statistics = (
            CacheStatistics()
        )

        self.memory = MemoryLRUCache[Any](
            maximum_entries=(
                memory_maximum_entries
            ),
            maximum_bytes=(
                memory_maximum_bytes
            ),
            statistics=self.statistics,
        )

        self.disk = PersistentDiskCache(
            self.root,
            maximum_entries=(
                disk_maximum_entries
            ),
            maximum_bytes=(
                disk_maximum_bytes
            ),
            lock_timeout_seconds=(
                lock_timeout_seconds
            ),
            lock_poll_seconds=(
                lock_poll_seconds
            ),
            stale_lock_seconds=(
                stale_lock_seconds
            ),
            statistics=self.statistics,
        )

        self.prune_interval_seconds = max(
            0,
            int(prune_interval_seconds),
        )

        self.auto_prune = bool(
            auto_prune
        )

        self._last_prune_at = 0.0
        self._manager_lock = (
            threading.RLock()
        )

        self._stampede_locks: dict[
            tuple[str, str],
            threading.Lock,
        ] = {}

        self._stampede_lock_guard = (
            threading.RLock()
        )

        if namespace_policies:
            for (
                namespace,
                policy,
            ) in namespace_policies.items():
                self.register_policy(
                    namespace,
                    policy,
                )

    def namespace(
        self,
        name: str,
        *,
        policy: CachePolicy | None = None,
    ) -> NamespaceCache[Any]:
        """Return a namespace-bound cache facade."""

        return NamespaceCache(
            self,
            name,
            policy=policy,
        )

    def register_policy(
        self,
        namespace: str,
        policy: CachePolicy
        | Mapping[str, Any],
    ) -> CachePolicy:
        """Register or replace one namespace policy."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        if isinstance(
            policy,
            CachePolicy,
        ):
            normalized_policy = policy

        elif isinstance(
            policy,
            Mapping,
        ):
            normalized_policy = (
                CachePolicy(
                    **dict(policy)
                )
            )

        else:
            raise TypeError(
                "Cache policy must be a "
                "CachePolicy or mapping."
            )

        self.namespace_policies[
            normalized_namespace
        ] = normalized_policy

        return normalized_policy

    def remove_policy(
        self,
        namespace: str,
    ) -> bool:
        """Remove one namespace-specific policy."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        return (
            self.namespace_policies.pop(
                normalized_namespace,
                None,
            )
            is not None
        )

    def policy_for(
        self,
        namespace: str,
    ) -> CachePolicy:
        """Return the active policy for one namespace."""

        return self.namespace_policies.get(
            normalize_namespace(
                namespace
            ),
            self.default_policy,
        )

    def get(
        self,
        key: Any,
        default: Any = None,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        policy: CachePolicy | None = None,
        allow_stale: bool = False,
    ) -> Any:
        """Return one cached value or a default."""

        entry = self.get_entry(
            key,
            namespace=namespace,
            policy=policy,
            allow_stale=allow_stale,
        )

        if entry is None:
            return default

        return entry.value

    def get_entry(
        self,
        key: Any,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        policy: CachePolicy | None = None,
        allow_stale: bool = False,
    ) -> CacheEntry[Any] | None:
        """Read one entry from memory or disk."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        active_policy = (
            policy
            if policy is not None
            else self.policy_for(
                normalized_namespace
            )
        )

        canonical_key, key_digest = (
            cache_key(
                key,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        self._maybe_prune()

        if active_policy.memory:
            memory_entry = self.memory.get(
                normalized_namespace,
                key_digest,
                allow_stale=allow_stale,
                refresh_on_access=(
                    active_policy
                    .refresh_on_access
                ),
            )

            if memory_entry is not None:
                return memory_entry

        if active_policy.disk:
            disk_entry = self.disk.get(
                normalized_namespace,
                key_digest,
                allow_stale=allow_stale,
                verify_payload=(
                    active_policy
                    .verify_hash
                ),
                touch=(
                    active_policy
                    .refresh_on_access
                ),
            )

            if disk_entry is not None:
                if active_policy.memory:
                    self.memory.set(
                        normalized_namespace,
                        key_digest,
                        disk_entry.value,
                        disk_entry.metadata,
                    )

                return disk_entry

        return None

    def set(
        self,
        key: Any,
        value: Any,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        policy: CachePolicy | None = None,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        tags: Iterable[str] | None = None,
        source: str = "",
        content_type: str = "",
        etag: str = "",
        last_modified: str = "",
        status_code: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        """Store one value according to the active policy."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        active_policy = self._effective_policy(
            policy
            if policy is not None
            else self.policy_for(
                normalized_namespace
            ),
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
        )

        if (
            value is None
            and not active_policy.cache_none
        ):
            return False

        canonical_key, key_digest = (
            cache_key(
                key,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        if (
            not active_policy.memory
            and not active_policy.disk
        ):
            return False

        metadata: (
            CacheEntryMetadata
            | None
        ) = None

        if active_policy.disk:
            metadata = self.disk.set(
                namespace=(
                    normalized_namespace
                ),
                key=canonical_key,
                key_hash_value=(
                    key_digest
                ),
                value=value,
                policy=active_policy,
                tags=tags,
                source=source,
                content_type=(
                    content_type
                ),
                etag=etag,
                last_modified=(
                    last_modified
                ),
                status_code=status_code,
                extra=extra,
            )

        if metadata is None:
            current_time = time.time()

            expires_at = (
                current_time
                + active_policy.ttl_seconds
                if active_policy.ttl_seconds
                > 0
                else None
            )

            stale_until = (
                (
                    expires_at
                    + active_policy
                    .stale_ttl_seconds
                )
                if (
                    expires_at is not None
                    and active_policy
                    .stale_ttl_seconds
                    > 0
                )
                else expires_at
            )

            metadata = (
                CacheEntryMetadata(
                    schema_version=(
                        CACHE_SCHEMA_VERSION
                    ),
                    namespace=(
                        normalized_namespace
                    ),
                    key=canonical_key,
                    key_hash=key_digest,
                    created_at=current_time,
                    updated_at=current_time,
                    accessed_at=current_time,
                    expires_at=expires_at,
                    stale_until=(
                        stale_until
                    ),
                    serializer=(
                        active_policy
                        .serializer
                    ),
                    compression=(
                        COMPRESSION_NONE
                    ),
                    payload_hash="",
                    payload_bytes=(
                        estimate_size_bytes(
                            value
                        )
                    ),
                    stored_bytes=0,
                    hits=0,
                    tags=sorted(
                        {
                            normalize_key(tag)
                            for tag in (
                                tags or []
                            )
                            if normalize_key(
                                tag
                            )
                        }
                    ),
                    source=normalize_space(
                        source
                    ),
                    content_type=(
                        normalize_space(
                            content_type
                        )
                    ),
                    etag=normalize_space(
                        etag
                    ),
                    last_modified=(
                        normalize_space(
                            last_modified
                        )
                    ),
                    status_code=(
                        int(status_code)
                        if status_code
                        is not None
                        else None
                    ),
                    extra=dict(
                        extra or {}
                    ),
                )
            )

        if active_policy.memory:
            self.memory.set(
                normalized_namespace,
                key_digest,
                value,
                metadata,
            )

        if not active_policy.disk:
            self.statistics.writes += 1

        self._maybe_prune()

        return True

    def contains(
        self,
        key: Any,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        allow_stale: bool = False,
        policy: CachePolicy | None = None,
    ) -> bool:
        """Return whether a usable entry exists."""

        return (
            self.get_entry(
                key,
                namespace=namespace,
                policy=policy,
                allow_stale=allow_stale,
            )
            is not None
        )

    def metadata(
        self,
        key: Any,
        *,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> CacheEntryMetadata | None:
        """Return metadata for one key."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        _canonical_key, key_digest = (
            cache_key(
                key,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        for metadata in self.memory.metadata(
            namespace=(
                normalized_namespace
            )
        ):
            if (
                metadata.key_hash
                == key_digest
            ):
                return metadata

        return self.disk.metadata(
            normalized_namespace,
            key_digest,
        )

    def delete(
        self,
        key: Any,
        *,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> bool:
        """Delete one key from all cache layers."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        _canonical_key, key_digest = (
            cache_key(
                key,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        memory_deleted = self.memory.delete(
            normalized_namespace,
            key_digest,
        )

        disk_deleted = self.disk.delete(
            normalized_namespace,
            key_digest,
        )

        return (
            memory_deleted
            or disk_deleted
        )

    def clear(
        self,
        *,
        namespace: str | None = None,
    ) -> int:
        """Clear one namespace or the entire cache."""

        memory_count = self.memory.clear(
            namespace=namespace
        )

        disk_count = self.disk.clear(
            namespace=namespace
        )

        return max(
            memory_count,
            disk_count,
        )

    def get_or_set(
        self,
        key: Any,
        factory: Callable[[], Any],
        *,
        namespace: str = DEFAULT_NAMESPACE,
        policy: CachePolicy | None = None,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        tags: Iterable[str] | None = None,
        source: str = "",
        content_type: str = "",
        etag: str = "",
        last_modified: str = "",
        status_code: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Return a cached value or compute and store it.

        A per-key in-process lock prevents duplicate concurrent factory calls.
        """

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        active_policy = (
            policy
            if policy is not None
            else self.policy_for(
                normalized_namespace
            )
        )

        entry = self.get_entry(
            key,
            namespace=(
                normalized_namespace
            ),
            policy=active_policy,
            allow_stale=allow_stale,
        )

        if entry is not None:
            return entry.value

        _canonical_key, key_digest = (
            cache_key(
                key,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        stampede_lock = (
            self._stampede_lock(
                normalized_namespace,
                key_digest,
            )
        )

        with stampede_lock:
            entry = self.get_entry(
                key,
                namespace=(
                    normalized_namespace
                ),
                policy=active_policy,
                allow_stale=allow_stale,
            )

            if entry is not None:
                return entry.value

            value = factory()

            self.set(
                key,
                value,
                namespace=(
                    normalized_namespace
                ),
                policy=active_policy,
                ttl_seconds=ttl_seconds,
                stale_ttl_seconds=(
                    stale_ttl_seconds
                ),
                tags=tags,
                source=source,
                content_type=(
                    content_type
                ),
                etag=etag,
                last_modified=(
                    last_modified
                ),
                status_code=status_code,
                extra=extra,
            )

            return value

    def invalidate_tag(
        self,
        tag: str,
        *,
        namespace: str | None = None,
    ) -> int:
        """Delete all entries carrying a tag."""

        normalized_tag = normalize_key(
            tag
        )

        if not normalized_tag:
            return 0

        metadata_entries = (
            self.disk.find_by_tag(
                normalized_tag,
                namespace=namespace,
            )
        )

        deleted = 0

        for metadata in metadata_entries:
            if self.delete_hash(
                metadata.key_hash,
                namespace=(
                    metadata.namespace
                ),
            ):
                deleted += 1

        return deleted

    def invalidate_source(
        self,
        source: str,
        *,
        namespace: str | None = None,
    ) -> int:
        """Delete all persistent entries matching a source."""

        normalized_source = (
            normalize_space(
                source
            )
        )

        if not normalized_source:
            return 0

        metadata_entries = (
            self.disk.find_by_source(
                normalized_source,
                namespace=namespace,
            )
        )

        deleted = 0

        for metadata in metadata_entries:
            if self.delete_hash(
                metadata.key_hash,
                namespace=(
                    metadata.namespace
                ),
            ):
                deleted += 1

        return deleted

    def delete_hash(
        self,
        key_hash_value: str,
        *,
        namespace: str,
    ) -> bool:
        """Delete one entry using its normalized hash."""

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        normalized_hash = normalize_key(
            key_hash_value
        )

        memory_deleted = self.memory.delete(
            normalized_namespace,
            normalized_hash,
        )

        disk_deleted = self.disk.delete(
            normalized_namespace,
            normalized_hash,
        )

        return (
            memory_deleted
            or disk_deleted
        )

    def prune(
        self,
        *,
        namespace: str | None = None,
        delete_stale: bool = False,
    ) -> CachePruneResult:
        """Prune both cache layers."""

        self.memory.prune()

        result = self.disk.prune(
            namespace=namespace,
            delete_stale=delete_stale,
        )

        self._last_prune_at = time.time()

        return result

    def verify(
        self,
        *,
        namespace: str | None = None,
        verify_payloads: bool = True,
    ) -> CacheVerification:
        """Verify the persistent cache."""

        return self.disk.verify(
            namespace=namespace,
            verify_payloads=(
                verify_payloads
            ),
        )

    def usage(
        self,
        *,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Return combined memory and disk usage."""

        disk_usage = self.disk.usage(
            namespace=namespace
        )

        return {
            "memory": {
                "entries": len(
                    self.memory
                ),
                "estimated_bytes": (
                    self.memory.size_bytes
                ),
                "maximum_entries": (
                    self.memory
                    .maximum_entries
                ),
                "maximum_bytes": (
                    self.memory
                    .maximum_bytes
                ),
            },
            "disk": {
                **disk_usage,
                "maximum_entries": (
                    self.disk
                    .maximum_entries
                ),
                "maximum_bytes": (
                    self.disk
                    .maximum_bytes
                ),
            },
            "statistics": (
                self.statistics.to_dict()
            ),
        }

    def export_metadata(
        self,
        path: Path,
        *,
        namespace: str | None = None,
    ) -> None:
        """Write cache metadata and usage as JSON."""

        metadata_entries = [
            metadata.to_dict()
            for metadata
            in self.disk.iter_metadata(
                namespace=namespace
            )
        ]

        atomic_write_json(
            Path(path),
            {
                "schema_version": (
                    CACHE_SCHEMA_VERSION
                ),
                "generated_at": utc_now(),
                "namespace": (
                    normalize_namespace(
                        namespace
                    )
                    if namespace
                    is not None
                    else None
                ),
                "usage": self.usage(
                    namespace=namespace
                ),
                "entries": (
                    metadata_entries
                ),
            },
        )

    def cached(
        self,
        *,
        namespace: str,
        key_builder: Callable[
            ...,
            Any
        ] | None = None,
        policy: CachePolicy | None = None,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        allow_stale: bool = False,
        tags: Iterable[str] | None = None,
    ) -> Callable[
        [Callable[..., ValueType]],
        Callable[..., ValueType],
    ]:
        """
        Return a decorator that caches function results.
        """

        normalized_namespace = (
            normalize_namespace(
                namespace
            )
        )

        def decorator(
            function: Callable[
                ...,
                ValueType
            ],
        ) -> Callable[
            ...,
            ValueType
        ]:
            def wrapped(
                *args: Any,
                **kwargs: Any,
            ) -> ValueType:
                if key_builder is not None:
                    key_value = key_builder(
                        *args,
                        **kwargs,
                    )
                else:
                    key_value = {
                        "module": (
                            function.__module__
                        ),
                        "qualname": (
                            function.__qualname__
                        ),
                        "args": args,
                        "kwargs": kwargs,
                    }

                return self.get_or_set(
                    key_value,
                    lambda: function(
                        *args,
                        **kwargs,
                    ),
                    namespace=(
                        normalized_namespace
                    ),
                    policy=policy,
                    allow_stale=(
                        allow_stale
                    ),
                    ttl_seconds=(
                        ttl_seconds
                    ),
                    stale_ttl_seconds=(
                        stale_ttl_seconds
                    ),
                    tags=tags,
                    source=(
                        function.__qualname__
                    ),
                )

            wrapped.__name__ = (
                function.__name__
            )

            wrapped.__qualname__ = (
                function.__qualname__
            )

            wrapped.__doc__ = (
                function.__doc__
            )

            wrapped.__module__ = (
                function.__module__
            )

            return wrapped

        return decorator

    def _effective_policy(
        self,
        policy: CachePolicy,
        *,
        ttl_seconds: int | None,
        stale_ttl_seconds: int | None,
    ) -> CachePolicy:
        """Return a copy of a policy with optional TTL overrides."""

        values = policy.to_dict()

        if ttl_seconds is not None:
            values["ttl_seconds"] = max(
                0,
                int(ttl_seconds),
            )

        if stale_ttl_seconds is not None:
            values[
                "stale_ttl_seconds"
            ] = max(
                0,
                int(
                    stale_ttl_seconds
                ),
            )

        return CachePolicy(
            **values
        )

    def _stampede_lock(
        self,
        namespace: str,
        key_hash_value: str,
    ) -> threading.Lock:
        """Return a stable in-process lock for one cache key."""

        identity = (
            normalize_namespace(
                namespace
            ),
            normalize_key(
                key_hash_value
            ),
        )

        with self._stampede_lock_guard:
            lock = (
                self._stampede_locks.get(
                    identity
                )
            )

            if lock is None:
                lock = threading.Lock()

                self._stampede_locks[
                    identity
                ] = lock

            return lock

    def _maybe_prune(self) -> None:
        """Run interval-based automatic pruning."""

        if (
            not self.auto_prune
            or self.prune_interval_seconds
            <= 0
        ):
            return

        current = time.time()

        if (
            current
            - self._last_prune_at
            < self.prune_interval_seconds
        ):
            return

        with self._manager_lock:
            current = time.time()

            if (
                current
                - self._last_prune_at
                < self.prune_interval_seconds
            ):
                return

            self.prune()


@dataclass(slots=True)
class HTTPResponseCacheValue:
    """Serializable cached HTTP response."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str
    method: str
    retrieved_at: str
    encoding: str = "utf-8"

    def json(self) -> Any:
        """Decode the response body as JSON."""

        return json.loads(
            self.body.decode(
                self.encoding
                or "utf-8"
            )
        )

    @property
    def text(self) -> str:
        """Decode the response body as text."""

        return self.body.decode(
            self.encoding
            or "utf-8",
            errors="replace",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible description."""

        return {
            "status_code": (
                self.status_code
            ),
            "headers": dict(
                self.headers
            ),
            "body_bytes": len(
                self.body
            ),
            "url": self.url,
            "method": self.method,
            "retrieved_at": (
                self.retrieved_at
            ),
            "encoding": self.encoding,
        }


class HTTPResponseCache:
    """
    HTTP response cache facade.

    This class does not perform network requests. It builds deterministic keys,
    stores response payloads, and exposes conditional-request headers.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "http",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager

        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=60 * 60,
                stale_ttl_seconds=(
                    24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=(
                    SERIALIZER_PICKLE
                ),
                compression=(
                    COMPRESSION_GZIP
                ),
                cache_none=False,
                verify_hash=True,
            )
        )

    def build_key(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        headers: Mapping[
            str,
            Any,
        ] | None = None,
        body: Any = None,
        vary_headers: Sequence[str] = (
            "accept",
            "content-type",
            "authorization",
        ),
    ) -> dict[str, Any]:
        """Build a deterministic HTTP cache key."""

        normalized_headers = {
            normalize_key(key): (
                normalize_space(value)
            )
            for key, value in (
                headers or {}
            ).items()
        }

        selected_headers = {
            normalize_key(header): (
                normalized_headers.get(
                    normalize_key(
                        header
                    ),
                    "",
                )
            )
            for header in vary_headers
        }

        return {
            "method": normalize_key(
                method
            ).upper(),
            "url": normalize_space(
                url
            ),
            "params": dict(
                sorted(
                    (
                        str(key),
                        canonicalize_value(
                            value
                        ),
                    )
                    for key, value
                    in (
                        params or {}
                    ).items()
                )
            ),
            "headers": selected_headers,
            "body": canonicalize_value(
                body
            ),
        }

    def get(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        headers: Mapping[
            str,
            Any,
        ] | None = None,
        body: Any = None,
        allow_stale: bool = False,
    ) -> HTTPResponseCacheValue | None:
        """Read one cached HTTP response."""

        key_value = self.build_key(
            method=method,
            url=url,
            params=params,
            headers=headers,
            body=body,
        )

        value = self.manager.get(
            key_value,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

        if isinstance(
            value,
            HTTPResponseCacheValue,
        ):
            return value

        if isinstance(
            value,
            Mapping,
        ):
            try:
                raw_body = value.get(
                    "body",
                    b"",
                )

                if isinstance(
                    raw_body,
                    str,
                ):
                    raw_body = raw_body.encode(
                        value.get(
                            "encoding",
                            "utf-8",
                        )
                        or "utf-8"
                    )

                return (
                    HTTPResponseCacheValue(
                        status_code=int(
                            value.get(
                                "status_code",
                                0,
                            )
                        ),
                        headers={
                            str(key): str(item)
                            for key, item
                            in dict(
                                value.get(
                                    "headers",
                                    {},
                                )
                            ).items()
                        },
                        body=bytes(
                            raw_body
                        ),
                        url=str(
                            value.get(
                                "url",
                                url,
                            )
                        ),
                        method=str(
                            value.get(
                                "method",
                                method,
                            )
                        ),
                        retrieved_at=str(
                            value.get(
                                "retrieved_at",
                                "",
                            )
                        ),
                        encoding=str(
                            value.get(
                                "encoding",
                                "utf-8",
                            )
                        ),
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                return None

        return None

    def set(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        response_headers: Mapping[
            str,
            Any,
        ],
        body: bytes,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        request_headers: Mapping[
            str,
            Any,
        ] | None = None,
        request_body: Any = None,
        encoding: str = "utf-8",
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
    ) -> bool:
        """Store one HTTP response."""

        key_value = self.build_key(
            method=method,
            url=url,
            params=params,
            headers=request_headers,
            body=request_body,
        )

        normalized_response_headers = {
            normalize_key(key): (
                normalize_space(value)
            )
            for key, value
            in response_headers.items()
        }

        cache_value = (
            HTTPResponseCacheValue(
                status_code=int(
                    status_code
                ),
                headers={
                    str(key): str(value)
                    for key, value
                    in response_headers.items()
                },
                body=bytes(body),
                url=normalize_space(
                    url
                ),
                method=normalize_key(
                    method
                ).upper(),
                retrieved_at=utc_now(),
                encoding=(
                    normalize_space(
                        encoding
                    )
                    or "utf-8"
                ),
            )
        )

        return self.manager.set(
            key_value,
            cache_value,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
            tags=(
                "http",
                normalize_key(
                    method
                ),
            ),
            source=normalize_space(
                url
            ),
            content_type=(
                normalized_response_headers
                .get(
                    "content-type",
                    "",
                )
            ),
            etag=(
                normalized_response_headers
                .get(
                    "etag",
                    "",
                )
            ),
            last_modified=(
                normalized_response_headers
                .get(
                    "last-modified",
                    "",
                )
            ),
            status_code=status_code,
            extra={
                "cache_control": (
                    normalized_response_headers
                    .get(
                        "cache-control",
                        "",
                    )
                ),
                "expires": (
                    normalized_response_headers
                    .get(
                        "expires",
                        "",
                    )
                ),
            },
        )

    def conditional_headers(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        headers: Mapping[
            str,
            Any,
        ] | None = None,
        body: Any = None,
    ) -> dict[str, str]:
        """Return If-None-Match and If-Modified-Since headers."""

        key_value = self.build_key(
            method=method,
            url=url,
            params=params,
            headers=headers,
            body=body,
        )

        metadata = self.manager.metadata(
            key_value,
            namespace=self.namespace,
        )

        if metadata is None:
            return {}

        result: dict[str, str] = {}

        if metadata.etag:
            result[
                "If-None-Match"
            ] = metadata.etag

        if metadata.last_modified:
            result[
                "If-Modified-Since"
            ] = metadata.last_modified

        return result

    def refresh_not_modified(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        headers: Mapping[
            str,
            Any,
        ] | None = None,
        body: Any = None,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
    ) -> bool:
        """Refresh TTL after an HTTP 304 response."""

        key_value = self.build_key(
            method=method,
            url=url,
            params=params,
            headers=headers,
            body=body,
        )

        normalized_namespace = (
            self.namespace
        )

        _canonical_key, key_digest = (
            cache_key(
                key_value,
                namespace=(
                    normalized_namespace
                ),
            )
        )

        active_ttl = (
            self.policy.ttl_seconds
            if ttl_seconds is None
            else max(
                0,
                int(ttl_seconds),
            )
        )

        active_stale = (
            self.policy.stale_ttl_seconds
            if stale_ttl_seconds
            is None
            else max(
                0,
                int(
                    stale_ttl_seconds
                ),
            )
        )

        touched = self.manager.disk.touch(
            normalized_namespace,
            key_digest,
            extend_ttl_seconds=(
                active_ttl
            ),
            extend_stale_seconds=(
                active_stale
            ),
        )

        if touched:
            self.manager.memory.delete(
                normalized_namespace,
                key_digest,
            )

        return touched

    def delete(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[
            str,
            Any,
        ] | None = None,
        headers: Mapping[
            str,
            Any,
        ] | None = None,
        body: Any = None,
    ) -> bool:
        """Delete one cached HTTP response."""

        key_value = self.build_key(
            method=method,
            url=url,
            params=params,
            headers=headers,
            body=body,
        )

        return self.manager.delete(
            key_value,
            namespace=self.namespace,
        )


class SQLiteLookupCache:
    """
    Cache facade for rebuildable SQLite lookup results.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "sqlite",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager

        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=15 * 60,
                stale_ttl_seconds=0,
                memory=True,
                disk=False,
                serializer=(
                    SERIALIZER_PICKLE
                ),
                compression=(
                    COMPRESSION_NONE
                ),
                cache_none=True,
                verify_hash=False,
            )
        )

    def query_key(
        self,
        *,
        operation: str,
        parameters: Mapping[
            str,
            Any,
        ] | Sequence[Any] | None = None,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a deterministic lookup key."""

        return {
            "operation": normalize_key(
                operation
            ),
            "parameters": (
                canonicalize_value(
                    parameters
                )
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def get_or_query(
        self,
        *,
        operation: str,
        parameters: Mapping[
            str,
            Any,
        ] | Sequence[Any] | None,
        query: Callable[[], Any],
        generation: Any = None,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Return a cached query result or execute the query."""

        key_value = self.query_key(
            operation=operation,
            parameters=parameters,
            generation=generation,
        )

        return self.manager.get_or_set(
            key_value,
            query,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            tags=(
                "sqlite",
                normalize_key(
                    operation
                ),
            ),
            source=normalize_key(
                operation
            ),
        )

    def invalidate_operation(
        self,
        operation: str,
    ) -> int:
        """Invalidate entries associated with an operation."""

        return self.manager.invalidate_tag(
            normalize_key(
                operation
            ),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear the SQLite lookup namespace."""

        return self.manager.clear(
            namespace=self.namespace
        )


class ReconciliationCache:
    """
    Cache facade for identity, source, synonym, and candidate lookups.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "reconciliation",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager

        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=30 * 60,
                stale_ttl_seconds=0,
                memory=True,
                disk=False,
                serializer=(
                    SERIALIZER_PICKLE
                ),
                compression=(
                    COMPRESSION_NONE
                ),
                cache_none=True,
                verify_hash=False,
            )
        )

    def source_key(
        self,
        provider: Any,
        provider_id: Any,
        *,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a source-identifier lookup key."""

        return {
            "kind": "source",
            "provider": normalize_key(
                provider
            ),
            "provider_id": normalize_space(
                provider_id
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def identity_key(
        self,
        identity_key_value: Any,
        *,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build an identity lookup key."""

        return {
            "kind": "identity",
            "identity_key": normalize_key(
                identity_key_value
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def synonym_key(
        self,
        synonym: Any,
        *,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a synonym lookup key."""

        return {
            "kind": "synonym",
            "synonym": normalize_key(
                synonym
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def candidates_key(
        self,
        *,
        canonical_name: Any,
        rank: Any,
        kingdom: Any = "",
        family: Any = "",
        genus: Any = "",
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a weighted-candidate lookup key."""

        return {
            "kind": "candidates",
            "canonical_name": (
                normalize_key(
                    canonical_name
                )
            ),
            "rank": normalize_key(
                rank
            ),
            "kingdom": normalize_key(
                kingdom
            ),
            "family": normalize_key(
                family
            ),
            "genus": normalize_key(
                genus
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def get_or_resolve(
        self,
        key_value: Mapping[str, Any],
        resolver: Callable[[], Any],
    ) -> Any:
        """Return a cached reconciliation result or resolve it."""

        return self.manager.get_or_set(
            key_value,
            resolver,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "reconciliation",
                normalize_key(
                    key_value.get(
                        "kind",
                        "lookup",
                    )
                ),
            ),
            source=normalize_key(
                key_value.get(
                    "kind",
                    "lookup",
                )
            ),
        )

    def invalidate_kind(
        self,
        kind: str,
    ) -> int:
        """Invalidate one reconciliation lookup category."""

        return self.manager.invalidate_tag(
            normalize_key(kind),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear all reconciliation lookups."""

        return self.manager.clear(
            namespace=self.namespace
        )

  @dataclass(slots=True)
class ProviderCacheRecord:
    """Serializable provider cache record."""

    provider: str
    operation: str
    cursor: str
    parameters: dict[str, Any]
    value: Any
    created_at: str
    source_url: str = ""
    request_count: int = 0
    exhausted: bool = False
    next_cursor: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible provider cache record."""

        return {
            "provider": self.provider,
            "operation": self.operation,
            "cursor": self.cursor,
            "parameters": dict(
                self.parameters
            ),
            "value": self.value,
            "created_at": self.created_at,
            "source_url": self.source_url,
            "request_count": (
                self.request_count
            ),
            "exhausted": self.exhausted,
            "next_cursor": self.next_cursor,
            "metadata": dict(
                self.metadata
            ),
        }


class ProviderCache:
    """
    Provider API and normalized-record cache.

    Provider cache entries are separated by provider and operation. Raw API
    pages, normalized Taxon batches, cursor metadata, and provider-specific
    lookup results may all be stored independently.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "providers",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=6 * 60 * 60,
                stale_ttl_seconds=(
                    7 * 24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_GZIP,
                compress_above_bytes=(
                    DEFAULT_COMPRESSION_THRESHOLD_BYTES
                ),
                cache_none=False,
                verify_hash=True,
            )
        )

    def build_key(
        self,
        *,
        provider: Any,
        operation: Any,
        cursor: Any = "",
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        version: Any = None,
    ) -> dict[str, Any]:
        """Build a deterministic provider cache key."""

        normalized_provider = (
            normalize_key(provider)
        )

        normalized_operation = (
            normalize_key(operation)
        )

        if not normalized_provider:
            raise CacheKeyError(
                "Provider cache key requires "
                "a provider."
            )

        if not normalized_operation:
            raise CacheKeyError(
                "Provider cache key requires "
                "an operation."
            )

        return {
            "provider": normalized_provider,
            "operation": normalized_operation,
            "cursor": (
                normalize_space(cursor)
            ),
            "parameters": (
                canonicalize_value(
                    parameters or {}
                )
            ),
            "version": (
                canonicalize_value(
                    version
                )
            ),
        }

    def get(
        self,
        *,
        provider: Any,
        operation: Any,
        cursor: Any = "",
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        version: Any = None,
        allow_stale: bool = False,
    ) -> Any:
        """Return one cached provider value."""

        key_value = self.build_key(
            provider=provider,
            operation=operation,
            cursor=cursor,
            parameters=parameters,
            version=version,
        )

        return self.manager.get(
            key_value,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

    def get_entry(
        self,
        *,
        provider: Any,
        operation: Any,
        cursor: Any = "",
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        version: Any = None,
        allow_stale: bool = False,
    ) -> CacheEntry[Any] | None:
        """Return one complete provider cache entry."""

        key_value = self.build_key(
            provider=provider,
            operation=operation,
            cursor=cursor,
            parameters=parameters,
            version=version,
        )

        return self.manager.get_entry(
            key_value,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

    def set(
        self,
        *,
        provider: Any,
        operation: Any,
        value: Any,
        cursor: Any = "",
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        version: Any = None,
        source_url: str = "",
        request_count: int = 0,
        exhausted: bool = False,
        next_cursor: Any = None,
        metadata: Mapping[
            str,
            Any,
        ] | None = None,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
    ) -> bool:
        """Store one provider result."""

        normalized_provider = (
            normalize_key(provider)
        )

        normalized_operation = (
            normalize_key(operation)
        )

        key_value = self.build_key(
            provider=normalized_provider,
            operation=normalized_operation,
            cursor=cursor,
            parameters=parameters,
            version=version,
        )

        record = ProviderCacheRecord(
            provider=normalized_provider,
            operation=normalized_operation,
            cursor=normalize_space(cursor),
            parameters=dict(
                parameters or {}
            ),
            value=value,
            created_at=utc_now(),
            source_url=normalize_space(
                source_url
            ),
            request_count=max(
                0,
                int(request_count),
            ),
            exhausted=bool(exhausted),
            next_cursor=(
                None
                if next_cursor is None
                else str(next_cursor)
            ),
            metadata=dict(
                metadata or {}
            ),
        )

        return self.manager.set(
            key_value,
            record,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
            tags=(
                "provider",
                normalized_provider,
                normalized_operation,
            ),
            source=source_url,
            extra={
                "provider": (
                    normalized_provider
                ),
                "operation": (
                    normalized_operation
                ),
                "cursor": (
                    normalize_space(
                        cursor
                    )
                ),
            },
        )

    def get_value(
        self,
        **kwargs: Any,
    ) -> Any:
        """Return the payload contained in a cached provider record."""

        record = self.get(
            **kwargs
        )

        if isinstance(
            record,
            ProviderCacheRecord,
        ):
            return record.value

        if isinstance(
            record,
            Mapping,
        ) and "value" in record:
            return record.get(
                "value"
            )

        return record

    def get_or_fetch(
        self,
        *,
        provider: Any,
        operation: Any,
        fetcher: Callable[[], Any],
        cursor: Any = "",
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        version: Any = None,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        source_url: str = "",
    ) -> Any:
        """Return a cached provider value or call the provider fetcher."""

        key_value = self.build_key(
            provider=provider,
            operation=operation,
            cursor=cursor,
            parameters=parameters,
            version=version,
        )

        def factory() -> ProviderCacheRecord:
            result = fetcher()

            return ProviderCacheRecord(
                provider=normalize_key(
                    provider
                ),
                operation=normalize_key(
                    operation
                ),
                cursor=normalize_space(
                    cursor
                ),
                parameters=dict(
                    parameters or {}
                ),
                value=result,
                created_at=utc_now(),
                source_url=normalize_space(
                    source_url
                ),
            )

        cached = self.manager.get_or_set(
            key_value,
            factory,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
            ttl_seconds=ttl_seconds,
            stale_ttl_seconds=(
                stale_ttl_seconds
            ),
            tags=(
                "provider",
                normalize_key(provider),
                normalize_key(operation),
            ),
            source=source_url,
            extra={
                "provider": (
                    normalize_key(provider)
                ),
                "operation": (
                    normalize_key(operation)
                ),
            },
        )

        if isinstance(
            cached,
            ProviderCacheRecord,
        ):
            return cached.value

        if isinstance(
            cached,
            Mapping,
        ) and "value" in cached:
            return cached.get(
                "value"
            )

        return cached

    def invalidate_provider(
        self,
        provider: Any,
    ) -> int:
        """Invalidate all cache entries for one provider."""

        return self.manager.invalidate_tag(
            normalize_key(provider),
            namespace=self.namespace,
        )

    def invalidate_operation(
        self,
        operation: Any,
    ) -> int:
        """Invalidate all provider entries for one operation."""

        return self.manager.invalidate_tag(
            normalize_key(operation),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear the provider cache namespace."""

        return self.manager.clear(
            namespace=self.namespace
        )


class TaxonomyCache:
    """
    Cache normalized taxonomic records and taxonomy utility results.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "taxonomy",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=24 * 60 * 60,
                stale_ttl_seconds=(
                    30 * 24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_GZIP,
                cache_none=True,
                verify_hash=True,
            )
        )

    def record_key(
        self,
        *,
        provider: Any,
        provider_id: Any,
        assertion_hash: Any = "",
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a key for one normalized provider taxon."""

        return {
            "kind": "record",
            "provider": normalize_key(
                provider
            ),
            "provider_id": normalize_space(
                provider_id
            ),
            "assertion_hash": normalize_key(
                assertion_hash
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def canonical_name_key(
        self,
        *,
        scientific_name: Any,
        authorship: Any = "",
        rank: Any = "",
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a key for canonical scientific-name generation."""

        return {
            "kind": "canonical-name",
            "scientific_name": (
                normalize_space(
                    scientific_name
                )
            ),
            "authorship": normalize_space(
                authorship
            ),
            "rank": normalize_key(
                rank
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def lineage_key(
        self,
        *,
        provider: Any,
        provider_id: Any,
        lineage_hash: Any = "",
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a key for normalized lineage data."""

        return {
            "kind": "lineage",
            "provider": normalize_key(
                provider
            ),
            "provider_id": normalize_space(
                provider_id
            ),
            "lineage_hash": normalize_key(
                lineage_hash
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def get_record(
        self,
        *,
        provider: Any,
        provider_id: Any,
        assertion_hash: Any = "",
        taxonomy_version: Any = None,
        allow_stale: bool = False,
    ) -> Any:
        """Read one normalized taxonomic record."""

        return self.manager.get(
            self.record_key(
                provider=provider,
                provider_id=provider_id,
                assertion_hash=(
                    assertion_hash
                ),
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

    def set_record(
        self,
        *,
        provider: Any,
        provider_id: Any,
        record: Any,
        assertion_hash: Any = "",
        taxonomy_version: Any = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Store one normalized taxonomic record."""

        normalized_provider = (
            normalize_key(provider)
        )

        return self.manager.set(
            self.record_key(
                provider=normalized_provider,
                provider_id=provider_id,
                assertion_hash=(
                    assertion_hash
                ),
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            record,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            tags=(
                "taxonomy",
                "record",
                normalized_provider,
            ),
            source=(
                f"{normalized_provider}:"
                f"{normalize_space(provider_id)}"
            ),
        )

    def get_or_normalize(
        self,
        *,
        provider: Any,
        provider_id: Any,
        normalizer: Callable[[], Any],
        assertion_hash: Any = "",
        taxonomy_version: Any = None,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Return a normalized taxon or calculate and cache it."""

        normalized_provider = (
            normalize_key(provider)
        )

        return self.manager.get_or_set(
            self.record_key(
                provider=normalized_provider,
                provider_id=provider_id,
                assertion_hash=(
                    assertion_hash
                ),
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            normalizer,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
            ttl_seconds=ttl_seconds,
            tags=(
                "taxonomy",
                "record",
                normalized_provider,
            ),
            source=(
                f"{normalized_provider}:"
                f"{normalize_space(provider_id)}"
            ),
        )

    def get_or_build_canonical_name(
        self,
        *,
        scientific_name: Any,
        builder: Callable[[], str],
        authorship: Any = "",
        rank: Any = "",
        taxonomy_version: Any = None,
    ) -> str:
        """Return a cached canonical scientific name."""

        result = self.manager.get_or_set(
            self.canonical_name_key(
                scientific_name=(
                    scientific_name
                ),
                authorship=authorship,
                rank=rank,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            builder,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "taxonomy",
                "canonical-name",
            ),
        )

        return str(
            result
            if result is not None
            else ""
        )

    def get_lineage(
        self,
        *,
        provider: Any,
        provider_id: Any,
        lineage_hash: Any = "",
        taxonomy_version: Any = None,
    ) -> Any:
        """Read one normalized lineage."""

        return self.manager.get(
            self.lineage_key(
                provider=provider,
                provider_id=provider_id,
                lineage_hash=lineage_hash,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            namespace=self.namespace,
            policy=self.policy,
        )

    def set_lineage(
        self,
        *,
        provider: Any,
        provider_id: Any,
        lineage: Any,
        lineage_hash: Any = "",
        taxonomy_version: Any = None,
    ) -> bool:
        """Store one normalized lineage."""

        normalized_provider = (
            normalize_key(provider)
        )

        return self.manager.set(
            self.lineage_key(
                provider=normalized_provider,
                provider_id=provider_id,
                lineage_hash=lineage_hash,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            lineage,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "taxonomy",
                "lineage",
                normalized_provider,
            ),
            source=(
                f"{normalized_provider}:"
                f"{normalize_space(provider_id)}"
            ),
        )

    def invalidate_provider(
        self,
        provider: Any,
    ) -> int:
        """Invalidate normalized taxonomy data for one provider."""

        return self.manager.invalidate_tag(
            normalize_key(provider),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear taxonomy cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


class SynonymCache:
    """
    Cache synonym lookup results and synonym lists for canonical taxa.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "synonyms",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=30 * 60,
                stale_ttl_seconds=(
                    6 * 60 * 60
                ),
                memory=True,
                disk=False,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_NONE,
                cache_none=True,
                verify_hash=False,
            )
        )

    def lookup_key(
        self,
        *,
        synonym: Any,
        rank: Any = "",
        kingdom: Any = "",
        family: Any = "",
        genus: Any = "",
        provider: Any = "",
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a synonym-resolution cache key."""

        return {
            "kind": "lookup",
            "synonym": normalize_key(
                synonym
            ),
            "rank": normalize_key(
                rank
            ),
            "kingdom": normalize_key(
                kingdom
            ),
            "family": normalize_key(
                family
            ),
            "genus": normalize_key(
                genus
            ),
            "provider": normalize_key(
                provider
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def taxon_key(
        self,
        *,
        speciedex_id: Any,
        include_providers: bool = False,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a canonical taxon synonym-list key."""

        return {
            "kind": "taxon",
            "speciedex_id": (
                normalize_space(
                    speciedex_id
                )
            ),
            "include_providers": bool(
                include_providers
            ),
            "generation": (
                canonicalize_value(
                    generation
                )
            ),
        }

    def get_or_lookup(
        self,
        *,
        synonym: Any,
        resolver: Callable[[], Any],
        rank: Any = "",
        kingdom: Any = "",
        family: Any = "",
        genus: Any = "",
        provider: Any = "",
        generation: Any = None,
    ) -> Any:
        """Return a synonym lookup result or execute the resolver."""

        return self.manager.get_or_set(
            self.lookup_key(
                synonym=synonym,
                rank=rank,
                kingdom=kingdom,
                family=family,
                genus=genus,
                provider=provider,
                generation=generation,
            ),
            resolver,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "synonyms",
                "lookup",
            ),
        )

    def get_or_load_taxon_synonyms(
        self,
        *,
        speciedex_id: Any,
        loader: Callable[[], Any],
        include_providers: bool = False,
        generation: Any = None,
    ) -> Any:
        """Return cached synonyms for one canonical taxon."""

        return self.manager.get_or_set(
            self.taxon_key(
                speciedex_id=speciedex_id,
                include_providers=(
                    include_providers
                ),
                generation=generation,
            ),
            loader,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "synonyms",
                "taxon",
            ),
            source=normalize_space(
                speciedex_id
            ),
        )

    def invalidate_synonym(
        self,
        *,
        synonym: Any,
        rank: Any = "",
        kingdom: Any = "",
        family: Any = "",
        genus: Any = "",
        provider: Any = "",
        generation: Any = None,
    ) -> bool:
        """Delete one synonym lookup."""

        return self.manager.delete(
            self.lookup_key(
                synonym=synonym,
                rank=rank,
                kingdom=kingdom,
                family=family,
                genus=genus,
                provider=provider,
                generation=generation,
            ),
            namespace=self.namespace,
        )

    def invalidate_taxon(
        self,
        speciedex_id: Any,
    ) -> int:
        """Invalidate taxon-synonym results associated with an identifier."""

        return self.manager.invalidate_source(
            normalize_space(
                speciedex_id
            ),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear the synonym cache namespace."""

        return self.manager.clear(
            namespace=self.namespace
        )


class AuthorityCache:
    """
    Cache normalized nomenclatural authority parsing and comparisons.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "authority",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=(
                    30 * 24 * 60 * 60
                ),
                stale_ttl_seconds=(
                    180 * 24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_GZIP,
                cache_none=True,
                verify_hash=True,
            )
        )

    def parse_key(
        self,
        *,
        authority: Any,
        registry_version: Any = None,
    ) -> dict[str, Any]:
        """Build an authority-parsing key."""

        return {
            "kind": "parse",
            "authority": normalize_space(
                authority
            ),
            "registry_version": (
                canonicalize_value(
                    registry_version
                )
            ),
        }

    def comparison_key(
        self,
        *,
        left: Any,
        right: Any,
        registry_version: Any = None,
        threshold: float = 0.85,
    ) -> dict[str, Any]:
        """Build an authority-comparison key."""

        normalized_pair = sorted(
            (
                normalize_space(left),
                normalize_space(right),
            )
        )

        return {
            "kind": "compare",
            "left": normalized_pair[0],
            "right": normalized_pair[1],
            "registry_version": (
                canonicalize_value(
                    registry_version
                )
            ),
            "threshold": round(
                float(threshold),
                8,
            ),
        }

    def get_or_parse(
        self,
        *,
        authority: Any,
        parser: Callable[[], Any],
        registry_version: Any = None,
    ) -> Any:
        """Return a parsed authority or invoke the parser."""

        return self.manager.get_or_set(
            self.parse_key(
                authority=authority,
                registry_version=(
                    registry_version
                ),
            ),
            parser,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "authority",
                "parse",
            ),
        )

    def get_or_compare(
        self,
        *,
        left: Any,
        right: Any,
        comparer: Callable[[], Any],
        registry_version: Any = None,
        threshold: float = 0.85,
    ) -> Any:
        """Return a cached authority comparison."""

        return self.manager.get_or_set(
            self.comparison_key(
                left=left,
                right=right,
                registry_version=(
                    registry_version
                ),
                threshold=threshold,
            ),
            comparer,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "authority",
                "compare",
            ),
        )

    def clear(self) -> int:
        """Clear authority cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


class LineageCache:
    """
    Cache lineage normalization, fingerprints, comparisons, and merge results.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "lineage",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=24 * 60 * 60,
                stale_ttl_seconds=(
                    30 * 24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_GZIP,
                cache_none=True,
                verify_hash=True,
            )
        )

    def normalized_key(
        self,
        lineage: Any,
        *,
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a normalized-lineage cache key."""

        return {
            "kind": "normalize",
            "lineage": canonicalize_value(
                lineage
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def comparison_key(
        self,
        *,
        left: Any,
        right: Any,
        ranks: Sequence[Any] | None = None,
        weights: Mapping[
            Any,
            Any,
        ] | None = None,
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a lineage-comparison key."""

        left_value = canonicalize_value(
            left
        )

        right_value = canonicalize_value(
            right
        )

        ordered_pair = sorted(
            (
                left_value,
                right_value,
            ),
            key=lambda value: json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        return {
            "kind": "compare",
            "left": ordered_pair[0],
            "right": ordered_pair[1],
            "ranks": canonicalize_value(
                ranks
            ),
            "weights": canonicalize_value(
                weights
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def merge_key(
        self,
        *,
        left: Any,
        right: Any,
        prefer: str,
        taxonomy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a lineage-merge key."""

        return {
            "kind": "merge",
            "left": canonicalize_value(
                left
            ),
            "right": canonicalize_value(
                right
            ),
            "prefer": normalize_key(
                prefer
            ),
            "taxonomy_version": (
                canonicalize_value(
                    taxonomy_version
                )
            ),
        }

    def get_or_normalize(
        self,
        *,
        lineage: Any,
        normalizer: Callable[[], Any],
        taxonomy_version: Any = None,
    ) -> Any:
        """Return normalized lineage data."""

        return self.manager.get_or_set(
            self.normalized_key(
                lineage,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            normalizer,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "lineage",
                "normalize",
            ),
        )

    def get_or_compare(
        self,
        *,
        left: Any,
        right: Any,
        comparer: Callable[[], Any],
        ranks: Sequence[Any] | None = None,
        weights: Mapping[
            Any,
            Any,
        ] | None = None,
        taxonomy_version: Any = None,
    ) -> Any:
        """Return a cached lineage comparison."""

        return self.manager.get_or_set(
            self.comparison_key(
                left=left,
                right=right,
                ranks=ranks,
                weights=weights,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            comparer,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "lineage",
                "compare",
            ),
        )

    def get_or_merge(
        self,
        *,
        left: Any,
        right: Any,
        merger: Callable[[], Any],
        prefer: str = "left",
        taxonomy_version: Any = None,
    ) -> Any:
        """Return a cached lineage merge result."""

        return self.manager.get_or_set(
            self.merge_key(
                left=left,
                right=right,
                prefer=prefer,
                taxonomy_version=(
                    taxonomy_version
                ),
            ),
            merger,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "lineage",
                "merge",
            ),
        )

    def clear(self) -> int:
        """Clear lineage cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


@dataclass(slots=True)
class CachedBatch:
    """Serializable provider or processing batch."""

    records: list[Any]
    next_cursor: Any
    exhausted: bool
    requests: int = 0
    raw: int = 0
    created_at: str = field(
        default_factory=utc_now
    )
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible batch description."""

        return {
            "records": list(
                self.records
            ),
            "next_cursor": (
                self.next_cursor
            ),
            "exhausted": self.exhausted,
            "requests": self.requests,
            "raw": self.raw,
            "created_at": self.created_at,
            "metadata": dict(
                self.metadata
            ),
        }


class BatchCache:
    """
    Cache provider pages and normalized ingestion batches.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "batches",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = (
            normalize_namespace(
                namespace
            )
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=12 * 60 * 60,
                stale_ttl_seconds=(
                    7 * 24 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_GZIP,
                cache_none=False,
                verify_hash=True,
            )
        )

    def build_key(
        self,
        *,
        provider: Any,
        cursor: Any,
        batch_size: int,
        filters: Mapping[
            str,
            Any,
        ] | None = None,
        provider_version: Any = None,
        normalizer_version: Any = None,
    ) -> dict[str, Any]:
        """Build a deterministic batch cache key."""

        return {
            "provider": normalize_key(
                provider
            ),
            "cursor": normalize_space(
                cursor
            ),
            "batch_size": max(
                1,
                int(batch_size),
            ),
            "filters": canonicalize_value(
                filters or {}
            ),
            "provider_version": (
                canonicalize_value(
                    provider_version
                )
            ),
            "normalizer_version": (
                canonicalize_value(
                    normalizer_version
                )
            ),
        }

    def get(
        self,
        *,
        provider: Any,
        cursor: Any,
        batch_size: int,
        filters: Mapping[
            str,
            Any,
        ] | None = None,
        provider_version: Any = None,
        normalizer_version: Any = None,
        allow_stale: bool = False,
    ) -> CachedBatch | None:
        """Read one cached batch."""

        value = self.manager.get(
            self.build_key(
                provider=provider,
                cursor=cursor,
                batch_size=batch_size,
                filters=filters,
                provider_version=(
                    provider_version
                ),
                normalizer_version=(
                    normalizer_version
                ),
            ),
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

        if isinstance(
            value,
            CachedBatch,
        ):
            return value

        if isinstance(
            value,
            Mapping,
        ):
            records = value.get(
                "records",
                [],
            )

            if not isinstance(
                records,
                list,
            ):
                records = list(records)

            return CachedBatch(
                records=records,
                next_cursor=value.get(
                    "next_cursor"
                ),
                exhausted=bool(
                    value.get(
                        "exhausted",
                        False,
                    )
                ),
                requests=max(
                    0,
                    safe_int(
                        value.get(
                            "requests",
                            0,
                        )
                    ),
                ),
                raw=max(
                    0,
                    safe_int(
                        value.get(
                            "raw",
                            len(records),
                        )
                    ),
                ),
                created_at=normalize_space(
                    value.get(
                        "created_at",
                        utc_now(),
                    )
                ),
                metadata=(
                    dict(
                        value.get(
                            "metadata",
                            {},
                        )
                    )
                    if isinstance(
                        value.get(
                            "metadata",
                            {},
                        ),
                        Mapping,
                    )
                    else {}
                ),
            )

        return None

    def set(
        self,
        *,
        provider: Any,
        cursor: Any,
        batch_size: int,
        records: Iterable[Any],
        next_cursor: Any,
        exhausted: bool,
        requests: int = 0,
        raw: int | None = None,
        filters: Mapping[
            str,
            Any,
        ] | None = None,
        provider_version: Any = None,
        normalizer_version: Any = None,
        metadata: Mapping[
            str,
            Any,
        ] | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Store one normalized batch."""

        record_values = list(
            records
        )

        normalized_provider = (
            normalize_key(provider)
        )

        batch = CachedBatch(
            records=record_values,
            next_cursor=next_cursor,
            exhausted=bool(exhausted),
            requests=max(
                0,
                int(requests),
            ),
            raw=(
                max(
                    0,
                    int(raw),
                )
                if raw is not None
                else len(record_values)
            ),
            metadata=dict(
                metadata or {}
            ),
        )

        return self.manager.set(
            self.build_key(
                provider=normalized_provider,
                cursor=cursor,
                batch_size=batch_size,
                filters=filters,
                provider_version=(
                    provider_version
                ),
                normalizer_version=(
                    normalizer_version
                ),
            ),
            batch,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            tags=(
                "batch",
                normalized_provider,
            ),
            source=normalized_provider,
            extra={
                "cursor": (
                    normalize_space(
                        cursor
                    )
                ),
                "batch_size": max(
                    1,
                    int(batch_size),
                ),
                "record_count": len(
                    record_values
                ),
            },
        )

    def invalidate_provider(
        self,
        provider: Any,
    ) -> int:
        """Invalidate all batches for one provider."""

        return self.manager.invalidate_tag(
            normalize_key(provider),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear all cached batches."""

        return self.manager.clear(
            namespace=self.namespace
        )

class StatisticsCache:
    """
    Cache generated archive statistics and expensive aggregate queries.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "statistics",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = normalize_namespace(
            namespace
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=15 * 60,
                stale_ttl_seconds=(
                    6 * 60 * 60
                ),
                memory=True,
                disk=True,
                serializer=SERIALIZER_JSON,
                compression=COMPRESSION_GZIP,
                cache_none=False,
                verify_hash=True,
            )
        )

    def build_key(
        self,
        *,
        name: Any,
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        generation: Any = None,
    ) -> dict[str, Any]:
        """Build a deterministic statistics cache key."""

        normalized_name = normalize_key(
            name
        )

        if not normalized_name:
            raise CacheKeyError(
                "Statistics cache key requires "
                "a name."
            )

        return {
            "name": normalized_name,
            "parameters": canonicalize_value(
                parameters or {}
            ),
            "generation": canonicalize_value(
                generation
            ),
        }

    def get(
        self,
        *,
        name: Any,
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        generation: Any = None,
        allow_stale: bool = False,
    ) -> Any:
        """Return one cached statistics result."""

        return self.manager.get(
            self.build_key(
                name=name,
                parameters=parameters,
                generation=generation,
            ),
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
        )

    def set(
        self,
        *,
        name: Any,
        value: Any,
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        generation: Any = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Store one statistics result."""

        normalized_name = normalize_key(
            name
        )

        return self.manager.set(
            self.build_key(
                name=normalized_name,
                parameters=parameters,
                generation=generation,
            ),
            value,
            namespace=self.namespace,
            policy=self.policy,
            ttl_seconds=ttl_seconds,
            tags=(
                "statistics",
                normalized_name,
            ),
            source=normalized_name,
        )

    def get_or_calculate(
        self,
        *,
        name: Any,
        calculator: Callable[
            [],
            Any,
        ],
        parameters: Mapping[
            str,
            Any,
        ] | None = None,
        generation: Any = None,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Return cached statistics or execute the calculator."""

        normalized_name = normalize_key(
            name
        )

        return self.manager.get_or_set(
            self.build_key(
                name=normalized_name,
                parameters=parameters,
                generation=generation,
            ),
            calculator,
            namespace=self.namespace,
            policy=self.policy,
            allow_stale=allow_stale,
            ttl_seconds=ttl_seconds,
            tags=(
                "statistics",
                normalized_name,
            ),
            source=normalized_name,
        )

    def invalidate(
        self,
        name: Any,
    ) -> int:
        """Invalidate one statistics category."""

        return self.manager.invalidate_tag(
            normalize_key(name),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear all cached statistics."""

        return self.manager.clear(
            namespace=self.namespace
        )


class ManifestCache:
    """
    Cache parsed manifests and derived manifest summaries.

    This cache does not replace ManifestManager. ManifestManager remains the
    persistence owner. This facade avoids repeated parsing and repeated
    derivation of immutable manifest views.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "manifest",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = normalize_namespace(
            namespace
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=5 * 60,
                stale_ttl_seconds=0,
                memory=True,
                disk=False,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_NONE,
                cache_none=False,
                verify_hash=False,
            )
        )

    def build_key(
        self,
        *,
        path: Path,
        modified_ns: int | None = None,
        view: str = "full",
    ) -> dict[str, Any]:
        """Build a manifest cache key."""

        source = Path(path)

        if modified_ns is None:
            try:
                modified_ns = (
                    source.stat().st_mtime_ns
                )
            except OSError:
                modified_ns = 0

        return {
            "path": source.resolve().as_posix(),
            "modified_ns": int(
                modified_ns
            ),
            "view": normalize_key(
                view
            ),
        }

    def get_or_load(
        self,
        path: Path,
        *,
        loader: Callable[
            [],
            Any,
        ] | None = None,
        view: str = "full",
    ) -> Any:
        """Return a parsed manifest or read it from disk."""

        source = Path(path)

        def default_loader() -> Any:
            try:
                return json.loads(
                    source.read_text(
                        encoding="utf-8",
                    )
                )
            except OSError as error:
                raise CacheError(
                    "Unable to read manifest "
                    f"{source}: {error}"
                ) from error
            except json.JSONDecodeError as error:
                raise CacheIntegrityError(
                    "Invalid manifest JSON in "
                    f"{source}: {error}"
                ) from error

        return self.manager.get_or_set(
            self.build_key(
                path=source,
                view=view,
            ),
            (
                loader
                if loader is not None
                else default_loader
            ),
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "manifest",
                normalize_key(view),
            ),
            source=source.as_posix(),
        )

    def invalidate_path(
        self,
        path: Path,
    ) -> int:
        """Invalidate cached views for one manifest path."""

        return self.manager.invalidate_source(
            Path(path).as_posix(),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear manifest cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


class HistoryCache:
    """
    Cache retained statistics history and provider-run history.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "history",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = normalize_namespace(
            namespace
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=10 * 60,
                stale_ttl_seconds=(
                    60 * 60
                ),
                memory=True,
                disk=False,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_NONE,
                cache_none=False,
                verify_hash=False,
            )
        )

    def file_key(
        self,
        *,
        path: Path,
        category: str,
    ) -> dict[str, Any]:
        """Build a history-file cache key."""

        source = Path(path)

        try:
            stat = source.stat()

            modified_ns = (
                stat.st_mtime_ns
            )

            size_bytes = stat.st_size

        except OSError:
            modified_ns = 0
            size_bytes = 0

        return {
            "category": normalize_key(
                category
            ),
            "path": source.resolve().as_posix(),
            "modified_ns": modified_ns,
            "size_bytes": size_bytes,
        }

    def get_or_load(
        self,
        *,
        path: Path,
        category: str,
        loader: Callable[
            [],
            Any,
        ],
    ) -> Any:
        """Return cached history data or invoke a loader."""

        source = Path(path)

        return self.manager.get_or_set(
            self.file_key(
                path=source,
                category=category,
            ),
            loader,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "history",
                normalize_key(category),
            ),
            source=source.as_posix(),
        )

    def invalidate_path(
        self,
        path: Path,
    ) -> int:
        """Invalidate history data associated with one file."""

        return self.manager.invalidate_source(
            Path(path).as_posix(),
            namespace=self.namespace,
        )

    def clear(self) -> int:
        """Clear history cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


class DeduplicationCache:
    """
    Cache duplicate candidate discovery and pairwise duplicate scores.
    """

    def __init__(
        self,
        manager: CacheManager,
        *,
        namespace: str = "deduplication",
        policy: CachePolicy | None = None,
    ) -> None:
        self.manager = manager
        self.namespace = normalize_namespace(
            namespace
        )

        self.policy = (
            policy
            if policy is not None
            else CachePolicy(
                ttl_seconds=30 * 60,
                stale_ttl_seconds=0,
                memory=True,
                disk=False,
                serializer=SERIALIZER_PICKLE,
                compression=COMPRESSION_NONE,
                cache_none=True,
                verify_hash=False,
            )
        )

    def candidate_key(
        self,
        *,
        signature: Any,
        generation: Any = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Build a duplicate candidate cache key."""

        return {
            "kind": "candidates",
            "signature": canonicalize_value(
                signature
            ),
            "generation": canonicalize_value(
                generation
            ),
            "limit": (
                int(limit)
                if limit is not None
                else None
            ),
        }

    def comparison_key(
        self,
        *,
        left: Any,
        right: Any,
        policy_version: Any = None,
    ) -> dict[str, Any]:
        """Build a symmetric duplicate-comparison key."""

        left_value = canonicalize_value(
            left
        )

        right_value = canonicalize_value(
            right
        )

        pair = sorted(
            (
                left_value,
                right_value,
            ),
            key=lambda value: json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        return {
            "kind": "comparison",
            "left": pair[0],
            "right": pair[1],
            "policy_version": (
                canonicalize_value(
                    policy_version
                )
            ),
        }

    def get_or_find_candidates(
        self,
        *,
        signature: Any,
        finder: Callable[[], Any],
        generation: Any = None,
        limit: int | None = None,
    ) -> Any:
        """Return cached duplicate candidates."""

        return self.manager.get_or_set(
            self.candidate_key(
                signature=signature,
                generation=generation,
                limit=limit,
            ),
            finder,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "deduplication",
                "candidates",
            ),
        )

    def get_or_compare(
        self,
        *,
        left: Any,
        right: Any,
        comparer: Callable[[], Any],
        policy_version: Any = None,
    ) -> Any:
        """Return a cached duplicate comparison."""

        return self.manager.get_or_set(
            self.comparison_key(
                left=left,
                right=right,
                policy_version=(
                    policy_version
                ),
            ),
            comparer,
            namespace=self.namespace,
            policy=self.policy,
            tags=(
                "deduplication",
                "comparison",
            ),
        )

    def clear(self) -> int:
        """Clear duplicate-analysis cache data."""

        return self.manager.clear(
            namespace=self.namespace
        )


@dataclass(slots=True)
class CacheWarmTask:
    """One cache-warming task."""

    name: str
    namespace: str
    key: Any
    loader: Callable[[], Any]
    policy: CachePolicy | None = None
    ttl_seconds: int | None = None
    stale_ttl_seconds: int | None = None
    tags: tuple[str, ...] = ()
    source: str = ""
    overwrite: bool = False


@dataclass(slots=True)
class CacheWarmResult:
    """Result of warming multiple cache entries."""

    attempted: int
    loaded: int
    already_cached: int
    failed: int
    errors: list[dict[str, str]] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible warm result."""

        return {
            "attempted": self.attempted,
            "loaded": self.loaded,
            "already_cached": (
                self.already_cached
            ),
            "failed": self.failed,
            "errors": list(
                self.errors
            ),
        }


def warm_cache(
    manager: CacheManager,
    tasks: Iterable[CacheWarmTask],
    *,
    allow_stale: bool = False,
) -> CacheWarmResult:
    """Execute cache-warming tasks in deterministic order."""

    task_values = sorted(
        list(tasks),
        key=lambda task: (
            normalize_namespace(
                task.namespace
            ),
            normalize_space(
                task.name
            ),
        ),
    )

    result = CacheWarmResult(
        attempted=0,
        loaded=0,
        already_cached=0,
        failed=0,
    )

    for task in task_values:
        result.attempted += 1

        try:
            if (
                not task.overwrite
                and manager.contains(
                    task.key,
                    namespace=(
                        task.namespace
                    ),
                    allow_stale=(
                        allow_stale
                    ),
                    policy=task.policy,
                )
            ):
                result.already_cached += 1
                continue

            value = task.loader()

            manager.set(
                task.key,
                value,
                namespace=task.namespace,
                policy=task.policy,
                ttl_seconds=(
                    task.ttl_seconds
                ),
                stale_ttl_seconds=(
                    task.stale_ttl_seconds
                ),
                tags=task.tags,
                source=task.source,
            )

            result.loaded += 1

        except Exception as error:
            result.failed += 1

            result.errors.append(
                {
                    "name": task.name,
                    "namespace": (
                        normalize_namespace(
                            task.namespace
                        )
                    ),
                    "error": str(error),
                }
            )

    return result


@dataclass(slots=True)
class CacheExportResult:
    """Result of exporting persistent cache entries."""

    output_path: str
    entries: int
    payload_bytes: int
    namespaces: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible export result."""

        return {
            "output_path": self.output_path,
            "entries": self.entries,
            "payload_bytes": (
                self.payload_bytes
            ),
            "namespaces": list(
                self.namespaces
            ),
        }


@dataclass(slots=True)
class CacheImportResult:
    """Result of importing cache entries."""

    source_path: str
    entries_seen: int
    entries_imported: int
    entries_skipped: int
    entries_failed: int
    errors: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible import result."""

        return {
            "source_path": self.source_path,
            "entries_seen": (
                self.entries_seen
            ),
            "entries_imported": (
                self.entries_imported
            ),
            "entries_skipped": (
                self.entries_skipped
            ),
            "entries_failed": (
                self.entries_failed
            ),
            "errors": list(
                self.errors
            ),
        }


def export_cache(
    manager: CacheManager,
    output_path: Path,
    *,
    namespace: str | None = None,
    include_values: bool = True,
    allow_stale: bool = True,
) -> CacheExportResult:
    """
    Export persistent cache entries to a portable pickle file.

    Pickle export is intended only for trusted Speciedex cache data.
    """

    destination = Path(
        output_path
    )

    records: list[
        dict[str, Any]
    ] = []

    namespaces: set[str] = set()
    payload_bytes = 0

    for metadata in (
        manager.disk.iter_metadata(
            namespace=namespace
        )
    ):
        record: dict[str, Any] = {
            "metadata": (
                metadata.to_dict()
            ),
        }

        if include_values:
            entry = manager.disk.get(
                metadata.namespace,
                metadata.key_hash,
                allow_stale=allow_stale,
                verify_payload=True,
                touch=False,
            )

            if entry is None:
                continue

            record["value"] = entry.value

        records.append(record)
        namespaces.add(
            metadata.namespace
        )

        payload_bytes += (
            metadata.payload_bytes
        )

    package = {
        "schema_version": (
            CACHE_SCHEMA_VERSION
        ),
        "generated_at": utc_now(),
        "namespace": (
            normalize_namespace(
                namespace
            )
            if namespace is not None
            else None
        ),
        "include_values": (
            include_values
        ),
        "entries": records,
    }

    payload = pickle.dumps(
        package,
        protocol=pickle.HIGHEST_PROTOCOL,
    )

    atomic_write_bytes(
        destination,
        gzip.compress(
            payload,
            compresslevel=6,
            mtime=0,
        ),
    )

    return CacheExportResult(
        output_path=(
            destination.as_posix()
        ),
        entries=len(records),
        payload_bytes=payload_bytes,
        namespaces=sorted(
            namespaces
        ),
    )


def import_cache(
    manager: CacheManager,
    source_path: Path,
    *,
    overwrite: bool = False,
    namespace_override: str | None = None,
) -> CacheImportResult:
    """
    Import a trusted cache export produced by export_cache().
    """

    source = Path(
        source_path
    )

    result = CacheImportResult(
        source_path=source.as_posix(),
        entries_seen=0,
        entries_imported=0,
        entries_skipped=0,
        entries_failed=0,
    )

    try:
        compressed = source.read_bytes()

        package = pickle.loads(
            gzip.decompress(
                compressed
            )
        )

    except (
        OSError,
        OSError,
        pickle.PickleError,
        EOFError,
        ValueError,
    ) as error:
        raise CacheSerializationError(
            "Unable to import cache package "
            f"{source}: {error}"
        ) from error

    if not isinstance(
        package,
        Mapping,
    ):
        raise CacheSerializationError(
            "Cache import package root is not "
            "a mapping."
        )

    if safe_int(
        package.get(
            "schema_version"
        )
    ) != CACHE_SCHEMA_VERSION:
        raise CacheIntegrityError(
            "Cache import schema version "
            "does not match."
        )

    entries = package.get(
        "entries",
        [],
    )

    if not isinstance(
        entries,
        list,
    ):
        raise CacheSerializationError(
            "Cache import entries value is "
            "not a list."
        )

    for record in entries:
        result.entries_seen += 1

        try:
            if not isinstance(
                record,
                Mapping,
            ):
                raise CacheSerializationError(
                    "Cache import entry is not "
                    "a mapping."
                )

            metadata_value = record.get(
                "metadata"
            )

            if not isinstance(
                metadata_value,
                Mapping,
            ):
                raise CacheSerializationError(
                    "Cache import entry has no "
                    "metadata mapping."
                )

            metadata = (
                CacheEntryMetadata
                .from_dict(
                    metadata_value
                )
            )

            namespace = (
                normalize_namespace(
                    namespace_override
                )
                if namespace_override
                is not None
                else metadata.namespace
            )

            if (
                not overwrite
                and manager.disk.exists(
                    namespace,
                    metadata.key_hash,
                    include_expired=True,
                )
            ):
                result.entries_skipped += 1
                continue

            if "value" not in record:
                result.entries_skipped += 1
                continue

            value = record["value"]

            current_time = time.time()

            remaining_ttl: int

            if metadata.expires_at is None:
                remaining_ttl = 0
            else:
                remaining_ttl = max(
                    1,
                    int(
                        metadata.expires_at
                        - current_time
                    ),
                )

            if (
                metadata.stale_until is None
                or metadata.expires_at is None
            ):
                remaining_stale = 0
            else:
                remaining_stale = max(
                    0,
                    int(
                        metadata.stale_until
                        - max(
                            current_time,
                            metadata.expires_at,
                        )
                    ),
                )

            policy = CachePolicy(
                ttl_seconds=remaining_ttl,
                stale_ttl_seconds=(
                    remaining_stale
                ),
                memory=True,
                disk=True,
                serializer=(
                    metadata.serializer
                ),
                compression=(
                    metadata.compression
                ),
                compress_above_bytes=0,
                refresh_on_access=True,
                cache_none=True,
                verify_hash=True,
            )

            manager.set(
                metadata.key,
                value,
                namespace=namespace,
                policy=policy,
                tags=metadata.tags,
                source=metadata.source,
                content_type=(
                    metadata.content_type
                ),
                etag=metadata.etag,
                last_modified=(
                    metadata.last_modified
                ),
                status_code=(
                    metadata.status_code
                ),
                extra=metadata.extra,
            )

            result.entries_imported += 1

        except Exception as error:
            result.entries_failed += 1
            result.errors.append(
                str(error)
            )

    return result


@dataclass(slots=True)
class CacheRebuildResult:
    """Result of rebuilding cache metadata."""

    scanned_directories: int
    repaired_metadata: int
    deleted_corrupt_entries: int
    deleted_orphans: int
    errors: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible rebuild result."""

        return {
            "scanned_directories": (
                self.scanned_directories
            ),
            "repaired_metadata": (
                self.repaired_metadata
            ),
            "deleted_corrupt_entries": (
                self.deleted_corrupt_entries
            ),
            "deleted_orphans": (
                self.deleted_orphans
            ),
            "errors": list(
                self.errors
            ),
        }


def rebuild_cache(
    manager: CacheManager,
    *,
    namespace: str | None = None,
    delete_corrupt: bool = True,
) -> CacheRebuildResult:
    """
    Inspect persistent entry directories and repair recoverable metadata.
    """

    search_root = (
        manager.root
        / normalize_namespace(
            namespace
        )
        if namespace is not None
        else manager.root
    )

    result = CacheRebuildResult(
        scanned_directories=0,
        repaired_metadata=0,
        deleted_corrupt_entries=0,
        deleted_orphans=0,
    )

    if not search_root.exists():
        return result

    entry_directories: set[
        Path
    ] = set()

    for metadata_path in search_root.rglob(
        "metadata.json"
    ):
        entry_directories.add(
            metadata_path.parent
        )

    for payload_path in (
        manager.disk._all_payload_paths(
            search_root
        )
    ):
        entry_directories.add(
            payload_path.parent
        )

    for entry_root in sorted(
        entry_directories
    ):
        result.scanned_directories += 1

        metadata_path = (
            entry_root
            / "metadata.json"
        )

        payload_candidates = [
            entry_root
            / (
                "payload"
                + CACHE_FILE_SUFFIX
            ),
            entry_root
            / (
                "payload"
                + CACHE_COMPRESSED_SUFFIX
            ),
        ]

        payload_path = next(
            (
                path
                for path
                in payload_candidates
                if path.is_file()
            ),
            None,
        )

        if payload_path is None:
            if delete_corrupt:
                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted_orphans += 1

            continue

        if not metadata_path.is_file():
            if delete_corrupt:
                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted_orphans += 1

            continue

        try:
            metadata = (
                CacheEntryMetadata
                .from_dict(
                    read_json(
                        metadata_path
                    )
                )
            )

            stored_payload = (
                payload_path.read_bytes()
            )

            serialized = decompress_payload(
                stored_payload,
                compression=(
                    metadata.compression
                ),
            )

            calculated_hash = payload_hash(
                serialized
            )

            changed = False

            if (
                metadata.payload_hash
                != calculated_hash
            ):
                metadata.payload_hash = (
                    calculated_hash
                )

                changed = True

            if (
                metadata.payload_bytes
                != len(serialized)
            ):
                metadata.payload_bytes = len(
                    serialized
                )

                changed = True

            if (
                metadata.stored_bytes
                != len(stored_payload)
            ):
                metadata.stored_bytes = len(
                    stored_payload
                )

                changed = True

            if (
                metadata.schema_version
                != CACHE_SCHEMA_VERSION
            ):
                metadata.schema_version = (
                    CACHE_SCHEMA_VERSION
                )

                changed = True

            deserialize_value(
                serialized,
                serializer=(
                    metadata.serializer
                ),
            )

            if changed:
                metadata.updated_at = (
                    time.time()
                )

                atomic_write_json(
                    metadata_path,
                    metadata.to_dict(),
                )

                result.repaired_metadata += 1

        except Exception as error:
            result.errors.append(
                f"{entry_root.as_posix()}: "
                f"{error}"
            )

            if delete_corrupt:
                shutil.rmtree(
                    entry_root,
                    ignore_errors=True,
                )

                result.deleted_corrupt_entries += 1

    manager.memory.clear(
        namespace=namespace
    )

    return result


def default_namespace_policies(
) -> dict[str, CachePolicy]:
    """Return recommended Speciedex namespace policies."""

    return {
        "http": CachePolicy(
            ttl_seconds=60 * 60,
            stale_ttl_seconds=(
                24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=False,
            verify_hash=True,
        ),
        "providers": CachePolicy(
            ttl_seconds=6 * 60 * 60,
            stale_ttl_seconds=(
                7 * 24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=False,
            verify_hash=True,
        ),
        "batches": CachePolicy(
            ttl_seconds=12 * 60 * 60,
            stale_ttl_seconds=(
                7 * 24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=False,
            verify_hash=True,
        ),
        "taxonomy": CachePolicy(
            ttl_seconds=24 * 60 * 60,
            stale_ttl_seconds=(
                30 * 24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=True,
            verify_hash=True,
        ),
        "authority": CachePolicy(
            ttl_seconds=(
                30 * 24 * 60 * 60
            ),
            stale_ttl_seconds=(
                180 * 24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=True,
            verify_hash=True,
        ),
        "lineage": CachePolicy(
            ttl_seconds=24 * 60 * 60,
            stale_ttl_seconds=(
                30 * 24 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_GZIP,
            cache_none=True,
            verify_hash=True,
        ),
        "statistics": CachePolicy(
            ttl_seconds=15 * 60,
            stale_ttl_seconds=(
                6 * 60 * 60
            ),
            memory=True,
            disk=True,
            serializer=SERIALIZER_JSON,
            compression=COMPRESSION_GZIP,
            cache_none=False,
            verify_hash=True,
        ),
        "sqlite": CachePolicy(
            ttl_seconds=15 * 60,
            stale_ttl_seconds=0,
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=True,
            verify_hash=False,
        ),
        "reconciliation": CachePolicy(
            ttl_seconds=30 * 60,
            stale_ttl_seconds=0,
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=True,
            verify_hash=False,
        ),
        "synonyms": CachePolicy(
            ttl_seconds=30 * 60,
            stale_ttl_seconds=(
                6 * 60 * 60
            ),
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=True,
            verify_hash=False,
        ),
        "deduplication": CachePolicy(
            ttl_seconds=30 * 60,
            stale_ttl_seconds=0,
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=True,
            verify_hash=False,
        ),
        "manifest": CachePolicy(
            ttl_seconds=5 * 60,
            stale_ttl_seconds=0,
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=False,
            verify_hash=False,
        ),
        "history": CachePolicy(
            ttl_seconds=10 * 60,
            stale_ttl_seconds=(
                60 * 60
            ),
            memory=True,
            disk=False,
            serializer=SERIALIZER_PICKLE,
            compression=COMPRESSION_NONE,
            cache_none=False,
            verify_hash=False,
        ),
    }


@dataclass(slots=True)
class CacheBundle:
    """
    Complete Speciedex cache facade bundle.
    """

    manager: CacheManager
    http: HTTPResponseCache
    providers: ProviderCache
    batches: BatchCache
    taxonomy: TaxonomyCache
    synonyms: SynonymCache
    authority: AuthorityCache
    lineage: LineageCache
    statistics: StatisticsCache
    sqlite: SQLiteLookupCache
    reconciliation: ReconciliationCache
    deduplication: DeduplicationCache
    manifest: ManifestCache
    history: HistoryCache

    def verify(
        self,
        *,
        verify_payloads: bool = True,
    ) -> CacheVerification:
        """Verify all persistent cache namespaces."""

        return self.manager.verify(
            verify_payloads=(
                verify_payloads
            )
        )

    def prune(
        self,
        *,
        delete_stale: bool = False,
    ) -> CachePruneResult:
        """Prune all cache namespaces."""

        return self.manager.prune(
            delete_stale=delete_stale
        )

    def usage(self) -> dict[str, Any]:
        """Return combined cache usage."""

        return self.manager.usage()

    def clear(self) -> int:
        """Clear all cache data."""

        return self.manager.clear()

    def close(
        self,
        *,
        prune: bool = False,
    ) -> None:
        """Perform optional shutdown pruning."""

        if prune:
            self.manager.prune()


def create_cache_bundle(
    root: Path,
    *,
    default_policy: CachePolicy | None = None,
    namespace_policies: Mapping[
        str,
        CachePolicy
        | Mapping[str, Any]
    ] | None = None,
    memory_maximum_entries: int = (
        DEFAULT_MEMORY_MAX_ENTRIES
    ),
    memory_maximum_bytes: int = (
        DEFAULT_MEMORY_MAX_BYTES
    ),
    disk_maximum_entries: int = (
        DEFAULT_DISK_MAX_ENTRIES
    ),
    disk_maximum_bytes: int = (
        DEFAULT_DISK_MAX_BYTES
    ),
    prune_interval_seconds: int = (
        DEFAULT_PRUNE_INTERVAL_SECONDS
    ),
    auto_prune: bool = True,
) -> CacheBundle:
    """Create a complete Speciedex cache bundle."""

    policies = (
        dict(
            namespace_policies
        )
        if namespace_policies is not None
        else default_namespace_policies()
    )

    manager = CacheManager(
        Path(root),
        default_policy=(
            default_policy
            if default_policy
            is not None
            else CachePolicy()
        ),
        namespace_policies=policies,
        memory_maximum_entries=(
            memory_maximum_entries
        ),
        memory_maximum_bytes=(
            memory_maximum_bytes
        ),
        disk_maximum_entries=(
            disk_maximum_entries
        ),
        disk_maximum_bytes=(
            disk_maximum_bytes
        ),
        prune_interval_seconds=(
            prune_interval_seconds
        ),
        auto_prune=auto_prune,
    )

    return CacheBundle(
        manager=manager,
        http=HTTPResponseCache(
            manager,
            policy=manager.policy_for(
                "http"
            ),
        ),
        providers=ProviderCache(
            manager,
            policy=manager.policy_for(
                "providers"
            ),
        ),
        batches=BatchCache(
            manager,
            policy=manager.policy_for(
                "batches"
            ),
        ),
        taxonomy=TaxonomyCache(
            manager,
            policy=manager.policy_for(
                "taxonomy"
            ),
        ),
        synonyms=SynonymCache(
            manager,
            policy=manager.policy_for(
                "synonyms"
            ),
        ),
        authority=AuthorityCache(
            manager,
            policy=manager.policy_for(
                "authority"
            ),
        ),
        lineage=LineageCache(
            manager,
            policy=manager.policy_for(
                "lineage"
            ),
        ),
        statistics=StatisticsCache(
            manager,
            policy=manager.policy_for(
                "statistics"
            ),
        ),
        sqlite=SQLiteLookupCache(
            manager,
            policy=manager.policy_for(
                "sqlite"
            ),
        ),
        reconciliation=(
            ReconciliationCache(
                manager,
                policy=(
                    manager.policy_for(
                        "reconciliation"
                    )
                ),
            )
        ),
        deduplication=(
            DeduplicationCache(
                manager,
                policy=(
                    manager.policy_for(
                        "deduplication"
                    )
                ),
            )
        ),
        manifest=ManifestCache(
            manager,
            policy=manager.policy_for(
                "manifest"
            ),
        ),
        history=HistoryCache(
            manager,
            policy=manager.policy_for(
                "history"
            ),
        ),
    )


_default_cache_bundle: (
    CacheBundle
    | None
) = None

_default_cache_lock = (
    threading.RLock()
)


def get_default_cache(
    root: Path | None = None,
) -> CacheBundle:
    """
    Return the process-wide default cache bundle.

    The first call must provide a root unless the SPECIEDEX_CACHE_ROOT
    environment variable is configured.
    """

    global _default_cache_bundle

    with _default_cache_lock:
        if (
            _default_cache_bundle
            is not None
        ):
            return _default_cache_bundle

        configured_root = (
            Path(root)
            if root is not None
            else (
                Path(
                    os.environ[
                        "SPECIEDEX_CACHE_ROOT"
                    ]
                )
                if os.environ.get(
                    "SPECIEDEX_CACHE_ROOT"
                )
                else None
            )
        )

        if configured_root is None:
            raise CacheConfigurationError(
                "A default cache root was not "
                "provided and "
                "SPECIEDEX_CACHE_ROOT is unset."
            )

        _default_cache_bundle = (
            create_cache_bundle(
                configured_root
            )
        )

        return _default_cache_bundle


def set_default_cache(
    bundle: CacheBundle | None,
) -> None:
    """Replace or clear the process-wide default cache."""

    global _default_cache_bundle

    with _default_cache_lock:
        _default_cache_bundle = bundle


def close_default_cache(
    *,
    prune: bool = False,
) -> None:
    """Close and clear the process-wide default cache."""

    global _default_cache_bundle

    with _default_cache_lock:
        if (
            _default_cache_bundle
            is not None
        ):
            _default_cache_bundle.close(
                prune=prune
            )

        _default_cache_bundle = None


@contextmanager
def cache_bundle(
    root: Path,
    **kwargs: Any,
) -> Iterator[CacheBundle]:
    """Create and automatically close a temporary cache bundle."""

    bundle = create_cache_bundle(
        root,
        **kwargs,
    )

    try:
        yield bundle

    finally:
        bundle.close()


def cached(
    *,
    namespace: str,
    key_builder: Callable[
        ...,
        Any
    ] | None = None,
    policy: CachePolicy | None = None,
    ttl_seconds: int | None = None,
    stale_ttl_seconds: int | None = None,
    allow_stale: bool = False,
    tags: Iterable[str] | None = None,
    root: Path | None = None,
) -> Callable[
    [Callable[..., ValueType]],
    Callable[..., ValueType],
]:
    """
    Module-level caching decorator using the default cache bundle.
    """

    bundle = get_default_cache(
        root
    )

    return bundle.manager.cached(
        namespace=namespace,
        key_builder=key_builder,
        policy=policy,
        ttl_seconds=ttl_seconds,
        stale_ttl_seconds=(
            stale_ttl_seconds
        ),
        allow_stale=allow_stale,
        tags=tags,
    )


__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "BatchCache",
    "CACHE_COMPRESSED_SUFFIX",
    "CACHE_FILE_SUFFIX",
    "CACHE_KEY_PREFIX",
    "CACHE_LOCK_SUFFIX",
    "CACHE_SCHEMA_VERSION",
    "COMPRESSION_GZIP",
    "COMPRESSION_NONE",
    "CacheBundle",
    "CacheConfigurationError",
    "CacheEntry",
    "CacheEntryMetadata",
    "CacheError",
    "CacheExportResult",
    "CacheFileLock",
    "CacheImportResult",
    "CacheIntegrityError",
    "CacheKeyError",
    "CacheLockError",
    "CacheManager",
    "CachePaths",
    "CachePolicy",
    "CachePruneResult",
    "CacheRebuildResult",
    "CacheSerializationError",
    "CacheStatistics",
    "CacheVerification",
    "CacheWarmResult",
    "CacheWarmTask",
    "CachedBatch",
    "DeduplicationCache",
    "DEFAULT_COMPRESSION_THRESHOLD_BYTES",
    "DEFAULT_DISK_MAX_BYTES",
    "DEFAULT_DISK_MAX_ENTRIES",
    "DEFAULT_LOCK_POLL_SECONDS",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_MEMORY_MAX_BYTES",
    "DEFAULT_MEMORY_MAX_ENTRIES",
    "DEFAULT_NAMESPACE",
    "DEFAULT_PRUNE_INTERVAL_SECONDS",
    "DEFAULT_STALE_LOCK_SECONDS",
    "DEFAULT_STALE_TTL_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "HistoryCache",
    "HTTPResponseCache",
    "HTTPResponseCacheValue",
    "LineageCache",
    "ManifestCache",
    "MemoryLRUCache",
    "NamespaceCache",
    "PersistentDiskCache",
    "ProviderCache",
    "ProviderCacheRecord",
    "ReconciliationCache",
    "SERIALIZER_JSON",
    "SERIALIZER_PICKLE",
    "SERIALIZER_RAW",
    "SQLiteLookupCache",
    "StatisticsCache",
    "SynonymCache",
    "TaxonomyCache",
    "atomic_write_bytes",
    "atomic_write_json",
    "cache_bundle",
    "cache_key",
    "cached",
    "canonicalize_value",
    "close_default_cache",
    "compress_payload",
    "create_cache_bundle",
    "decompress_payload",
    "default_namespace_policies",
    "deserialize_value",
    "estimate_size_bytes",
    "export_cache",
    "get_default_cache",
    "import_cache",
    "normalize_key",
    "normalize_namespace",
    "normalize_space",
    "payload_hash",
    "read_json",
    "rebuild_cache",
    "serialize_value",
    "set_default_cache",
    "stable_hash",
    "stable_json_bytes",
    "utc_now",
    "verify_hash",
    "warm_cache",
]
