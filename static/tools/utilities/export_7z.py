#!/usr/bin/env python3
"""Compatibility wrapper for 7Z archive export."""

from __future__ import annotations

import sys
from export_archive import main


if __name__ == "__main__":
    arguments = list(sys.argv[1:])
    if "--format" not in arguments:
        arguments.extend(["--format", "7z"])
    raise SystemExit(main(arguments))
