import asyncio
import io
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image as PILImage

from agent import mcp_server as S

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"list_presets", "describe_params", "generate", "preview", "server_load_test", "install_world", "edit_world",
         "game_start", "game_status", "game_command", "game_stop", "ingame_tour"}


@pytest.fixture(scope="module")
def small_export():
    name = f"mcp_test_{uuid.uuid4().hex[:8]}"
    first = S.generate("natural_plains", {"width": 64, "length": 64, "name": name})
    yield first
    shutil.rmtree(first["out"], ignore_errors=True)


def test_list_presets_has_descriptions():
    presets = S.list_presets()
    assert "natural_plains" in presets and presets["natural_plains"]


def test_describe_params_compact():
    d = S.describe_params()
    assert len(d["numeric"]["seed"]) == 3 and "biome" in d["choices"]


def test_generate_then_cache_hit(small_export):
    assert small_export["cache_hit"] is False
    assert {"out", "cache_key", "height", "rock_pct", "tree_pct", "seconds"} <= set(small_export)
    p = Path(small_export["out"])
    again = S.generate("natural_plains", {"width": 64, "length": 64, "name": p.name.rsplit("-", 1)[0]})
    assert again["cache_hit"] is True and again["cache_key"] == small_export["cache_key"]


def test_generate_bad_param_is_short_error():
    with pytest.raises(S.ToolError, match="ValueError"):
        S.generate("natural_plains", {"no_such_param": 1})


def test_preview_size(small_export):
    img, path = S.preview(small_export["out"], "topdown")
    pil = PILImage.open(io.BytesIO(img.data))
    src = PILImage.open(Path(small_export["out"]) / "previews" / "topdown.png")
    assert pil.width == min(src.width, S.PREVIEW_MAX_W) and "topdown.png" in path


def test_preview_rejects_outside_exports(tmp_path):
    with pytest.raises(S.ToolError):
        S.preview(str(tmp_path), "oblique")


def test_install_world_rejects_bad_name(small_export):
    with pytest.raises(S.ToolError, match="new_name"):
        S.install_world(small_export["out"], "..\\evil")


def test_stdio_handshake_lists_tools():
    from mcp import ClientSession, StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "agent" / "mcp_server.py")])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return {t.name for t in (await s.list_tools()).tools}

    assert asyncio.run(asyncio.wait_for(run(), 60)) == TOOLS


@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="slow: WorldPainter + MC server; set RUN_SLOW=1")
def test_server_load_test_tool(small_export):
    S.generate("natural_plains", {"width": 64, "length": 64, "name": Path(small_export["out"]).name.rsplit("-", 1)[0]},
               export_world=True)
    assert S.server_load_test(small_export["out"])["ok"] is True


def test_edit_world_tool_safety_and_run(tmp_path):
    from terrain_engine.export import output_dir
    p = S.P.load("natural_plains", {"name": "natural_plains_256"})
    src = output_dir(p) / "world" / p["name"]
    if not (src / "level.dat").exists():
        pytest.skip("natural_plains_256 world not exported (tests/test_edit_world.py exports it)")
    with pytest.raises(S.ToolError, match="ValueError"):
        S.edit_world(str(src), str(src), [64, 64, 128, 128], "forest_hills")
    with pytest.raises(S.ToolError, match="box"):
        S.edit_world(str(src), str(tmp_path / "a"), [64, 64, 128], "forest_hills")
    (tmp_path / "taken").mkdir()
    with pytest.raises(S.ToolError, match="FileExistsError"):
        S.edit_world(str(src), str(tmp_path / "taken"), [64, 64, 128, 128], "forest_hills")
    r = S.edit_world(str(src), str(tmp_path / "w"), [64, 64, 128, 128], "forest_hills", {"seed": 3}, 16)
    assert r["chunks_written"] == 16 and r["edge_jump_added_max"] <= 2 and (tmp_path / "w" / "level.dat").exists()


def test_generate_tiled_matches_full_and_rejects_bad_tile():
    import shutil
    import numpy as np
    ov = {"width": 128, "length": 128, "seed": 3}
    full = S.generate("natural_plains", {**ov, "name": "pytest_mcp_full"}, force=True)
    tiled = S.generate("natural_plains", {**ov, "name": "pytest_mcp_tiled"}, force=True, tile=48)
    try:
        assert tiled["tile"] == 48
        a, b = (np.load(Path(r["out"]) / "fields.npz") for r in (full, tiled))
        assert np.array_equal(a["height"], b["height"]) and np.array_equal(a["terrain"], b["terrain"])
        with pytest.raises(S.ToolError, match="multiple of 16"):
            S.generate("natural_plains", {**ov, "name": "pytest_mcp_bad"}, tile=10)
    finally:
        for r in (full, tiled):
            shutil.rmtree(r["out"], ignore_errors=True)
