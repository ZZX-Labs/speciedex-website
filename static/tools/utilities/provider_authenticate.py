#!/usr/bin/env python3
"""Securely load bundled Speciedex provider credentials.

The recommended GitHub secret is:

    SPECIEDEX_PROVIDER_CREDENTIALS

Its value is one JSON object containing provider environment variables.
This utility:

* validates the JSON object;
* derives an allowlist from providers.json and built-in infrastructure names;
* masks every secret value in GitHub Actions logs;
* writes values only to the ephemeral GITHUB_ENV file;
* never prints secret values;
* optionally checks repository files for accidental exact-value leakage.

Example secret value:

    {
      "EOL_API_KEY": "...",
      "IUCN_API_TOKEN": "...",
      "BACDIVE_USERNAME": "...",
      "BACDIVE_PASSWORD": "...",
      "SPECIEDEX_MARIADB_HOST": "...",
      "SPECIEDEX_MARIADB_DATABASE": "...",
      "SPECIEDEX_MARIADB_USERNAME": "...",
      "SPECIEDEX_MARIADB_PASSWORD": "..."
    }
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY = Path("static/tools/providers.json")
DEFAULT_SECRET_ENV = "SPECIEDEX_PROVIDER_CREDENTIALS"
VALID_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
MAX_VALUE_BYTES = 64 * 1024

BUILTIN_ALLOWED = {
    "EOL_API_KEY",
    "IUCN_API_TOKEN",
    "NATURESERVE_API_KEY",
    "NCBI_API_KEY",
    "BACDIVE_USERNAME",
    "BACDIVE_PASSWORD",
    "BHL_API_KEY",
    "GEONAMES_USERNAME",
    "YOUTUBE_API_KEY",
    "GOOGLE_API_KEY",
    "SPECIES_PLUS_API_TOKEN",
    "TROPICOS_API_KEY",
    "ALA_API_KEY",
    "EBIRD_API_KEY",
    "GBIF_USERNAME",
    "GBIF_PASSWORD",
    "GBIF_INSTALLATION_KEY",
    "SPECIEDEX_MARIADB_HOST",
    "SPECIEDEX_MARIADB_PORT",
    "SPECIEDEX_MARIADB_DATABASE",
    "SPECIEDEX_MARIADB_USERNAME",
    "SPECIEDEX_MARIADB_PASSWORD",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and validate bundled Speciedex credentials."
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--secret-env", default=DEFAULT_SECRET_ENV)
    parser.add_argument(
        "--github-env",
        type=Path,
        default=Path(os.environ["GITHUB_ENV"])
        if os.environ.get("GITHUB_ENV")
        else None,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate without exporting credentials.",
    )
    parser.add_argument(
        "--require-bundle",
        action="store_true",
        help="Fail when the credential bundle is absent.",
    )
    parser.add_argument(
        "--scan-repository",
        type=Path,
        default=None,
        help="Scan text files for exact credential-value leakage.",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Additional approved environment variable name.",
    )
    return parser.parse_args(argv)


def load_registry_allowlist(path: Path) -> set[str]:
    allowed = set(BUILTIN_ALLOWED)

    if not path.is_file():
        return allowed

    value = json.loads(path.read_text(encoding="utf-8"))
    providers = value.get("providers", [])
    if not isinstance(providers, list):
        raise SystemExit(
            f"{path}: providers must be an array"
        )

    for definition in providers:
        if not isinstance(definition, Mapping):
            continue
        for field in (
            "required_env",
            "optional_env",
            "credentials",
            "environment",
        ):
            names = definition.get(field, [])
            if isinstance(names, str):
                names = [names]
            if not isinstance(names, list):
                continue
            for name in names:
                normalized = str(name).strip()
                if VALID_ENV_NAME.fullmatch(normalized):
                    allowed.add(normalized)

    return allowed


def load_bundle(environment_name: str) -> dict[str, str]:
    raw = os.environ.get(environment_name, "").strip()
    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit(
            f"{environment_name} is invalid JSON at "
            f"line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(value, dict):
        raise SystemExit(
            f"{environment_name} must contain one JSON object."
        )

    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip()

        if not VALID_ENV_NAME.fullmatch(name):
            raise SystemExit(
                f"Invalid credential variable name: {name!r}"
            )

        if raw_value is None:
            continue

        if isinstance(raw_value, (dict, list)):
            raise SystemExit(
                f"Credential {name} must be a scalar string value."
            )

        text = str(raw_value)
        if not text.strip():
            continue

        encoded = text.encode("utf-8")
        if len(encoded) > MAX_VALUE_BYTES:
            raise SystemExit(
                f"Credential {name} exceeds {MAX_VALUE_BYTES} bytes."
            )

        if "\x00" in text or "\r" in text:
            raise SystemExit(
                f"Credential {name} contains prohibited control characters."
            )

        result[name] = text

    return result


def github_mask(value: str) -> None:
    print(f"::add-mask::{value}")


def append_github_environment(
    path: Path,
    credentials: Mapping[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for name, value in sorted(credentials.items()):
            delimiter = "SPECIEDEX_" + secrets.token_hex(16).upper()
            while delimiter in value:
                delimiter = "SPECIEDEX_" + secrets.token_hex(16).upper()

            handle.write(f"{name}<<{delimiter}\n")
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.write(f"{delimiter}\n")


def likely_text_file(path: Path) -> bool:
    if path.suffix.casefold() in {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
        ".zip", ".7z", ".rar", ".gz", ".xz", ".tar",
        ".sqlite", ".sqlite3", ".db", ".pdf", ".doc", ".docx",
        ".epub", ".woff", ".woff2", ".ttf", ".otf",
    }:
        return False
    return True


def scan_for_leaks(
    root: Path,
    credentials: Mapping[str, str],
) -> list[str]:
    findings: list[str] = []
    values = [
        (name, value)
        for name, value in credentials.items()
        if len(value) >= 6
    ]

    for path in sorted(root.rglob("*")):
        if not path.is_file() or not likely_text_file(path):
            continue
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        except OSError:
            continue

        for name, value in values:
            if value in text:
                findings.append(
                    f"{path.as_posix()}: exact value for {name}"
                )

    return findings


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    allowed = load_registry_allowlist(args.registry)
    for name in args.allow:
        normalized = name.strip()
        if not VALID_ENV_NAME.fullmatch(normalized):
            raise SystemExit(
                f"Invalid --allow name: {name!r}"
            )
        allowed.add(normalized)

    credentials = load_bundle(args.secret_env)

    if not credentials:
        if args.require_bundle:
            raise SystemExit(
                f"No credential bundle found in {args.secret_env}."
            )
        print(json.dumps({
            "bundle_present": False,
            "credentials_loaded": 0,
            "exported": False,
        }, indent=2, sort_keys=True))
        return 0

    unknown = sorted(set(credentials) - allowed)
    if unknown:
        raise SystemExit(
            "Credential bundle contains unapproved variable names: "
            + ", ".join(unknown)
        )

    for value in credentials.values():
        github_mask(value)

    leaks: list[str] = []
    if args.scan_repository is not None:
        leaks = scan_for_leaks(
            args.scan_repository,
            credentials,
        )
        if leaks:
            raise SystemExit(
                "Credential leakage detected:\n"
                + "\n".join(f"  {finding}" for finding in leaks)
            )

    exported = False
    if not args.check_only:
        if args.github_env is None:
            raise SystemExit(
                "GITHUB_ENV is unavailable. Use --check-only outside GitHub Actions."
            )
        append_github_environment(
            args.github_env,
            credentials,
        )
        exported = True

    print(json.dumps({
        "bundle_present": True,
        "credentials_loaded": len(credentials),
        "credential_names": sorted(credentials),
        "exported": exported,
        "repository_scan": (
            args.scan_repository.as_posix()
            if args.scan_repository is not None
            else None
        ),
        "leaks_detected": len(leaks),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
