import numpy as np
import pytest

from terrain_engine import generator as G
from terrain_engine import params as P
from terrain_engine import regions as R

BASE = {"seed": "20261118", "width": "256", "length": "256", "river_width": "0", "valley_width": "0"}


def _load(regions, base="forest_hills"):
    return P.load(base, {**BASE, "regions": regions})


@pytest.fixture(scope="module")
def split():
    """East half rocky cliffs on forest hills; no river, so the blend band is pure terrain."""
    p = _load([{"shape": "half", "side": "east", "blend": 48, "preset": "rocky_cliffs"}])
    return p, G.generate(p)


def test_no_regions_identical():
    p = P.load("fantasy_forest_valley", {"seed": "20261118"})
    a = G.generate(p)
    b = G.generate({**p, "regions": []})
    assert "regions" not in p
    for k in a:
        assert np.array_equal(a[k], b[k]), k


def test_preset_demo_loads_and_generates():
    p = P.load("west_forest_east_rocky", {"width": "64", "length": "64"})
    assert len(p["regions"]) == 2 and p["regions"][1]["set"]["biome"] == "rocky"
    assert np.isfinite(G.generate(p)["height"]).all()


def test_tiles_stitch_with_regions(split):
    p = _load(split[0]["regions"] + [{"shape": "circle", "cx": 60, "cz": 70, "r": 30, "set": {"cliffiness": 1.0}}])
    d = G.generate(p)
    full = {k: np.zeros_like(v) for k, v in d.items()}
    for x0, z0, part in G.generate_tiled(p, tile=96):
        for k, v in part.items():
            full[k][z0:z0 + v.shape[0], x0:x0 + v.shape[1]] = v
    for k in d:
        assert np.array_equal(full[k], d[k]), k


@pytest.mark.parametrize("r", [
    {"shape": "half", "side": "west", "blend": 32},
    {"shape": "box", "x0": 64, "z0": 64, "x1": 192, "z1": 192, "blend": 32},
    {"shape": "circle", "cx": 128, "cz": 128, "r": 64, "blend": 32},
    {"shape": "polygon", "points": [[64, 64], [192, 64], [192, 192], [64, 192]], "blend": 32},
])
def test_mask_range_inside_outside(r):
    X, Z = np.meshgrid(np.arange(256.0), np.arange(256.0))
    w = R.weight(R.validate([r], P.NUMERIC, P.CHOICES, P.preset_values)[0], X, Z, 256, 256)
    assert w.min() >= 0 and w.max() <= 1
    inside, outside = ((128, 20), (128, 250)) if r["shape"] == "half" else ((128, 128), (5, 5))
    assert w[inside] == 1.0 and w[outside] == 0.0 and w[128, 250] == 0.0
    assert 0 < w[128, 64 if r["shape"] != "half" else 128] < 1  # inside the blend band


def test_polygon_matches_box():
    X, Z = np.meshgrid(np.arange(-20.0, 280.0, 3.7), np.arange(-20.0, 280.0, 3.7))
    box = R.signed_distance({"shape": "box", "x0": 64, "z0": 40, "x1": 200, "z1": 190}, X, Z, 256, 256)
    poly = R.signed_distance({"shape": "polygon", "points": [[64, 40], [200, 40], [200, 190], [64, 190]]},
                             X, Z, 256, 256)
    assert np.allclose(np.sign(box), np.sign(poly)) and np.allclose(np.abs(box[box > 0]), poly[box > 0])


def test_values_actually_change(split):
    p, d = split
    west, east = d["terrain"][:, 20:100], d["terrain"][:, 160:236]
    rock = [G.T[k] for k in ("ROCK", "STONE_MIX", "ANDESITE", "STONE", "TUFF", "CALCITE")]
    assert np.isin(east, rock).mean() > np.isin(west, rock).mean() + 0.1
    trees = d["trees_deciduous"].astype(int) + d["trees_pine"]
    assert trees[:, 20:100].mean() > trees[:, 160:236].mean()


def _seam_metrics(blend):
    # erosion 0: thermal erosion would also hide a hard cut, so test the raw blend; edge_falloff 0: the coastal
    # cap (v0.9) bends the control strips near the border, which would dilute the hard-cut control ratio
    p = P.load("forest_hills", {**BASE, "erosion": "0", "edge_falloff": "0", "regions": [
        {"shape": "half", "side": "east", "blend": blend, "preset": "rocky_cliffs"}]})
    h = G.generate(p)["height"].astype(np.float64)
    lap = np.abs(4 * h[1:-1, 1:-1] - h[:-2, 1:-1] - h[2:, 1:-1] - h[1:-1, :-2] - h[1:-1, 2:])
    step = np.abs(np.diff(h, axis=1))[30:-30]
    band = np.percentile(lap[30:-30, 103:151], 99.9)
    rest = np.percentile(np.concatenate([lap[30:-30, 30:90], lap[30:-30, 170:225]], axis=1), 99.9)
    return band / rest, step[:, 104:152].max() / max(step[:, 30:90].max(), step[:, 170:225].max())


def test_no_seam_at_blend_band():
    lap_ratio, step_ratio = _seam_metrics(48)
    assert lap_ratio < 1.5 and step_ratio <= 1.0, (lap_ratio, step_ratio)
    hard_lap, hard_step = _seam_metrics(0)  # control: a hard cut must be caught by the same metrics
    assert hard_lap > 3 and hard_step > 2, (hard_lap, hard_step)


def test_biome_dominance():
    X, Z = np.meshgrid(np.arange(256.0), np.arange(8.0))
    p = _load([{"shape": "half", "side": "east", "blend": 40, "set": {"biome": "rocky"}},
               {"shape": "box", "x0": 200, "z0": -10, "x1": 300, "z1": 20, "blend": 0, "set": {"biome": "taiga"}}])
    b = R.apply(p, X, Z)["biome"]
    assert (b[:, :127] == "temperate").all() and (b[:, 129:199] == "rocky").all() and (b[:, 201:] == "taiga").all()


@pytest.mark.parametrize("regions, msg", [
    ({"shape": "half"}, "must be a list"),
    ([{"shape": "hex"}], "shape"),
    ([{"shape": "half", "side": "up"}], "side"),
    ([{"shape": "box", "x0": 0, "z0": 0, "x1": 0, "z1": 5}], "x1 > x0"),
    ([{"shape": "circle", "cx": 0, "cz": 0}], "missing"),
    ([{"shape": "circle", "cx": 0, "cz": 0, "r": -1}], "r must be"),
    ([{"shape": "polygon", "points": [[0, 0], [1, 1]]}], "points"),
    ([{"shape": "half", "side": "west", "blend": -1}], "blend"),
    ([{"shape": "half", "side": "west", "set": {"seed": 3}}], "global"),
    ([{"shape": "half", "side": "west", "set": {"erosion": 0.2}}], "global"),
    ([{"shape": "half", "side": "west", "set": {"mountain_height": 9999}}], "outside"),
    ([{"shape": "half", "side": "west", "set": {"biome": "desert"}}], "biome"),
    ([{"shape": "half", "side": "west", "preset": "nope"}], "preset not found"),
    ([{"shape": "half", "side": "west", "preset": "../presets/forest_hills"}], "preset not found"),
    ([{"shape": "half", "side": "west", "colour": "red"}], "unknown"),
])
def test_validation_errors(regions, msg):
    with pytest.raises(ValueError, match=msg.replace("(", r"\(")):
        _load(regions)


def test_validate_is_idempotent(split):
    p, _ = split
    assert P.validate(p) == p
