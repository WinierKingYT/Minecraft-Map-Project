"""Faz 6: large maps. Fast tests run by default; multi-minute ones need RUN_SLOW=1."""
import os
import shutil
import time

import numpy as np
import pytest

from terrain_engine import generator as G
from terrain_engine import hydraulic as H
from terrain_engine import params as P
from terrain_engine import verify_world as V
from terrain_engine.export import PREVIEW_CELLS, _oblique, output_dir, preview_fields

SEED = {"seed": "20261118"}
slow = pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="multi-minute; set RUN_SLOW=1")


def _land_slopes(d):
    s = d["slope"][d["coast"] > 0.999]
    return np.median(s), np.percentile(s, 90)


@pytest.fixture(scope="module")
def mv512():
    p = P.load("mountain_valley", {**SEED, "width": "512", "length": "512"})
    G._HYDRO_CACHE.clear()
    return p, G.generate(p), G.generate({**p, "hydraulic": 0.0})


def test_cell_size_grows_with_map():
    assert [H.cell_size(n, n) for n in (128, 256, 512, 1024, 2048, 4096, 16384)] == [2, 2, 4, 8, 16, 16, 16]
    assert H.cell_size(256, 1024) == 8  # longest side decides
    assert H.relax_talus(2) == H.RELAX_TALUS and H.relax_talus(16) < H.relax_talus(4) < H.relax_talus(2)


def test_scale_aware_hydraulic_lowers_slopes_512(mv512):
    _, on, off = mv512
    med_on, p90_on = _land_slopes(on)
    med_off, p90_off = _land_slopes(off)
    assert med_on < med_off and p90_on < p90_off, (med_on, med_off, p90_on, p90_off)
    assert np.ptp(on["height"]) > 0.8 * np.ptp(off["height"])


def test_tiled_equals_full_with_hydraulic_512(mv512):
    p, full, _ = mv512
    for x0, z0, d in G.generate_tiled(p, 208):  # not aligned to the 4-block erosion cells nor 512
        for k in d:
            assert np.array_equal(d[k], full[k][z0:z0 + d[k].shape[0], x0:x0 + d[k].shape[1]]), k


def test_parallel_tiles_equal_sequential():
    p = P.load("mountain_valley", {**SEED, "width": "256", "length": "192"})
    full = G.generate(p)
    for x0, z0, d in G.generate_tiled(p, 112, workers=2):
        for k in d:
            assert np.array_equal(d[k], full[k][z0:z0 + d[k].shape[0], x0:x0 + d[k].shape[1]]), k


def test_preview_downscaled_and_fast():
    n = 4096
    d = {"height": np.full((n, n), 70, np.float32), "terrain": np.zeros((n, n), np.uint8),
         "trees_deciduous": np.zeros((n, n), np.uint8), "trees_pine": np.zeros((n, n), np.uint8)}
    pv = preview_fields(d)
    assert max(pv["height"].shape) <= PREVIEW_CELLS
    t0 = time.perf_counter()
    img = _oblique(pv, 62)
    assert img.shape[1] <= 1024 and time.perf_counter() - t0 < 10
    small = {k: v[:256, :256] for k, v in d.items()}
    assert preview_fields(small)["height"].shape == (256, 256), "small maps keep full-res previews"


def test_pick_regions_even_and_deterministic(tmp_path):
    reg = tmp_path / "region"
    reg.mkdir()
    for rx in range(-1, 9):
        for rz in range(-1, 9):
            (reg / f"r.{rx}.{rz}.mca").write_bytes(b"")
    files, picked = V.pick_regions(tmp_path, 4096, 4096, 9)
    assert len(files) == 100 and len(picked) == 9
    xz = [V._region_xz(f) for f in picked]
    assert all(0 <= x < 8 and 0 <= z < 8 for x, z in xz) and len(set(xz)) == 9
    assert picked == V.pick_regions(tmp_path, 4096, 4096, 9)[1]
    assert len(V.pick_regions(tmp_path, 1024, 1024, 9)[1]) == 4  # small maps: every region


def test_streaming_verify_on_small_export(tmp_path):
    p = P.load("fantasy_forest_valley", {**SEED, "name": "fantasy_valley_256"})
    out = output_dir(p)
    world = out / "world" / p["name"]
    if not (world / "region").is_dir():
        pytest.skip("world not exported yet (run generate --verify)")
    fields = dict(np.load(out / "fields.npz"))
    r = V.verify(world, p, fields)
    assert r["regions_verified"] == 1 and r["chunks_in_region_files"] == r["map_chunks"] == 256
    assert r["columns_covered_pct"] == 100.0 and r["land_height_within_1_pct"] >= 99.0
    # a box scan reads only that box
    sc = V.scan(world, p["width"], p["length"], p["water_level"], box=(32, 48, 96, 80))
    assert sc["top"].shape == (32, 64) and (sc["top"] > -999).all()
    # zero sampled regions: chunk counts still complete, block checks empty
    copy = tmp_path / "w"
    shutil.copytree(world, copy)
    r0 = V.verify(copy, p, fields, sample_regions=0)
    assert r0["regions_verified"] == 0 and r0["chunks_in_region_files"] == 256 and r0["columns_verified"] == 0


@slow
def test_slopes_drop_at_2048_tiled():
    p = P.load("mountain_valley", {**SEED, "width": "2048", "length": "2048"})
    stats = {}
    for hyd in (0.0, p["hydraulic"]):
        s = [d["slope"][d["coast"] > 0.999] for _, _, d in G.generate_tiled({**p, "hydraulic": hyd}, 1024, G.WORKERS)]
        s = np.concatenate(s)
        stats[hyd] = (np.median(s), np.percentile(s, 90))
    (m_off, p_off), (m_on, p_on) = stats[0.0], stats[p["hydraulic"]]
    assert m_on < m_off and p_on < p_off, stats
