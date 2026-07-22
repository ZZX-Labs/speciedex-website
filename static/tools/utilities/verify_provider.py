#!/usr/bin/env python3
"""Verify Speciedex provider definitions and modules without network access."""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY = Path("static/tools/providers.json")
DEFAULT_TOOLS_ROOT = Path("static/tools")
VALID_NAME = re.compile(r"^[a-z0-9_]+$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("providers", nargs="*")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--tools-root", type=Path, default=DEFAULT_TOOLS_ROOT)
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--check-files", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"Provider registry not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    providers = value.get("providers")
    if not isinstance(providers, list):
        raise SystemExit(f"{path}: root.providers must be an array")
    result: list[dict[str, Any]] = []
    for index, definition in enumerate(providers):
        if not isinstance(definition, dict):
            raise SystemExit(f"{path}: providers[{index}] must be an object")
        result.append(dict(definition))
    return result


def select(definitions: Sequence[Mapping[str, Any]], requested: Sequence[str]) -> list[dict[str, Any]]:
    by_name = {str(item.get("name", "")).strip(): dict(item) for item in definitions}
    if not requested:
        return [by_name[name] for name in sorted(by_name) if name]
    names = [name.strip() for name in requested if name.strip()]
    missing = sorted(set(names) - set(by_name))
    if missing:
        raise SystemExit("Unknown providers: " + ", ".join(missing))
    return [by_name[name] for name in names]


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def verify(definition: Mapping[str, Any], tools_root: Path, require_enabled: bool, check_files: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    name = str(definition.get("name", "")).strip()
    if not VALID_NAME.fullmatch(name):
        return [f"invalid provider name: {name!r}"], warnings

    enabled = as_bool(definition.get("enabled"), True)
    if require_enabled and not enabled:
        errors.append("provider is disabled")
    elif not enabled:
        warnings.append("provider is disabled")

    module_path = tools_root / "providers" / f"{name}.py"
    if not module_path.is_file():
        return [f"missing provider module: {module_path}"], warnings

    module_name = str(definition.get("module", f"providers.{name}")).strip()
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        return [f"module import failed: {type(error).__name__}: {error}"], warnings

    provider_class = getattr(module, "Provider", None)
    if provider_class is None or not inspect.isclass(provider_class):
        return ["module does not export Provider class"], warnings
    if not callable(getattr(provider_class, "fetch", None)):
        errors.append("Provider.fetch is missing or not callable")

    provider_name = str(getattr(provider_class, "PROVIDER_NAME", getattr(provider_class, "NAME", ""))).strip()
    if provider_name and provider_name != name:
        errors.append(f"provider class name mismatch: {provider_name!r}")

    required_env = definition.get("required_env", [])
    if required_env is not None and not isinstance(required_env, list):
        errors.append("required_env must be an array")

    if check_files:
        for key in ("dataset_path", "source_path", "response_schema_path"):
            value = str(definition.get(key, "")).strip()
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            if not candidate.exists():
                warnings.append(f"configured {key} is absent: {candidate}")
    return errors, warnings


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.tools_root.is_dir():
        raise SystemExit(f"Tools root not found: {args.tools_root}")
    sys.path.insert(0, str(args.tools_root.resolve()))

    reports: list[dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    for definition in select(load_registry(args.registry), args.providers):
        name = str(definition.get("name", "")).strip()
        errors, warnings = verify(definition, args.tools_root, args.require_enabled, args.check_files)
        total_errors += len(errors)
        total_warnings += len(warnings)
        reports.append({"provider": name, "ok": not errors, "errors": errors, "warnings": warnings})

    ok = total_errors == 0 and (not args.strict or total_warnings == 0)
    print(json.dumps({"providers_checked": len(reports), "errors": total_errors, "warnings": total_warnings, "ok": ok, "providers": reports}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
