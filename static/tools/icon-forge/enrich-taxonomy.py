#!/usr/bin/env python3
"""
Speciedex taxonomy enrichment.

Adds stable, structural biological traits to normalized taxonomic records.
These traits are deterministic defaults and are intended for use by the
Speciedex Icon Forge parameter generator.

Expected location:
    static/tools/icon-forge/enrich-taxonomy.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_TRAITS = {
    "Animalia": {
        "cellularity": "multicellular",
        "motility": "mobile",
        "body_plan": "bilateral",
    },
    "Plantae": {
        "cellularity": "multicellular",
        "motility": "sessile",
        "trophic_level": "autotroph",
    },
    "Fungi": {
        "cellularity": "multicellular",
        "motility": "sessile",
        "trophic_level": "heterotroph",
    },
    "Bacteria": {
        "cellularity": "unicellular",
    },
    "Archaea": {
        "cellularity": "unicellular",
    },
    "Viruses": {
        "cellularity": "acellular",
    },
}

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrich normalized Speciedex taxonomy with default structural traits."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)

    count = 0

    with src.open("r", encoding="utf-8") as infile, \
         dst.open("w", encoding="utf-8") as outfile:

        for line in infile:
            if not line.strip():
                continue

            record = json.loads(line)

            traits = dict(record.get("traits") or {})
            lineage = {
                node.get("name")
                for node in record.get("lineage", [])
                if isinstance(node, dict)
            }

            for root_name, defaults in DEFAULT_TRAITS.items():
                if root_name in lineage:
                    for key, value in defaults.items():
                        traits.setdefault(key, value)

            record["traits"] = traits

            outfile.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                ) + "\n"
            )

            count += 1

    print(f"enriched={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
