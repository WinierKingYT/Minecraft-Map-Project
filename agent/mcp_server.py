"""MCP server (stdio) exposing the terrain engine with compact, low-token results.

Run: .venv/Scripts/python agent/mcp_server.py   (registered in .mcp.json)
Params pass through to terrain_engine.params.load untouched, so new keys (e.g. "regions") work automatically.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from mcp.server.mcpserver import Image, MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

from terrain_engine import params as P  # noqa: E402
from terrain_engine.export import build  # noqa: E402

from agent import bridge_client as BC  # noqa: E402

EXPORTS = ROOT / "exports"
PREVIEW_MAX_W = 768
ROCK_TYPES = ("ROCK", "STONE_MIX", "ANDESITE", "STONE", "TUFF", "CALCITE")
SAVE_NAME_RE = re.compile(r"[A-Za-z0-9_\- ]{1,64}")

mcp = MCPServer("minecraft-terrain", instructions=(
    "Low-token flow: list_presets -> generate(preset, overrides) -> preview only if needed. "
    "Never request raw arrays; results are compact JSON."))


def _tool(fn):
    """Register fn as a tool: dicts go out as one-line JSON (fewest tokens), expected failures as short errors."""
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError:
            raise
        except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, OSError,
                subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e
    @wraps(fn)
    def compact(*a, **kw):
        res = wrapper(*a, **kw)
        return json.dumps(res, separators=(",", ":")) if isinstance(res, dict) else res
    mcp.tool(structured_output=False)(compact)
    return wrapper


def _export_dir(export_dir: str) -> Path:
    out = Path(export_dir).resolve()
    if EXPORTS.resolve() not in out.parents or not (out / "params.json").exists():
        raise ValueError(f"not an export dir under {EXPORTS}: {export_dir}")
    return out


def _world_dir(out: Path) -> Path:
    name = json.loads((out / "params.json").read_text(encoding="utf-8"))["name"]
    world = out / "world" / name
    if not (world / "level.dat").exists():
        raise FileNotFoundError(f"no exported world in {out}; run generate(export_world=True) first")
    return world


@_tool
def list_presets() -> dict:
    """Preset names with one-line descriptions."""
    return {n: json.loads((P.PRESET_DIR / f"{n}.json").read_text(encoding="utf-8")).get("description", "")
            for n in P.list_presets()}


@_tool
def describe_params() -> dict:
    """Numeric params as name: [default, min, max]; choice params as name: [options]."""
    from terrain_engine import regions as R
    return {"numeric": {k: list(v) for k, v in P.NUMERIC.items()},
            "choices": {k: list(v) for k, v in P.CHOICES.items()},
            "regions": {"shapes": {k: list(v) for k, v in R.SHAPES.items()}, "sides": list(R.SIDES),
                        "blend": [R.DEFAULT_BLEND, 0, R.BLEND_MAX], "keys": ["preset", "set", "name"],
                        "blendable": list(R.BLENDABLE) + list(R.BLEND_CHOICES)}}


AUTO_TILE = 1024  # 4096² measured: ~2.9 GB peak with 1024 tiles (docs/CHANGELOG.md 0.7.0)


@_tool
def generate(preset: str | None = None, overrides: dict | None = None, export_world: bool = False,
             verify: bool = False, force: bool = False, tile: int | None = None) -> dict:
    """Generate (cached) terrain. overrides = any params (see describe_params). verify implies export_world.
    tile: block size for tiled generation (output identical); default auto = 1024 for maps larger than 1024."""
    t0 = time.time()
    p = P.load(preset, overrides)
    if tile is None and max(p["width"], p["length"]) > AUTO_TILE:
        tile = AUTO_TILE
    if tile is not None and (tile < 16 or tile % 16):
        raise ValueError("tile must be a positive multiple of 16")
    out, man, hit = build(p, force=force, tile=tile)
    s = man["stats"]
    cells = p["width"] * p["length"]
    res = {"out": str(out), "cache_hit": hit, "tile": tile, "cache_key": man["cache_key"],
           "height": [round(s["height_min"], 1), round(s["height_max"], 1)],
           "rock_pct": round(100 * sum(s["terrain_counts"].get(k, 0) for k in ROCK_TYPES) / cells, 1),
           "tree_pct": round(100 * s["tree_cells"] / cells, 1)}
    if export_world or verify:
        from terrain_engine import verify_world, worldpainter
        world = out / "world" / p["name"]
        res["world_cached"] = (world / "level.dat").exists() and not force
        if not res["world_cached"]:
            if world.exists():
                shutil.rmtree(world)
            world = worldpainter.run(p, out)
        res["world"] = str(world)
        if verify:
            report = verify_world.verify(world, p, dict(np.load(out / "fields.npz")))
            (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            res["verify"] = {k: v for k, v in report.items() if k != "level"}
    res["seconds"] = round(time.time() - t0, 2)
    return res


@_tool
def preview(export_dir: str, kind: str = "oblique") -> list:
    """Return a preview image (<=768px wide) of an export. kind: oblique | topdown."""
    if kind not in ("oblique", "topdown"):
        raise ValueError("kind must be 'oblique' or 'topdown'")
    path = _export_dir(export_dir) / "previews" / f"{kind}.png"
    img = PILImage.open(path)
    if img.width > PREVIEW_MAX_W:
        img = img.resize((PREVIEW_MAX_W, round(img.height * PREVIEW_MAX_W / img.width)), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return [Image(data=buf.getvalue(), format="png"), f"{path} ({img.width}x{img.height})"]


@_tool
def server_load_test(export_dir: str) -> dict:
    """Load the exported world in the local Minecraft 1.21.4 server and probe blocks (slow, ~1 min)."""
    out = _export_dir(export_dir)
    _world_dir(out)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "mc_server_load_test.py"), str(out)],
                          cwd=ROOT, capture_output=True, text=True, timeout=900)
    report = out / "server_load_test.json"
    if not report.exists():
        raise RuntimeError(f"load test wrote no report (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return {"ok": proc.returncode == 0, **json.loads(report.read_text(encoding="utf-8"))}


@_tool
def install_world(export_dir: str, new_name: str | None = None) -> dict:
    """Copy the exported world into %APPDATA%/.minecraft/saves under a new folder name. Never overwrites."""
    if new_name is not None and not (SAVE_NAME_RE.fullmatch(new_name) and new_name.strip()):
        raise ValueError("new_name must be 1-64 chars of letters, digits, space, '_' or '-'")
    world = _world_dir(_export_dir(export_dir))
    name = new_name or world.name  # export names come from validated params, still a single path component
    saves = Path(os.environ["APPDATA"]) / ".minecraft" / "saves"
    if not saves.is_dir():
        raise FileNotFoundError(f"Minecraft saves folder not found: {saves}")
    dst = saves / name
    if dst.exists():
        raise FileExistsError(f"save already exists: {dst} (pick another new_name)")
    shutil.copytree(world, dst)
    (dst / "session.lock").unlink(missing_ok=True)
    return {"installed": str(dst)}


@_tool
def edit_world(source: str, out: str, box: list[int], preset: str | None = None, overrides: dict | None = None,
               blend: float = 24) -> dict:
    """Improve box [x0, z0, x1, z1] (x1/z1 exclusive) of an existing world, written to a NEW folder `out`.
    Never writes the source; refuses an existing out, out inside the source, or out under %APPDATA%/.minecraft."""
    from terrain_engine.edit_world import edit_world as run
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, int) for v in box)):
        raise ValueError("box must be [x0, z0, x1, z1] integers")
    return run(source, out, box, preset, overrides, blend)


# ---- live dev client (bridge/, Phase 11) -------------------------------------------------------------------------
_game = BC.Game()
TOUR_THUMB_W = 512


@_tool
def game_start() -> dict:
    """Launch the Fabric dev client (bridge/, `gradlew runClient`) and wait for the title screen (first run: minutes)."""
    return _game.start()


@_tool
def game_status() -> dict:
    """Live client state: screen, in_world, world, player pos, fps, rendered chunks."""
    return _game.client.status()


@_tool
def game_command(cmd: str) -> dict:
    """Run a command as the player (op level) in the open world; returns the feedback lines."""
    return {"feedback": _game.client.command(cmd)}


@_tool
def game_stop() -> dict:
    """Quit the world (saves) and close the dev client; kills it if it does not exit in time."""
    return _game.stop()


@_tool
def ingame_tour(export_dir: str, views: list[dict] | None = None) -> list:
    """Screenshot an export in the live client. views: [{name,x,y,z,yaw,pitch}] or None = auto (overview, river,
    mountain, coast, meadow, cave). PNGs go to <export>/previews/ingame/. Starts/stops the game if it was not running."""
    out = _export_dir(export_dir)
    _world_dir(out)
    if views is not None:
        BC.validate_views(views)
    started = not _game.client.alive()
    if started:
        _game.start()
    try:
        shots = BC.tour_export(out, views, client=_game.client)
    finally:
        if started:
            _game.stop()
    res = []
    for s in shots:
        img = PILImage.open(s["path"]).convert("RGB")
        img = img.resize((TOUR_THUMB_W, round(img.height * TOUR_THUMB_W / img.width)), PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        res.append(Image(data=buf.getvalue(), format="jpeg"))
    res.append(json.dumps(shots, separators=(",", ":")))
    return res


if __name__ == "__main__":
    mcp.run("stdio")
