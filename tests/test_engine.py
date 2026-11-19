import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from terrain_engine import generator as G
from terrain_engine import params as P
from terrain_engine.export import build, height_to_u16, output_dir, u16_to_height

ROOT = Path(__file__).resolve().parent.parent
TEST_MAP = {"seed": "20261118", "name": "fantasy_valley_256"}


@pytest.fixture(scope="module")
def valley():
    p = P.load("fantasy_forest_valley", TEST_MAP)
    return p, G.generate(p)


def test_heightmap_size_and_range(valley):
    p, d = valley
    assert d["height"].shape == (256, 256)
    assert -64 <= d["height"].min() and d["height"].max() < 320
    assert d["height"].max() - d["height"].min() > 40  # real relief, not flat


def test_deterministic_same_seed(valley):
    p, d = valley
    d2 = G.generate(P.load("fantasy_forest_valley", TEST_MAP))
    for k in d:
        assert np.array_equal(d[k], d2[k]), k


def test_different_seed_differs(valley):
    _, d = valley
    d2 = G.generate(P.load("fantasy_forest_valley", {"seed": "7"}))
    assert not np.array_equal(d["height"], d2["height"])


def test_tiles_stitch_exactly(valley):
    p, d = valley
    full = np.zeros_like(d["height"])
    for x0, z0, part in G.generate_tiled(p, tile=96):  # deliberately not a divisor of 256
        full[z0:z0 + part["height"].shape[0], x0:x0 + part["height"].shape[1]] = part["height"]
    assert np.array_equal(full, d["height"])


def test_river_bed_consistent(valley):
    p, d = valley
    wl, hw = p["water_level"], p["river_width"] / 2
    bed = d["river_dist"] < hw
    assert (d["height"][bed] < wl).all(), "river bed must be under water"
    # 0.11.0: intended lakes/streams (water_level > 0) are the only other water inland; their surface is never
    # below the sea level, and nothing else floods
    lv = d["water_level"]
    inland = (d["river_dist"] > hw + 1) & (d["coast"] > 0.999) & (lv == 0)
    assert (d["height"][inland] >= wl).all(), "no accidental flooding inland outside the river and lakes/streams"
    assert (lv[lv > 0] >= wl).all()
    assert bed.any(axis=1).all(), "river must cross every row (continuous N-S)"
    near = (d["river_dist"] > hw) & (d["river_dist"] < hw + p["bank_width"])
    assert np.median(d["slope"][near]) < 20, "banks/floodplain should be gentle"


def test_border_matches_outside_ocean(valley):
    p, d = valley
    from terrain_engine.generator import SEA_DEPTH, T
    ring = np.zeros_like(d["coast"], dtype=bool)
    ring[:2, :] = ring[-2:, :] = ring[:, :2] = ring[:, -2:] = True
    seabed = p["water_level"] - SEA_DEPTH
    assert np.abs(d["height"][ring] - seabed).max() < 0.5, "border must sit on the flat sea floor (no seam)"
    assert np.isin(d["terrain"][ring], (T["SAND"], T["GRAVEL"])).all()
    assert (d["coast"] > 0.999).mean() > 0.6, "falloff must not eat the map"


def test_no_spikes(valley):
    _, d = valley
    h = d["height"].astype(np.float64)
    lap = np.abs(4 * h[1:-1, 1:-1] - h[:-2, 1:-1] - h[2:, 1:-1] - h[1:-1, :-2] - h[1:-1, 2:])
    # 0.11.0: stream channels are narrow trenches by design; judge spikes away from them
    wet = G._near(d["water_level"] > 0, 2)[1:-1, 1:-1]
    assert np.percentile(lap[~wet], 99.9) < 6, "isolated single-cell spikes detected"


def test_u16_roundtrip(valley):
    _, d = valley
    back = u16_to_height(height_to_u16(d["height"]))
    assert np.abs(back - d["height"]).max() < 0.01


def test_validation_rejects_bad_params():
    with pytest.raises(ValueError):
        P.load("river_valley", {"mountain_height": "9999"})
    with pytest.raises(ValueError):
        P.load("river_valley", {"no_such_param": "1"})
    with pytest.raises(ValueError):
        P.load("river_valley", {"width": "100"})  # not chunk aligned


@pytest.mark.parametrize("preset", P.list_presets())
def test_all_presets_generate(preset):
    p = P.load(preset, {"width": "128", "length": "128"})
    d = G.generate(p)
    assert d["height"].shape == (128, 128) and np.isfinite(d["height"]).all()


def test_cache_hit_and_files():
    p = P.load("natural_plains", {"width": "64", "length": "64", "name": "pytest_cache"})
    out = output_dir(p)
    shutil.rmtree(out, ignore_errors=True)
    try:
        _, m1, hit1 = build(p)
        _, m2, hit2 = build(p)
        assert (hit1, hit2) == (False, True) and m1["cache_key"] == m2["cache_key"]
        img = Image.open(out / "heightmap16.png")
        assert img.size == (64, 64) and img.mode.startswith("I")
        assert json.loads((out / "params.json").read_text())["seed"] == p["seed"]
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_exported_world_matches_heightmap(valley):
    p, d = valley
    world = output_dir(p) / "world" / p["name"]
    if not (world / "region").is_dir():
        pytest.skip("world not exported yet (run generate --verify)")
    from terrain_engine.verify_world import verify
    r = verify(world, p, d)
    assert r["chunk_statuses"] == ["full"] and r["columns_covered_pct"] == 100.0
    assert r["land_height_within_1_pct"] >= 99.0
    assert r["river_water_pct"] >= 95.0 and r["dry_land_flooded_pct"] < 1.0
    assert r["cave_air_pct"] > 0 and r["cave_air_under_river_blocks"] == 0
    assert r["river_core_is_river_biome_pct"] >= 90 and r["biome_match_pct"] >= 85


def test_anvil_fix_renames_legacy_grass(valley, tmp_path):
    p, d = valley
    world = output_dir(p) / "world" / p["name"]
    if not (world / "region").is_dir():
        pytest.skip("world not exported yet")
    from terrain_engine import anvil_fix
    from terrain_engine.verify_world import _chunks, verify
    copy = tmp_path / "w"
    shutil.copytree(world, copy)
    anvil_fix.fix_world(copy)
    names = {str(b["Name"]) for rf in (copy / "region").glob("*.mca") for c in _chunks(rf)
             for s in c["sections"] for b in s.get("block_states", {}).get("palette", [])}
    assert "minecraft:grass" not in names and "minecraft:short_grass" in names
    assert anvil_fix.fix_world(copy) == 0  # idempotent
    assert verify(copy, p, d)["land_height_within_1_pct"] >= 99.0  # rewritten region still reads back


def test_player_dimension_fix(tmp_path):
    """WP's level.dat lacks Player.SpawnDimension/Dimension -> MC logs "Not a string"; the fix adds both once."""
    import nbtlib
    from terrain_engine.worldpainter import fix_player_dimension
    nbtlib.File({"Data": nbtlib.Compound({"Player": nbtlib.Compound({"SpawnX": nbtlib.Int(1)})})},
                gzipped=True).save(tmp_path / "level.dat")
    assert fix_player_dimension(tmp_path) and not fix_player_dimension(tmp_path)
    player = nbtlib.load(tmp_path / "level.dat")["Data"]["Player"]
    assert player["SpawnDimension"] == player["Dimension"] == "minecraft:overworld"
