import json
import shutil
import uuid
from pathlib import Path

import pytest

from terrain_engine import intent as I
from terrain_engine import params as P

EX1 = "256×256 boyutlarında, ortasında nehir olan dağlık bir vadi oluştur."
EX2 = "Ormanlık ve kayalık bir fantastik bölge üret."
EX3 = "Bu dağları daha doğal yap ve geçişleri yumuşat."
EX4 = "Bu haritanın batı tarafına orman, doğusuna kayalık arazi ekle."
EX5 = "Mevcut Minecraft haritamın belirttiğim bölgesini geliştir."
EN = {EX1: "Create a 256x256 mountain valley with a river in the middle.",
      EX2: "Generate a fantasy region with forests and rocks.",
      EX3: "Make these mountains more natural and smooth the transitions.",
      EX4: "Add forest to the west side of this map and rocky terrain to the east.",
      EX5: "Improve the selected area of my existing Minecraft map."}


def C(req, base=None):
    res = I.compile_request(req, base)
    I.params_of(res["plan"])  # every plan loads through params.load
    return res


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_brief_examples(lang):
    t = (lambda s: s) if lang == "tr" else EN.get
    r1 = C(t(EX1))
    p1 = r1["plan"]
    assert p1["preset"] == "mountain_valley" and p1["size"] == [256, 256] and not p1["regions"]
    assert p1["overrides"]["river_position"] == 0.5 and p1["overrides"]["river_width"] > 0
    assert r1["unmatched"] == [] and r1["needs"] == []

    p2 = C(t(EX2))["plan"]
    base2 = P.preset_values("fantasy_forest_valley")
    assert p2["preset"] == "fantasy_forest_valley"
    assert p2["overrides"]["tree_density"] > base2["tree_density"] and p2["overrides"]["rock_ratio"] > base2["rock_ratio"]

    p3 = C(t(EX3), p1)["plan"]  # revision keeps the base and only adds deltas
    assert p3["preset"] == "mountain_valley" and p3["overrides"]["river_position"] == 0.5
    mv = P.preset_values("mountain_valley")
    assert p3["overrides"]["erosion"] > mv["erosion"] and p3["overrides"]["ridge_sharpness"] < mv["ridge_sharpness"]

    r4 = C(t(EX4), p1)
    regs = {r["side"]: r["preset"] for r in r4["plan"]["regions"]}
    assert regs == {"west": "forest_hills", "east": "rocky_cliffs"} and r4["plan"]["preset"] == "mountain_valley"

    r5 = C(t(EX5))
    assert r5["plan"]["action"] == "edit" and {"source", "box"} <= set(r5["needs"])


def test_revision_softens_existing_region_transitions():
    p4 = C(EX4, C(EX1)["plan"])["plan"]
    p5 = C(EX3, p4)["plan"]
    assert all(r["blend"] >= 64 for r in p5["regions"])
    east = next(r for r in p5["regions"] if r["side"] == "east")
    assert east["set"]["cliffiness"] < P.preset_values("rocky_cliffs")["cliffiness"]  # global smoothing reaches regions


def test_same_side_region_is_replaced():
    p = C("batıya orman ekle", C(EX4, C(EX1)["plan"])["plan"])["plan"]
    assert [r["side"] for r in p["regions"]].count("west") == 1


def test_center_theme_is_circle_and_corner_is_box():
    p = C("512x512 ova, ortasında kayalık tepe, kuzeydoğuda orman")["plan"]
    shapes = {r["name"]: r["shape"] for r in p["regions"]}
    assert shapes == {"center": "circle", "northeast": "box"} and p["preset"] == "natural_plains"
    c = next(r for r in p["regions"] if r["name"] == "center")
    assert (c["cx"], c["cz"], c["r"]) == (256, 256, 128)


@pytest.mark.parametrize("req,key,check", [
    ("daha yüksek dağlar", "mountain_height", lambda v, b: v > b),
    ("daha az kaya", "rock_ratio", lambda v, b: v < b),
    ("daha yoğun orman", "tree_density", lambda v, b: v > b),
    ("nehir yok", "river_width", lambda v, b: v == 0),
    ("no river", "river_width", lambda v, b: v == 0),
    ("mağaralar olsun", "caves", lambda v, b: v >= 0.5),
    ("karlı zirveler", "snow_line", lambda v, b: v < b),
    ("smoother terrain", "erosion", lambda v, b: v > b),
])
def test_intensity_words(req, key, check):
    base = C(EX1)["plan"]
    before = I._get(base, key)
    p = C(req, base)["plan"]
    assert check(p["overrides"][key], before), (req, p["overrides"])


def test_size_seed_name_and_clamping():
    assert C("1k orman")["plan"]["size"] == [1024, 1024]
    assert C("4096 dağlık vadi")["plan"]["size"] == [4096, 4096]
    r = C("20000x20000 plains seed 42 name: Big_1")
    assert r["plan"]["size"] == [16384, 16384] and r["warnings"] and r["plan"]["overrides"]["seed"] == 42
    assert r["plan"]["name"] == "Big_1"
    assert C("8x8 forest")["plan"]["size"] == [16, 16]
    assert C("250x250 forest")["warnings"]  # rounded to chunk multiple


def test_bad_inputs():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="empty"):
            I.compile_request(bad)
    with pytest.raises(ValueError, match="longer"):
        I.compile_request("dağ " * 1000)
    r = C("asdf qwerty zxcv")
    assert r["unmatched"] == ["asdf qwerty zxcv"] and "preset" in r["needs"]
    r = C("dağlık vadi, uçan adalar")
    assert r["plan"]["preset"] == "mountain_valley" and r["unmatched"] == ["ucan adalar"]
    with pytest.raises(ValueError):
        I.check_plan({"name": "../x"})
    with pytest.raises(ValueError):
        I.check_plan({"overrides": {"width": 64}})
    with pytest.raises(ValueError):
        I.params_of({"overrides": {"mountain_height": 9999}})


def test_deterministic():
    a = json.dumps(C(EX4, C(EX1)["plan"]), sort_keys=True)
    b = json.dumps(C(EX4, C(EX1)["plan"]), sort_keys=True)
    assert a == b


def test_history_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "PLAN_DIR", tmp_path)
    name = f"hist_{uuid.uuid4().hex[:6]}"
    first = I.plan(EX1, name=name)
    second = I.plan(EX4, name=name)  # base = last plan in history
    assert second["history"] == 2 and second["plan"]["overrides"]["river_position"] == 0.5
    assert len(second["plan"]["regions"]) == 2 and first["plan"]["name"] == second["plan"]["name"] == name
    h = I.history(name)
    assert [e["request"] for e in h] == [EX1, EX4]
    with pytest.raises(ValueError):
        I.plan(EX1, name="..\\evil")


def test_mcp_plan_and_build(tmp_path, monkeypatch):
    from agent import mcp_server as S
    monkeypatch.setattr(I, "PLAN_DIR", tmp_path)
    name = f"nl_test_{uuid.uuid4().hex[:6]}"
    plan = S.plan_terrain("64x64 ova, batıya orman", name=name)["plan"]
    res = S.build_from_plan(plan)
    try:
        assert {"out", "cache_key", "height"} <= set(res) and Path(res["out"]).name.startswith(name)
    finally:
        shutil.rmtree(res["out"], ignore_errors=True)
    with pytest.raises(S.ToolError, match="needs"):
        S.build_from_plan(S.plan_terrain(EX5)["plan"] | {"name": name})
    with pytest.raises(S.ToolError):
        S.build_from_plan({"bogus": 1})
