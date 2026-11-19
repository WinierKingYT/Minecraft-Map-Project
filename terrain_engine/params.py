"""Terrain parameters: defaults, preset loading, override merging, validation.

The AI (or the user) only edits these JSON values; all block math is local.
"""
import json
import re
from pathlib import Path

from . import regions as R

ROOT = Path(__file__).resolve().parent.parent
PRESET_DIR = ROOT / "presets"

# name: (default, min, max)  -- numeric params
NUMERIC = {
    "width": (256, 16, 16384),
    "length": (256, 16, 16384),
    "seed": (1, 0, 2**31 - 1),
    "water_level": (62, -60, 300),
    "base_height": (66.0, -60, 300),       # floodplain / plain ground level
    "mountain_height": (90.0, 0, 250),     # added on top of base at valley edges
    "roughness": (0.5, 0.0, 1.0),          # fBm gain (detail amount)
    "hill_amplitude": (8.0, 0, 80),        # small-scale undulation, blocks
    "noise_scale": (180.0, 8, 4000),       # feature size of mountains, blocks
    "octaves": (6, 1, 9),
    "warp_strength": (40.0, 0, 400),       # domain warp, blocks
    "ridge_sharpness": (0.6, 0.0, 3.0),    # 0 = rounded, 3 = sharp
    "cliffiness": (0.0, 0.0, 1.0),         # terracing / cliff bands
    "terrace_step": (7.0, 2, 40),
    "erosion": (0.5, 0.0, 1.0),            # thermal erosion + smoothing amount
    "hydraulic": (0.0, 0.0, 1.0),          # droplet erosion: gullies, sediment fans (0 = off)
    "valley_width": (0.55, 0.0, 1.0),      # fraction of map across river axis
    "river_width": (9.0, 0, 64),           # 0 disables river
    "river_depth": (4.0, 1, 20),
    "river_meander": (0.12, 0.0, 0.4),     # meander amplitude / map size
    "river_position": (0.5, 0.1, 0.9),     # center line across axis, 0..1
    "bank_width": (6.0, 0, 40),
    "edge_falloff": (0.0, 0, 1024),        # >0: map fades into sea over this many blocks (no edge seam)
    "tree_density": (0.5, 0.0, 1.0),
    "forest_coverage": (0.6, 0.0, 1.0),
    "rock_ratio": (0.5, 0.0, 1.0),         # higher -> rock appears on gentler slopes
    "soil_ratio": (0.5, 0.0, 1.0),         # podzol / coarse dirt amount in forests
    "snow_line": (175.0, 0, 320),
    "tree_line": (150.0, 0, 320),
    "pine_line": (105.0, 0, 320),
    "caves": (0.0, 0.0, 1.0),              # WorldPainter Caverns/Chasms layer amount (0 = off)
    "tributaries": (0.0, 0.0, 1.0),        # side streams joining the river along valleys (0 = off)
    "lakes": (0.0, 0.0, 1.0),              # lakes in natural basins, flat at their spill level (0 = off)
    "vegetation": (0.0, 0.0, 1.0),         # post-export features: flower patches, bushes, fallen logs, boulders
}
CHOICES = {
    "river_axis": ("north_south", "west_east"),
    "biome": ("temperate", "taiga", "fantasy_forest", "plains", "rocky"),
    "outside": ("ocean", "vanilla"),  # what Minecraft generates beyond the map
    "biomes": ("off", "on"),  # on: river/ocean/forest/taiga/peak biomes painted via WP Biomes layer
}
INTEGER = {"width", "length", "seed", "octaves", "water_level"}


def defaults() -> dict:
    d = {k: v[0] for k, v in NUMERIC.items()}
    d.update({"river_axis": "north_south", "biome": "temperate", "outside": "ocean", "biomes": "off", "name": "untitled"})
    return d


NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate(p: dict) -> dict:
    unknown = set(p) - set(NUMERIC) - set(CHOICES) - {"name", "description", "regions"}
    if unknown:
        raise ValueError(f"unknown parameter(s): {sorted(unknown)}")
    out = dict(p)
    # name becomes a folder under exports/ that may be rmtree'd: no path separators or "..", ever
    if not isinstance(out.get("name"), str) or not NAME_RE.fullmatch(out["name"]):
        raise ValueError(f"name must match {NAME_RE.pattern}, got {out.get('name')!r}")
    for k, (_, lo, hi) in NUMERIC.items():
        v = out[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"{k} must be a number, got {v!r}")
        if not lo <= v <= hi:
            raise ValueError(f"{k}={v} outside [{lo}, {hi}]")
        out[k] = int(v) if k in INTEGER else float(v)
    for k, opts in CHOICES.items():
        if out[k] not in opts:
            raise ValueError(f"{k}={out[k]!r} not in {opts}")
    if out["width"] % 16 or out["length"] % 16:
        raise ValueError("width/length must be multiples of 16 (chunk aligned)")
    if "regions" in out:  # absent key keeps pre-0.3 params (and cache keys) unchanged
        out["regions"] = R.validate(out["regions"], NUMERIC, CHOICES, preset_values)
    return out


def _coerce(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _preset_json(name) -> dict:
    if name not in list_presets():  # also blocks path tricks in names coming from region specs
        raise ValueError(f"preset not found: {name} (have: {list_presets()})")
    return json.loads((PRESET_DIR / f"{name}.json").read_text(encoding="utf-8"))


def preset_values(name) -> dict:
    """defaults <- preset JSON: what the preset looks like on its own."""
    return {**defaults(), **_preset_json(name)}


def load(preset: str | None = None, overrides: dict | None = None, params_file: str | None = None) -> dict:
    """defaults <- preset <- params file <- overrides, then validate."""
    p = defaults()
    if preset:
        p.update(_preset_json(preset))
        p.setdefault("name", preset)
    if params_file:
        p.update(json.loads(Path(params_file).read_text(encoding="utf-8")))
    for k, v in (overrides or {}).items():
        p[k] = _coerce(v) if isinstance(v, str) else v
    return validate(p)


def list_presets() -> list[str]:
    return sorted(f.stem for f in PRESET_DIR.glob("*.json"))
