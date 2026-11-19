"""Faz 5: improve a box of an EXISTING Java 1.18+ (Anvil, 1.21.x) world -- always on a copy.

Flow: copy source -> out, read the box (+ margin) into one block-id grid, generate engine terrain for the
box in box-local coords, blend it into the old ground heights, rebuild the edited columns, clear trees
that would float, re-encode only changed sections and rewrite only changed chunks (other chunk blobs
are kept byte for byte). Edited chunks get empty Heightmaps + isLightOn=0, so the game recomputes both.
"""
import io
import math
import os
import shutil
import struct
import time
import zlib
from pathlib import Path

import nbtlib
import numpy as np

from . import params as P
from .generator import T, generate, smoothstep
from .verify_world import _NOT_GROUND, Y_MAX, Y_MIN, _section_columns

KEEP = 5  # outermost box columns are never rebuilt: trees rooted outside reach at most ~4 blocks in
SUB_DEPTH = 3  # blocks of sub-material under the top block, stone below
BOX_MAX = 512  # shortcut: whole box (+margin) held as one id grid in memory; tile it for bigger edits
BLEND_MAX = 256
HEIGHT = Y_MAX - Y_MIN
_SECTIONS = range(Y_MIN // 16, Y_MAX // 16)

# engine terrain class -> (top block, sub block)
_STACK = {T["GRASS"]: ("grass_block", "dirt"), T["BARE_GRASS"]: ("grass_block", "dirt"),
          T["DIRT"]: ("dirt", "dirt"), T["PODZOL"]: ("podzol", "dirt"), T["COARSE_DIRT"]: ("coarse_dirt", "dirt"),
          T["MOSS"]: ("moss_block", "dirt"), T["SAND"]: ("sand", "sand"), T["GRAVEL"]: ("gravel", "gravel"),
          T["CLAY"]: ("clay", "clay"), T["STONE"]: ("stone", "stone"), T["ROCK"]: ("stone", "stone"),
          T["STONE_MIX"]: ("stone", "stone"), T["ANDESITE"]: ("andesite", "stone"),
          T["CALCITE"]: ("calcite", "stone"), T["TUFF"]: ("tuff", "stone"), T["SNOW"]: ("snow_block", "stone")}
_PROPS = {"grass_block": {"snowy": "false"}, "podzol": {"snowy": "false"}, "water": {"level": "0"}}


# ---------------------------------------------------------------- safety

def check_paths(source, out) -> tuple[Path, Path]:
    """Refuse anything that could write to the source or to a Minecraft save folder."""
    src = Path(os.path.realpath(source))
    dst_abs = Path(os.path.abspath(out))
    dst = Path(os.path.realpath(out))
    if not (src / "level.dat").is_file() or not (src / "region").is_dir():
        raise FileNotFoundError(f"not a Minecraft world (level.dat + region/): {source}")
    for d in {dst_abs, dst}:
        if os.path.normcase(d) == os.path.normcase(src) or src in d.parents or d in src.parents:
            raise ValueError("out must be a new folder outside the source world")
    if os.path.lexists(dst_abs):
        raise FileExistsError(f"out already exists: {out}")
    appdata = os.environ.get("APPDATA")
    if appdata:  # string compare only: %APPDATA%/.minecraft itself is never read
        saves = os.path.normcase(os.path.join(os.path.abspath(appdata), ".minecraft"))
        for d in {dst_abs, dst}:
            if os.path.normcase(str(d)).startswith(saves + os.sep) or os.path.normcase(str(d)) == saves:
                raise ValueError("refusing to write inside %APPDATA%/.minecraft; pick an out folder elsewhere")
    return src, dst_abs


# ---------------------------------------------------------------- region / section I/O

def read_slots(path: Path) -> dict:
    """slot -> (timestamp bytes, stored chunk blob: length + compression + payload)."""
    raw = path.read_bytes()
    slots = {}
    for i in range(1024):
        loc, = struct.unpack(">I", raw[i * 4:i * 4 + 4])
        off = loc >> 8
        if off == 0:
            continue
        length, comp = struct.unpack(">IB", raw[off * 4096:off * 4096 + 5])
        if comp != 2:
            raise ValueError(f"unsupported chunk compression {comp} in {path.name}")
        slots[i] = (raw[4096 + i * 4:4100 + i * 4], raw[off * 4096:off * 4096 + 4 + length])
    return slots


def write_slots(path: Path, slots: dict) -> None:
    header, ts, body = bytearray(4096), bytearray(4096), bytearray()
    for slot in sorted(slots):
        stamp, blob = slots[slot]
        blob = blob + b"\0" * (-len(blob) % 4096)
        header[slot * 4:slot * 4 + 4] = struct.pack(">I", ((2 + len(body) // 4096) << 8) | (len(blob) // 4096))
        ts[slot * 4:slot * 4 + 4] = stamp
        body += blob
    path.write_bytes(bytes(header) + bytes(ts) + bytes(body))


def _parse(blob: bytes):
    return nbtlib.File.parse(io.BytesIO(zlib.decompress(blob[5:])))


def _blob(chunk) -> bytes:
    buf = io.BytesIO()
    chunk.write(buf)
    data = zlib.compress(buf.getvalue())
    return struct.pack(">IB", len(data) + 1, 2) + data


def pack_states(idx: np.ndarray, n: int):
    """Pack 4096 palette indices (y, z, x order) into the 1.16+ long array (no spanning across longs)."""
    bits = max(4, math.ceil(math.log2(n)))
    per = 64 // bits
    words = -(-4096 // per)
    v = np.zeros(words * per, dtype=np.uint64)
    v[:4096] = idx
    shifts = (np.arange(per, dtype=np.uint64) * np.uint64(bits))
    packed = np.bitwise_or.reduce(v.reshape(words, per) << shifts, axis=1)
    return nbtlib.LongArray(packed.view(np.int64))


class _Palette:
    """One block-state palette for the whole working grid (id -> NBT compound)."""

    def __init__(self):
        self.states, self.names, self._ids = [], [], {}
        self.AIR = self.block("air")

    def add(self, c) -> int:
        key = (str(c["Name"]), tuple(sorted((k, str(v)) for k, v in c.get("Properties", {}).items())))
        if key not in self._ids:
            self._ids[key] = len(self.states)
            self.states.append(c)
            self.names.append(key[0])
        return self._ids[key]

    def block(self, name: str) -> int:
        c = nbtlib.Compound({"Name": nbtlib.String(f"minecraft:{name}")})
        if name in _PROPS:
            c["Properties"] = nbtlib.Compound({k: nbtlib.String(v) for k, v in _PROPS[name].items()})
        return self.add(c)

    def lut(self, pred) -> np.ndarray:
        return np.array([bool(pred(n)) for n in self.names])


def _is_ground(n): return not _NOT_GROUND.search(n)
def _is_water(n): return any(k in n for k in ("water", "seagrass", "kelp", "bubble_column"))
def _is_log(n): return n.endswith(("_log", "_wood"))
def _is_tree(n): return _is_log(n) or n.endswith("_leaves")


def load_area(world: Path, x0: int, z0: int, x1: int, z1: int) -> dict:
    """Read every chunk overlapping [x0,x1) x [z0,z1) into one id grid [y, z, x] (missing chunks = air)."""
    gx0, gz0 = x0 // 16 * 16, z0 // 16 * 16
    gx1, gz1 = -(-x1 // 16) * 16, -(-z1 // 16) * 16
    pal = _Palette()
    ids = np.full((HEIGHT, gz1 - gz0, gx1 - gx0), pal.AIR, dtype=np.uint16)
    chunks, regions = {}, {}
    for rz in range(gz0 >> 9, (gz1 - 1 >> 9) + 1):
        for rx in range(gx0 >> 9, (gx1 - 1 >> 9) + 1):
            path = world / "region" / f"r.{rx}.{rz}.mca"
            if not path.exists():
                continue
            regions[path] = read_slots(path)
            for slot, (_, blob) in regions[path].items():
                cx, cz = rx * 32 + slot % 32, rz * 32 + slot // 32
                if not (gx0 <= cx * 16 < gx1 and gz0 <= cz * 16 < gz1):
                    continue
                chunk = _parse(blob)
                chunks[(cx, cz)] = (path, slot, chunk)
                xo, zo = cx * 16 - gx0, cz * 16 - gz0
                for sec in chunk.get("sections", []):
                    sy, sc = int(sec["Y"]), _section_columns(sec)
                    if sy not in _SECTIONS or sc is None:
                        continue
                    lut = np.array([pal.add(c) for c in sec["block_states"]["palette"]], dtype=np.uint16)
                    y = (sy - _SECTIONS.start) * 16
                    ids[y:y + 16, zo:zo + 16, xo:xo + 16] = lut[sc[1]].reshape(16, 16, 16)
    return {"ids": ids, "pal": pal, "gx0": gx0, "gz0": gz0, "chunks": chunks, "regions": regions}


def columns(ids: np.ndarray, pal: _Palette) -> dict:
    """Per column: top ground y (verify_world's classification, -999 if none), its block, water top."""
    g = pal.lut(_is_ground)[ids]
    has = g.any(axis=0)
    ti = HEIGHT - 1 - np.argmax(g[::-1], axis=0)
    top = np.where(has, ti + Y_MIN, -999)
    ys = np.arange(HEIGHT)[:, None, None]
    dry = ~pal.lut(_is_water)[ids] & (ys > ti)
    first_dry = np.where(dry.any(axis=0), np.argmax(dry, axis=0), HEIGHT)
    surface = np.array(pal.names, dtype=object)[np.take_along_axis(ids, ti[None], 0)[0]]
    return {"top": top, "surface": surface, "water_top": np.where(has, first_dry - 1 + Y_MIN, -999)}


def read_area(world, box) -> dict:
    """Heights / surface block / water top for box = (x0, z0, x1, z1), x1/z1 exclusive."""
    x0, z0, x1, z1 = box
    a = load_area(Path(world), x0, z0, x1, z1)
    sl = (slice(z0 - a["gz0"], z1 - a["gz0"]), slice(x0 - a["gx0"], x1 - a["gx0"]))
    return {k: v[sl] for k, v in columns(a["ids"], a["pal"]).items()}


# ---------------------------------------------------------------- edit

def weights(bw: int, bh: int, blend: float) -> tuple[np.ndarray, np.ndarray]:
    """(d, w): blocks from the box edge, and the new-terrain weight (0 for d < KEEP, 1 after the blend band)."""
    zz, xx = np.mgrid[0:bh, 0:bw]
    d = np.minimum(np.minimum(xx, bw - 1 - xx), np.minimum(zz, bh - 1 - zz))
    return d, np.where(d < KEEP, 0.0, smoothstep(KEEP - 1, KEEP - 1 + max(blend, 1), d))


def local_params(preset, overrides, bw: int, bh: int) -> dict:
    """Engine params for the box in box-local coords (regions too); no coast fade -- the blend is the edge.
    shortcut: no lakes/streams/vegetation in edited boxes (the rebuild only knows the sea level); Faz 10."""
    p = P.load(preset, overrides)
    return P.validate({**p, "width": max(16, -(-bw // 16) * 16), "length": max(16, -(-bh // 16) * 16),
                       "edge_falloff": 0.0, "tributaries": 0.0, "lakes": 0.0, "vegetation": 0.0})


def _rebuild(ids, pal, T0, T1, wtop, edited, terrain):
    """Rebuild edited columns: top block, SUB_DEPTH sub blocks, stone down to the old surface; air/water above."""
    top_ids, sub_ids = np.zeros(terrain.shape, np.uint16), np.zeros(terrain.shape, np.uint16)
    for cls, (top, sub) in _STACK.items():
        m = terrain == cls
        top_ids[m], sub_ids[m] = pal.block(top), pal.block(sub)
    unknown = ~np.isin(terrain, list(_STACK))
    top_ids[unknown], sub_ids[unknown] = pal.block("grass_block"), pal.block("dirt")
    wet = wtop > T1
    top_ids[wet & (top_ids == pal.block("grass_block"))] = pal.block("dirt")  # no grass under water
    y = np.arange(Y_MIN, Y_MAX, dtype=np.int16)[:, None, None]
    k = T1 - y
    stack = np.where(k == 0, top_ids, np.where(k <= SUB_DEPTH, sub_ids, pal.block("stone")))
    lo = np.minimum(T0, T1 - SUB_DEPTH)
    above = np.where(y <= wtop, pal.block("water"), pal.AIR)
    new = np.where((y >= lo) & (y <= T1), stack, np.where(y > T1, above, ids))
    return np.where(edited, new, ids).astype(np.uint16)


def _grow(a):
    for ax in range(3):
        b = a.copy()
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        lo[ax], hi[ax] = slice(1, None), slice(None, -1)
        b[tuple(lo)] |= a[tuple(hi)]
        b[tuple(hi)] |= a[tuple(lo)]
        a = b
    return a


def floating_trees(ids, pal, cols) -> np.ndarray:
    """Logs/leaves in `cols` not 26-connected (through logs/leaves) to a log standing on ground."""
    tree = pal.lut(_is_tree)[ids]
    out = np.zeros(ids.shape, dtype=bool)
    if not tree.any():
        return out
    ys = np.nonzero(tree.any(axis=(1, 2)))[0]
    a, b = max(ys[0] - 1, 0), ys[-1] + 1
    t = tree[a:b]
    s = pal.lut(_is_log)[ids[a:b]]
    s[1:] &= pal.lut(_is_ground)[ids[a:b - 1]]
    s[0] = False
    while True:
        n = _grow(s) & t
        if np.array_equal(n, s):
            break
        s = n
    out[a:b] = t & ~s & cols[None]
    return out


def _save_chunks(area, changed) -> tuple[int, int, set]:
    """Re-encode changed sections, drop block entities on changed blocks, mark light/heightmaps for recompute."""
    ids, pal, gx0, gz0 = area["ids"], area["pal"], area["gx0"], area["gz0"]
    n_chunks, n_be, touched = 0, 0, set()
    for (cx, cz), (path, slot, chunk) in area["chunks"].items():
        xo, zo = cx * 16 - gx0, cz * 16 - gz0
        cs = (slice(None), slice(zo, zo + 16), slice(xo, xo + 16))
        if not changed[cs].any():
            continue
        secs = {int(s["Y"]): s for s in chunk["sections"]}
        for sy in _SECTIONS:
            y = (sy - _SECTIONS.start) * 16
            if not changed[y:y + 16, zo:zo + 16, xo:xo + 16].any():
                continue
            if sy not in secs:  # shortcut: new sections get the nearest section's biomes
                near = min(secs, key=lambda k: abs(k - sy))
                secs[sy] = nbtlib.Compound({"Y": nbtlib.Byte(sy), "biomes": secs[near]["biomes"]})
                chunk["sections"].append(secs[sy])
            u, inv = np.unique(ids[y:y + 16, zo:zo + 16, xo:xo + 16].reshape(-1), return_inverse=True)
            bs = nbtlib.Compound({"palette": nbtlib.List[nbtlib.Compound]([pal.states[i] for i in u])})
            if len(u) > 1:
                bs["data"] = pack_states(inv.reshape(-1), len(u))
            secs[sy]["block_states"] = bs
        chunk["sections"] = nbtlib.List[nbtlib.Compound](sorted(chunk["sections"], key=lambda s: int(s["Y"])))
        for s in chunk["sections"]:
            s.pop("SkyLight", None)
            s.pop("BlockLight", None)
        keep = [be for be in chunk.get("block_entities", [])
                if not changed[int(be["y"]) - Y_MIN, int(be["z"]) - gz0, int(be["x"]) - gx0]]
        n_be += len(chunk.get("block_entities", [])) - len(keep)
        chunk["block_entities"] = nbtlib.List[nbtlib.Compound](keep)
        chunk["Heightmaps"] = nbtlib.Compound()  # game primes missing heightmaps on load
        chunk["isLightOn"] = nbtlib.Byte(0)  # game relights the chunk
        stamp = area["regions"][path][slot][0]
        area["regions"][path][slot] = (stamp, _blob(chunk))
        touched.add(path)
        n_chunks += 1
    return n_chunks, n_be, touched


def edit_world(source, out, box, preset=None, overrides=None, blend: float = 24) -> dict:
    """Copy `source` to `out` and replace box = (x0, z0, x1, z1) (x1/z1 exclusive) with engine terrain."""
    t0 = time.time()
    x0, z0, x1, z1 = (int(v) for v in box)
    if not (0 < x1 - x0 <= BOX_MAX and 0 < z1 - z0 <= BOX_MAX):
        raise ValueError(f"box must be x0 z0 x1 z1 with x1>x0, z1>z0 and sides <= {BOX_MAX}")
    if not 0 <= blend <= BLEND_MAX:
        raise ValueError(f"blend must be in [0, {BLEND_MAX}]")
    src, dst = check_paths(source, out)
    bw, bh = x1 - x0, z1 - z0
    p = local_params(preset, overrides, bw, bh)
    gen = generate(p)
    d, w = weights(bw, bh, blend)

    area = load_area(src, x0, z0, x1, z1)
    ids, pal = area["ids"], area["pal"]
    sl = (slice(z0 - area["gz0"], z1 - area["gz0"]), slice(x0 - area["gx0"], x1 - area["gx0"]))
    col = columns(ids, pal)
    T0, wt0 = col["top"][sl].astype(np.int16), col["water_top"][sl].astype(np.int16)
    if (T0 == -999).any():
        raise ValueError("box reaches columns without chunks/ground in the source world")
    target = T0 + (gen["height"][:bh, :bw].astype(np.float64) - T0) * w
    T1 = np.clip(np.floor(target + 0.5), Y_MIN + SUB_DEPTH + 2, Y_MAX - 2).astype(np.int16)
    edited = (w > 0) & ((T1 != T0) | (w > 0.5))
    wtop = np.maximum(np.where(wt0 > T0, wt0, -999), np.where(T1 < p["water_level"], p["water_level"], -999)).astype(np.int16)

    orig = ids.copy()
    ids[(slice(None),) + sl] = _rebuild(ids[(slice(None),) + sl], pal, T0, T1, wtop, edited,
                                        gen["terrain"][:bh, :bw])
    in_box = np.zeros(ids.shape[1:], dtype=bool)
    in_box[sl] = True
    floating = floating_trees(ids, pal, in_box)
    ids[floating] = pal.AIR
    changed = ids != orig
    n_chunks, n_be, touched = _save_chunks(area, changed)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("session.lock"))
    for path in touched:  # area was read from the source; only the copy is written
        write_slots(dst / "region" / path.name, area["regions"][path])

    new_top = columns(ids, pal)["top"][sl].astype(np.int64)
    zone = d <= KEEP + 2  # the box rim where old ground meets the blend band
    new_j, old_j = pair_jumps(new_top, zone), pair_jumps(T0.astype(np.int64), zone)
    inner = w >= 1
    return {"out": str(dst), "box": [x0, z0, x1, z1], "blend": blend, "preset": preset,
            "columns_edited": int(edited.sum()), "chunks_written": n_chunks, "regions_written": len(touched),
            "height_change": [int((T1 - T0)[edited].min(initial=0)), int((T1 - T0)[edited].max(initial=0))],
            "interior_max_err": int(np.abs(new_top - np.floor(gen["height"][:bh, :bw] + 0.5))[inner].max(initial=0)),
            "edge_jump_max": int(new_j.max(initial=0)), "edge_jump_added_max": int((new_j - old_j).max(initial=0)),
            "band_jump_max": int(pair_jumps(new_top, (w > 0) & (w < 1)).max(initial=0)),
            "tree_blocks_cleared": int(floating.sum()), "block_entities_dropped": n_be,
            "seconds": round(time.time() - t0, 2)}


def pair_jumps(top: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """|height step| between x- and z-neighbours that both lie in zone."""
    return np.concatenate([np.abs(np.diff(top, axis=1))[zone[:, 1:] & zone[:, :-1]],
                           np.abs(np.diff(top, axis=0))[zone[1:] & zone[:-1]]])
