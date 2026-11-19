"""Post-export natural features (Faz 9): flower patches, bushes, fallen logs, boulders.

WorldPainter's plant / custom-object layers (PlantLayer, Bo2Layer) have no documented script API (only GUI-made
.layer files load), so the exported world is decorated here instead. Sites are a jittered 8-block grid; every
choice is a hash of (seed, x, z), so the result is a pure function of world coordinates. Features never cross
a region border (anchors keep EDGE blocks away), so each region file is decorated on its own.
Blocks are only put on dry ground with air / short plants above, away from water and WP trees; boulders rise at
most one block above the ground (the verifier's +-1 height check stays exact).
"""
import json
from pathlib import Path

import nbtlib
import numpy as np

from .edit_world import _save_chunks, columns, load_area, write_slots
from .generator import T
from .verify_world import Y_MIN

CELL = 8   # one candidate site per CELL x CELL blocks
EDGE = 4   # anchors stay this far from map and region borders (largest feature reaches 3 blocks out)
KINDS = ("flowers", "bush", "log", "boulder")
_OPEN = {"minecraft:air", "minecraft:short_grass", "minecraft:fern"}  # replaceable single-block plants
_SOIL = {"minecraft:grass_block", "minecraft:podzol", "minecraft:coarse_dirt", "minecraft:moss_block",
         "minecraft:dirt"}
_ROCKY = _SOIL | {"minecraft:stone", "minecraft:andesite", "minecraft:tuff", "minecraft:gravel"}
_FLOWERS = ("poppy", "dandelion", "oxeye_daisy", "cornflower", "azure_bluet")
_FANTASY_FLOWERS = ("allium", "lily_of_the_valley", "cornflower", "oxeye_daisy", "pink_tulip")
_BOULDER = ("stone", "andesite", "cobblestone", "mossy_cobblestone")


def _hash(x, z, seed, salt):
    """Uniform [0, 1) from integer coords (splitmix64 finaliser)."""
    with np.errstate(over="ignore"):
        v = (np.asarray(x, dtype=np.uint64) * np.uint64(0x9E3779B97F4A7C15)
             ^ np.asarray(z, dtype=np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)
             ^ np.uint64((seed * 1000003 + salt) & 0xFFFFFFFFFFFFFFFF))
        v = (v ^ (v >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        v = (v ^ (v >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        v = v ^ (v >> np.uint64(31))
    return (v >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def plan(p: dict, f: dict) -> list[tuple]:
    """Feature anchors [(kind, x, z)] for the map, from params + engine fields (deterministic)."""
    v = p.get("vegetation", 0.0)
    if v <= 0:
        return []
    W, L, s, wl = p["width"], p["length"], p["seed"], p["water_level"]
    cz, cx = np.mgrid[0:L // CELL, 0:W // CELL] * CELL
    x = cx + 2 + np.floor(_hash(cx, cz, s, 1) * (CELL - 3)).astype(np.int64)
    z = cz + 2 + np.floor(_hash(cx, cz, s, 2) * (CELL - 3)).astype(np.int64)
    ok = ((x % 512 >= EDGE) & (x % 512 < 512 - EDGE) & (z % 512 >= EDGE) & (z % 512 < 512 - EDGE)
          & (x >= EDGE) & (x < W - EDGE) & (z >= EDGE) & (z < L - EDGE))
    x, z = np.clip(x, 0, W - 1), np.clip(z, 0, L - 1)
    h, slope, t = f["height"][z, x], f["slope"][z, x], f["terrain"][z, x]
    trees = f["trees_deciduous"][z, x].astype(int) + f["trees_pine"][z, x]
    ok &= (h > wl + 1.5) & (f["coast"][z, x] > 0.999) & (f["river_dist"][z, x] > p["river_width"] / 2 + 4)
    if "water_level" in f:  # keep the whole footprint (+1) clear of lakes and streams
        r = EDGE + 1
        lw = np.pad((f["water_level"] > 0).astype(np.int64), r + 1).cumsum(0).cumsum(1)
        zi, xi = z + r + 1, x + r + 1
        ok &= (lw[zi + r, xi + r] - lw[zi - r - 1, xi + r] - lw[zi + r, xi - r - 1] + lw[zi - r - 1, xi - r - 1]) == 0
    grass = np.isin(t, (T["GRASS"], T["BARE_GRASS"]))
    soil = np.isin(t, (T["GRASS"], T["PODZOL"], T["COARSE_DIRT"], T["MOSS"]))
    rocky = np.isin(t, (T["STONE"], T["ANDESITE"], T["TUFF"], T["GRAVEL"], T["BARE_GRASS"]))
    prob = np.stack([
        0.6 * grass * (slope < 28) * (trees < 6),                         # flower patches in open grass, clearings
        (0.3 * ((trees > 0) & (trees < 11)) + 0.1 * (trees == 0)) * soil * (slope < 35),  # bushes, forest edges
        0.35 * (trees >= 3) * soil * (slope < 20),                        # fallen logs in the woods
        0.4 * rocky * (slope < 50) + 0.12 * soil * (slope > 12) * (slope < 40),  # boulders
    ]) * v * ok
    u = _hash(x, z, s, 3)
    cum = np.cumsum(prob, axis=0)
    kind = np.argmax(u[None] < cum, axis=0)
    hit = u < cum[-1]
    return [(KINDS[k], int(a), int(b)) for k, a, b in zip(kind[hit], x[hit], z[hit])]


def footprint(p: dict, f: dict) -> np.ndarray:
    """Columns a planned feature may touch (server probes avoid them)."""
    m = np.zeros((p["length"], p["width"]), dtype=bool)
    for _, x, z in plan(p, f):
        m[max(z - 3, 0):z + 4, max(x - 3, 0):x + 4] = True
    return m


def _state(name, **props):
    c = nbtlib.Compound({"Name": nbtlib.String(f"minecraft:{name}")})
    if props:
        c["Properties"] = nbtlib.Compound({k: nbtlib.String(str(v)) for k, v in props.items()})
    return c


class _Placer:
    """Feature writer on one loaded area (ids [y, z, x] relative to gx0/gz0)."""

    def __init__(self, area, seed, fantasy):
        self.a, self.ids, self.pal, self.seed, self.fantasy = area, area["ids"], area["pal"], seed, fantasy
        col = columns(self.ids, self.pal)
        self.top, self.surface, self.wtop = col["top"], col["surface"], col["water_top"]
        trunk = self.pal.lut(lambda n: n.endswith(("_log", "_wood")))  # canopy overhead is fine, trunks are not
        self.tree_col = trunk[self.ids].any(axis=0)

    def _ix(self, x, z):
        return z - self.a["gz0"], x - self.a["gx0"]

    def _above_open(self, x, z, n):
        zi, xi = self._ix(x, z)
        y = self.top[zi, xi] - Y_MIN
        return all(self.pal.names[self.ids[y + k, zi, xi]] in _OPEN for k in range(1, n + 1))

    def _site(self, cells, ground, n_open=2):
        """Column tops of cells if all are dry, on `ground`, open above and tree-free; else None."""
        tops = []
        for x, z in cells:
            zi, xi = self._ix(x, z)
            if (self.wtop[zi, xi] != self.top[zi, xi] or self.surface[zi, xi] not in ground
                    or self.tree_col[zi, xi]
                    or not self._above_open(x, z, n_open)):
                return None
            tops.append(int(self.top[zi, xi]))
        return tops

    def put(self, x, z, y, state):
        zi, xi = self._ix(x, z)
        self.ids[y - Y_MIN, zi, xi] = self.pal.add(state)

    def u(self, x, z, salt):
        return float(_hash(x, z, self.seed, salt))

    def flowers(self, x, z):
        pool = _FANTASY_FLOWERS if self.fantasy else _FLOWERS
        name = pool[int(self.u(x, z, 10) * len(pool))]
        n = 0
        for dz in range(-2, 3):
            for dx in range(-2, 3):
                cx, cz = x + dx, z + dz
                if dx * dx + dz * dz > 5 or self.u(cx, cz, 11) > 0.6:
                    continue
                tops = self._site([(cx, cz)], {"minecraft:grass_block"}, 1)
                if tops:
                    self.put(cx, cz, tops[0] + 1, _state(name))
                    n += 1
        return n > 0

    def bush(self, x, z):
        tops = self._site([(x, z)], _SOIL, 2)
        if not tops:
            return False
        y = tops[0]
        pine = self.u(x, z, 20) < 0.3 and not self.fantasy
        log = "spruce_log" if pine else "oak_log"
        leaves = ("spruce_leaves" if pine else
                  ("flowering_azalea_leaves" if self.u(x, z, 21) < 0.4 else "azalea_leaves") if self.fantasy
                  else "oak_leaves")
        leaf = _state(leaves, distance=1, persistent="true", waterlogged="false")
        self.put(x, z, y + 1, _state(log, axis="y"))
        self.put(x, z, y + 2, leaf)
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                cx, cz = x + dx, z + dz
                corner = dx != 0 and dz != 0
                if (dx or dz) and not (corner and self.u(cx, cz, 22) < 0.5) and self._open_at(cx, cz, y + 1, y - 1):
                    self.put(cx, cz, y + 1, leaf)
                if (dx or dz) and not corner and self._open_at(cx, cz, y + 2, y - 1):
                    self.put(cx, cz, y + 2, leaf)
        return True

    def _open_at(self, x, z, y, min_top):
        """Block (x, y, z) is air / a short plant, over ground no lower than min_top (no floating leaves)."""
        zi, xi = self._ix(x, z)
        return (min_top <= self.top[zi, xi] < y and self.wtop[zi, xi] == self.top[zi, xi]
                and self.pal.names[self.ids[y - Y_MIN, zi, xi]] in _OPEN)

    def log(self, x, z):
        n = 3 + int(self.u(x, z, 30) * 3)
        along_x = self.u(x, z, 31) < 0.5
        cells = [(x + i - n // 2, z) if along_x else (x, z + i - n // 2) for i in range(n)]
        tops = self._site(cells, _SOIL, 2)
        if not tops or max(tops) - min(tops) > 1:  # rests on the highest point, at most one block of gap
            return False
        r = self.u(x, z, 32)
        wood = "spruce_log" if r < 0.25 else "birch_log" if r < 0.5 else "dark_oak_log" if self.fantasy and r < 0.7 else "oak_log"
        y = max(tops) + 1
        for i, (cx, cz) in enumerate(cells):
            self.put(cx, cz, y, _state(wood, axis="x" if along_x else "z"))
            if 0 < i < n - 1 and self.u(cx, cz, 33) < 0.45:
                self.put(cx, cz, y + 1, _state("moss_carpet"))
        return True

    def boulder(self, x, z):
        big = self.u(x, z, 40) < 0.5
        r = (-1, 0, 1) if big else (0, 1)
        cells = [(x + dx, z + dz) for dz in r for dx in r if not (big and dx and dz and self.u(x + dx, z + dz, 41) < 0.6)]
        tops = self._site(cells, _ROCKY, 1)
        if not tops or max(tops) - min(tops) > 1:
            return False
        mossy = self.u(x, z, 42) < 0.5
        for (cx, cz), t in zip(cells, tops):
            k = self.u(cx, cz, 43)
            block = _BOULDER[3 if mossy and k < 0.5 else int(k * 3)]
            self.put(cx, cz, t, _state(block))  # embedded: replaces the ground block
            if (not big or (cx == x or cz == z)) and t <= min(tops) and self.u(cx, cz, 44) < 0.85:
                self.put(cx, cz, t + 1, _state(_BOULDER[int(self.u(cx, cz, 45) * 4)]))
        return True


def decorate_world(world: Path, p: dict, f: dict) -> dict:
    """Place the planned features into the exported world (in place). Returns counts per kind."""
    feats = plan(p, f)
    counts = dict.fromkeys(KINDS, 0)
    counts["planned"] = len(feats)
    by_region = {}
    for k, x, z in feats:
        by_region.setdefault((x >> 9, z >> 9), []).append((k, x, z))
    fantasy = p["biome"] == "fantasy_forest" if isinstance(p["biome"], str) else False
    for (rx, rz), items in sorted(by_region.items()):
        xs, zs = [x for _, x, _ in items], [z for _, _, z in items]
        area = load_area(world, min(xs) - EDGE, min(zs) - EDGE, max(xs) + EDGE + 1, max(zs) + EDGE + 1)
        orig = area["ids"].copy()
        pl = _Placer(area, p["seed"], fantasy)
        for k, x, z in items:
            if getattr(pl, k)(x, z):
                counts[k] += 1
        changed = area["ids"] != orig
        if changed.any():
            _, _, touched = _save_chunks(area, changed)
            for path in touched:
                write_slots(world / "region" / path.name, area["regions"][path])
    (world.parent.parent / "decorations.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return counts
