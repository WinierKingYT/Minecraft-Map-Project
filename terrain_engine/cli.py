"""CLI: python -m terrain_engine generate --preset river_valley --set seed=7 --worldpainter

Regeneration needs no AI call: same preset/params/engine version -> cache hit.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

from . import params as P
from .export import build


def _overrides(pairs):
    out = {}
    for kv in pairs or []:
        k, _, v = kv.partition("=")
        if not _:
            raise SystemExit(f"--set expects key=value, got {kv!r}")
        out[k.strip()] = v.strip()
    return out


def cmd_generate(a) -> int:
    return _run(P.load(a.preset, _overrides(a.set), a.params), a)


def _run(p, a) -> int:
    out, man, hit = build(p, force=a.force, tile=a.tile)
    print(f"{'CACHE HIT' if hit else 'generated'}: {out}  ({man['generation_seconds']}s, key {man['cache_key']})")
    if a.worldpainter or a.verify:
        from . import worldpainter, verify_world
        world = out / "world" / p["name"]
        if not (world / "level.dat").exists() or a.force:
            if world.exists():
                shutil.rmtree(world)
            world = worldpainter.run(p, out)
            print(f"world exported: {world}")
        else:
            print(f"world cached: {world}")
        report = verify_world.verify(world, p, dict(np.load(out / "fields.npz")))
        (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    return 0


def cmd_edit_world(a) -> int:
    from .edit_world import edit_world
    overrides = json.loads(Path(a.params).read_text(encoding="utf-8")) if a.params else {}
    overrides.update(_overrides(a.set))
    print(json.dumps(edit_world(a.source, a.out, a.box, a.preset, overrides, a.blend)))
    return 0


def cmd_plan(a) -> int:
    from . import intent
    base = json.loads(Path(a.base).read_text(encoding="utf-8")) if a.base else None
    res = intent.plan(a.request, base, a.name)
    print(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    if not (a.build or a.verify):
        return 0
    if res["needs"] or res["plan"]["action"] != "generate":
        raise ValueError(f"plan not buildable from the CLI yet, needs: {res['needs'] or ['action=generate']}")
    a.force, a.tile, a.worldpainter = False, None, False
    return _run(intent.params_of(res["plan"]), a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="terrain_engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--preset", help=f"one of {P.list_presets()}")
    g.add_argument("--params", help="JSON file with parameter overrides")
    g.add_argument("--set", action="append", metavar="KEY=VALUE")
    g.add_argument("--tile", type=int, help="generate in tiles of N blocks (large maps)")
    g.add_argument("--force", action="store_true", help="ignore cache")
    g.add_argument("--worldpainter", action="store_true", help="export Minecraft world via WorldPainter")
    g.add_argument("--verify", action="store_true", help="verify exported world (implies export)")
    sub.add_parser("presets")
    e = sub.add_parser("edit-world", help="improve a box of an existing world, written to a NEW copy")
    e.add_argument("--source", required=True, help="existing world folder (only read)")
    e.add_argument("--out", required=True, help="new world folder (must not exist)")
    e.add_argument("--box", required=True, type=int, nargs=4, metavar=("X0", "Z0", "X1", "Z1"),
                   help="block coords, x1/z1 exclusive")
    e.add_argument("--preset", help=f"one of {P.list_presets()}")
    e.add_argument("--params", help="JSON file with parameter overrides")
    e.add_argument("--set", action="append", metavar="KEY=VALUE")
    e.add_argument("--blend", type=float, default=24, help="blend band width, blocks")
    pl = sub.add_parser("plan", help="natural-language request -> plan JSON (rule based, no AI call)")
    pl.add_argument("request")
    pl.add_argument("--name", help="map name; reuses exports/plans/<name>.json as the base (revision)")
    pl.add_argument("--base", help="JSON file with a base plan to revise")
    pl.add_argument("--build", action="store_true", help="generate the planned terrain")
    pl.add_argument("--verify", action="store_true", help="build + export world + verify")
    a = ap.parse_args(argv)
    if a.cmd == "presets":
        print("\n".join(P.list_presets()))
        return 0
    try:
        return {"edit-world": cmd_edit_world, "plan": cmd_plan}.get(a.cmd, cmd_generate)(a)
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
