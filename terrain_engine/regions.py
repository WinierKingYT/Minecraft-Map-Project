"""Regional editing: blend per-region parameter overrides with smooth masks.

A region = shape (signed distance field in global block coords) + blend width +
parameter values. Blendable numeric params become per-cell arrays
base*(1-w) + region*w (later regions on top); biome takes the dominant weight.
Masks depend only on global coords, so tiles still stitch bit-for-bit.
"""
import math

import numpy as np

# Spatially meaningful params. Everything else (size, seed, water, octaves,
# noise_scale, erosion -> iteration count/margin, river/valley geometry, edge
# falloff) is global: varying it per cell would fold noise or change the margin.
# caves and biomes (on/off) are global too: one WP layer pass for the whole map.
BLENDABLE = ("base_height", "mountain_height", "roughness", "hill_amplitude", "warp_strength",
             "ridge_sharpness", "cliffiness", "terrace_step", "tree_density", "forest_coverage",
             "rock_ratio", "soil_ratio", "snow_line", "tree_line", "pine_line")
BLEND_CHOICES = ("biome",)
SHAPES = {"half": ("side",), "box": ("x0", "z0", "x1", "z1"), "circle": ("cx", "cz", "r"),
          "polygon": ("points",)}
SIDES = ("west", "east", "north", "south")
BLEND_MAX = 1024.0
DEFAULT_BLEND = 32.0


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number, got {v!r}")
    return float(v)


def _geometry(i, r):
    shape = r.get("shape")
    if shape not in SHAPES:
        raise ValueError(f"regions[{i}].shape={shape!r} not in {tuple(SHAPES)}")
    missing = [k for k in SHAPES[shape] if k not in r]
    if missing:
        raise ValueError(f"regions[{i}] ({shape}) missing {missing}")
    g = {"shape": shape}
    if shape == "half":
        if r["side"] not in SIDES:
            raise ValueError(f"regions[{i}].side={r['side']!r} not in {SIDES}")
        g["side"] = r["side"]
    elif shape == "polygon":
        pts = r["points"]
        if not isinstance(pts, list) or len(pts) < 3 or any(not isinstance(q, list) or len(q) != 2 for q in pts):
            raise ValueError(f"regions[{i}].points must be a list of >= 3 [x, z] pairs")
        g["points"] = [[_num(a, f"regions[{i}].points"), _num(b, f"regions[{i}].points")] for a, b in pts]
    else:
        g.update({k: _num(r[k], f"regions[{i}].{k}") for k in SHAPES[shape]})
    if shape == "box" and not (g["x1"] > g["x0"] and g["z1"] > g["z0"]):
        raise ValueError(f"regions[{i}] box needs x1 > x0 and z1 > z0")
    if shape == "circle" and g["r"] <= 0:
        raise ValueError(f"regions[{i}].r must be > 0")
    return g


def _values(i, r, numeric, choices, preset_values):
    vals = {}
    if "preset" in r:
        vals.update({k: v for k, v in preset_values(r["preset"]).items() if k in BLENDABLE + BLEND_CHOICES})
    sets = r.get("set", {})
    if not isinstance(sets, dict):
        raise ValueError(f"regions[{i}].set must be an object")
    bad = sorted(set(sets) - set(BLENDABLE) - set(BLEND_CHOICES))
    if bad:
        raise ValueError(f"regions[{i}].set: {bad} cannot vary per region (global params); "
                         f"allowed: {list(BLENDABLE + BLEND_CHOICES)}")
    vals.update(sets)
    for k, v in vals.items():
        if k in choices:
            if v not in choices[k]:
                raise ValueError(f"regions[{i}].{k}={v!r} not in {choices[k]}")
            continue
        _, lo, hi = numeric[k]
        if not lo <= _num(v, f"regions[{i}].{k}") <= hi:
            raise ValueError(f"regions[{i}].{k}={v} outside [{lo}, {hi}]")
        vals[k] = float(v)
    return vals


def validate(regions, numeric, choices, preset_values) -> list:
    """Return regions in canonical form; 'set' holds fully resolved values (preset
    merged in), so the cache key changes when a referenced preset file changes."""
    if not isinstance(regions, list):
        raise ValueError("regions must be a list")
    out = []
    for i, r in enumerate(regions):
        if not isinstance(r, dict):
            raise ValueError(f"regions[{i}] must be an object")
        unknown = set(r) - {"shape", "blend", "preset", "set", "name"} - set(SHAPES.get(r.get("shape"), ()))
        if unknown:
            raise ValueError(f"regions[{i}] unknown key(s) {sorted(unknown)}")
        g = _geometry(i, r)
        blend = _num(r.get("blend", DEFAULT_BLEND), f"regions[{i}].blend")
        if not 0 <= blend <= BLEND_MAX:
            raise ValueError(f"regions[{i}].blend={blend} outside [0, {BLEND_MAX}]")
        c = {**g, "blend": blend, "set": _values(i, r, numeric, choices, preset_values)}
        for k in ("preset", "name"):
            if k in r:
                c[k] = r[k]
        out.append(c)
    return out


def _sd_box(X, Z, x0, z0, x1, z1):
    dx = np.maximum(x0 - X, X - x1)
    dz = np.maximum(z0 - Z, Z - z1)
    return np.hypot(np.maximum(dx, 0), np.maximum(dz, 0)) + np.minimum(np.maximum(dx, dz), 0)


def _sd_polygon(X, Z, pts):
    d2 = np.full(X.shape, np.inf)
    inside = np.zeros(X.shape, dtype=bool)
    for (ax, az), (bx, bz) in zip(pts, pts[1:] + pts[:1]):
        ex, ez = bx - ax, bz - az
        t = np.clip(((X - ax) * ex + (Z - az) * ez) / max(ex * ex + ez * ez, 1e-12), 0, 1)
        d2 = np.minimum(d2, (X - ax - t * ex) ** 2 + (Z - az - t * ez) ** 2)
        crosses = (az > Z) != (bz > Z)  # even-odd rule
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = ax + (Z - az) * ex / (ez if ez else 1e-12)
        inside ^= crosses & (X < xi)
    return np.where(inside, -1.0, 1.0) * np.sqrt(d2)


def signed_distance(r, X, Z, width, length):
    """Blocks to the shape border; negative inside."""
    s = r["shape"]
    if s == "half":
        return {"west": X - width / 2, "east": width / 2 - X,
                "north": Z - length / 2, "south": length / 2 - Z}[r["side"]]
    if s == "box":
        return _sd_box(X, Z, r["x0"], r["z0"], r["x1"], r["z1"])
    if s == "circle":
        return np.hypot(X - r["cx"], Z - r["cz"]) - r["r"]
    return _sd_polygon(X, Z, r["points"])


def weight(r, X, Z, width, length):
    """Smooth mask in [0, 1]: 1 deeper than blend/2 inside, 0 beyond blend/2 outside."""
    half = max(r["blend"] / 2, 1e-6)
    t = np.clip((half - signed_distance(r, X, Z, width, length)) / (2 * half), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def apply(p: dict, X, Z) -> dict:
    """Params with blended keys replaced by per-cell arrays (p itself if no regions)."""
    regions = p.get("regions") or []
    if not regions:
        return p
    ws = [weight(r, X, Z, p["width"], p["length"]) for r in regions]
    q = dict(p)
    for k in BLENDABLE:
        for r, w in zip(regions, ws):
            if k in r["set"]:
                q[k] = q[k] * (1 - w) + r["set"][k] * w
    for k in BLEND_CHOICES:
        if any(k in r["set"] for r in regions):
            q[k] = _dominant(p[k], [(r["set"].get(k), w) for r, w in zip(regions, ws)])
    return q


def _dominant(base, layers):
    acc = {base: np.ones_like(layers[0][1])}
    for name, w in layers:
        if name is None:
            continue
        acc = {n: a * (1 - w) for n, a in acc.items()}
        acc[name] = acc.get(name, 0) + w
    names = list(acc)
    return np.array(names)[np.argmax(np.stack([acc[n] for n in names]), axis=0)]


def crop(q: dict, sl) -> dict:
    return {k: (v[sl] if isinstance(v, np.ndarray) else v) for k, v in q.items()}

