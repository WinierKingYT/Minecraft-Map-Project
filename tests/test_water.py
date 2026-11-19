"""Faz 9: tributary streams, basin lakes and the post-export vegetation plan."""
import numpy as np
import pytest

from terrain_engine import decorate as D
from terrain_engine import generator as G
from terrain_engine import params as P
from terrain_engine import water as Wt

SEED = {"seed": "20261118"}


@pytest.fixture(scope="module")
def valley():
    p = P.load("fantasy_forest_valley", SEED)
    assert p["tributaries"] > 0 and p["lakes"] > 0 and p["vegetation"] > 0, "preset should enable Faz 9 features"
    return p, G.generate(p)


def _wet(d):
    lv = d["water_level"].astype(np.int64)
    return lv, (lv > 0) & (np.floor(d["height"] + 0.5) < lv)


def test_network_has_streams_and_lakes(valley):
    p, _ = valley
    net = G._water_net(p)
    assert net["streams"] >= 3 and net["lakes"] >= 1


def test_tiles_stitch_exactly(valley):
    p, d = valley
    full = {k: np.zeros_like(v) for k, v in d.items()}
    for x0, z0, part in G.generate_tiled(p, tile=96):  # not a divisor of 256
        for k, v in part.items():
            full[k][z0:z0 + v.shape[0], x0:x0 + v.shape[1]] = v
    for k in d:
        assert np.array_equal(full[k], d[k]), k


def test_deterministic(valley):
    p, d = valley
    G._HYDRO_CACHE.clear()
    d2 = G.generate(p)
    for k in d:
        assert np.array_equal(d[k], d2[k]), k


def test_water_is_contained(valley):
    """Every lake/stream column's dry 4-neighbours reach its water surface (no leaks sideways)."""
    p, d = valley
    lv, wet = _wet(d)
    top = np.floor(d["height"] + 0.5)
    sea = top < p["water_level"]
    for dz, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb_top = np.roll(top, (dz, dx), axis=(0, 1))
        nb_wet = np.roll(wet | sea, (dz, dx), axis=(0, 1))
        inner = np.zeros_like(wet)
        inner[1:-1, 1:-1] = True
        leak = wet & inner & ~nb_wet & (nb_top < lv)
        assert not leak.any(), (dz, dx, np.argwhere(leak)[:5])


def test_lakes_flat_and_above_sea(valley):
    p, d = valley
    lv, wet = _wet(d)
    assert wet.sum() > 300
    assert (lv[wet] >= p["water_level"]).all()
    lab, n = Wt._label(G._water_net(p)["lake"] > 0)
    for k in range(n):  # one surface per lake
        assert len(np.unique(G._water_net(p)["lake"][lab == k])) == 1


def test_streams_reach_the_river(valley):
    p, d = valley
    lv, wet = _wet(d)
    ends = G._water_net(p)["segs"]
    # stream water surfaces fall to the river level where they end
    assert (lv[wet] == p["water_level"]).any()
    assert len(ends) > 0


def test_off_has_no_water_field():
    p = P.load("fantasy_forest_valley", {**SEED, "width": "128", "length": "96", "tributaries": "0", "lakes": "0"})
    assert "water_level" not in G.generate(p)


def test_vegetation_plan_deterministic_and_off(valley):
    p, d = valley
    a, b = D.plan(p, d), D.plan(p, d)
    assert a == b and len(a) > 30
    assert {k for k, _, _ in a} == set(D.KINDS)
    assert D.plan({**p, "vegetation": 0.0}, d) == []
    lv, wet = _wet(d)
    fp = D.footprint(p, d)
    assert not (fp & wet).any(), "no feature next to lakes/streams"
    for _, x, z in a:  # region borders stay clear: each region file is decorated on its own
        assert D.EDGE <= x % 512 < 512 - D.EDGE and D.EDGE <= z % 512 < 512 - D.EDGE


def test_param_validation():
    with pytest.raises(ValueError):
        P.load(None, {"lakes": "2"})
    with pytest.raises(ValueError):
        P.load(None, {"regions": [{"shape": "half", "side": "west", "set": {"tributaries": 0.5}}]})
