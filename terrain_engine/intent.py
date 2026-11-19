"""Rule-based intent compiler: short Turkish/English terrain request -> plan JSON (no LLM, deterministic).

plan = {name, preset, size: [w, l], overrides: {global params}, regions: [...], action: generate|edit, source?, box?}
Whatever the rules cannot place is returned in `unmatched` (clauses) / `needs` (missing inputs) so the calling
AI only decides those few things. Revisions apply on top of a base plan (or the last plan in exports/plans/<name>.json).
"""
import copy
import json
import re

from . import params as P
from . import regions as R

PLAN_DIR = P.ROOT / "exports" / "plans"
MAX_REQUEST = 2000
_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ×", "iissgguuooccaaiiuux")

# stems: a word matches if it starts with the stem; a trailing "=" means exact word
DIRS = {"west": ("bati", "west"), "east": ("dogu", "east"), "north": ("kuzey", "north"),
        "south": ("guney", "south"), "center": ("orta", "center", "centre", "middle", "merkez")}
CORNERS = {"northwest": r"kuzey\s*bati|north\s*-?\s*west", "northeast": r"kuzey\s*dogu|north\s*-?\s*east",
           "southwest": r"guney\s*bati|south\s*-?\s*west", "southeast": r"guney\s*dogu|south\s*-?\s*east"}
# (preset, stems, flavor effects when it is not the main theme), in priority order
THEMES = [
    ("fantasy_forest_valley", ("fantast", "fantasy", "buyulu", "magic", "mistik", "mystic"),
     [("set", "biome", "fantasy_forest")]),
    ("river_valley", (), []),  # phrase "nehir vadisi"/"river valley", or "vadi"/"valley" without mountains
    ("mountain_valley", ("dag", "mountain", "zirve", "peak", "alp"), [("mul", "mountain_height", 1.35)]),
    ("rocky_cliffs", ("kaya", "rock", "ucurum", "cliff", "tasli", "stony", "mesa", "kanyon", "canyon"),
     [("add", "rock_ratio", 0.25), ("add", "cliffiness", 0.2)]),
    ("forest_hills", ("orman", "forest", "woods", "woodland", "hill", "tepe"),
     [("add", "tree_density", 0.25), ("add", "forest_coverage", 0.2)]),
    ("natural_plains", ("ova", "plain", "duzluk", "duzlu", "cayir", "meadow", "bozkir", "steppe", "grassland"),
     [("mul", "mountain_height", 0.65)]),
]
VALLEY = ("vadi", "valley")
MORE_WEAK = ("daha", "more")  # ignored when an adjective modifier ("daha dogal") owns them
MORE = ("cok", "fazla", "yogun", "dense", "sik", "artir", "increase", "bol", "lots", "much=", "extra")
LESS = ("az=", "azalt", "less", "fewer", "seyrek", "sparse", "reduce")
HIGH = ("yuksek", "yuksel", "high", "tall", "buyuk", "bigger", "big=", "huge", "devasa")
LOW = ("alcak", "alcal", "low", "kucuk", "small", "basik", "az=", "less", "fewer")
NEG = ("yok", "no=", "without", "olmasin", "kaldir", "remove", "olmadan", "none", "nehirsiz", "riverless")
RIVER = ("nehir", "river", "dere", "stream", "irmak")
# (label, subject stems or None, [(qualifiers, effects)]) -- first matching qualifier wins
MODS = [
    ("smoother", None, [(("yumusa", "smooth", "dogal", "natural", "gentle", "puruzsuz", "realistic", "gercekci"),
                         [("add", "erosion", 0.2), ("add", "hydraulic", 0.4), ("mul", "ridge_sharpness", 0.7),
                          ("mul", "cliffiness", 0.6)])]),
    ("rougher", None, [(("keskin", "sharp", "sert=", "rugged", "engebe", "puruzlu", "rough", "jagged", "vahsi", "wild"),
                        [("mul", "ridge_sharpness", 1.4), ("add", "roughness", 0.15), ("add", "erosion", -0.15)])]),
    ("flatter", None, [(("duzles", "flatten", "flat="), [("mul", "mountain_height", 0.5), ("mul", "hill_amplitude", 0.5)])]),
    ("river", RIVER, [(NEG, [("set", "river_width", 0)]),
                      (("genis", "wide", "buyuk", "big=", "broad"), [("mul", "river_width", 1.6), ("min", "river_width", 12)]),
                      (("dar=", "narrow", "ince", "kucuk", "small"), [("mul", "river_width", 0.6), ("min", "river_width", 3)]),
                      ((), [("min", "river_width", 6)])]),
    ("caves", ("magara", "cave", "cavern"), [(NEG, [("set", "caves", 0)]), (LESS, [("mul", "caves", 0.5)]),
                                             (MORE + MORE_WEAK, [("min", "caves", 0.8)]), ((), [("min", "caves", 0.5)])]),
    ("snow", ("kar=", "karli", "karla", "snow"), [(NEG, [("set", "snow_line", 320)]), (LESS, [("add", "snow_line", 30)]),
                                                  ((), [("add", "snow_line", -45)])]),
    ("mountains", ("dag", "mountain", "zirve", "peak", "tepe", "hill"),
     [(LOW, [("mul", "mountain_height", 0.65)]), (HIGH, [("mul", "mountain_height", 1.35)])]),
    ("rock", ("kaya", "rock", "tas=", "tasli", "stone", "ucurum", "cliff"),
     [(LESS, [("mul", "rock_ratio", 0.5), ("mul", "cliffiness", 0.5)]),
      (MORE + MORE_WEAK, [("add", "rock_ratio", 0.25), ("add", "cliffiness", 0.2)])]),
    ("forest", ("orman", "forest", "agac", "tree", "woods"),
     [(LESS, [("mul", "tree_density", 0.5), ("mul", "forest_coverage", 0.6)]),
      (MORE + MORE_WEAK, [("add", "tree_density", 0.25), ("add", "forest_coverage", 0.2)])]),
]
TRANSITION = ("gecis", "transition", "blend", "sinir", "border", "edge")
EDIT_SUBJ = ("mevcut", "existing", "varolan", "kendi", "haritam", "dunyam", "my=")
EDIT_VERB = ("gelistir", "improve", "duzenle", "edit", "degistir", "modify", "iyilestir", "guzellestir", "enhance")
STOP = ("bu=", "su=", "bir=", "ve=", "ile=", "olan=", "olsun", "olust", "uret", "yap", "ekle", "harita", "arazi",
        "bolge", "alan=", "taraf", "kisim", "boyut", "size", "blok", "block", "kare", "the=", "a=", "an=", "make",
        "create", "generate", "build", "add", "map", "terrain", "area", "region", "side", "part", "please",
        "lutfen", "of=", "in=", "on=", "to=", "at=", "me=", "this", "these", "that", "olarak", "icin", "for=",
        "minecraft", "dunya", "world", "lik=", "li=", "da=", "de=", "is=", "be=", "some", "biraz")
SPLIT = re.compile(r"[,.;!?\n]+|\s(?:ve|and|ile|with|ama|but|plus|also|ayrica)\s")


def _has(words, stems) -> bool:
    return any(w == s[:-1] if s.endswith("=") else w.startswith(s) for w in words for s in stems)


def _fold(text: str) -> str:
    return text.translate(_FOLD)


def _extract(t: str, raw: str, warnings: list) -> tuple[str, dict]:
    """Pull size / seed / name out of the text; return the rest."""
    got = {}
    m = re.search(r"\b(?:name|isim|adi|ad)\s*[:=]\s*([A-Za-z0-9_-]{1,64})|\bnamed\s+([A-Za-z0-9_-]{1,64})", raw, re.I)
    if m:
        got["name"] = m.group(1) or m.group(2)
        t = t.replace(m.group(0).casefold(), " ")
    m = re.search(r"\b(?:seed|tohum)\s*[:=]?\s*(\d{1,12})\b", t)
    if m:
        got["seed"] = min(int(m.group(1)), P.NUMERIC["seed"][2])
        t = t[:m.start()] + " " + t[m.end():]
    m = (re.search(r"\b(\d{1,6})\s*[x*]\s*(\d{1,6})\b", t) or re.search(r"\b(\d{1,2}(?:[.,]\d)?)\s*k\b", t)
         or re.search(r"\b(\d{2,6})\b", t))
    if m:
        vals = m.groups()
        if m.re.pattern.endswith(r"k\b"):
            vals = (round(float(vals[0].replace(",", ".")) * 1024),) * 2
        elif len(vals) == 1:
            vals = vals * 2
        got["size"] = [_size(int(v), warnings) for v in vals]
        warnings[:] = list(dict.fromkeys(warnings))
        t = t[:m.start()] + " " + t[m.end():]
    return t, got


def _size(v: int, warnings: list) -> int:
    lo, hi = P.NUMERIC["width"][1:]
    s = min(max(round(v / 16) * 16, lo), hi)
    if s != v:
        warnings.append(f"size {v} -> {s} (multiple of 16 within [{lo}, {hi}])")
    return s


def _clause(words: list[str], text: str, revision: bool) -> dict:
    c = {"text": text, "dirs": [], "themes": [], "mods": [], "edit": False}
    c["dirs"] = [k for k, rx in CORNERS.items() if re.search(rx, text)]
    if not c["dirs"]:
        c["dirs"] = [k for k, stems in DIRS.items() if _has(words, stems)]
    if _has(words, EDIT_SUBJ) and _has(words, EDIT_VERB):
        c["edit"] = True
    style = False
    for label, subj, rules in MODS:
        if subj is not None and not _has(words, subj):
            continue
        for quals, effects in rules:  # style mods come first in MODS, so `style` is known for the rest
            if quals == () or _has(words, tuple(q for q in quals if not (style and q in MORE_WEAK))):
                c["mods"].append((label, effects))
                style = style or subj is None
                break
    if any(m[0] == "smoother" for m in c["mods"]) and _has(words, TRANSITION):
        c["mods"].append(("softer_transitions", [("blend", None, 2.0)]))
    mountain = _has(words, THEMES[2][1])
    for preset, stems, flavor in THEMES:
        hit = _has(words, stems) if stems else (re.search(r"nehir vadi|river valley", text)
                                                or (_has(words, VALLEY) and not mountain))
        if hit:
            c["themes"].append((preset, flavor))
    # "ortasinda nehir" = river placement, not a circle region; the river mod is a feature, not a theme override
    if any(m[0] == "river" for m in c["mods"]) and len(c["dirs"]) == 1 and c["dirs"][0] in DIRS:
        side = c["dirs"].pop()
        pos = {"center": 0.5, "west": 0.3, "north": 0.3, "east": 0.7, "south": 0.7}[side]
        axis = "west_east" if side in ("north", "south") else "north_south"
        c["mods"].append(("river_place", [("set", "river_position", pos), ("set", "river_axis", axis)]))
    if revision and not c["dirs"] and c["mods"]:
        c["themes"] = []  # "bu daglari daha dogal yap": the nouns name what to change, not a new theme
    c["matched"] = bool(c["dirs"] or c["themes"] or c["mods"] or c["edit"])
    c["filler"] = all(_has([w], STOP) for w in words)
    return c


def _region_geom(side: str, w: int, l: int) -> dict:
    if side in R.SIDES:
        return {"shape": "half", "side": side}
    if side == "center":
        return {"shape": "circle", "cx": w / 2, "cz": l / 2, "r": min(w, l) / 4}
    x0, x1 = (0, w / 2) if "west" in side else (w / 2, w)
    z0, z1 = (0, l / 2) if "north" in side else (l / 2, l)
    return {"shape": "box", "x0": x0, "z0": z0, "x1": x1, "z1": z1}


def _base_vals(preset):
    return P.preset_values(preset) if preset else P.defaults()


def _get(plan, key, region=None):
    if region is not None:
        if key in region.get("set", {}):
            return region["set"][key]
        if region.get("preset"):
            return P.preset_values(region["preset"])[key]
    return plan["overrides"].get(key, _base_vals(plan["preset"])[key])


def _apply(cur, op, arg, key):
    if key in P.CHOICES:
        return arg
    v = {"mul": lambda: cur * arg, "add": lambda: cur + arg, "set": lambda: arg, "min": lambda: max(cur, arg)}[op]()
    _, lo, hi = P.NUMERIC[key]
    return round(min(max(v, lo), hi), 3)


def _effects(plan, effects, region=None):
    for op, key, arg in effects:
        if op == "blend":
            for r in plan["regions"]:
                r["blend"] = min(R.BLEND_MAX, max(64.0, r.get("blend", R.DEFAULT_BLEND) * arg))
        elif region is not None and key in R.BLENDABLE + R.BLEND_CHOICES:
            region.setdefault("set", {})[key] = _apply(_get(plan, key, region), op, arg, key)
        else:
            plan["overrides"][key] = _apply(_get(plan, key), op, arg, key)
            if key in R.BLENDABLE and region is None:  # a global change also reaches regions that pin the key
                for r in plan["regions"]:
                    if r.get("preset") or key in r.get("set", {}):
                        r.setdefault("set", {})[key] = _apply(_get(plan, key, r), op, arg, key)


def new_plan() -> dict:
    return {"name": "terrain", "preset": None, "size": [256, 256], "overrides": {}, "regions": [], "action": "generate"}


def compile_request(request: str, base_plan: dict | None = None) -> dict:
    """Pure function: request (+ optional base plan) -> {plan, unmatched, needs, warnings, explanation}."""
    if not isinstance(request, str) or not request.strip():
        raise ValueError("empty request: describe the terrain, e.g. '256x256 mountain valley with a river'")
    if len(request) > MAX_REQUEST:
        raise ValueError(f"request longer than {MAX_REQUEST} chars")
    revision = base_plan is not None
    plan = check_plan(copy.deepcopy(base_plan)) if revision else new_plan()
    warnings, unmatched = [], []
    raw = _fold(request)
    t, got = _extract(raw.casefold(), raw, warnings)
    if "size" in got:
        plan["size"] = got["size"]
    if "seed" in got:
        plan["overrides"]["seed"] = got["seed"]
    clauses = [_clause(re.findall(r"[a-z0-9]+", s), " ".join(re.findall(r"[a-z0-9]+", s)), revision)
               for s in SPLIT.split(t)]
    clauses = [c for c in clauses if c["text"]]
    # 1) main theme from undirected clauses; the other themes only add their flavor
    themes = [th for c in clauses if not c["dirs"] for th in c["themes"]]
    flavors = []
    if themes:
        order = [p for p, _, _ in THEMES]
        main = min(themes, key=lambda th: order.index(th[0]))
        if main[0] != plan["preset"]:
            plan["preset"] = main[0]
        flavors = [th for th in dict.fromkeys(p for p, _ in themes) if th != main[0]]
    pending = [(None, f) for p, _, f in THEMES if p in flavors]
    # 2) regions from directional clauses
    for c in clauses:
        if c["edit"]:
            plan["action"] = "edit"
        if not c["matched"]:
            if not c["filler"]:
                unmatched.append(c["text"])
            continue
        for side in c["dirs"]:
            if not (c["themes"] or c["mods"]):
                unmatched.append(c["text"])
                break
            region = {"name": side, **_region_geom(side, *plan["size"]), "blend": R.DEFAULT_BLEND}
            if c["themes"]:
                region["preset"] = c["themes"][0][0]
                pending += [(region, f) for _, f in c["themes"][1:]]
            plan["regions"] = [r for r in plan["regions"] if r.get("name") != side] + [region]
            pending += [(region, effects) for _, effects in c["mods"]]
        if not c["dirs"]:
            pending += [(None, effects) for _, effects in c["mods"]]
    # 3) parameter deltas, each distinct change once per target
    seen = set()
    for region, effects in pending:
        k = (id(region), json.dumps(effects))
        if k not in seen:
            seen.add(k)
            _effects(plan, effects, region)
    if got.get("name"):
        plan["name"] = got["name"]
    elif not revision:
        plan["name"] = f"{plan['preset'] or 'terrain'}_{plan['size'][0]}"
    needs = []
    if plan["action"] == "edit":
        needs = [k for k in ("source", "box") if not plan.get(k)]
    if not plan["preset"] and not plan["regions"] and not plan["overrides"]:
        needs.append("preset")
    params_of(plan)  # every plan must load
    return {"plan": plan, "unmatched": unmatched, "needs": needs, "warnings": warnings,
            "explanation": _explain(plan, unmatched, needs)}


def _explain(plan, unmatched, needs) -> str:
    parts = [f"{plan['action']} {plan['preset'] or 'defaults'} {plan['size'][0]}x{plan['size'][1]}"]
    if plan["regions"]:
        parts.append("regions " + ", ".join(f"{r.get('name')}={r.get('preset') or 'tweak'}" for r in plan["regions"]))
    if plan["overrides"]:
        parts.append("set " + ",".join(sorted(plan["overrides"])))
    if unmatched:
        parts.append(f"{len(unmatched)} unmatched")
    if needs:
        parts.append("needs " + ",".join(needs))
    return "; ".join(parts)


PLAN_KEYS = {"name", "preset", "size", "overrides", "regions", "action", "source", "box"}


def check_plan(plan) -> dict:
    """Shape check for plans coming from outside (AI / history file); params themselves are checked by params.load."""
    if not isinstance(plan, dict) or set(plan) - PLAN_KEYS:
        raise ValueError(f"plan must be an object with keys from {sorted(PLAN_KEYS)}")
    out = {**new_plan(), **plan}
    if not P.NAME_RE.fullmatch(str(out["name"])):
        raise ValueError(f"plan name must match {P.NAME_RE.pattern}")
    if out["action"] not in ("generate", "edit"):
        raise ValueError("plan action must be 'generate' or 'edit'")
    if not (isinstance(out["size"], list) and len(out["size"]) == 2 and all(isinstance(v, int) for v in out["size"])):
        raise ValueError("plan size must be [width, length] integers")
    if not isinstance(out["overrides"], dict) or not isinstance(out["regions"], list):
        raise ValueError("plan overrides must be an object and regions a list")
    if {"width", "length", "name", "regions"} & set(out["overrides"]):
        raise ValueError("put width/length in plan.size, name in plan.name, regions in plan.regions")
    if "box" in plan and not (isinstance(plan["box"], list) and len(plan["box"]) == 4
                              and all(isinstance(v, int) for v in plan["box"])):
        raise ValueError("plan box must be [x0, z0, x1, z1] integers")
    return out


def overrides_of(plan) -> dict:
    o = {**plan["overrides"], "width": plan["size"][0], "length": plan["size"][1], "name": plan["name"]}
    if plan["regions"]:
        o["regions"] = plan["regions"]
    return o


def params_of(plan) -> dict:
    plan = check_plan(plan)
    return P.load(plan["preset"], overrides_of(plan))


# ---- plan history: exports/plans/<name>.json = [{"request", "plan"}, ...] ----------------------------------------
def _history_path(name: str):
    if not P.NAME_RE.fullmatch(name or ""):
        raise ValueError(f"name must match {P.NAME_RE.pattern}")
    return PLAN_DIR / f"{name}.json"


def history(name: str) -> list:
    path = _history_path(name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def plan(request: str, base_plan: dict | None = None, name: str | None = None) -> dict:
    """compile_request + history: with `name` and no base_plan, the last saved plan of that map is the base."""
    if base_plan is None and name:
        h = history(name)
        base_plan = h[-1]["plan"] if h else None
    res = compile_request(request, base_plan)
    if name:
        res["plan"]["name"] = name
    path = _history_path(res["plan"]["name"])
    h = history(res["plan"]["name"])
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(h + [{"request": request, "plan": res["plan"]}], ensure_ascii=False, indent=1),
                    encoding="utf-8")
    res["history"] = len(h) + 1
    return res
