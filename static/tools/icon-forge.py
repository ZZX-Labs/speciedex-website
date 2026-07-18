#!/usr/bin/env python3
"""
Speciedex Icon Forge
====================

Deterministically generates a unique PNG glyph for every taxonomic node:
domain, kingdom, phylum, class, order, family, genus, species, subspecies,
strain, clade, taxon, and arbitrary custom ranks.

Design goals
------------
1. Identical normalized taxonomic input always produces the same icon.
2. Different taxonomic identities produce different visual glyphs.
3. Lineage is visible: descendants inherit stable visual features from ancestors.
4. Biological traits influence motif selection, symmetry, branching, density,
   ornament, and palette.
5. Every image is rendered inside the Speciedex rounded hexagonal badge.
6. PNG only. No SVG dependency.
7. Output can be generated one-at-a-time, from JSON/JSONL/CSV, or as a sprite sheet.
8. A machine-readable manifest records hashes and generation parameters.

Dependencies
------------
    pip install pillow

Examples
--------
Single icon:
    python static/tools/icon-forge.py generate \
      --scientific-name "Panthera leo" \
      --rank species \
      --lineage "Eukaryota|Animalia|Chordata|Mammalia|Carnivora|Felidae|Panthera|Panthera leo" \
      --traits '{"habitat":["terrestrial"],"trophic_level":"carnivore","motility":"mobile"}' \
      --output static/images/taxa/panthera-leo.png

Batch JSONL:
    python static/tools/icon-forge.py batch \
      --input taxa.jsonl \
      --output-dir static/images/taxa \
      --manifest static/data/icon-manifest.json

Sprite sheet:
    python static/tools/icon-forge.py sprite \
      --input-dir static/images/taxa \
      --output static/images/taxa-sprite.png \
      --index static/data/taxa-sprite.json \
      --cell-size 128 \
      --columns 16

Input JSON object:
{
  "id": "gbif:5219404",
  "scientific_name": "Panthera leo",
  "rank": "species",
  "lineage": [
    {"rank":"domain","name":"Eukaryota"},
    {"rank":"kingdom","name":"Animalia"},
    {"rank":"phylum","name":"Chordata"},
    {"rank":"class","name":"Mammalia"},
    {"rank":"order","name":"Carnivora"},
    {"rank":"family","name":"Felidae"},
    {"rank":"genus","name":"Panthera"},
    {"rank":"species","name":"Panthera leo"}
  ],
  "traits": {
    "habitat": ["terrestrial"],
    "motility": "mobile",
    "trophic_level": "carnivore",
    "body_plan": "bilateral",
    "cellularity": "multicellular"
  }
}
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError as exc:
    raise SystemExit("Pillow is required: pip install pillow") from exc


GENERATOR_VERSION = "1.0.0"
DEFAULT_SIZE = 1024

# Speciedex house palette, derived from the established badge family.
NAVY_OUTER = (7, 35, 61, 255)
NAVY_INNER = (8, 44, 72, 255)
NAVY_DEEP = (4, 28, 50, 255)
GOLD = (244, 202, 83, 255)
GOLD_LIGHT = (250, 222, 126, 255)
TEAL = (74, 202, 199, 255)
TEAL_LIGHT = (138, 231, 218, 255)
TEAL_DARK = (27, 132, 148, 255)
MINT = (167, 239, 214, 255)
WHITE = (241, 250, 248, 255)
TRANSPARENT = (0, 0, 0, 0)

RANK_ORDER = {
    "domain": 0,
    "superkingdom": 0,
    "kingdom": 1,
    "subkingdom": 2,
    "phylum": 3,
    "division": 3,
    "subphylum": 4,
    "class": 5,
    "subclass": 6,
    "order": 7,
    "suborder": 8,
    "family": 9,
    "subfamily": 10,
    "tribe": 11,
    "genus": 12,
    "subgenus": 13,
    "species": 14,
    "subspecies": 15,
    "variety": 16,
    "form": 17,
    "strain": 18,
    "cultivar": 18,
    "isolate": 19,
    "clade": 8,
    "taxon": 12,
    "unranked": 12,
}

# Root shape language. A descendant retains selected ancestor parameters,
# allowing a family resemblance without duplicating the parent's glyph.
RANK_MOTIFS = {
    "domain": "mandala",
    "superkingdom": "mandala",
    "kingdom": "crown",
    "subkingdom": "crown",
    "phylum": "coral",
    "division": "coral",
    "subphylum": "coral",
    "class": "crystal",
    "subclass": "crystal",
    "order": "orbit",
    "suborder": "orbit",
    "family": "tree",
    "subfamily": "tree",
    "tribe": "knot",
    "genus": "snowflake",
    "subgenus": "snowflake",
    "species": "sigil",
    "subspecies": "sigil",
    "variety": "petal",
    "form": "petal",
    "strain": "cell",
    "cultivar": "cell",
    "isolate": "cell",
    "clade": "branch",
    "taxon": "glyph",
    "unranked": "glyph",
}

HABITAT_BITS = {
    "marine": 1,
    "freshwater": 2,
    "aquatic": 3,
    "terrestrial": 4,
    "soil": 5,
    "arboreal": 6,
    "aerial": 7,
    "subterranean": 8,
    "cave": 9,
    "desert": 10,
    "polar": 11,
    "tundra": 12,
    "forest": 13,
    "grassland": 14,
    "wetland": 15,
    "host-associated": 16,
    "parasitic": 17,
    "extremophile": 18,
}

TRAIT_ALIASES = {
    "habitats": "habitat",
    "environment": "habitat",
    "environments": "habitat",
    "feeding": "trophic_level",
    "diet": "trophic_level",
    "locomotion": "motility",
    "symmetry": "body_plan",
    "cell_type": "cellularity",
}


@dataclass(frozen=True)
class TaxonRecord:
    scientific_name: str
    rank: str
    lineage: tuple[tuple[str, str], ...]
    traits: Mapping[str, Any]
    identifier: str = ""
    common_name: str = ""

    @property
    def canonical_identity(self) -> str:
        lineage_text = "|".join(f"{r}:{n}" for r, n in self.lineage)
        trait_text = canonical_json(self.traits)
        return (
            f"speciedex-icon-v{GENERATOR_VERSION}|id={self.identifier}|"
            f"name={self.scientific_name}|rank={self.rank}|"
            f"lineage={lineage_text}|traits={trait_text}"
        )


@dataclass(frozen=True)
class IconParameters:
    identity_sha256: str
    lineage_sha256: str
    rank: str
    motif: str
    symmetry: int
    ring_count: int
    branch_depth: int
    branch_factor: int
    radial_phase: float
    angular_jitter: float
    density: float
    stroke_ratio: float
    core_ratio: float
    ornament_count: int
    habitat_mask: int
    trait_mask: int
    palette_variant: int


class HashStream:
    """Counter-mode SHA-256 pseudo-random byte stream."""

    def __init__(self, seed: bytes):
        self.seed = seed
        self.counter = 0
        self.buffer = bytearray()

    def _fill(self) -> None:
        block = hashlib.sha256(
            self.seed + self.counter.to_bytes(8, "big", signed=False)
        ).digest()
        self.counter += 1
        self.buffer.extend(block)

    def bytes(self, n: int) -> bytes:
        while len(self.buffer) < n:
            self._fill()
        out = bytes(self.buffer[:n])
        del self.buffer[:n]
        return out

    def uint(self, bits: int = 32) -> int:
        n = max(1, (bits + 7) // 8)
        return int.from_bytes(self.bytes(n), "big") & ((1 << bits) - 1)

    def unit(self) -> float:
        return self.uint(53) / float(1 << 53)

    def integer(self, low: int, high: int) -> int:
        if high < low:
            low, high = high, low
        span = high - low + 1
        return low + (self.uint(64) % span)

    def choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise ValueError("Cannot choose from an empty sequence")
        return values[self.uint(32) % len(values)]

    def signed(self) -> float:
        return self.unit() * 2.0 - 1.0


def canonical_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def canonical_rank(rank: str) -> str:
    value = canonical_text(rank).lower().replace(" ", "_").replace("-", "_")
    return value or "unranked"


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            canonical_text(k): normalize_json(v)
            for k, v in sorted(value.items(), key=lambda kv: canonical_text(kv[0]))
        }
    if isinstance(value, (list, tuple, set)):
        normalized = [normalize_json(v) for v in value]
        return sorted(normalized, key=lambda x: canonical_json(x))
    if isinstance(value, str):
        return canonical_text(value).lower()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return canonical_text(value)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", canonical_text(text))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "taxon"


def normalize_traits(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        k = canonical_text(key).lower().replace(" ", "_").replace("-", "_")
        k = TRAIT_ALIASES.get(k, k)
        if isinstance(value, str) and "," in value:
            value = [part.strip() for part in value.split(",") if part.strip()]
        out[k] = normalize_json(value)
    return out


def parse_lineage(raw: Any, scientific_name: str, rank: str) -> tuple[tuple[str, str], ...]:
    lineage: list[tuple[str, str]] = []

    if isinstance(raw, str):
        parts = [canonical_text(x) for x in raw.split("|") if canonical_text(x)]
        for i, name in enumerate(parts):
            guessed_rank = list(RANK_ORDER.keys())[min(i, len(RANK_ORDER) - 1)]
            lineage.append((guessed_rank, name))

    elif isinstance(raw, Sequence):
        for item in raw:
            if isinstance(item, Mapping):
                r = canonical_rank(str(item.get("rank", "unranked")))
                n = canonical_text(item.get("name", item.get("scientific_name", "")))
                if n:
                    lineage.append((r, n))
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
                lineage.append((canonical_rank(str(item[0])), canonical_text(item[1])))
            else:
                name = canonical_text(item)
                if name:
                    lineage.append(("unranked", name))

    elif isinstance(raw, Mapping):
        ordered = sorted(
            ((canonical_rank(k), canonical_text(v)) for k, v in raw.items() if canonical_text(v)),
            key=lambda item: RANK_ORDER.get(item[0], 100),
        )
        lineage.extend(ordered)

    name = canonical_text(scientific_name)
    rank = canonical_rank(rank)
    if not lineage or lineage[-1][1].casefold() != name.casefold():
        lineage.append((rank, name))

    # Remove adjacent duplicates without erasing legitimate repeated names elsewhere.
    deduped: list[tuple[str, str]] = []
    for item in lineage:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    return tuple(deduped)


def taxon_from_mapping(data: Mapping[str, Any]) -> TaxonRecord:
    scientific_name = canonical_text(
        data.get("scientific_name")
        or data.get("canonical_name")
        or data.get("name")
        or data.get("taxon")
    )
    if not scientific_name:
        raise ValueError("A taxon requires scientific_name, canonical_name, name, or taxon")

    rank = canonical_rank(str(data.get("rank", "unranked")))
    lineage = parse_lineage(data.get("lineage", []), scientific_name, rank)
    traits = normalize_traits(data.get("traits", {}))
    identifier = canonical_text(
        data.get("id") or data.get("identifier") or data.get("taxon_id") or ""
    )
    common_name = canonical_text(data.get("common_name", ""))

    return TaxonRecord(
        scientific_name=scientific_name,
        rank=rank,
        lineage=lineage,
        traits=traits,
        identifier=identifier,
        common_name=common_name,
    )


def trait_tokens(traits: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key, value in traits.items():
        tokens.add(canonical_text(key).lower())
        if isinstance(value, list):
            tokens.update(canonical_text(v).lower() for v in value)
        else:
            tokens.add(canonical_text(value).lower())
    return {t for t in tokens if t}


def build_parameters(record: TaxonRecord) -> IconParameters:
    identity_digest = hashlib.sha256(record.canonical_identity.encode("utf-8")).digest()
    lineage_identity = "|".join(f"{r}:{n}" for r, n in record.lineage)
    lineage_digest = hashlib.sha256(lineage_identity.encode("utf-8")).digest()

    # Parent seed controls the inherited "family resemblance."
    parent_identity = "|".join(f"{r}:{n}" for r, n in record.lineage[:-1])
    parent_digest = hashlib.sha256(parent_identity.encode("utf-8")).digest()

    own_rng = HashStream(identity_digest)
    inherited_rng = HashStream(parent_digest)
    rank = record.rank
    motif = RANK_MOTIFS.get(rank, "glyph")

    tokens = trait_tokens(record.traits)
    bilateral = any(t in tokens for t in ("bilateral", "bilateria", "bilaterally_symmetric"))
    radial = any(t in tokens for t in ("radial", "radially_symmetric"))
    colonial = any(t in tokens for t in ("colonial", "colony"))
    sessile = "sessile" in tokens
    mobile = any(t in tokens for t in ("mobile", "motile", "swimming", "flying"))
    unicellular = any(t in tokens for t in ("unicellular", "single-celled", "single_celled"))
    multicellular = "multicellular" in tokens

    inherited_symmetry = inherited_rng.integer(4, 10)
    if bilateral:
        symmetry = 2 * own_rng.integer(2, 5)
    elif radial:
        symmetry = own_rng.integer(5, 12)
    else:
        symmetry = max(3, min(12, inherited_symmetry + own_rng.integer(-2, 2)))

    rank_depth = RANK_ORDER.get(rank, 12)
    ring_count = 1 + ((rank_depth + own_rng.integer(0, 3)) % 4)
    branch_depth = 2 + own_rng.integer(0, 3)
    branch_factor = 2 + own_rng.integer(0, 2)
    if colonial:
        branch_depth = min(5, branch_depth + 1)
        branch_factor = min(4, branch_factor + 1)
    if unicellular:
        ring_count = max(1, ring_count - 1)
    if multicellular:
        branch_depth = min(5, branch_depth + 1)

    habitat_mask = 0
    habitat_values = record.traits.get("habitat", [])
    if not isinstance(habitat_values, list):
        habitat_values = [habitat_values]
    for value in habitat_values:
        bit = HABITAT_BITS.get(canonical_text(value).lower())
        if bit:
            habitat_mask |= 1 << bit

    # Trait mask captures biologically meaningful categorical distinctions.
    trait_mask = 0
    for token in sorted(tokens):
        bit = int.from_bytes(hashlib.blake2s(token.encode(), digest_size=2).digest(), "big") % 31
        trait_mask |= 1 << bit

    density = 0.40 + own_rng.unit() * 0.45
    if colonial:
        density = min(1.0, density + 0.12)
    if sessile:
        density = min(1.0, density + 0.06)
    if mobile:
        density = max(0.30, density - 0.05)

    return IconParameters(
        identity_sha256=identity_digest.hex(),
        lineage_sha256=lineage_digest.hex(),
        rank=rank,
        motif=motif,
        symmetry=symmetry,
        ring_count=ring_count,
        branch_depth=branch_depth,
        branch_factor=branch_factor,
        radial_phase=own_rng.unit() * math.tau,
        angular_jitter=own_rng.unit() * 0.20,
        density=density,
        stroke_ratio=0.010 + own_rng.unit() * 0.010,
        core_ratio=0.10 + own_rng.unit() * 0.09,
        ornament_count=4 + own_rng.integer(0, 12),
        habitat_mask=habitat_mask,
        trait_mask=trait_mask,
        palette_variant=own_rng.integer(0, 7),
    )


def regular_polygon(
    cx: float,
    cy: float,
    radius: float,
    sides: int,
    rotation: float = -math.pi / 2,
) -> list[tuple[float, float]]:
    return [
        (
            cx + math.cos(rotation + math.tau * i / sides) * radius,
            cy + math.sin(rotation + math.tau * i / sides) * radius,
        )
        for i in range(sides)
    ]


def rounded_hex_mask(size: int, radius_ratio: float = 0.47, corner_radius_ratio: float = 0.055) -> Image.Image:
    """Create a high-quality rounded hexagon mask by supersampling."""
    scale = 4
    s = size * scale
    cx = cy = s / 2
    r = s * radius_ratio
    points = regular_polygon(cx, cy, r, 6, 0)

    # Pillow has no native rounded polygon. Build it as a stroked path plus fill.
    mask = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mask)
    width = int(s * corner_radius_ratio * 2)
    d.line(points + [points[0]], fill=255, width=width, joint="curve")
    d.polygon(points, fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def draw_badge_base(size: int) -> tuple[Image.Image, Image.Image]:
    image = Image.new("RGBA", (size, size), TRANSPARENT)
    outer_mask = rounded_hex_mask(size, 0.485, 0.055)
    gold_mask = rounded_hex_mask(size, 0.455, 0.050)
    inner_mask = rounded_hex_mask(size, 0.436, 0.047)

    outer = Image.new("RGBA", image.size, NAVY_OUTER)
    image.alpha_composite(Image.composite(outer, Image.new("RGBA", image.size), outer_mask))

    gold = Image.new("RGBA", image.size, GOLD)
    image.alpha_composite(Image.composite(gold, Image.new("RGBA", image.size), gold_mask))

    # Subtle vertical gradient inside the badge.
    grad = Image.new("RGBA", image.size)
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(NAVY_INNER[0] * (1 - t) + NAVY_DEEP[0] * t)
        g = int(NAVY_INNER[1] * (1 - t) + NAVY_DEEP[1] * t)
        b = int(NAVY_INNER[2] * (1 - t) + NAVY_DEEP[2] * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    image.alpha_composite(Image.composite(grad, Image.new("RGBA", image.size), inner_mask))
    return image, inner_mask


def rotate_point(
    point: tuple[float, float],
    center: tuple[float, float],
    angle: float,
) -> tuple[float, float]:
    x, y = point
    cx, cy = center
    dx, dy = x - cx, y - cy
    c, s = math.cos(angle), math.sin(angle)
    return cx + dx * c - dy * s, cy + dx * s + dy * c


def draw_star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, color: tuple[int, int, int, int]) -> None:
    points = []
    for i in range(8):
        angle = -math.pi / 2 + i * math.pi / 4
        radius = r if i % 2 == 0 else r * 0.28
        points.append((x + math.cos(angle) * radius, y + math.sin(angle) * radius))
    draw.polygon(points, fill=color)


def draw_background_ornaments(
    layer: Image.Image,
    params: IconParameters,
    rng: HashStream,
    size: int,
) -> None:
    d = ImageDraw.Draw(layer)
    cx = cy = size / 2
    for i in range(params.ornament_count):
        angle = params.radial_phase + math.tau * i / params.ornament_count
        angle += rng.signed() * 0.18
        radius = size * (0.26 + rng.unit() * 0.12)
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius
        r = size * (0.0035 + rng.unit() * 0.008)
        if i % 3 == 0:
            draw_star(d, x, y, r * 1.8, TEAL_LIGHT)
        else:
            d.ellipse((x-r, y-r, x+r, y+r), fill=TEAL_LIGHT)


def draw_rank_marker(
    layer: Image.Image,
    rank: str,
    size: int,
    color: tuple[int, int, int, int],
) -> None:
    """Small root-rank marker at the top of the inner field."""
    d = ImageDraw.Draw(layer)
    cx = size / 2
    y = size * 0.205
    r = size * 0.025
    level = RANK_ORDER.get(rank, 12)

    if rank in ("domain", "superkingdom"):
        d.ellipse((cx-r, y-r, cx+r, y+r), outline=color, width=max(2, int(size*0.006)))
        d.ellipse((cx-r*0.3, y-r*0.3, cx+r*0.3, y+r*0.3), fill=color)
    elif rank in ("kingdom", "subkingdom"):
        pts = [(cx-r, y+r*0.6), (cx-r*0.6, y-r), (cx, y-r*0.2),
               (cx+r*0.6, y-r), (cx+r, y+r*0.6)]
        d.line(pts, fill=color, width=max(2, int(size*0.007)), joint="curve")
    elif rank in ("phylum", "division", "subphylum"):
        d.line((cx, y+r, cx, y-r), fill=color, width=max(2, int(size*0.006)))
        d.line((cx, y, cx-r, y-r*0.6), fill=color, width=max(2, int(size*0.005)))
        d.line((cx, y, cx+r, y-r*0.6), fill=color, width=max(2, int(size*0.005)))
    elif rank in ("class", "subclass"):
        d.polygon(regular_polygon(cx, y, r, 4, math.pi/4), outline=color)
    elif rank in ("order", "suborder"):
        d.ellipse((cx-r, y-r*0.45, cx+r, y+r*0.45), outline=color, width=max(2, int(size*0.006)))
    elif rank in ("family", "subfamily", "tribe"):
        d.line((cx-r, y+r, cx, y-r, cx+r, y+r), fill=color, width=max(2, int(size*0.006)))
    elif rank in ("genus", "subgenus"):
        for a in (0, math.pi/3, 2*math.pi/3):
            x1, y1 = cx+math.cos(a)*r, y+math.sin(a)*r
            x2, y2 = cx-math.cos(a)*r, y-math.sin(a)*r
            d.line((x1,y1,x2,y2), fill=color, width=max(2, int(size*0.005)))
    else:
        sides = 3 + (level % 6)
        d.polygon(regular_polygon(cx, y, r, sides), outline=color)


def draw_orbit(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    color: tuple[int, int, int, int],
    width: int,
    rotation: float,
) -> None:
    pts = []
    for i in range(180):
        a = math.tau * i / 180
        p = (cx + math.cos(a) * rx, cy + math.sin(a) * ry)
        pts.append(rotate_point(p, (cx, cy), rotation))
    d.line(pts + [pts[0]], fill=color, width=width, joint="curve")


def draw_tree_branch(
    d: ImageDraw.ImageDraw,
    start: tuple[float, float],
    angle: float,
    length: float,
    depth: int,
    factor: int,
    spread: float,
    width: int,
    color: tuple[int, int, int, int],
    rng: HashStream,
) -> None:
    if depth <= 0 or length < 2:
        return
    x1, y1 = start
    x2 = x1 + math.cos(angle) * length
    y2 = y1 + math.sin(angle) * length
    d.line((x1, y1, x2, y2), fill=color, width=max(1, width), joint="curve")
    for i in range(factor):
        offset = (i - (factor - 1) / 2) * spread
        jitter = rng.signed() * spread * 0.14
        draw_tree_branch(
            d,
            (x2, y2),
            angle + offset + jitter,
            length * (0.61 + rng.unit() * 0.08),
            depth - 1,
            factor,
            spread * 0.87,
            max(1, int(width * 0.73)),
            color,
            rng,
        )


def draw_sigil(layer: Image.Image, params: IconParameters, record: TaxonRecord, size: int) -> None:
    d = ImageDraw.Draw(layer)
    cx = cy = size / 2
    identity = bytes.fromhex(params.identity_sha256)
    rng = HashStream(identity)
    stroke = max(3, int(size * params.stroke_ratio))
    main = [TEAL_LIGHT, TEAL, MINT][params.palette_variant % 3]
    secondary = [TEAL_DARK, TEAL, GOLD_LIGHT][(params.palette_variant // 2) % 3]
    motif = params.motif

    # A faint lineage halo inherited by every descendant.
    halo_r = size * 0.245
    for ring in range(params.ring_count):
        rr = halo_r * (0.68 + ring * 0.11)
        d.ellipse(
            (cx-rr, cy-rr, cx+rr, cy+rr),
            outline=(*main[:3], 78),
            width=max(1, stroke // 3),
        )

    if motif in ("mandala", "snowflake", "crystal"):
        spokes = params.symmetry
        base_r = size * 0.07
        tip_r = size * 0.245
        for i in range(spokes):
            a = params.radial_phase + math.tau * i / spokes
            jitter = rng.signed() * params.angular_jitter
            a += jitter
            x1, y1 = cx + math.cos(a)*base_r, cy + math.sin(a)*base_r
            x2, y2 = cx + math.cos(a)*tip_r, cy + math.sin(a)*tip_r
            d.line((x1,y1,x2,y2), fill=main, width=stroke)
            # Crystal/snowflake side facets encode hash bits.
            facets = 2 + rng.integer(0, 3)
            for j in range(1, facets+1):
                t = j / (facets + 1)
                px, py = x1 + (x2-x1)*t, y1 + (y2-y1)*t
                branch_len = size * (0.022 + rng.unit()*0.035)
                for side in (-1, 1):
                    ba = a + math.pi + side*(0.55 + rng.unit()*0.25)
                    qx, qy = px + math.cos(ba)*branch_len, py + math.sin(ba)*branch_len
                    d.line((px,py,qx,qy), fill=secondary, width=max(2, stroke//2))
        core_sides = 3 + (identity[0] % 7)
        d.polygon(regular_polygon(cx, cy, size*params.core_ratio, core_sides, params.radial_phase),
                  outline=GOLD_LIGHT, width=stroke)

    elif motif in ("tree", "coral", "branch"):
        roots = params.symmetry if motif == "coral" else max(3, params.symmetry // 2)
        for i in range(roots):
            a = params.radial_phase + math.tau * i / roots
            start_r = size * 0.035
            start = (cx + math.cos(a)*start_r, cy + math.sin(a)*start_r)
            draw_tree_branch(
                d, start, a, size*0.085, params.branch_depth,
                params.branch_factor, 0.52 + rng.unit()*0.32,
                stroke, main, rng
            )
        d.ellipse(
            (cx-size*0.045, cy-size*0.045, cx+size*0.045, cy+size*0.045),
            fill=GOLD_LIGHT,
        )

    elif motif == "orbit":
        orbits = 2 + (identity[1] % 4)
        for i in range(orbits):
            rot = params.radial_phase + i * math.pi / max(2, orbits)
            draw_orbit(
                d, cx, cy,
                size*(0.18 + i*0.018),
                size*(0.075 + (i%2)*0.025),
                main if i % 2 == 0 else secondary,
                max(2, stroke//2),
                rot,
            )
            a = params.radial_phase + i * math.tau / orbits
            px = cx + math.cos(a) * size * 0.18
            py = cy + math.sin(a) * size * 0.075
            d.ellipse((px-stroke, py-stroke, px+stroke, py+stroke), fill=GOLD_LIGHT)
        d.polygon(
            regular_polygon(cx, cy, size*params.core_ratio, 5 + identity[2] % 4, params.radial_phase),
            outline=main, width=stroke,
        )

    elif motif == "crown":
        points = []
        peaks = max(3, min(9, params.symmetry))
        left = cx - size*0.22
        right = cx + size*0.22
        bottom = cy + size*0.14
        top = cy - size*0.18
        points.append((left, bottom))
        for i in range(peaks):
            x = left + (right-left) * i / max(1, peaks-1)
            y = top + (i % 2) * size*0.09 + rng.signed()*size*0.012
            points.append((x, y))
        points.append((right, bottom))
        d.line(points, fill=main, width=stroke, joint="curve")
        d.line((left,bottom,right,bottom), fill=GOLD_LIGHT, width=stroke)
        for i in range(peaks):
            x = left + (right-left) * i / max(1, peaks-1)
            y = top + (i % 2) * size*0.09
            d.ellipse((x-stroke, y-stroke, x+stroke, y+stroke), fill=secondary)

    elif motif in ("cell", "petal"):
        petals = max(4, params.symmetry)
        for i in range(petals):
            a = params.radial_phase + math.tau*i/petals
            pcx = cx + math.cos(a)*size*0.115
            pcy = cy + math.sin(a)*size*0.115
            prx = size*(0.055 + rng.unit()*0.025)
            pry = size*(0.10 + rng.unit()*0.035)
            box = (pcx-prx, pcy-pry, pcx+prx, pcy+pry)
            petal = Image.new("RGBA", layer.size, TRANSPARENT)
            pd = ImageDraw.Draw(petal)
            pd.ellipse(box, outline=main, width=stroke)
            petal = petal.rotate(math.degrees(a)+90, center=(pcx,pcy), resample=Image.Resampling.BICUBIC)
            layer.alpha_composite(petal)
        d = ImageDraw.Draw(layer)
        d.ellipse(
            (cx-size*params.core_ratio, cy-size*params.core_ratio,
             cx+size*params.core_ratio, cy+size*params.core_ratio),
            fill=(*TEAL_DARK[:3], 180), outline=GOLD_LIGHT, width=stroke
        )
        internal = 3 + identity[3] % 6
        for i in range(internal):
            a = math.tau*i/internal + params.radial_phase
            rr = size*params.core_ratio*0.55
            x, y = cx+math.cos(a)*rr, cy+math.sin(a)*rr
            d.ellipse((x-stroke/2,y-stroke/2,x+stroke/2,y+stroke/2), fill=MINT)

    elif motif == "knot":
        loops = max(3, params.symmetry // 2)
        points = []
        steps = 360
        for i in range(steps):
            t = math.tau * i / steps
            r = size * (0.13 + 0.075 * math.sin(loops*t + params.radial_phase))
            x = cx + math.cos(t)*r
            y = cy + math.sin(t)*r
            points.append((x,y))
        d.line(points + [points[0]], fill=main, width=stroke, joint="curve")
        d.ellipse((cx-size*0.045,cy-size*0.045,cx+size*0.045,cy+size*0.045),
                  outline=GOLD_LIGHT, width=stroke)

    else:  # sigil / glyph
        nodes = max(6, min(18, 5 + params.symmetry + identity[4] % 5))
        radii = [size*(0.075 + (i % 3)*0.07) for i in range(nodes)]
        points = []
        for i in range(nodes):
            a = params.radial_phase + math.tau*i/nodes + rng.signed()*params.angular_jitter
            rr = radii[i] * (0.88 + rng.unit()*0.25)
            points.append((cx+math.cos(a)*rr, cy+math.sin(a)*rr))

        # Hash-driven graph edges.
        for i, p in enumerate(points):
            step = 1 + identity[i % len(identity)] % max(1, nodes-1)
            q = points[(i + step) % nodes]
            color = main if i % 3 else secondary
            d.line((p[0],p[1],q[0],q[1]), fill=color, width=max(2, stroke//2))
        for i, (x,y) in enumerate(points):
            rr = stroke * (0.55 if i % 2 else 0.85)
            d.ellipse((x-rr,y-rr,x+rr,y+rr), fill=GOLD_LIGHT if i%4==0 else main)
        core = regular_polygon(cx, cy, size*params.core_ratio, 3 + identity[5] % 7, params.radial_phase)
        d.polygon(core, fill=(*TEAL_DARK[:3], 150), outline=GOLD_LIGHT, width=stroke)

    # Habitat code ring: short/long ticks encode habitat_mask.
    ring_r = size * 0.285
    tick_count = 24
    for i in range(tick_count):
        bit = (params.habitat_mask >> (i % 20)) & 1
        a = params.radial_phase + math.tau*i/tick_count
        inner = ring_r - size*(0.009 if bit else 0.004)
        outer = ring_r + size*(0.012 if bit else 0.006)
        p1 = (cx+math.cos(a)*inner, cy+math.sin(a)*inner)
        p2 = (cx+math.cos(a)*outer, cy+math.sin(a)*outer)
        d.line((*p1,*p2), fill=GOLD_LIGHT if bit else (*TEAL[:3], 100), width=max(1, stroke//3))


def draw_taxon_icon(
    record: TaxonRecord,
    size: int = DEFAULT_SIZE,
    transparent_background: bool = True,
) -> tuple[Image.Image, IconParameters]:
    if size < 64:
        raise ValueError("size must be at least 64 pixels")

    params = build_parameters(record)
    image, inner_mask = draw_badge_base(size)
    content = Image.new("RGBA", image.size, TRANSPARENT)
    rng = HashStream(bytes.fromhex(params.identity_sha256) + b":ornaments")

    draw_background_ornaments(content, params, rng, size)
    draw_rank_marker(content, record.rank, size, GOLD_LIGHT)
    draw_sigil(content, params, record, size)

    # Keep all internal art clipped to the inner badge.
    content.putalpha(Image.composite(content.getchannel("A"), Image.new("L", image.size, 0), inner_mask))
    image.alpha_composite(content)

    if not transparent_background:
        bg = Image.new("RGBA", image.size, WHITE)
        bg.alpha_composite(image)
        image = bg

    return image, params


def save_png(image: Image.Image, path: Path, optimize: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        path,
        format="PNG",
        optimize=optimize,
        compress_level=9,
    )


def record_manifest_entry(
    record: TaxonRecord,
    params: IconParameters,
    output_path: Path,
    size: int,
) -> dict[str, Any]:
    return {
        "generator": "Speciedex Icon Forge",
        "generator_version": GENERATOR_VERSION,
        "id": record.identifier,
        "scientific_name": record.scientific_name,
        "common_name": record.common_name,
        "rank": record.rank,
        "lineage": [{"rank": r, "name": n} for r, n in record.lineage],
        "traits": record.traits,
        "file": output_path.as_posix(),
        "size": size,
        "parameters": asdict(params),
    }


def read_records(path: Path) -> Iterator[TaxonRecord]:
    suffix = path.suffix.lower()

    if suffix in (".jsonl", ".ndjson"):
        with path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield taxon_from_mapping(json.loads(line))
                except Exception as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
        return

    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            payload = payload.get("taxa", payload.get("records", [payload]))
        if not isinstance(payload, list):
            raise ValueError("JSON input must be an object or an array of objects")
        for item in payload:
            yield taxon_from_mapping(item)
        return

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                data: dict[str, Any] = dict(row)
                if data.get("traits"):
                    data["traits"] = json.loads(data["traits"])
                if data.get("lineage"):
                    text = data["lineage"].strip()
                    if text.startswith("[") or text.startswith("{"):
                        data["lineage"] = json.loads(text)
                yield taxon_from_mapping(data)
        return

    raise ValueError("Input must be .json, .jsonl, .ndjson, or .csv")


def unique_output_name(record: TaxonRecord, params: IconParameters) -> str:
    # Human-readable and collision-resistant. The 12 hex chars are not the
    # uniqueness source; full SHA-256 remains in the manifest.
    return f"{slugify(record.scientific_name)}--{record.rank}--{params.identity_sha256[:12]}.png"


def cmd_generate(args: argparse.Namespace) -> int:
    lineage = args.lineage or args.scientific_name
    traits = json.loads(args.traits) if args.traits else {}
    record = taxon_from_mapping({
        "id": args.identifier,
        "scientific_name": args.scientific_name,
        "common_name": args.common_name,
        "rank": args.rank,
        "lineage": lineage,
        "traits": traits,
    })
    image, params = draw_taxon_icon(record, args.size, not args.opaque)
    output = Path(args.output)
    save_png(image, output, not args.no_optimize)

    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(record_manifest_entry(record, params, output, args.size),
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(output.as_posix())
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    failures = 0

    for index, record in enumerate(read_records(input_path), 1):
        try:
            params = build_parameters(record)
            if params.identity_sha256 in seen_hashes:
                # Exact duplicate normalized identity; do not regenerate.
                continue
            seen_hashes.add(params.identity_sha256)
            image, params = draw_taxon_icon(record, args.size, not args.opaque)
            output = output_dir / unique_output_name(record, params)
            save_png(image, output, not args.no_optimize)
            entries.append(record_manifest_entry(record, params, output, args.size))
            if not args.quiet:
                print(f"[{index}] {record.scientific_name} -> {output.name}")
        except Exception as exc:
            failures += 1
            print(f"[{index}] ERROR: {record.scientific_name}: {exc}", file=sys.stderr)
            if args.fail_fast:
                return 1

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generator": "Speciedex Icon Forge",
        "generator_version": GENERATOR_VERSION,
        "input": input_path.as_posix(),
        "count": len(entries),
        "failures": failures,
        "icons": entries,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Generated {len(entries)} icons; failures={failures}; manifest={manifest_path}")
    return 1 if failures and args.strict else 0


def cmd_sprite(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    files = sorted(input_dir.glob("*.png"))
    if not files:
        raise SystemExit(f"No PNG files found in {input_dir}")

    cell = args.cell_size
    cols = max(1, args.columns)
    rows = math.ceil(len(files) / cols)
    sheet = Image.new("RGBA", (cols * cell, rows * cell), TRANSPARENT)
    index: dict[str, Any] = {
        "cell_size": cell,
        "columns": cols,
        "rows": rows,
        "sprites": {},
    }

    for i, path in enumerate(files):
        image = Image.open(path).convert("RGBA")
        image.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        x = (i % cols) * cell + (cell - image.width) // 2
        y = (i // cols) * cell + (cell - image.height) // 2
        sheet.alpha_composite(image, (x, y))
        index["sprites"][path.stem] = {
            "x": (i % cols) * cell,
            "y": (i // cols) * cell,
            "width": cell,
            "height": cell,
            "source": path.name,
        }

    output = Path(args.output)
    save_png(sheet, output, not args.no_optimize)
    index_path = Path(args.index)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"{output} ({len(files)} sprites)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    records = list(read_records(Path(args.input)))
    hashes: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []

    for record in records:
        params = build_parameters(record)
        prior = hashes.get(params.identity_sha256)
        if prior and prior != record.scientific_name:
            duplicates.append((params.identity_sha256, prior, record.scientific_name))
        hashes[params.identity_sha256] = record.scientific_name

    print(f"records={len(records)} unique_normalized_identities={len(hashes)}")
    if duplicates:
        for digest, a, b in duplicates:
            print(f"collision: {digest} :: {a} :: {b}", file=sys.stderr)
        return 1
    print("No SHA-256 identity collisions detected.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icon-forge.py",
        description="Deterministic Speciedex taxonomic PNG glyph generator.",
    )
    parser.add_argument("--version", action="version", version=GENERATOR_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="Generate one taxonomic icon.")
    p.add_argument("--scientific-name", required=True)
    p.add_argument("--rank", default="species")
    p.add_argument("--lineage", default="", help="Pipe-separated lineage or current name.")
    p.add_argument("--traits", default="{}", help="JSON object of biological traits.")
    p.add_argument("--identifier", default="")
    p.add_argument("--common-name", default="")
    p.add_argument("--size", type=int, default=DEFAULT_SIZE)
    p.add_argument("--output", required=True)
    p.add_argument("--metadata", default="")
    p.add_argument("--opaque", action="store_true")
    p.add_argument("--no-optimize", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("batch", help="Generate icons from JSON, JSONL, NDJSON, or CSV.")
    p.add_argument("--input", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--size", type=int, default=DEFAULT_SIZE)
    p.add_argument("--opaque", action="store_true")
    p.add_argument("--no-optimize", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("sprite", help="Build a PNG sprite sheet from generated icons.")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--cell-size", type=int, default=128)
    p.add_argument("--columns", type=int, default=16)
    p.add_argument("--no-optimize", action="store_true")
    p.set_defaults(func=cmd_sprite)

    p = sub.add_parser("verify", help="Verify deterministic identity uniqueness.")
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
