"""WorldPainter bridge: writes a wpscript (Nashorn JS) and runs it headless.

Only documented API calls are used (worldpainter.net/trac/wiki/Scripting/API),
plus World2.setName/setSpawnPoint which are plain public Java setters.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import nbtlib
from PIL import Image

from . import anvil_fix
from .export import HEIGHT_MAX, ROOT
from .generator import SEA_DEPTH, T

_ENUM = {"COARSE_DIRT": "PERMADIRT"}  # our name -> WorldPainter Terrain enum name

WP_DIR = ROOT / "tools" / "worldpainter" / "WorldPainter"
MAP_FORMAT = "org.pepsoft.anvil.1.20.5"  # "Minecraft 1.20.5 - 1.21.10" in WP 2.27.1

_JS = """
var DIR = {dir};
var hm = wp.getHeightMap().fromFile(DIR + '/heightmap16.png').go();
var fmt = wp.getMapFormat().withId({fmt}).go();
var world = wp.createWorld().fromHeightMap(hm)
    .fromLevels(0, 65535).toLevels(0, {hmax})
    .withWaterLevel({wl}).withMapFormat(fmt)
    .withLowerBuildLimit(-64).withUpperBuildLimit(320).go();
world.setName({name});
try {{ world.setSpawnPoint(new java.awt.Point({sx}, {sz})); }} catch (e) {{ print('spawn not set: ' + e); }}
var tm = wp.getHeightMap().fromFile(DIR + '/terrain_index.png').go();
var op = wp.applyHeightMap(tm).toWorld(world).applyToTerrain();
{terrain_map}
op.go();
var layers = {{'Deciduous': 'trees_deciduous.png', 'Pine': 'trees_pine.png'}};
for (var name in layers) {{
    var m = wp.getHeightMap().fromFile(DIR + '/' + layers[name]).go();
    var layer = wp.getLayer().withName(name).go();  // must be fetched before applyHeightMap() starts
    wp.applyHeightMap(m).toWorld(world).applyToLayer(layer)
        .fromLevels(0, 15).toLevels(0, 15).go();
}}
{extra_layers}
wp.saveWorld(world).toFile(DIR + '/worldpainter/' + {name} + '.world').go();
wp.exportWorld(world).toDirectory(DIR + '/world').go();
print('WP_EXPORT_OK');
"""


# PNG -> WorldPainter default layer (names probed in 2.27.1). Caves skip water columns (river, sea).
_EXTRA = {"caverns": "Caverns", "chasms": "Chasms", "biomes": "Biomes"}


def _extra_layers(out: Path) -> str:
    js = ["var noWater = wp.createFilter().exceptOnWater().go();"]
    for key, layer in _EXTRA.items():
        png = out / f"{key}.png"
        if not png.exists():
            continue
        if key == "biomes":  # direct mapping: value = biome ID; map each explicitly (0 = ocean must not be skipped)
            used = sorted(int(v) for v in np.unique(np.asarray(Image.open(png))))
            levels, filt = "".join(f".fromLevel({v}).toLevel({v})" for v in used), ""
        else:
            levels, filt = ".fromLevels(0, 15).toLevels(0, 15)", ".withFilter(noWater)"
        js.append(f"(function () {{ var layer = wp.getLayer().withName('{layer}').go();\n"
                  f"  var m = wp.getHeightMap().fromFile(DIR + '/{png.name}').go();\n"
                  f"  wp.applyHeightMap(m).toWorld(world){filt}.applyToLayer(layer){levels}.go(); }})();")
    return "\n".join(js) if len(js) > 1 else ""


def write_script(p: dict, out: Path) -> Path:
    used = sorted(int(v) for v in np.unique(np.asarray(Image.open(out / "terrain_index.png"))))
    names = {code: _ENUM.get(n, n) for n, code in T.items()}
    # toTerrain() takes a position in Terrain.VALUES, which differs from the enum ordinal: resolve by name in WP
    lines = ["var TV = Java.type('org.pepsoft.worldpainter.Terrain').VALUES;",
             "function tIdx(n) { for (var i = 0; i < TV.length; i++) if (TV[i].name() == n) return i; throw 'no terrain ' + n; }"]
    lines += [f"op = op.fromLevel({c}).toTerrain(tIdx('{names[c]}'));" for c in used]
    js = _JS.format(dir=json.dumps(out.as_posix()), fmt=json.dumps(MAP_FORMAT), hmax=int(HEIGHT_MAX),
                    wl=p["water_level"], name=json.dumps(p["name"]), sx=p["width"] // 2, sz=p["length"] // 2,
                    terrain_map="\n".join(lines), extra_layers=_extra_layers(out))
    path = out / "worldpainter" / "build_world.js"
    path.parent.mkdir(exist_ok=True)
    (out / "world").mkdir(exist_ok=True)
    path.write_text(js, encoding="utf-8")
    return path


def run(p: dict, out: Path, timeout: int | None = None) -> Path:
    """Run wpscript headless; returns exported world dir. Raises on failure.
    Default timeout: 15 min + 1 s per 10k columns (~43 min at 4096^2)."""
    timeout = timeout or 900 + p["width"] * p["length"] // 10_000
    exe = WP_DIR / "wpscript.exe"
    if not exe.exists():
        raise FileNotFoundError(f"WorldPainter not found at {exe}")
    script = write_script(p, out)
    res = subprocess.run([str(exe), str(script)], cwd=WP_DIR, capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "JAVA_TOOL_OPTIONS": "-Duser.language=en -Duser.country=US"})
    log = out / "worldpainter" / "wpscript.log"
    log.write_text(res.stdout + "\n--- stderr ---\n" + res.stderr, encoding="utf-8")
    if res.returncode != 0 or "WP_EXPORT_OK" not in res.stdout:
        raise RuntimeError(f"wpscript failed (exit {res.returncode}); see {log}")
    world = out / "world" / p["name"]
    if not (world / "region").is_dir():  # e.g. Turkish locale writes "regıon"
        raise RuntimeError(f"export has no 'region' dir: {sorted(x.name for x in world.iterdir())}")
    if not (world / "level.dat").exists():
        raise RuntimeError(f"export finished but {world / 'level.dat'} missing; see {log}")
    anvil_fix.fix_world(world)  # WP writes minecraft:grass, gone since 1.20.3
    fix_player_dimension(world)
    if p["outside"] == "ocean":
        set_outside_ocean(world, p["water_level"])
    return world


def fix_player_dimension(world: Path) -> bool:
    """WP 2.27.1 writes Data.Player with SpawnX/Y/Z but no SpawnDimension (and no Dimension); MC 1.21.4 then
    logs "Not a string" on every load while parsing the missing dimension. Adds both as overworld."""
    from nbtlib import String
    lvl = nbtlib.load(world / "level.dat")
    player = lvl["Data"].get("Player")
    missing = [k for k in ("Dimension", "SpawnDimension") if player is not None and k not in player]
    for k in missing:
        player[k] = String("minecraft:overworld")
    if missing:
        lvl.save()
    return bool(missing)


def set_outside_ocean(world: Path, water_level: int) -> None:
    """Make Minecraft generate a flat ocean (sand floor at water_level - SEA_DEPTH) beyond the exported map.

    Edits only WorldGenSettings of the overworld in the freshly exported level.dat (a .bak copy is kept).
    """
    from nbtlib import Byte, Compound, Int, List, String

    lvl_path = world / "level.dat"
    shutil.copy2(lvl_path, lvl_path.with_suffix(".dat.bak"))
    sand_top = water_level - SEA_DEPTH
    layers = [("minecraft:bedrock", 1), ("minecraft:stone", sand_top - 4 - (-64)),
              ("minecraft:sand", 4), ("minecraft:water", SEA_DEPTH)]  # covers y=-64..water_level
    lvl = nbtlib.load(lvl_path)
    dims = lvl["Data"]["WorldGenSettings"]["dimensions"]
    dims["minecraft:overworld"]["generator"] = Compound({
        "type": String("minecraft:flat"),
        "settings": Compound({
            "biome": String("minecraft:ocean"), "features": Byte(0), "lakes": Byte(0),
            "layers": List[Compound]([Compound({"block": String(b), "height": Int(h)}) for b, h in layers]),
            "structure_overrides": List[String]([]),
        }),
    })
    lvl.save()
