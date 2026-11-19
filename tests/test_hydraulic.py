import hashlib
import time

import numpy as np
import pytest

from terrain_engine import generator as G
from terrain_engine import hydraulic as H
from terrain_engine import params as P

SEED = {"seed": "20261118"}
# sha256 over all output fields of a 128x96 map, recorded with engine v0.3.0 (before hydraulic existed)
V030 = {"fantasy_forest_valley": "e7853965c4e8d621d038b1c9a8c7913a072978658e46f2bd3dc85edd49611b8a",
        "west_forest_east_rocky": "4c26ec36056d61b4bf332bbc3bb3adb7d0289a03d55e9459eabf2cdb2742838e",
        "mountain_valley": "3ae3434b768a31b05986390a1dea4f7221da32ff02e3e9c530483de523437372"}


def _digest(d):
    return hashlib.sha256(b"".join(np.ascontiguousarray(d[k]).tobytes() for k in sorted(d))).hexdigest()


def _stitch(p, tile):
    parts = list(G.generate_tiled(p, tile))
    full = {k: np.zeros((p["length"], p["width"]), dtype=v.dtype) for k, v in parts[0][2].items()}
    for x0, z0, part in parts:
        for k, v in part.items():
            full[k][z0:z0 + v.shape[0], x0:x0 + v.shape[1]] = v
    return full


def _slope_stats(d):
    s = d["slope"][d["coast"] > 0.999]
    return np.median(s), np.ptp(d["height"])


@pytest.fixture(scope="module")
def pair():
    on = P.load("fantasy_forest_valley", SEED)
    assert on["hydraulic"] > 0, "preset should enable hydraulic erosion"
    return on, G.generate(on), G.generate({**on, "hydraulic": 0.0})


@pytest.mark.parametrize("preset", sorted(V030))
def test_off_is_bit_identical_to_v030(preset):
    p = P.load(preset, {**SEED, "width": "128", "length": "96", "hydraulic": "0", "caves": "0", "biomes": "off"})
    assert _digest(G.generate(p)) == V030[preset]


def test_deterministic(pair):
    p, d, _ = pair
    G._HYDRO_CACHE.clear()  # recompute the global pass from scratch
    d2 = G.generate(p)
    for k in d:
        assert np.array_equal(d[k], d2[k]), k


def test_tiles_stitch_exactly(pair):
    p, d, _ = pair
    full = _stitch(p, 96)  # not a divisor of 256 nor aligned to the erosion grid
    for k in d:
        assert np.array_equal(full[k], d[k]), k


def test_tiles_stitch_with_regions():
    p = P.load("west_forest_east_rocky", {**SEED, "width": "192", "length": "160", "hydraulic": "0.8"})
    d = G.generate(p)
    full = _stitch(p, 80)
    for k in d:
        assert np.array_equal(full[k], d[k]), k


def test_slopes_lower_relief_kept(pair):
    _, on, off = pair
    med_on, relief_on = _slope_stats(on)
    med_off, relief_off = _slope_stats(off)
    assert med_on < 0.78 * med_off, (med_on, med_off)
    assert relief_on > 0.8 * relief_off, (relief_on, relief_off)


def test_finite_and_moderate_change(pair):
    p, on, off = pair
    assert np.isfinite(on["height"]).all() and np.isfinite(on["slope"]).all()
    land = on["coast"] > 0.999
    diff = on["height"][land].astype(np.float64) - off["height"][land]
    # net loss only (fine silt leaves with dying droplets), and bounded: material moves, the map doesn't sink
    assert -10 < diff.mean() < 0.5, diff.mean()
    assert on["height"].max() <= off["height"].max() + 1e-3, "erosion never builds above the highest summit"


def test_no_pits_or_spikes(pair):
    _, d, _ = pair
    h = d["height"].astype(np.float64)
    hp = np.pad(h, 1, mode="edge")
    nb = np.stack([hp[1 + dz:1 + dz + h.shape[0], 1 + dx:1 + dx + h.shape[1]]
                   for dz in (-1, 0, 1) for dx in (-1, 0, 1) if dz or dx])
    assert (nb.min(axis=0) - h).max() < 0.5, "single-cell pit"
    assert (h - nb.max(axis=0)).max() < 0.5, "single-cell spike"


def test_border_and_river_rules_kept(pair):
    p, d, _ = pair
    hw, wl = p["river_width"] / 2, p["water_level"]
    assert (d["height"][d["river_dist"] < hw] < wl).all()
    inland = (d["river_dist"] > hw + 1) & (d["coast"] > 0.999)
    assert (d["height"][inland] >= wl).all(), "no flooding inland"
    ring = np.zeros_like(d["coast"], dtype=bool)
    ring[:2, :] = ring[-2:, :] = ring[:, :2] = ring[:, -2:] = True
    assert np.abs(d["height"][ring] - (wl - G.SEA_DEPTH)).max() < 0.5


def test_erode_unit_behaviour():
    z, x = np.mgrid[0:64, 0:64]
    cone = 80.0 - np.hypot(x - 32, z - 32) * 1.5  # blocks
    out = H.erode(cone, 1.0, 5, np.ones_like(cone))
    assert np.isfinite(out).all() and cone.min() <= out.min() and out.max() <= cone.max()
    assert not np.array_equal(out, cone)
    assert np.array_equal(H.erode(cone, 1.0, 5, np.zeros_like(cone)), cone), "erodibility 0 -> untouched"


def test_param_validation():
    with pytest.raises(ValueError):
        P.load("mountain_valley", {"hydraulic": "1.5"})
    with pytest.raises(ValueError, match="global"):
        P.load("forest_hills", {"regions": [{"shape": "half", "side": "west", "set": {"hydraulic": 0.5}}]})


def test_performance_smoke():
    p = P.load("mountain_valley", {**SEED, "name": "perf"})
    G._HYDRO_CACHE.clear()
    t0 = time.perf_counter()
    G.generate(p)
    assert time.perf_counter() - t0 < 15, "256^2 with hydraulic erosion should take a few seconds"
