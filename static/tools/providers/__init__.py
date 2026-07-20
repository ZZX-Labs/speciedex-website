#!/usr/bin/env python3
"""
Speciedex.org
static/tools/providers/__init__.py

Speciedex provider plug-in package.

Provider modules expose a public ``Provider`` class derived from
``providers.common.BaseProvider`` and are loaded dynamically through
``providers.loader.load_provider``.

Shared provider infrastructure remains available from:

- providers.common
- providers.loader
"""

from __future__ import annotations

__all__ = [
    "common",
    "loader",
]

__version__ = "1.0.0"
