#!/usr/bin/env python3
"""
Speciedex Icon Forge

Build sprite sheets from generated PNG icons.

Expected location:

    static/tools/icon-forge/build-sprites.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image


def collect_icons(root: Path) -> list[Path]:
    return sorted(root.rglob("*.png"))


def build_sprite_sheet(
    icons: list[Path],
    output: Path,
    index: Path,
    cell_size: int,
    columns: int,
) -> int:

    if not icons:
        raise RuntimeError("No PNG icons found.")

    rows = math.ceil(len(icons) / columns)

    sheet = Image.new(
        "RGBA",
        (
            columns * cell_size,
            rows * cell_size,
        ),
        (0, 0, 0, 0),
    )

    sprite_index = {
        "cell_size": cell_size,
        "columns": columns,
        "rows": rows,
        "count": len(icons),
        "sprites": {},
    }

    for position, icon in enumerate(icons):

        x = (position % columns) * cell_size
        y = (position // columns) * cell_size

        with Image.open(icon) as image:

            sprite = image.convert("RGBA")

            if sprite.size != (cell_size, cell_size):
                sprite = sprite.resize(
                    (
                        cell_size,
                        cell_size,
                    ),
                    Image.Resampling.LANCZOS,
                )

        sheet.alpha_composite(
            sprite,
            (x, y),
        )

        key = icon.stem

        sprite_index["sprites"][key] = {
            "source": icon.as_posix(),
            "x": x,
            "y": y,
            "width": cell_size,
            "height": cell_size,
            "column": position % columns,
            "row": position // columns,
        }

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    sheet.save(
        output,
        "PNG",
        optimize=True,
        compress_level=9,
    )

    index.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    index.write_text(
        json.dumps(
            sprite_index,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return len(icons)


def parser() -> argparse.ArgumentParser:

    p = argparse.ArgumentParser(
        description="Generate Speciedex sprite sheets."
    )

    p.add_argument(
        "--input",
        required=True,
    )

    p.add_argument(
        "--output",
        required=True,
    )

    p.add_argument(
        "--index",
        required=True,
    )

    p.add_argument(
        "--cell-size",
        type=int,
        default=64,
    )

    p.add_argument(
        "--columns",
        type=int,
        default=16,
    )

    return p


def main() -> int:

    args = parser().parse_args()

    icons = collect_icons(
        Path(args.input)
    )

    count = build_sprite_sheet(
        icons=icons,
        output=Path(args.output),
        index=Path(args.index),
        cell_size=args.cell_size,
        columns=args.columns,
    )

    print(
        f"sprites={count}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
