"""Procedural terrain: heightfield, river, erosion, surface + vegetation masks.

Everything is a function of global block coordinates + params, and every
neighbourhood operation (erosion, blur, slope) is computed with a margin, so a
tile generated alone is bit-identical to the same area of the full map.
"""
import json
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from . import hydraulic, regions
from .noise import fbm, perlin, ridged, warp

# WorldPainter terrain indices (verified with wpscript 2.27.1 probe)
T = dict(GRASS=0, DIRT=1, SAND=2, STONE=4, ROCK=5, SNOW=8, GRAVEL=10, CLAY=11,
         BARE_GRASS=29, COARSE_DIRT=49, PODZOL=50, ANDESITE=74, STONE_MIX=75,
         CALCITE=157, MOSS=160, TUFF=161)
# ROCK / STONE_MIX are WorldPainter mixes layered by height: on slopes they show as contour stripes,
# so the surface pass (>= 0.9.0) paints rock from pure blocks in noise patches instead (_rock_mix)
# Biomes layer values = WP biome IDs for the 1.20.5 map format (probed: Minecraft1_21Biomes.BIOMES_BY_MODERN_ID)
B = dict(OCEAN=0, PLAINS=1, WINDSWEPT_HILLS=3, FOREST=4, TAIGA=5, RIVER=7, BEACH=16, BIRCH_FOREST=27,
         STONY_SHORE=25, DARK_FOREST=29, JAGGED_PEAKS=251, MEADOW=252, SNOWY_SLOPES=253, STONY_PEAKS=254)
CAVE_CLEARANCE = 8  # blocks kept cave-free beside the river, so its bed is never undercut


def smoothstep(a, b, x):
    t = np.clip((x - a) / np.maximum(b - a, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def erosion_iterations(p: dict) -> tuple[int, int]:
    return int(round(p["erosion"] * 150)), int(round(p["erosion"] * 3)) + 1


def margin(p: dict) -> int:
    thermal, blur = erosion_iterations(p)
    return thermal + blur + 2


def _river_geometry(p, X, Z):
    """Distance (blocks) from each cell to the meandering river centre line."""
    ns = p["river_axis"] == "north_south"
    along, across = (Z, X) if ns else (X, Z)
    span = p["width"] if ns else p["length"]
    run = p["length"] if ns else p["width"]
    rng = np.random.Generator(np.random.PCG64(p["seed"] + 404))
    ph1, ph2 = rng.uniform(0, 2 * np.pi, 2)
    amp = p["river_meander"] * span

    def centre(t):
        wav = 0.65 * np.sin(2 * np.pi * t / (run * 0.9) + ph1) + 0.35 * np.sin(2 * np.pi * t / (run * 0.37) + ph2)
        wobble = perlin(t / (run * 0.18), np.full_like(t, 0.37), p["seed"] + 405)
        return p["river_position"] * span + amp * (wav + 0.35 * wobble)

    c = centre(along)
    slope = centre(along + 0.5) - centre(along - 0.5)
    dist = np.abs(across - c) / np.sqrt(1 + slope * slope)
    return dist, span


_NB8 = [(dz, dx) for dz in range(3) for dx in range(3) if (dz, dx) != (1, 1)]


def _thermal(h, iterations, talus, rate=0.25):
    buf = np.zeros((h.shape[0] + 2, h.shape[1] + 2))  # zero ring stays zero: same as np.pad(move, 1)
    for _ in range(iterations):
        hp = np.pad(h, 1, mode="edge")
        delta = np.zeros_like(h)
        for dz, dx in _NB8:  # 8 neighbours, so diagonals can't stay steeper than talus
            nb = hp[dz:dz + h.shape[0], dx:dx + h.shape[1]]
            reach = 1.4142 if dz != 1 and dx != 1 else 1.0
            move = np.maximum(h - nb - talus * reach, 0.0) * rate * 0.25
            delta -= move
            # material lands on the lower neighbour: shift back by the opposite offset
            buf[1:-1, 1:-1] = move
            delta += buf[2 - dz:2 - dz + h.shape[0], 2 - dx:2 - dx + h.shape[1]]
        h = h + delta
    return h


def _blur(h, passes, weight=0.5):
    for _ in range(passes):
        hp = np.pad(h, 1, mode="edge")
        b = (hp[:-2, 1:-1] + hp[2:, 1:-1] + hp[1:-1, :-2] + hp[1:-1, 2:] + 4 * h) / 8.0
        h = h + (b - h) * weight
    return h


SEA_DEPTH = 10  # sea floor at the map border = water_level - 10, matching the flat ocean written outside the map
SHORE_STEEP = 20.0  # degrees: steeper shore cells are rocky (stony shore), gentler ones are beaches
MOUNTAIN_COAST = 1.0  # mountains also fade over the coast band (twice, with the coast fade): no wall into the sea


def _coast(p, X, Z, inner=1.0):
    """1 inland, 0 at the map border (and outside). Noisy coastline, exact 0 on the border."""
    fall = p["edge_falloff"]
    if fall <= 0:
        return np.ones_like(X, dtype=np.float64)
    e = np.minimum(np.minimum(X, p["width"] - 1 - X), np.minimum(Z, p["length"] - 1 - Z))
    wobble = fbm(X / 40.0, Z / 40.0, p["seed"] + 97, octaves=3) * 0.35 * fall * smoothstep(0, fall * 0.5, e)
    return smoothstep(2.0, max(fall * inner, 3.0), e + wobble)


def _pre_heights(p, X, Z, dist, span):
    """Heights up to thermal erosion + smoothing (before hydraulic erosion and river carving)."""
    s = p["seed"]
    wl = p["water_level"]
    sc = p["noise_scale"]
    wx, wz = warp(X, Z, s, sc * 1.5, p["warp_strength"])

    half_valley = max(p["valley_width"] * span / 2, 1.0)
    hw = p["river_width"] / 2
    floodplain = hw + p["bank_width"] + 0.08 * half_valley
    v = smoothstep(floodplain, half_valley, dist) if p["valley_width"] > 0 else np.ones_like(X, dtype=np.float64)

    ridge = ridged(wx / sc, wz / sc, s + 11, octaves=min(p["octaves"], 4),
                   gain=0.38 + 0.12 * p["roughness"], sharpness=p["ridge_sharpness"])
    broad = fbm(wx / (sc * 2.5), wz / (sc * 2.5), s + 23, octaves=3) * 0.5 + 0.5
    mountain = p["mountain_height"] * v ** 1.2 * (0.3 + 0.7 * ridge) * (0.6 + 0.4 * broad)
    mountain = mountain * _coast(p, X, Z, inner=MOUNTAIN_COAST)
    hills = fbm(wx / (sc * 0.35), wz / (sc * 0.35), s + 5, octaves=p["octaves"],
                gain=0.3 + 0.3 * p["roughness"]) * p["hill_amplitude"] * (0.35 + 0.65 * v)
    h = p["base_height"] + hills + mountain

    # coast: fade everything to the flat sea floor of the outside ocean, so the map edge has no seam
    seabed = wl - SEA_DEPTH
    coast = _coast(p, X, Z)
    h = seabed + (h - seabed) * coast

    if np.any(p["cliffiness"] > 0):
        step = p["terrace_step"]
        q = h / step
        frac = q - np.floor(q)
        k = 1.0 / np.maximum(1.0 - 0.9 * p["cliffiness"], 0.1)
        terr = (np.floor(q) + smoothstep(0, 1, (frac - 0.5) * k + 0.5)) * step
        band = p["cliffiness"] * v * smoothstep(-0.2, 0.3, fbm(X / 90, Z / 90, s + 61, octaves=2))
        h = h + (terr - h) * band

    thermal, blur = erosion_iterations(p)
    h = _thermal(h, thermal, talus=1.05 - 0.5 * p["erosion"], rate=0.5)
    return _blur(h, blur - 1, weight=0.5), coast


def _finish(p, X, Z, h, dist, coast):
    s = p["seed"]
    wl = p["water_level"]
    hw = p["river_width"] / 2
    seabed = wl - SEA_DEPTH
    if hw > 0:
        bank_h = wl + 1.0 + 0.6 * fbm(X / 30, Z / 30, s + 81, octaves=2)
        h = bank_h + (h - bank_h) * smoothstep(hw, hw + 2 * p["bank_width"] + 2, dist)
        bed = wl - p["river_depth"] * (1 - np.clip(dist / max(hw, 1e-6), 0, 1) ** 2) + 0.4 * perlin(X / 11, Z / 11, s + 83)
        h = np.where(dist < hw, np.minimum(h, np.minimum(bed, wl - 0.6)), h)
    inland = coast > 0.999
    h = np.where(inland & ~(dist < hw), np.maximum(h, wl + 1.0), h)
    h = seabed + (h - seabed) * _coast(p, X, Z, inner=0.35)  # river banks must not stick out into the sea
    return _blur(h, 1, weight=0.35), coast


def _rock_mix(n3, n4):
    """Natural rock from pure blocks in irregular patches: stone, andesite, tuff, gravel pockets, calcite."""
    r = np.full(n3.shape, T["STONE"], dtype=np.uint8)
    r[n3 > 0.15] = T["ANDESITE"]
    r[n4 > 0.4] = T["TUFF"]
    r[(n4 < -0.4) & (n3 < 0.05)] = T["GRAVEL"]
    r[(n3 < -0.45) & (n4 > 0.05)] = T["CALCITE"]
    return r


def _surface(p, X, Z, h, slope, dist, coast):
    s = p["seed"]
    wl = p["water_level"]
    hw = p["river_width"] / 2
    n1 = fbm(X / 24.0, Z / 24.0, s + 71, octaves=3)
    n2 = fbm(X / 60.0, Z / 60.0, s + 72, octaves=2)
    n3 = fbm(X / 9.0, Z / 9.0, s + 73, octaves=2)  # fine patches: rock mix, ragged rock edge
    n4 = fbm(X / 17.0, Z / 17.0, s + 74, octaves=3)
    biome = p["biome"]  # str, or per-cell str array when regions set it
    rocks = _rock_mix(n3, n4)

    forest = smoothstep(-0.08, 0.08, fbm(X / 110.0, Z / 110.0, s + 91, octaves=4) * 0.5 + 0.5 - (1 - p["forest_coverage"]))
    t = np.full(h.shape, T["GRASS"], dtype=np.uint8)
    soil = (forest > 0.5) & (n1 > 0.55 - 0.6 * p["soil_ratio"] - np.where(biome == "taiga", 0.25, 0.0))
    t[soil] = T["PODZOL"]
    t[soil & (n2 > 0.35)] = T["COARSE_DIRT"]
    t[(biome == "fantasy_forest") & (forest > 0.6) & (n2 < -0.25) & (slope < 25)] = T["MOSS"]
    side = (h > wl + 20) & (slope > 18)  # open mountainside: soil patches between the grass
    t[side & (n4 > 0.3)] = T["COARSE_DIRT"]
    t[side & (n4 < -0.35)] = T["PODZOL"]
    alpine = h > p["tree_line"] + 5 * n1
    t[alpine] = T["BARE_GRASS"]
    t[alpine & (n2 > 0.2)] = rocks[alpine & (n2 > 0.2)]

    # rock only where really steep; fine noise makes its edge ragged so grass creeps up the gentler parts
    rock_thr = 55 - 16 * p["rock_ratio"] + 6 * n1 + 5 * n3 - np.where(biome == "rocky", 8.0, 0.0)
    scree = (slope > rock_thr - 10) & (slope <= rock_thr)
    t[scree & (n3 > 0.25)] = T["GRAVEL"]
    t[scree & (n3 < -0.2)] = T["COARSE_DIRT"]
    rock = slope > rock_thr
    t[rock] = rocks[rock]
    t[(h > p["snow_line"] + 6 * n1) & (slope < 42)] = T["SNOW"]

    if hw > 0:
        t[(dist < hw + 2.5) & (h < wl + 2.2)] = T["SAND"]
        bed = dist < hw
        t[bed] = np.where(n1[bed] > -0.1, T["GRAVEL"], T["SAND"])
        t[bed & (n2 > 0.45)] = T["CLAY"]

    # sea shore: beaches (some pebble) where gentle, rocky shore where steep; sand/gravel/clay sea floor
    shore = coast < 0.999
    low = shore & (h < wl + 2.2)
    steep = slope > SHORE_STEEP + 8 * n3
    t[low & ~steep] = np.where(n4[low & ~steep] > 0.35, T["GRAVEL"], T["SAND"])
    t[low & steep] = np.where(n3[low & steep] > 0.3, T["GRAVEL"], rocks[low & steep])
    sea = shore & (h < wl - 1) & ~steep
    t[sea & (n2 > 0.3)] = T["GRAVEL"]
    t[sea & (n4 < -0.4) & (h > wl - 6)] = T["CLAY"]
    t[sea & (h < wl - 5) & (n3 > 0.1)] = T["GRAVEL"]

    suit = (h > wl + 1.5) * (1 - smoothstep(30, 42, slope)) * (1 - smoothstep(p["tree_line"] - 12, p["tree_line"], h))
    suit = suit * (dist > hw + 4) * np.isin(t, (T["GRASS"], T["PODZOL"], T["COARSE_DIRT"], T["MOSS"]))
    ledge = 0.35 * smoothstep(15, 25, slope) * (h > wl + 15)  # sparse trees on grassy mountain ledges
    density = np.maximum(forest, ledge) * suit * p["tree_density"]
    pine_w = smoothstep(p["pine_line"] - 12, p["pine_line"] + 12, h)
    pine_w = np.where(biome == "taiga", np.maximum(pine_w, 0.75), pine_w)
    deciduous = np.round(density * (1 - pine_w) * 15).astype(np.uint8)
    pine = np.round(density * pine_w * 15).astype(np.uint8)
    out = {"terrain": t, "trees_deciduous": deciduous, "trees_pine": pine}
    if p["biomes"] == "on":  # key only present when on: old params keep identical outputs
        out["biomes"] = _biomes(p, h, slope, dist, coast, t, forest, pine_w, alpine, rock, n2, steep)
    return out


def _biomes(p, h, slope, dist, coast, t, forest, pine_w, alpine, rock, n2, steep):
    wl, biome = p["water_level"], p["biome"]
    b = np.full(h.shape, B["PLAINS"], dtype=np.uint8)
    b[(biome == "rocky") & (slope > 20)] = B["WINDSWEPT_HILLS"]
    woods = (forest > 0.5) & (h > wl + 1.5) & ~alpine
    b[woods] = B["FOREST"]
    fantasy = woods & (biome == "fantasy_forest")
    b[fantasy & (n2 < -0.2)] = B["DARK_FOREST"]
    b[fantasy & (n2 > 0.35)] = B["BIRCH_FOREST"]
    b[woods & (pine_w > 0.5)] = B["TAIGA"]
    b[alpine] = B["MEADOW"]
    b[alpine & rock] = B["STONY_PEAKS"]
    snow = t == T["SNOW"]
    b[snow] = B["SNOWY_SLOPES"]
    b[snow & (slope > 30)] = B["JAGGED_PEAKS"]
    shore = coast < 0.999
    b[shore & (h < wl + 2.2)] = B["BEACH"]
    b[shore & (h < wl + 2.2) & steep] = B["STONY_SHORE"]
    b[shore & (h < wl)] = B["OCEAN"]
    b[(dist < p["river_width"] / 2) & (h < wl)] = B["RIVER"]
    return b


def _caves(p, X, Z, h, dist, coast):
    """Caverns / Chasms layer intensities (0..15). Never under the river, the sea or low shore."""
    s, wl = p["seed"], p["water_level"]
    ok = (coast > 0.999) & (dist > p["river_width"] / 2 + CAVE_CLEARANCE) & (h > wl + 4)
    # WP carves almost nothing below intensity ~8 (probed: 6 -> 1.5% of columns, 10 -> 12%, 15 -> 18%),
    # so `caves` sets both the patch area and an intensity of 8..15 inside the patches
    shift = 0.3 * p["caves"]
    patch = smoothstep(0.15 - shift, 0.3 - shift, fbm(X / 70.0, Z / 70.0, s + 131, octaves=3))
    rift = 1 - np.abs(fbm(X / 160.0, Z / 160.0, s + 137, octaves=2))  # thin lines where the noise crosses 0
    level = 8 + 7 * p["caves"]
    caverns = np.round(level * patch * ok).astype(np.uint8)
    chasms = np.round(level * smoothstep(0.96, 0.99, rift) * ok * (h > wl + 12)).astype(np.uint8)
    return {"caverns": caverns, "chasms": chasms}


def _grid(p, x0, z0, w, h):
    m = margin(p)
    xs = np.arange(x0 - m, x0 + w + m, dtype=np.float64)
    zs = np.arange(z0 - m, z0 + h + m, dtype=np.float64)
    X, Z = np.meshgrid(xs, zs)  # rows = z (south), cols = x (east)
    dist, span = _river_geometry(p, X, Z)
    return X, Z, dist, span, regions.apply(p, X, Z)


_HYDRO_CACHE: dict = {}


def _shore_mask(coast):
    """0 on the coast fade: droplets running off the map must not cut sea inlets or seams."""
    return smoothstep(0.6, 1.0, coast)


HYDRO_TILE = 512  # blocks per tile of the global pass (multiple of 16, so of every erosion cell size)
PARALLEL_FROM = 1024 * 1024  # map area from which tiles are generated in worker processes
WORKERS = max(1, min(8, (os.cpu_count() or 2) // 2))  # ~0.6 GB peak per worker at 1024-block tiles


def _boxes(p, tile):
    return [(x0, z0, min(tile, p["width"] - x0), min(tile, p["length"] - z0))
            for z0 in range(0, p["length"], tile) for x0 in range(0, p["width"], tile)]


def _pmap(fn, p, boxes, workers, init=None):
    """fn(p, *box) for every box, in order; in `workers` processes (results streamed in order)."""
    if workers <= 1:
        return (fn(p, *b) for b in boxes)
    ex = ProcessPoolExecutor(min(workers, len(boxes)), initializer=_seed_cache, initargs=(init,))
    return _drain(ex, ex.map(fn, [p] * len(boxes), *zip(*boxes)))


def _drain(ex, it):
    with ex:
        yield from it


def _seed_cache(item):
    """Worker initializer: reuse the parent's global hydraulic pass instead of recomputing it."""
    if item is not None:
        _HYDRO_CACHE[item[0]] = item[1]


def _coarse_tile(p, x0, z0, w, hh):
    """Coarse means of pre-heights and shore mask of one tile (map area only)."""
    m, c = margin(p), hydraulic.cell_size(p["width"], p["length"])
    X, Z, dist, span, q = _grid(p, x0, z0, w, hh)
    pre, coast = _pre_heights(q, X, Z, dist, span)
    crop = (slice(m, m + hh), slice(m, m + w))
    return _down(pre[crop], c), _down(_shore_mask(coast[crop]), c)


def _down(a, c):
    """c x c block means (a's sides are multiples of c)."""
    return a.reshape(a.shape[0] // c, c, a.shape[1] // c, c).mean(axis=(1, 3))


def _hydro_field(p):
    """Global pass, cached per params: the hydraulic delta on the coarse grid (and its cell size).
    Pre-heights are built tile by tile (exact, thanks to the margin) and only their coarse means are
    kept, so memory is O(coarse grid) on any map size. Every tile samples the same delta -> exact stitching."""
    key = json.dumps(p, sort_keys=True)
    if key not in _HYDRO_CACHE:
        W, L = p["width"], p["length"]
        c = hydraulic.cell_size(W, L)
        coarse = np.empty((L // c, W // c))
        shore = np.empty_like(coarse)
        boxes = _boxes(p, HYDRO_TILE)
        workers = WORKERS if W * L >= PARALLEL_FROM else 1
        for (x0, z0, w, hh), (cp, sp) in zip(boxes, _pmap(_coarse_tile, p, boxes, workers)):
            sl = (slice(z0 // c, (z0 + hh) // c), slice(x0 // c, (x0 + w) // c))
            coarse[sl], shore[sl] = cp, sp
        talus = hydraulic.relax_talus(c)

        def relax(H, n):  # cell units: talus is the tangent of the angle of repose
            return _thermal(H, n, talus=talus, rate=0.5)
        # summits are hard rock: erodibility falls off over the top of the height range (keeps relief)
        hard = smoothstep(np.percentile(coarse, hydraulic.HARD_FROM), coarse.max(), coarse)
        erodibility = shore * (1 - hydraulic.HARD_CAP * hard)
        eroded = hydraulic.erode(coarse, p["hydraulic"], p["seed"], erodibility, relax, cell=c)
        delta = _blur(eroded - coarse, hydraulic.SMOOTH, weight=0.5)  # soften droplet-scale ripples
        _HYDRO_CACHE.clear()  # one map at a time keeps memory bounded
        _HYDRO_CACHE[key] = (delta, c)
    return _HYDRO_CACHE[key]


def _sample(delta, c, X, Z):
    """Bilinear sample of the coarse delta (c blocks per cell) at global block coords (edge-clamped)."""
    rows, cols = delta.shape
    fx = np.clip((X - (c - 1) / 2) / c, 0, cols - 1)
    fz = np.clip((Z - (c - 1) / 2) / c, 0, rows - 1)
    ix = np.minimum(fx.astype(np.int64), cols - 2)
    iz = np.minimum(fz.astype(np.int64), rows - 2)
    u, v = fx - ix, fz - iz
    return (delta[iz, ix] * (1 - u) * (1 - v) + delta[iz, ix + 1] * u * (1 - v)
            + delta[iz + 1, ix] * (1 - u) * v + delta[iz + 1, ix + 1] * u * v)


def _heights(p, q, X, Z, dist, span):
    h, coast = _pre_heights(q, X, Z, dist, span)
    if p["hydraulic"] > 0:
        delta, c = _hydro_field(p)
        h = h + _sample(delta, c, X, Z) * _shore_mask(coast)
    return _finish(q, X, Z, h, dist, coast)


def generate_region(p: dict, x0: int, z0: int, w: int, h: int) -> dict:
    """Generate the [x0, x0+w) x [z0, z0+h) area of the map described by p."""
    m = margin(p)
    X, Z, dist, span, q = _grid(p, x0, z0, w, h)
    height, coast = _heights(p, q, X, Z, dist, span)
    gz, gx = np.gradient(height)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    crop = (slice(m, m + h), slice(m, m + w))
    X, Z, height, slope, dist, coast = X[crop], Z[crop], height[crop], slope[crop], dist[crop], coast[crop]
    out = {"height": height.astype(np.float32), "slope": slope.astype(np.float32),
           "river_dist": dist.astype(np.float32), "coast": coast.astype(np.float32)}
    out.update(_surface(regions.crop(q, crop), X, Z, height, slope, dist, coast))
    if p["caves"] > 0:
        out.update(_caves(p, X, Z, height, dist, coast))
    return out


def generate(p: dict) -> dict:
    return generate_region(p, 0, 0, p["width"], p["length"])


def generate_tiled(p: dict, tile: int = 128, workers: int = 1):
    """Yield (x0, z0, data) tiles in row order; for maps too large to hold at once.
    workers > 1 generates tiles in parallel processes (same bits: every tile is a pure function)."""
    boxes = _boxes(p, tile)
    init = None
    if workers > 1 and p["hydraulic"] > 0:
        init = (json.dumps(p, sort_keys=True), _hydro_field(p))
    for (x0, z0, _, _), d in zip(boxes, _pmap(generate_region, p, boxes, workers, init)):
        yield x0, z0, d
