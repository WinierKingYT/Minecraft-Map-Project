"""Write generation outputs (PNGs, JSON, previews) with content-hash caching."""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import ENGINE_VERSION
from .generator import B, PARALLEL_FROM, T, WORKERS, generate, generate_tiled

ROOT = Path(__file__).resolve().parent.parent
HEIGHT_MAX = 320.0  # 16-bit PNG maps 0..65535 -> y 0..320 (WorldPainter fromLevels/toLevels)
OPTIONAL_LAYERS = ("caverns", "chasms", "biomes")  # water_level goes to WP as a sparse list (worldpainter.py)  # 8-bit PNGs, written only when generated
PREVIEW_CELLS = 1024 // 3  # previews draw 3 px per sample: larger maps are subsampled to <= 1024 px

_COLORS = {T["GRASS"]: (98, 150, 60), T["PODZOL"]: (110, 86, 46), T["COARSE_DIRT"]: (122, 92, 66),
           T["MOSS"]: (80, 130, 50), T["BARE_GRASS"]: (130, 140, 90), T["GRAVEL"]: (140, 134, 130),
           T["ROCK"]: (120, 118, 116), T["STONE_MIX"]: (128, 126, 124), T["ANDESITE"]: (136, 136, 136),
           T["STONE"]: (125, 125, 125), T["TUFF"]: (108, 109, 102), T["CALCITE"]: (223, 224, 220),
           T["SNOW"]: (245, 248, 252), T["SAND"]: (219, 207, 163), T["CLAY"]: (160, 166, 178)}


def cache_key(p: dict) -> str:
    blob = json.dumps({"engine": ENGINE_VERSION, "params": p}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def output_dir(p: dict) -> Path:
    return ROOT / "exports" / f"{p['name']}-{cache_key(p)}"


def height_to_u16(h: np.ndarray) -> np.ndarray:
    return np.round(np.clip(h, 0, HEIGHT_MAX) / HEIGHT_MAX * 65535).astype(np.uint16)


def u16_to_height(v: np.ndarray) -> np.ndarray:
    return v.astype(np.float64) / 65535 * HEIGHT_MAX


def _hillshade(h):
    gz, gx = np.gradient(h.astype(np.float64))
    nx, nz, ny = -gx, -gz, np.ones_like(h, dtype=np.float64)
    norm = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    light = np.array([-0.5, 0.75, -0.45])
    light /= np.linalg.norm(light)
    return np.clip((nx * light[0] + ny * light[1] + nz * light[2]) / norm, 0, 1)


def _colour_map(d, water_level, step=1):
    rgb = np.zeros(d["terrain"].shape + (3,), dtype=np.float64)
    for idx, c in _COLORS.items():
        rgb[d["terrain"] == idx] = c
    trees = (d["trees_deciduous"].astype(float) + d["trees_pine"]) / 15.0
    rgb *= (1 - 0.45 * np.clip(trees, 0, 1))[..., None]
    rgb *= (0.45 + 0.75 * _hillshade(d["height"] / step ** 0.5))[..., None]  # subsampled: relief kept readable
    surface = np.full(d["height"].shape, float(water_level))
    if "water_level" in d:  # lakes / streams above the sea level
        surface = np.where(d["water_level"] > 0, d["water_level"].astype(np.float64), surface)
    water = np.floor(d["height"] + 0.5) < surface if "water_level" in d else d["height"] < water_level
    depth = np.clip(surface - d["height"], 0, 6)[water]
    rgb[water] = np.stack([40 + 0 * depth, 90 - 6 * depth, 170 - 8 * depth], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _oblique(d, water_level, cell=3, tilt=0.55, vscale=1.6, step=1):
    """Cheap 3D preview: voxel-space column render, viewed from the south (step blocks per sample)."""
    h = np.maximum(d["height"], water_level).astype(np.float64)
    if "water_level" in d:
        h = np.maximum(h, d["water_level"])
    colours = _colour_map(d, water_level, step)
    vscale = vscale / step ** 0.5  # subsampled maps: sqrt keeps relief readable, not 13x exaggerated
    rows, cols = h.shape
    base = h.min()
    width = cols * cell
    height_px = int(rows * tilt + (h.max() - base) * vscale) + 20
    img = np.full((height_px, width, 3), (200, 222, 240), dtype=np.uint8)
    ybuf = np.full(cols, height_px, dtype=np.int64)
    for z in range(rows - 1, -1, -1):  # near (south) to far
        ytop = (height_px - 10 - (rows - 1 - z) * tilt - (h[z] - base) * vscale).astype(np.int64)
        for x in np.nonzero(ytop < ybuf)[0]:
            img[max(ytop[x], 0):ybuf[x], x * cell:(x + 1) * cell] = colours[z, x]
        ybuf = np.minimum(ybuf, ytop)
    return img


def preview_step(shape) -> int:
    return -(-max(shape) // PREVIEW_CELLS)


def preview_fields(d: dict) -> dict:
    """Strided subsample of the fields for previews (the identity up to PREVIEW_CELLS per side)."""
    step = preview_step(d["height"].shape)
    return {k: d[k][::step, ::step] for k in ("height", "terrain", "trees_deciduous", "trees_pine", "water_level") if k in d}


def write_outputs(p: dict, d: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "previews").mkdir(exist_ok=True)
    Image.fromarray(height_to_u16(d["height"])).save(out / "heightmap16.png")
    h8 = np.clip((d["height"] - d["height"].min()) / max(np.ptp(d["height"]), 1e-6) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(h8).save(out / "heightmap_preview8.png")
    Image.fromarray(d["terrain"]).save(out / "terrain_index.png")
    Image.fromarray(d["trees_deciduous"]).save(out / "trees_deciduous.png")
    Image.fromarray(d["trees_pine"]).save(out / "trees_pine.png")
    for k in OPTIONAL_LAYERS:
        if k in d:
            Image.fromarray(d[k]).save(out / f"{k}.png")
    pv, step = preview_fields(d), preview_step(d["height"].shape)
    Image.fromarray(_colour_map(pv, p["water_level"], step)).resize(
        (pv["height"].shape[1] * 3, pv["height"].shape[0] * 3), Image.Resampling.NEAREST).save(out / "previews" / "topdown.png")
    Image.fromarray(_oblique(pv, p["water_level"], step=step)).save(out / "previews" / "oblique.png")
    (out / "params.json").write_text(json.dumps(p, indent=2, sort_keys=True), encoding="utf-8")
    tc = np.bincount(d["terrain"].ravel(), minlength=256)
    stats = {"height_min": float(d["height"].min()), "height_max": float(d["height"].max()),
             "height_mean": float(d["height"].mean()), "slope_max_deg": float(d["slope"].max()),
             "terrain_counts": {k: int(tc[v]) for k, v in T.items() if tc[v]},
             "tree_cells": int(((d["trees_deciduous"] + d["trees_pine"]) > 0).sum())}
    if "biomes" in d:
        ids = {v: k for k, v in B.items()}
        stats["biome_counts"] = {ids[int(v)]: int(n) for v, n in zip(*np.unique(d["biomes"], return_counts=True))}
    if "caverns" in d:
        stats["cave_cells"] = {k: int((d[k] > 0).sum()) for k in ("caverns", "chasms")}
    return stats


def build(p: dict, force: bool = False, tile: int | None = None) -> tuple[Path, dict, bool]:
    """Generate (or reuse cached) outputs. Returns (dir, manifest, cache_hit)."""
    out = output_dir(p)
    man_path = out / "manifest.json"
    if man_path.exists() and not force:
        return out, json.loads(man_path.read_text(encoding="utf-8")), True
    t0 = time.perf_counter()
    if tile:  # stream tiles into the full-size (float32/uint8) fields; float64 intermediates stay per tile
        d = {}
        for x0, z0, part in generate_tiled(p, tile, WORKERS if p["width"] * p["length"] >= PARALLEL_FROM else 1):
            for k, arr in part.items():
                if k not in d:
                    d[k] = np.zeros((p["length"], p["width"]), dtype=arr.dtype)
                d[k][z0:z0 + arr.shape[0], x0:x0 + arr.shape[1]] = arr
    else:
        d = generate(p)
    gen_s = time.perf_counter() - t0
    stats = write_outputs(p, d, out)
    manifest = {"engine_version": ENGINE_VERSION, "cache_key": cache_key(p), "params": p,
                "generation_seconds": round(gen_s, 3), "tile": tile, "stats": stats,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    np.savez_compressed(out / "fields.npz", **d)
    return out, manifest, False
