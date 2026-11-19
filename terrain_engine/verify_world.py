"""Read an exported Anvil world back and compare it with the generated fields.

Independent of WorldPainter: parses level.dat and region files with nbtlib.
"""
import io
import math
import re
import struct
import zlib
from pathlib import Path

import nbtlib
import numpy as np

_NOT_GROUND = re.compile(r"air|water|leaves|_log|_wood|short_grass|tall_grass|fern|vine|flower|poppy|dandelion|"
                         r"bush|sapling|mushroom|seagrass|kelp|lily|carpet|tulip|orchid|allium|bluet|daisy|lilac|"
                         r"rose|peony|sunflower|berry|^minecraft:snow$|pumpkin|melon|cactus|reeds|sugar_cane|leaf_litter")


def read_level(world: Path) -> dict:
    lvl = nbtlib.load(world / "level.dat")
    data = lvl["Data"]
    return {"name": str(data.get("LevelName", "")), "data_version": int(data.get("DataVersion", -1)),
            "version_name": str(data.get("Version", {}).get("Name", "")) if "Version" in data else ""}


def _chunks(region_file: Path):
    raw = region_file.read_bytes()
    for i in range(1024):
        off, = struct.unpack(">I", b"\0" + raw[i * 4:i * 4 + 3])
        if off == 0:
            continue
        length, comp = struct.unpack(">IB", raw[off * 4096:off * 4096 + 5])
        payload = raw[off * 4096 + 5:off * 4096 + 4 + length]
        if comp != 2:
            raise ValueError(f"unsupported compression {comp} in {region_file.name}")
        yield nbtlib.File.parse(io.BytesIO(zlib.decompress(payload)))


def _section_columns(section):
    """Return (names, idx[4096]) for a section's block states."""
    bs = section.get("block_states")
    if bs is None:
        return None
    names = [str(b["Name"]) for b in bs["palette"]]
    if "data" not in bs:
        return names, np.zeros(4096, dtype=np.int64)
    bits = max(4, math.ceil(math.log2(len(names))))
    per = 64 // bits
    words = np.array(bs["data"], dtype=np.int64).view(np.uint64)
    i = np.arange(4096)
    vals = (words[i // per] >> ((i % per) * bits).astype(np.uint64)) & np.uint64((1 << bits) - 1)
    return names, vals.astype(np.int64)


Y_MIN, Y_MAX = -64, 320
CAVE_DEPTH = 8  # air at least this far below the surface counts as cave


def _section_biomes(section):
    """4x4x4 biome names of a section, indexed [y, z, x]; None if absent."""
    bi = section.get("biomes")
    if bi is None:
        return None
    names = [str(b) for b in bi["palette"]]
    if "data" not in bi:
        return np.full((4, 4, 4), names[0], dtype=object)
    bits = max(1, math.ceil(math.log2(len(names))))
    per = 64 // bits
    words = np.array(bi["data"], dtype=np.int64).view(np.uint64)
    i = np.arange(64)
    vals = (words[i // per] >> ((i % per) * bits).astype(np.uint64)) & np.uint64((1 << bits) - 1)
    return np.array(names, dtype=object)[vals.astype(np.int64)].reshape(4, 4, 4)


SAMPLE_REGIONS = 9  # region files read block-by-block; bigger worlds are checked on an even, deterministic spread


def _scan_chunk(chunk, water_level):
    """(top y, water at water_level, cave air count, biome at water_level, top water y) of one chunk's 16x16
    columns. Cave air = air >= CAVE_DEPTH below the top, above bedrock."""
    col_top = np.full((16, 16), -999, dtype=np.int64)
    water_top = np.full((16, 16), -999, dtype=np.int64)
    water = np.zeros((16, 16), dtype=bool)
    biome = np.full((16, 16), "", dtype=object)
    air = np.zeros((Y_MAX - Y_MIN, 16, 16), dtype=bool)
    for sec in sorted(chunk["sections"], key=lambda s: int(s["Y"])):
        y0 = int(sec["Y"]) * 16
        wy = water_level - y0
        if 0 <= wy < 16 and (b := _section_biomes(sec)) is not None:
            biome = np.repeat(np.repeat(b[wy // 4], 4, axis=0), 4, axis=1)
        sc = _section_columns(sec)
        if sc is None:
            continue
        names, idx = sc
        ground = np.array([not _NOT_GROUND.search(n) for n in names])
        is_water = np.array(["water" in n for n in names])
        is_air = np.array([n.endswith(("minecraft:air", "cave_air")) for n in names])
        g = ground[idx].reshape(16, 16, 16)  # y, z, x
        ys = y0 + np.arange(16)
        ymax = np.where(g.any(axis=0), ys[15 - np.argmax(g[::-1], axis=0)], -999)
        col_top = np.maximum(col_top, ymax)
        w = is_water[idx].reshape(16, 16, 16)
        water_top = np.maximum(water_top, np.where(w.any(axis=0), ys[15 - np.argmax(w[::-1], axis=0)], -999))
        if Y_MIN <= y0 < Y_MAX:
            air[y0 - Y_MIN:y0 - Y_MIN + 16] = is_air[idx].reshape(16, 16, 16)
        if 0 <= wy < 16:
            water = is_water[idx].reshape(16, 16, 16)[wy]
    ys = np.arange(Y_MIN, Y_MAX)[:, None, None]
    cave = (air & (ys <= col_top - CAVE_DEPTH) & (ys > Y_MIN + 4)).sum(axis=0)
    return col_top, water, cave, biome, water_top


def _region_xz(rf: Path) -> tuple[int, int]:
    _, rx, rz, _ = rf.name.split(".")
    return int(rx), int(rz)


def chunk_count(rf: Path) -> int:
    """Chunks stored in a region file (reads the 4 KiB offset header only)."""
    with open(rf, "rb") as fh:
        head = np.frombuffer(fh.read(4096), dtype=">u4")
    return int((head != 0).sum())


def scan(world: Path, width: int, length: int, water_level: int, box=None, region_files=None) -> dict:
    """Per column of box = (x0, z0, x1, z1) (default: the whole map): top ground y (-999 = no chunk), water at
    water_level, cave air count and biome name at water_level; plus the set of chunk statuses.
    Reads `region_files` (default: all); memory is O(box area)."""
    x0, z0, x1, z1 = box or (0, 0, width, length)
    shape = (z1 - z0, x1 - x0)
    top = np.full(shape, -999, dtype=np.int64)
    water = np.zeros(shape, dtype=bool)
    cave = np.zeros(shape, dtype=np.int64)
    biome = np.full(shape, "", dtype=object)
    wtop = np.full(shape, -999, dtype=np.int64)
    statuses = set()
    files = sorted((world / "region").glob("r.*.mca")) if region_files is None else region_files
    for rf in files:
        for chunk in _chunks(rf):
            cx, cz = int(chunk["xPos"]) * 16, int(chunk["zPos"]) * 16
            statuses.add(str(chunk.get("Status", "")))
            if not (x0 <= cx < x1 and z0 <= cz < z1 and cx < width and cz < length):
                continue
            sl = (slice(cz - z0, cz - z0 + 16), slice(cx - x0, cx - x0 + 16))
            top[sl], water[sl], cave[sl], biome[sl], wtop[sl] = _scan_chunk(chunk, water_level)
    return {"top": top, "water": water, "cave": cave, "biome": biome, "water_top": wtop, "statuses": statuses}


def _pct(n, d):
    return round(100 * n / d, 2) if d else None


def pick_regions(world: Path, width: int, length: int, limit: int = SAMPLE_REGIONS) -> tuple[list, list]:
    """(all region files, the map-overlapping ones to read): evenly spread, deterministic, if more than limit."""
    files = sorted((world / "region").glob("r.*.mca"))
    on_map = []
    for rf in files:
        rx, rz = _region_xz(rf)
        if 0 <= rx * 512 < width and 0 <= rz * 512 < length:
            on_map.append(rf)
    if len(on_map) > limit:
        on_map = [on_map[int(i)] for i in np.linspace(0, len(on_map) - 1, limit).round()]
    return files, on_map


def verify(world: Path, p: dict, fields: dict, sample_regions: int = SAMPLE_REGIONS) -> dict:
    """Compare the world with the generated fields. Chunk counts cover every region file; block-level checks
    read up to `sample_regions` map regions (all of them on maps <= 1536^2), one region at a time."""
    W, L, wl = p["width"], p["length"], p["water_level"]
    files, picked = pick_regions(world, W, L, sample_regions)
    a = dict.fromkeys(("cols", "covered", "land", "within1", "exact", "river", "river_water", "dry", "dry_water",
                       "cave_air", "cave_room", "cave_cols", "cave_river", "river_core_biome", "biome_match",
                       "local", "local_water", "stray"), 0)
    diff_hist = np.zeros(801, dtype=np.int64)  # land height offsets -400..400, for the median
    biomes, statuses, ids = {}, set(), None
    if "biomes" in fields:
        from .generator import B
        ids = {v: f"minecraft:{k.lower()}" for k, v in B.items()}
    for rf in picked:
        rx, rz = _region_xz(rf)
        box = (rx * 512, rz * 512, min(rx * 512 + 512, W), min(rz * 512 + 512, L))
        sc = scan(world, W, L, wl, box, [rf])
        statuses |= sc["statuses"]
        top, water, cave, biome = sc["top"], sc["water"], sc["cave"], sc["biome"]
        sl = (slice(box[1], box[3]), slice(box[0], box[2]))
        h, rd = fields["height"][sl], fields["river_dist"][sl]
        covered = top > -999
        lc = (h >= wl) & covered
        diff = top - np.floor(h + 0.5).astype(np.int64)
        river = rd < max(p["river_width"] / 2 - 1.5, 0)
        dry = h > wl + 1.5
        depth = np.maximum(top - CAVE_DEPTH - (Y_MIN + 4), 0)
        a["cols"] += top.size
        a["covered"] += int(covered.sum())
        a["land"] += int(lc.sum())
        a["within1"] += int((np.abs(diff[lc]) <= 1).sum())
        a["exact"] += int((diff[lc] == 0).sum())
        diff_hist += np.bincount(np.clip(diff[lc], -400, 400) + 400, minlength=diff_hist.size)
        a["river"] += int(river.sum())
        a["river_water"] += int(water[river].sum())
        a["dry"] += int(dry.sum())
        a["dry_water"] += int(water[dry].sum())
        a["cave_air"] += int(cave[lc].sum())
        a["cave_room"] += int(depth[lc].sum())
        a["cave_cols"] += int((cave[lc] > 0).sum())
        a["cave_river"] += int(cave[rd < p["river_width"] / 2].sum())
        a["river_core_biome"] += int((biome[river] == "minecraft:river").sum())
        # lakes/streams: the top water block sits at the planned level; no water anywhere else above the ground
        r = np.floor(h + 0.5)
        local = np.zeros(h.shape, dtype=bool)
        if "water_level" in fields:
            lv = fields["water_level"][sl].astype(np.int64)
            local = (lv > 0) & (r < lv)
            a["local"] += int(local.sum())
            a["local_water"] += int((sc["water_top"][local] == lv[local]).sum())
        sea = np.floor(h + 0.49) < wl  # h within 0.01 of x.5 may round either way through the 16-bit heightmap
        a["stray"] += int((covered & (sc["water_top"] > top) & ~sea & ~local).sum())
        for n, c in zip(*np.unique(biome[covered].astype(str), return_counts=True)):
            biomes[str(n)] = biomes.get(str(n), 0) + int(c)
        if ids is not None:  # painted map vs world, per column (MC stores biomes in 4x4 cells)
            want = np.vectorize(ids.get, otypes=[object])(fields["biomes"][sl])
            a["biome_match"] += int((want == biome).sum())
    cum = np.cumsum(diff_hist)
    n_bio = max(sum(biomes.values()), 1)
    report = {
        "level": read_level(world),
        "region_files": len(files),
        "regions_verified": len(picked),
        "chunks_in_region_files": sum(chunk_count(rf) for rf in files),
        "map_chunks": (W // 16) * (L // 16),
        "chunk_statuses": sorted(statuses),
        "columns_verified": a["cols"],
        "columns_covered_pct": _pct(a["covered"], a["cols"]),
        "land_height_within_1_pct": _pct(a["within1"], a["land"]),
        "land_height_exact_pct": _pct(a["exact"], a["land"]),
        # lower median (exact for odd counts); offsets are integers
        "land_height_median_offset": float(np.searchsorted(cum, (cum[-1] + 1) // 2) - 400) if cum[-1] else None,
        "river_water_pct": _pct(a["river_water"], a["river"]),
        "dry_land_flooded_pct": round(100 * a["dry_water"] / a["dry"], 3) if a["dry"] else None,
        "cave_air_pct": round(100 * a["cave_air"] / max(a["cave_room"], 1), 3),
        "cave_columns_pct": _pct(a["cave_cols"], a["land"]),
        "cave_air_under_river_blocks": a["cave_river"],
        "biome_pct": {n: round(100 * c / n_bio, 2) for n, c in sorted(biomes.items())},
        "river_core_is_river_biome_pct": _pct(a["river_core_biome"], a["river"]),
        "stray_water_columns": a["stray"],
    }
    if "water_level" in fields:
        report["lake_stream_columns"] = a["local"]
        report["lake_stream_water_pct"] = _pct(a["local_water"], a["local"])
    if ids is not None:
        report["biome_match_pct"] = _pct(a["biome_match"], a["cols"])
    return report
