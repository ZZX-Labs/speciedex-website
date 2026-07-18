#!/usr/bin/env python3
"""Shared provider infrastructure for Speciedex."""
from __future__ import annotations

import json
import os
import random
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def normalize_space(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


class ProviderError(RuntimeError):
    """Raised when a provider cannot return a valid batch."""


@dataclass
class HTTPClient:
    timeout: int = 30
    retries: int = 4
    backoff: float = 2.0
    user_agent: str = "Speciedex.org-StatGrabber/3.0.0"
    requests: int = 0

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if params:
            query = urlencode(
                {
                    key: value
                    for key, value in params.items()
                    if value is not None
                },
                doseq=True,
            )
            url += ("&" if "?" in url else "?") + query

        request_headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        request_headers.update(headers or {})
        request = Request(
            url,
            headers=request_headers,
            method="GET",
        )
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            try:
                self.requests += 1
                with urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    status = getattr(response, "status", 200)
                    if not 200 <= status < 300:
                        raise ProviderError(
                            f"HTTP {status}: {url}"
                        )
                    charset = (
                        response.headers.get_content_charset()
                        or "utf-8"
                    )
                    body = response.read().decode(charset)
                return json.loads(body)
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                json.JSONDecodeError,
                ProviderError,
            ) as error:
                last_error = error
                if attempt >= self.retries:
                    break
                delay = self.backoff ** (attempt - 1)
                delay += random.uniform(0, min(.5, delay / 4))
                time.sleep(delay)

        raise ProviderError(f"{url}: {last_error}")


@dataclass
class Taxon:
    provider: str
    provider_id: str
    scientific_name: str
    canonical_name: str
    rank: str
    status: str = "unknown"
    authorship: str = ""
    kingdom: str = ""
    phylum: str = ""
    class_name: str = ""
    order: str = ""
    family: str = ""
    genus: str = ""
    accepted_provider_id: str = ""
    source_url: str = ""
    source_modified: str = ""
    retrieved_at: str = ""
    synonyms: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["class"] = result.pop("class_name")
        return result


@dataclass
class Batch:
    records: list[Taxon]
    next_cursor: str | None
    exhausted: bool
    requests: int = 0
    raw: int = 0


class BaseProvider:
    """Base class for all provider modules."""

    PROVIDER_NAME = ""

    def __init__(
        self,
        definition: dict[str, Any],
        http: HTTPClient,
        state_path: Path,
        batch_size: int,
        repo_root: Path,
    ) -> None:
        self.definition = definition
        self.http = http
        self.state_path = state_path
        self.state = read_json(state_path, {})
        if not isinstance(self.state, dict):
            self.state = {}
        self.batch_size = batch_size
        self.repo_root = repo_root
        self.name = str(
            definition.get("name")
            or self.PROVIDER_NAME
        )

    @property
    def cursor(self) -> str | None:
        value = self.state.get("cursor")
        return str(value) if value not in (None, "") else None

    def fetch(self) -> Batch:
        raise NotImplementedError

    def save_success(self, batch: Batch) -> None:
        self.state.update(
            {
                "provider": self.name,
                "cursor": batch.next_cursor,
                "bootstrap_complete": batch.exhausted,
                "last_success": now(),
                "last_error": None,
                "last_batch_records": len(batch.records),
                "last_requests": batch.requests,
                "last_raw_records": batch.raw,
            }
        )
        write_json(self.state_path, self.state)

    def save_failure(self, error: Exception) -> None:
        self.state.update(
            {
                "provider": self.name,
                "last_attempt": now(),
                "last_error": str(error),
            }
        )
        write_json(self.state_path, self.state)


class FileJSONLProvider(BaseProvider):
    """Read normalized taxonomic records from a local JSONL export."""

    def fetch(self) -> Batch:
        configured = Path(str(self.definition["path"]))
        path = (
            configured
            if configured.is_absolute()
            else self.repo_root / configured
        )
        if not path.exists():
            raise ProviderError(
                f"Normalized provider export not found: {path}"
            )

        offset = safe_int(self.cursor, 0)
        records: list[Taxon] = []
        raw = 0
        next_offset = offset
        exhausted = True

        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle):
                if line_number < offset:
                    continue
                if len(records) >= self.batch_size:
                    exhausted = False
                    break

                next_offset = line_number + 1
                raw += 1
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue

                provider_id = item.get("id")
                scientific_name = normalize_space(
                    item.get("scientific_name")
                    or item.get("scientificName")
                    or item.get("name")
                )
                if provider_id in (None, "") or not scientific_name:
                    continue

                records.append(
                    Taxon(
                        provider=self.name,
                        provider_id=str(provider_id),
                        scientific_name=scientific_name,
                        canonical_name=normalize_space(
                            item.get("canonical_name")
                            or item.get("canonicalName")
                            or scientific_name
                        ),
                        rank=normalize_space(
                            item.get("rank")
                        ).lower() or "unknown",
                        status=normalize_space(
                            item.get("status")
                        ).lower() or "unknown",
                        authorship=normalize_space(
                            item.get("authorship")
                        ),
                        kingdom=normalize_space(
                            item.get("kingdom")
                        ),
                        phylum=normalize_space(
                            item.get("phylum")
                        ),
                        class_name=normalize_space(
                            item.get("class")
                        ),
                        order=normalize_space(
                            item.get("order")
                        ),
                        family=normalize_space(
                            item.get("family")
                        ),
                        genus=normalize_space(
                            item.get("genus")
                        ),
                        accepted_provider_id=normalize_space(
                            item.get("accepted_id")
                        ),
                        source_url=normalize_space(
                            item.get("source_url")
                        ),
                        source_modified=normalize_space(
                            item.get("modified")
                        ),
                        retrieved_at=now(),
                        synonyms=[
                            normalize_space(value)
                            for value in item.get("synonyms", [])
                            if normalize_space(value)
                        ],
                        extra={
                            "bulk_source": str(path),
                        },
                    )
                )

        return Batch(
            records=records,
            next_cursor=(
                None if exhausted else str(next_offset)
            ),
            exhausted=exhausted,
            requests=0,
            raw=raw,
        )
