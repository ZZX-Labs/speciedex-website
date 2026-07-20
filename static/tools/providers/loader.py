#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/loader.py

Dynamic provider module loader.

This module validates provider definitions, imports provider plug-ins, verifies
their Provider class, and constructs a BaseProvider instance using the shared
provider runtime contract.

Provider modules are expected to expose:

    class Provider(BaseProvider):
        ...

By default, a provider named ``gbif`` loads ``providers.gbif``. A registry
entry may optionally specify an explicit module path through ``module`` so long
as it remains inside the ``providers`` package.

Copyright (c) 2026 ZZX-Laboratories
Licensed under the MIT License.
"""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, TypeVar, cast

from .common import BaseProvider, HTTPClient, ProviderError


VALID_PROVIDER_NAME = re.compile(r"^[a-z0-9_]+$")
VALID_PROVIDER_MODULE = re.compile(
    r"^providers(?:\.[a-z_][a-z0-9_]*)+$"
)

ProviderType = TypeVar(
    "ProviderType",
    bound=BaseProvider,
)


def normalize_provider_name(value: Any) -> str:
    """Normalize and validate one provider registry name."""

    name = str(
        value
        if value is not None
        else ""
    ).strip().casefold()

    if not name:
        raise ProviderError(
            "Provider definition has no name."
        )

    if not VALID_PROVIDER_NAME.fullmatch(name):
        raise ProviderError(
            "Invalid provider name "
            f"{name!r}; expected lowercase letters, "
            "digits, and underscores only."
        )

    return name


def provider_module_name(
    definition: Mapping[str, Any],
) -> str:
    """
    Resolve the Python module path for one provider definition.

    The explicit ``module`` value may be either a short provider module name
    such as ``gbif`` or a fully qualified name such as ``providers.gbif``.
    Imports outside the ``providers`` package are rejected.
    """

    name = normalize_provider_name(
        definition.get("name")
    )

    configured = str(
        definition.get(
            "module",
            "",
        )
        or ""
    ).strip()

    if not configured:
        return f"providers.{name}"

    module_name = configured.casefold()

    if "." not in module_name:
        module_name = (
            f"providers.{module_name}"
        )

    if not VALID_PROVIDER_MODULE.fullmatch(
        module_name
    ):
        raise ProviderError(
            "Invalid provider module path "
            f"{configured!r}; provider modules must "
            "remain inside the providers package."
        )

    return module_name


def import_provider_module(
    definition: Mapping[str, Any],
) -> ModuleType:
    """Import and return one validated provider module."""

    module_name = provider_module_name(
        definition
    )

    try:
        return importlib.import_module(
            module_name
        )

    except ModuleNotFoundError as error:
        # Only convert the error when the provider module itself is missing.
        # A missing dependency imported by the provider must be reported
        # accurately rather than disguised as a missing provider module.
        if error.name == module_name:
            raise ProviderError(
                "Provider module not found: "
                f"{module_name.replace('.', '/')}.py"
            ) from error

        raise ProviderError(
            f"Provider module {module_name!r} has a "
            "missing dependency: "
            f"{error.name or error}"
        ) from error

    except ImportError as error:
        raise ProviderError(
            f"Unable to import provider module "
            f"{module_name!r}: {error}"
        ) from error

    except Exception as error:
        raise ProviderError(
            f"Provider module {module_name!r} raised "
            f"{type(error).__name__} during import: "
            f"{error}"
        ) from error


def provider_class_from_module(
    module: ModuleType,
    definition: Mapping[str, Any],
) -> type[BaseProvider]:
    """Return and validate the module's public Provider class."""

    name = normalize_provider_name(
        definition.get("name")
    )

    provider_class = getattr(
        module,
        "Provider",
        None,
    )

    if provider_class is None:
        raise ProviderError(
            f"Provider class missing from "
            f"{module.__name__.replace('.', '/')}.py; "
            "expected a public class named Provider."
        )

    if not inspect.isclass(
        provider_class
    ):
        raise ProviderError(
            f"{module.__name__}.Provider is not a class."
        )

    if provider_class is BaseProvider:
        raise ProviderError(
            f"{module.__name__}.Provider must be a "
            "concrete BaseProvider subclass."
        )

    if not issubclass(
        provider_class,
        BaseProvider,
    ):
        raise ProviderError(
            f"Provider in "
            f"{module.__name__.replace('.', '/')}.py "
            "does not inherit BaseProvider."
        )

    provider_name = str(
        getattr(
            provider_class,
            "PROVIDER_NAME",
            "",
        )
        or ""
    ).strip().casefold()

    if (
        provider_name
        and provider_name != name
    ):
        raise ProviderError(
            f"Provider name mismatch: registry={name!r}, "
            f"class={provider_name!r}, "
            f"module={module.__name__!r}."
        )

    fetch = getattr(
        provider_class,
        "fetch",
        None,
    )

    if (
        fetch is None
        or not callable(fetch)
        or fetch is BaseProvider.fetch
    ):
        raise ProviderError(
            f"{module.__name__}.Provider must implement "
            "fetch()."
        )

    return cast(
        type[BaseProvider],
        provider_class,
    )


def load_provider(
    definition: Mapping[str, Any],
    http: HTTPClient,
    state_path: Path,
    batch_size: int,
    repo_root: Path,
) -> BaseProvider:
    """
    Load and construct one provider plug-in.

    Args:
        definition:
            Provider registry definition.
        http:
            Shared HTTP client.
        state_path:
            Persistent provider-state path.
        batch_size:
            Maximum records requested for one batch.
        repo_root:
            Repository root used by file-backed providers.

    Returns:
        A fully constructed BaseProvider subclass instance.

    Raises:
        ProviderError:
            When validation, import, class verification, or construction fails.
    """

    if not isinstance(
        definition,
        Mapping,
    ):
        raise ProviderError(
            "Provider definition must be a mapping."
        )

    if not isinstance(
        http,
        HTTPClient,
    ):
        raise ProviderError(
            "Provider loader requires an HTTPClient instance."
        )

    try:
        parsed_batch_size = int(
            batch_size
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ProviderError(
            "Provider batch_size must be an integer."
        ) from error

    if parsed_batch_size < 1:
        raise ProviderError(
            "Provider batch_size must be positive."
        )

    normalized_definition = dict(
        definition
    )
    normalized_definition["name"] = (
        normalize_provider_name(
            definition.get("name")
        )
    )

    module = import_provider_module(
        normalized_definition
    )

    provider_class = (
        provider_class_from_module(
            module,
            normalized_definition,
        )
    )

    normalized_state_path = Path(
        state_path
    )
    normalized_repo_root = Path(
        repo_root
    )

    try:
        provider = provider_class(
            normalized_definition,
            http,
            normalized_state_path,
            parsed_batch_size,
            normalized_repo_root,
        )

    except ProviderError:
        raise

    except Exception as error:
        raise ProviderError(
            "Unable to initialize provider "
            f"{normalized_definition['name']!r} "
            f"from {module.__name__!r}: "
            f"{type(error).__name__}: {error}"
        ) from error

    if not isinstance(
        provider,
        BaseProvider,
    ):
        raise ProviderError(
            f"{module.__name__}.Provider returned an "
            "invalid provider instance."
        )

    if provider.name.casefold() != (
        normalized_definition["name"]
    ):
        raise ProviderError(
            "Constructed provider name mismatch: "
            f"expected={normalized_definition['name']!r}, "
            f"actual={provider.name!r}."
        )

    return provider
