#!/usr/bin/env python3
"""Dynamic provider module loader."""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from .common import BaseProvider, HTTPClient, ProviderError


VALID_PROVIDER_NAME = re.compile(r"^[a-z0-9_]+$")


def load_provider(
    definition: dict[str, Any],
    http: HTTPClient,
    state_path: Path,
    batch_size: int,
    repo_root: Path,
) -> BaseProvider:
    name = str(definition.get("name", "")).strip()
    if not VALID_PROVIDER_NAME.fullmatch(name):
        raise ProviderError(
            f"Invalid provider module name: {name!r}"
        )

    try:
        module = importlib.import_module(
            f"providers.{name}"
        )
    except ModuleNotFoundError as error:
        raise ProviderError(
            f"Provider module not found: providers/{name}.py"
        ) from error

    provider_class = getattr(module, "Provider", None)
    if provider_class is None:
        raise ProviderError(
            f"Provider class missing from providers/{name}.py"
        )
    if not issubclass(provider_class, BaseProvider):
        raise ProviderError(
            f"Provider in providers/{name}.py does not inherit "
            "BaseProvider"
        )

    return provider_class(
        definition,
        http,
        state_path,
        batch_size,
        repo_root,
    )
