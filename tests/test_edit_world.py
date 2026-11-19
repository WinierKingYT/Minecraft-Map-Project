"""Faz 5: edit a box of an existing (exported) world, on a copy only."""
import hashlib
import json
import shutil

import numpy as np
import pytest

from terrain_engine import cli
from terrain_engine import edit_world as E
from terrain_engine import params as P
from terrain_engine.export import build, output_dir
from terrain_engine.generator import generate
from terrain_engine.verify_world import _section_columns

BOX = (64, 64, 192, 192)
BLEND = 24
PRESET = "forest_hills"


@pytest.fixture(scope="module")
def source():
    """The 'existing world': a 256^2 natural_plains map exported by WorldPainter (exported on demand)."""
    p = P.load("natural_plains", {"name": "natural_plains_256"})
    world = output_dir(p) / "world" / p["name"]
    if not (world / "level.dat").exists():
        from terrain_engine import worldpainter
        out, _, _ = build(p)
        try:
            world = worldpainter.run(p, out)
        except (FileNotFoundError, RuntimeError) as e:
            pytest.skip(f"WorldPainter export unavailable: {e}")
    return world


def _hashes(root):
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob("*")) if f.is_file()}


@pytest.fixture(scope="module")
def edited(source, tmp_path_factory):
    out = tmp_path_factory.mktemp("edit") / "world"
    before = _hashes(source)
    assert cli.main(["edit-world", "--source", str(source), "--out", str(out), "--box", *map(str, BOX),
                     "--preset", PRESET, "--blend", str(BLEND)]) == 0
    return out, before


@pytest.fixture(scope="module")
def grids(source, edited):
    x0, z0, x1, z1 = BOX
    m = 16  # one chunk of context around the box
    return E.load_area(source, x0 - m, z0 - m, x1 + m, z1 + m), E.load_area(edited[0], x0 - m, z0 - m, x1 + m, z1 + m)


def _names(a):
    return np.array([str(c["Name"]) + str(sorted(c.get("Properties", {}).items())) for c in a["pal"].states])[a["ids"]]


def _box_cols(a):
    x0, z0, x1, z1 = BOX
    m = np.zeros(a["ids"].shape[1:], dtype=bool)
    m[z0 - a["gz0"]:z1 - a["gz0"], x0 - a["gx0"]:x1 - a["gx0"]] = True
    return m


def test_refuses_unsafe_outputs(source, tmp_path, monkeypatch):
    before = _hashes(source)
    with pytest.raises(ValueError):
        E.edit_world(source, source, BOX, PRESET)
    with pytest.raises(ValueError):
        E.edit_world(source, source / "inner", BOX, PRESET)
    (tmp_path / "exists").mkdir()
    with pytest.raises(FileExistsError):
        E.edit_world(source, tmp_path / "exists", BOX, PRESET)
    monkeypatch.setenv("APPDATA", str(tmp_path / "fake_appdata"))
    with pytest.raises(ValueError, match="minecraft"):
        E.edit_world(source, tmp_path / "fake_appdata" / ".minecraft" / "saves" / "x", BOX, PRESET)
    with pytest.raises(FileNotFoundError):
        E.edit_world(tmp_path, tmp_path / "o", BOX, PRESET)
    with pytest.raises(ValueError):  # box outside the world: fails before anything is copied
        E.edit_world(source, tmp_path / "far", (5000, 5000, 5100, 5100), PRESET)
    assert not (tmp_path / "far").exists() and not (tmp_path / "fake_appdata").exists()
    assert _hashes(source) == before


def test_source_byte_identical(source, edited):
    assert _hashes(source) == edited[1]
    assert not (edited[0] / "session.lock").exists()


def test_inside_matches_target(edited):
    x0, z0, x1, z1 = BOX
    p = E.local_params(PRESET, {}, x1 - x0, z1 - z0)
    want = np.floor(generate(p)["height"][:z1 - z0, :x1 - x0] + 0.5)
    d, w = E.weights(x1 - x0, z1 - z0, BLEND)
    top = E.read_area(edited[0], BOX)["top"]
    assert np.abs(top - want)[w >= 1].max() <= 1
    assert (w >= 1).sum() > 0.25 * w.size  # 72^2 interior of 128^2


def test_outside_identical_and_untouched_chunks_bytes(source, edited, grids):
    src, new = grids
    outside = ~_box_cols(src)
    assert np.array_equal(_names(src)[:, outside], _names(new)[:, outside])
    assert not np.array_equal(src["ids"][:, ~outside], new["ids"][:, ~outside])  # the box did change
    x0, z0, x1, z1 = BOX
    a = E.read_slots(source / "region" / "r.0.0.mca")
    b = E.read_slots(edited[0] / "region" / "r.0.0.mca")
    assert a.keys() == b.keys()
    for slot in a:
        cx, cz = slot % 32, slot // 32
        hit = cx * 16 < x1 and cx * 16 + 16 > x0 and cz * 16 < z1 and cz * 16 + 16 > z0
        assert (a[slot] == b[slot]) == (not hit), slot  # off the box: identical blob + timestamp; on it: rewritten
    for f in (source / "entities").rglob("*"):  # untouched files byte-identical
        if f.is_file():
            assert f.read_bytes() == (edited[0] / f.relative_to(source)).read_bytes()


def test_no_seam(source, edited):
    x0, z0, x1, z1 = BOX
    big = (x0 - 1, z0 - 1, x1 + 1, z1 + 1)  # one ring of untouched columns outside the box too
    new, old = E.read_area(edited[0], big)["top"], E.read_area(source, big)["top"]
    d, _ = E.weights(x1 - x0 + 2, z1 - z0 + 2, BLEND)
    zone = d <= E.KEEP + 3
    jn, jo = E.pair_jumps(new, zone), E.pair_jumps(old, zone)
    assert (jn <= np.maximum(jo, 2)).all()  # edge steps never exceed 2 blocks unless the source already had them
    assert (jn - jo).max() <= 2


def test_no_floating_trees_or_water(grids):
    _, new = grids
    box = _box_cols(new)
    assert not E.floating_trees(new["ids"], new["pal"], box).any()
    water = new["pal"].lut(lambda n: n.endswith("water"))[new["ids"]]
    air = new["pal"].lut(lambda n: n.endswith(":air"))[new["ids"]]
    assert not (water[1:] & air[:-1])[:, box].any()  # no water standing on air


def test_edited_chunks_marked_for_relight(source, edited):
    x0, z0 = BOX[:2]
    slot = (x0 // 16 + 2) + (z0 // 16 + 2) * 32
    c = E._parse(E.read_slots(edited[0] / "region" / "r.0.0.mca")[slot][1])
    assert int(c["isLightOn"]) == 0 and len(c["Heightmaps"]) == 0 and str(c["Status"]).endswith("full")


@pytest.mark.parametrize("n", [1, 2, 16, 17, 33, 64, 300])
def test_pack_roundtrip(n):
    import nbtlib
    rng = np.random.default_rng(n)
    idx = rng.integers(0, n, 4096)
    idx[:n] = np.arange(n)
    pal = nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String(f"x:{i}")}) for i in range(n)])
    sec = nbtlib.Compound({"block_states": nbtlib.Compound({"palette": pal, "data": E.pack_states(idx, n)})})
    bits = max(4, int(np.ceil(np.log2(n))))
    assert len(sec["block_states"]["data"]) == -(-4096 // (64 // bits))
    assert np.array_equal(_section_columns(sec)[1], idx)


def test_forest_world_trees_not_left_floating(tmp_path):
    """A forested source: trunks removed by the edit must not leave leaves/logs hanging in the box."""
    p = P.load("fantasy_forest_valley", {"seed": "20261118", "name": "fantasy_valley_256"})
    src = output_dir(p) / "world" / p["name"]
    if not (src / "level.dat").exists():
        pytest.skip("fantasy_valley_256 world not exported")
    box = (20, 20, 140, 140)
    r = E.edit_world(src, tmp_path / "w", box, "natural_plains", {"river_width": 0})
    assert r["tree_blocks_cleared"] > 0 and r["height_change"][0] < -10
    a, b = E.load_area(src, 0, 0, 160, 160), E.load_area(tmp_path / "w", 0, 0, 160, 160)
    inside = np.zeros(a["ids"].shape[1:], dtype=bool)
    inside[box[1]:box[3], box[0]:box[2]] = True
    assert not E.floating_trees(b["ids"], b["pal"], inside).any()
    assert np.array_equal(_names(a)[:, ~inside], _names(b)[:, ~inside])
