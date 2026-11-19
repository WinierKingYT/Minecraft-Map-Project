---
name: terrain
description: Build or revise a Minecraft terrain map from a natural-language request (Turkish or English). Use when the user asks for a map/terrain/arazi/harita — "dağlık vadi oluştur", "batıya orman ekle", "bu dağları daha doğal yap", "mevcut haritamı geliştir", "make a mountain valley with a river", "add rocky cliffs to the east".
---

# terrain — natural language → map (low token)

The rules do the planning; you only fill gaps. Never write block coordinates or block data.

1. `plan_terrain(request, base_plan=None, name=None)` (MCP, server `minecraft-terrain`).
   CLI fallback: `.venv/Scripts/python -m terrain_engine plan "<request>" [--name N] [--build] [--verify]`.
   - Revision of an earlier map ("bu dağları…", "batıya … ekle"): pass the same `name` (history in
     `exports/plans/<name>.json`) or the previous `plan` as `base_plan`.
2. Read only `unmatched`, `needs`, `warnings`, `explanation`.
   - `unmatched` empty and `needs` empty → go to step 3 unchanged.
   - Otherwise edit just those parts of `plan` JSON: `preset` (see `list_presets`), `overrides`
     (`describe_params` for bounds), `regions` (half/box/circle/polygon + `preset`/`set`, blendable keys only),
     for `action: "edit"` the user's `source` world folder and `box` [x0,z0,x1,z1]. Ask the user if a value is unknown
     (e.g. which world, which area). Do not rewrite fields the rules already set.
3. `build_from_plan(plan, export_world=False, verify=False, ingame_tour=False)`; `verify=True` when the user wants a
   playable world. `cache_hit: true` → done, do not rebuild.
4. Preview (`preview(out)`) or `ingame_tour` only if the user asks to see it. Install only on request (`install_world`).
