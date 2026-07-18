#!/usr/bin/env python3
"""Global Names provider.

This provider currently consumes a normalized JSONL export configured in
providers.json. Replace or extend Provider.fetch() when a current, licensed,
stable public API or bulk-release workflow is confirmed.
"""
from __future__ import annotations
from .common import FileJSONLProvider


class Provider(FileJSONLProvider):
    PROVIDER_NAME = "global_names"
