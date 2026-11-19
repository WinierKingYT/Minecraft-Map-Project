"""Load an exported world in the official Minecraft dedicated server (localhost only) and probe blocks.

Usage: python scripts/mc_server_load_test.py <export_dir>   (requires tests/mc_server/server.jar, eula accepted)
       python scripts/mc_server_load_test.py --world <world_dir> --box X0 Z0 X1 Z1   (edit-world output)
"""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRV = ROOT / "tests" / "mc_server"
GRID = 4  # probes: one dry, tree-free column per grid cell


def main(export_dir: str) -> int:
    out = Path(export_dir)
    p = json.loads((out / "params.json").read_text())
    d = np.load(out / "fields.npz")
    # probe columns without trees, one per cell of a GRID x GRID split of the map (spread over large maps):
    # block at round(h) must be solid, block above must be air. Each probe forceloads only its own chunk.
    rng = np.random.default_rng(0)
    h = d["height"]
    land = (h > p["water_level"] + 1) & ((d["trees_deciduous"] + d["trees_pine"]) == 0)
    probes = []
    for gz in range(GRID):
        for gx in range(GRID):
            z0, z1 = gz * p["length"] // GRID, (gz + 1) * p["length"] // GRID
            x0, x1 = gx * p["width"] // GRID, (gx + 1) * p["width"] // GRID
            zs, xs = np.nonzero(land[z0:z1, x0:x1])
            if len(xs):
                i = rng.integers(len(xs))
                x, z = x0 + int(xs[i]), z0 + int(zs[i])
                probes.append((x, int(np.floor(h[z, x] + 0.5)), z))
    cmds = [f"forceload add {x} {z}" for x, _, z in probes]
    wl, W, L = p["water_level"], p["width"], p["length"]
    # one condition per command: a chained `execute if A if B` prints nothing when A fails, hiding the failure
    checks = []
    if p.get("outside") == "ocean":  # outside the map: flat sea, water surface at wl, sand floor at wl-SEA_DEPTH
        sys.path.insert(0, str(ROOT))
        from terrain_engine.generator import SEA_DEPTH
        floor = wl - SEA_DEPTH
        for x, z in ((W + 40, L // 2), (-30, -30), (W // 2, L + 30), (0, L // 2), (W - 1, 0)):
            cmds.append(f"forceload add {x} {z}")
            checks += [f"if block {x} {wl} {z} minecraft:water", f"if block {x} {floor + 1} {z} minecraft:water",
                       f"unless block {x} {floor} {z} minecraft:air", f"unless block {x} {floor} {z} minecraft:water"]
    for x, y, z in probes:
        checks += [f"unless block {x} {y} {z} minecraft:air", f"if block {x} {y + 1} {z} #minecraft:replaceable"]
    cmds += [f"execute {c}" for c in checks]
    return run(out / "world" / p["name"], cmds, len(checks), out / "server_load_test.json")


def main_edited(world_dir: str, box: list[int]) -> int:
    """Probe an edit-world output: 12 dry columns in the box + 4 outside. The server must find exactly the
    surface block our Anvil reader sees, with no water above it."""
    sys.path.insert(0, str(ROOT))
    from terrain_engine.edit_world import read_area
    x0, z0, x1, z1 = box
    m = 8
    a = read_area(world_dir, (x0 - m, z0 - m, x1 + m, z1 + m))
    zz, xx = np.mgrid[z0 - m:z1 + m, x0 - m:x1 + m]
    inside = (xx >= x0) & (xx < x1) & (zz >= z0) & (zz < z1)
    dry = a["water_top"] == a["top"]
    rng = np.random.default_rng(0)
    cmds = [f"forceload add {x0 - m} {z0 - m} {x1 + m - 1} {z1 + m - 1}"]  # max 256 chunks per command
    n = 0
    for sel, k in ((inside & dry, 12), (~inside & dry, 4)):
        zs, xs = np.nonzero(sel)
        for i in rng.choice(len(xs), k, replace=False):
            x, z, y = int(xx[zs[i], xs[i]]), int(zz[zs[i], xs[i]]), int(a["top"][zs[i], xs[i]])
            cmds.append(f"execute if block {x} {y} {z} {a['surface'][zs[i], xs[i]]}")
            cmds.append(f"execute unless block {x} {y + 1} {z} minecraft:water")
            n += 2
    world = Path(world_dir)
    return run(world, cmds, n, world.parent / f"{world.name}_server_load_test.json")


def run(src_world: Path, cmds: list[str], n_probes: int, report: Path) -> int:
    world = SRV / "world_under_test"
    shutil.rmtree(world, ignore_errors=True)
    shutil.copytree(src_world, world)  # test a copy, never the world itself
    (SRV / "eula.txt").write_text("eula=true\n")
    (SRV / "server.properties").write_text(
        "level-name=world_under_test\nserver-ip=127.0.0.1\nserver-port=25599\nonline-mode=true\n"
        "spawn-protection=0\nmax-players=1\nview-distance=4\nsimulation-distance=4\nenable-rcon=false\n")
    proc = subprocess.Popen(["java", "-Xmx2G", "-jar", "server.jar", "nogui"], cwd=SRV, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    watchdog = threading.Timer(600, proc.kill)  # never hang, even if the server goes silent
    watchdog.start()
    log, passed, failed, t0 = [], 0, 0, time.time()
    sent = stopping = False
    for line in proc.stdout:
        log.append(line.rstrip())
        if "Done (" in line and not sent:
            proc.stdin.write("\n".join(cmds) + "\n")
            proc.stdin.flush()
            sent = True
        passed += "Test passed" in line
        failed += "Test failed" in line or "not loaded" in line or "Too many chunks" in line
        if sent and passed + failed >= n_probes and not stopping:
            stopping = True
            proc.stdin.write("stop\n")
            proc.stdin.flush()
    watchdog.cancel()
    proc.wait(timeout=60)
    benign = ("key missing: DragonFight",)  # vanilla datafixer note for worlds without End data
    errors = [l for l in log if any(k in l for k in ("ERROR", "Exception", "Failed to load", "Couldn't load"))
              and not any(b in l for b in benign)]
    result = {"done_seen": sent, "probes": n_probes, "passed": passed, "failed": failed,
              "error_lines": errors[:10], "exit_code": proc.returncode, "seconds": round(time.time() - t0, 1)}
    (SRV / "server_console.log").write_text("\n".join(log), encoding="utf-8")
    report.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if sent and failed == 0 and passed == n_probes and not errors else 1


if __name__ == "__main__":
    if sys.argv[1] == "--world":
        sys.exit(main_edited(sys.argv[2], [int(v) for v in sys.argv[4:8]]))
    sys.exit(main(sys.argv[1]))
