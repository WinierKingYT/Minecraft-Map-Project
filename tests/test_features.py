"""Faz 3: caves (Caverns/Chasms masks) and the biome map."""
import hashlib

import numpy as np
import pytest

from terrain_engine import generator as G
from terrain_engine import params as P
from terrain_engine.generator import B

SEED = {"seed": "20261118"}
OFF = {"caves": "0", "biomes": "off"}
# sha256 over all output fields of a 128x96 map per preset with caves/biomes off. First recorded with v0.4.0 (before
# Faz 3); re-recorded with v0.9.0, whose surface (rock mix, shores) and coast (10-deep sea) changes were intended.
V040 = {"fantasy_forest_valley": "9c6757a75d77b8a804a4ac8b6113814ddccfb4d0b28f2f907ea5ebb510ef4d73",
        "forest_hills": "c983ead8fa51ca8098bc853a6cad12f0749949e46bfbe28b25f478a5e15f3dde",
        "mountain_valley": "9569d9fbe09394a3ffdf2d31da37cc493a3e4be9a2580ff4f631b62fa2fe69eb",
        "natural_plains": "7efed3e48a477a7dff54c15930f59c7efbd9cad028b8bdd742584d1ffd09d002",
        "river_valley": "a79c497e7b1672b50e53d2274b3d6fcdfb127afad0867a9a88c2fe06787723fb",
        "rocky_cliffs": "951d4d51bd59db1289cbcf423e6fcf5d96333adbf1f00237fcb081f8485ac0c1",
        "west_forest_east_rocky": "10f7c5ac38d76645aa7cafabf5662ddd5e2a9d9106a7eeeee0e79e59298c18b1"}


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
    # edge_falloff 0: on a 128-block map the v0.9 coastal cap would flatten every peak (they all lie near the border)
    p = P.load("mountain_valley", {**SEED, "width": "128", "length": "128", "mountain_height": "160",
                                   "tree_line": "110", "snow_line": "140", "edge_falloff": "0"})
    b = G.generate(p)["biomes"]
    assert {B["MEADOW"], B["SNOWY_SLOPES"]} & set(np.unique(b).tolist())


def test_param_validation():
    with pytest.raises(ValueError):
        P.load(None, {"caves": "1.5"})
    with pytest.raises(ValueError):
        P.load(None, {"biomes": "maybe"})
    with pytest.raises(ValueError, match="global"):
        P.load(None, {"regions": [{"shape": "half", "side": "east", "set": {"caves": 0.5}}]})
