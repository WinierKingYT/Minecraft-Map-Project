"""Faz 3: caves (Caverns/Chasms masks) and the biome map."""
import hashlib

import numpy as np
import pytest

from terrain_engine import generator as G
from terrain_engine import params as P
from terrain_engine.generator import B

SEED = {"seed": "20261118"}
OFF = {"caves": "0", "biomes": "off"}
# sha256 over all output fields of a 128x96 map per preset, recorded with engine v0.4.0 (before Faz 3)
V040 = {"fantasy_forest_valley": "1e5fba29fc0024df62ec5159a07e4dd6e7a4e9e40f39ec612e64ee37752ce77f",
        "forest_hills": "ce9f1947c052ce8fb6a0a11c8cb0d9a762d07e2aa034ea3b4ef9fead14aca7a8",
        "mountain_valley": "67941c1212c4843379ceb365535730b81ec35799d32286bd8547f1514dd723cd",
        "natural_plains": "e023ccc812b5d39c30af8d924b553a00cfee1f46f2cae3ba1a3e028825456154",
        "river_valley": "cd55b2b70ad9c189797791c3e5dcfee2950cd02c2a4a30708e929513a10fe6cf",
        "rocky_cliffs": "a178a8f437754420d645f815c54d7afbdf0b6471d34721623bfd601d7cf2f5c3",
        "west_forest_east_rocky": "4c26ec36056d61b4bf332bbc3bb3adb7d0289a03d55e9459eabf2cdb2742838e"}


def _digest(d):
    return hashlib.sha256(b"".join(np.ascontiguousarray(d[k]).tobytes() for k in sorted(d))).hexdigest()


@pytest.fixture(scope="module")
def valley():
    p = P.load("fantasy_forest_valley", SEED)
    assert p["caves"] > 0 and p["biomes"] == "on", "preset should enable Faz 3 features"
    return p, G.generate(p)


@pytest.mark.parametrize("preset", sorted(V040))
def test_off_is_bit_identical_to_v040(preset):
    p = P.load(preset, {**SEED, "width": "128", "length": "96", **OFF})
    d = G.generate(p)
    assert not {"caverns", "chasms", "biomes"} & set(d)
    assert _digest(d) == V040[preset]


def test_features_do_not_change_base_fields(valley):
    p, d = valley
    off = G.generate({**p, "caves": 0.0, "biomes": "off"})
    for k in off:
        assert np.array_equal(off[k], d[k]), k


def test_masks_deterministic(valley):
    p, d = valley
    G._HYDRO_CACHE.clear()
    d2 = G.generate(p)
    for k in ("caverns", "chasms", "biomes"):
        assert np.array_equal(d[k], d2[k]), k


def test_tiles_exact(valley):
    p, d = valley
    full = {k: np.zeros_like(d[k]) for k in ("caverns", "chasms", "biomes")}
    for x0, z0, part in G.generate_tiled(p, 96):
        for k in full:
            full[k][z0:z0 + part[k].shape[0], x0:x0 + part[k].shape[1]] = part[k]
    for k in full:
        assert np.array_equal(full[k], d[k]), k


def test_cave_masks_avoid_water(valley):
    p, d = valley
    assert d["caverns"].max() <= 15 and (d["caverns"] > 0).mean() > 0.05
    assert (d["chasms"] > 0).any()
    wet = (d["river_dist"] < p["river_width"] / 2 + G.CAVE_CLEARANCE) | (d["coast"] < 0.999) \
        | (d["height"] < p["water_level"] + 4)
    assert not d["caverns"][wet].any() and not d["chasms"][wet].any()


def test_biome_classes_where_expected(valley):
    p, d = valley
    b, wl = d["biomes"], p["water_level"]
    river = (d["river_dist"] < p["river_width"] / 2) & (d["height"] < wl)
    assert (b[river] == B["RIVER"]).all()
    sea = (d["coast"] < 0.999) & (d["height"] < wl) & ~river
    assert (b[sea] == B["OCEAN"]).all()
    trees = (d["trees_deciduous"] + d["trees_pine"]) > 0
    forests = (B["FOREST"], B["DARK_FOREST"], B["BIRCH_FOREST"], B["TAIGA"])
    assert np.isin(b[trees], forests).mean() > 0.9
    assert set(np.unique(b).tolist()) <= set(B.values())
    assert {B["DARK_FOREST"], B["BIRCH_FOREST"], B["BEACH"], B["PLAINS"]} <= set(np.unique(b).tolist())


def test_alpine_biomes():
    p = P.load("mountain_valley", {**SEED, "width": "128", "length": "128", "mountain_height": "160",
                                   "tree_line": "110", "snow_line": "140"})
    b = G.generate(p)["biomes"]
    assert {B["MEADOW"], B["SNOWY_SLOPES"]} & set(np.unique(b).tolist())


def test_param_validation():
    with pytest.raises(ValueError):
        P.load(None, {"caves": "1.5"})
    with pytest.raises(ValueError):
        P.load(None, {"biomes": "maybe"})
    with pytest.raises(ValueError, match="global"):
        P.load(None, {"regions": [{"shape": "half", "side": "east", "set": {"caves": 0.5}}]})
