"""Driver for the live dev client (Phase 11): start `gradlew runClient`, talk to the localhost bridge mod, tour worlds.

The game is the Fabric Loom dev client of bridge/ (official game files, dev username, no account); its run directory
is bridge/run. The bridge mod listens on 127.0.0.1 only and requires the random token from bridge/run/bridge_token.txt.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "bridge"
RUN = BRIDGE / "run"
TOKEN_FILE = RUN / "bridge_token.txt"
LOG_FILE = ROOT / "logs" / "bridge_client.log"
HOST, PORT = "127.0.0.1", 25580
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TOKEN_HEADER = "X-Bridge-Token"
START_TIMEOUT = 1200  # first launch downloads the game + assets via Loom
Y_MIN, Y_MAX = -60.0, 380.0

# written into bridge/run/options.txt before launch (merged, other keys kept)
OPTIONS = {"renderDistance": "16", "simulationDistance": "8", "fov": "0.0", "guiScale": "2",
           "pauseOnLostFocus": "false", "onboardAccessibility": "false", "tutorialStep": "none",
           "skipMultiplayerWarning": "true", "joinedFirstServer": "true", "narrator": "0",
           "soundCategory_master": "0.0", "maxFps": "60", "enableVsync": "false", "graphicsMode": "1",
           "renderClouds": "\"false\""}  # clouds hide the overview (camera above y=192)


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    """Minimal JSON client for the bridge mod. Refuses anything but loopback hosts."""

    def __init__(self, host: str = HOST, port: int = PORT, token_file: Path = TOKEN_FILE, timeout: float = 30):
        if host not in LOOPBACK:
            raise ValueError(f"bridge host must be loopback, got {host!r}")
        self.base = f"http://{'[::1]' if host == '::1' else host}:{port}"
        self.token_file, self.timeout = Path(token_file), timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy

    def _token(self) -> str:
        try:
            return self.token_file.read_text(encoding="ascii").split()[0]
        except (FileNotFoundError, IndexError):
            raise BridgeError("bridge not running (no token file)") from None

    def call(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                     headers={TOKEN_HEADER: self._token(), "Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode()).get("error", "")
            except (ValueError, OSError):
                msg = ""
            raise BridgeError(f"{path}: HTTP {e.code} {msg}".strip()) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise BridgeError(f"{path}: bridge not reachable ({reason})") from None
        except ValueError:
            raise BridgeError(f"{path}: invalid JSON from bridge") from None
        if not res.get("ok"):
            raise BridgeError(f"{path}: {res.get('error', 'failed')}")
        return {k: v for k, v in res.items() if k != "ok"}

    def alive(self) -> bool:
        try:
            self.status()
            return True
        except BridgeError:
            return False

    def status(self) -> dict:
        return self.call("/status", timeout=10)

    def open_world(self, name: str, timeout: float = 240) -> dict:
        return self.call("/open_world", {"name": name, "timeout": timeout}, timeout=timeout + 30)

    def command(self, cmd: str) -> list[str]:
        return self.call("/command", {"cmd": cmd}, timeout=30)["feedback"]

    def camera(self, x: float, y: float, z: float, yaw: float, pitch: float, hide_hud: bool = True,
               timeout: float = 60) -> dict:
        return self.call("/camera", {"x": x, "y": y, "z": z, "yaw": yaw, "pitch": pitch, "hide_hud": hide_hud,
                                     "timeout": timeout}, timeout=timeout + 30)

    def screenshot(self, name: str, width: int | None = None, height: int | None = None, frames: int = 10) -> dict:
        body = {"name": name, "frames": frames}
        if width and height:
            body.update(width=width, height=height)
        return self.call("/screenshot", body, timeout=90)

    def quit_world(self) -> dict:
        return self.call("/quit_world", {}, timeout=150)

    def shutdown(self) -> dict:
        return self.call("/shutdown", {}, timeout=15)


def write_options(run_dir: Path = RUN, opts: dict = OPTIONS) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    f = run_dir / "options.txt"
    cur = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition(":")
            if sep:
                cur[k] = v
    cur.update(opts)
    f.write_text("".join(f"{k}:{v}\n" for k, v in cur.items()), encoding="utf-8")
    return f


def _java_home() -> str:
    """Loom 1.19 needs Gradle on JDK >= 25: prefer BRIDGE_JAVA_HOME, else the `java` found first on PATH."""
    if os.environ.get("BRIDGE_JAVA_HOME"):
        return os.environ["BRIDGE_JAVA_HOME"]
    java = shutil.which("java")
    if not java:
        raise BridgeError("no java on PATH (need JDK >= 25 for Loom 1.19); set BRIDGE_JAVA_HOME")
    return str(Path(java).resolve().parent.parent)


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _tail(path: Path, n: int = 400) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


class Game:
    """Owns the `gradlew runClient` process. One instance per driver process."""

    def __init__(self, client: BridgeClient | None = None):
        self.client = client or BridgeClient()
        self.proc: subprocess.Popen | None = None
        self.game_pid: int | None = None

    def start(self, timeout: float = START_TIMEOUT) -> dict:
        t0 = time.time()
        if self.client.alive():
            return {"started": False, "already_running": True}
        write_options()
        TOKEN_FILE.unlink(missing_ok=True)  # a stale token would make us talk to nothing
        LOG_FILE.parent.mkdir(exist_ok=True)
        env = {**os.environ, "JAVA_HOME": _java_home()}
        log = open(LOG_FILE, "w", encoding="utf-8")
        cmd = [str(BRIDGE / ("gradlew.bat" if sys.platform == "win32" else "gradlew")),
               "--no-daemon", "--console=plain", "runClient"]
        self.proc = subprocess.Popen(cmd, cwd=BRIDGE, stdout=log, stderr=subprocess.STDOUT, env=env,
                                     stdin=subprocess.DEVNULL)
        log.close()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise BridgeError(f"runClient exited with {self.proc.returncode}; log tail: {_tail(LOG_FILE)}")
            if TOKEN_FILE.exists():
                try:
                    st = self.client.status()
                    if not st["loading"] and st.get("screen") == "TitleScreen":
                        self.game_pid = st.get("pid")
                        return {"started": True, "seconds": round(time.time() - t0, 1), "log": str(LOG_FILE)}
                except BridgeError:
                    pass
            time.sleep(2)
        self.stop()
        raise BridgeError(f"game did not reach the title screen in {timeout}s; see {LOG_FILE}")

    def stop(self, timeout: float = 90) -> dict:
        graceful = False
        try:
            st = self.client.status()
            self.game_pid = st.get("pid") or self.game_pid
            if st.get("in_world"):
                self.client.quit_world()
            self.client.shutdown()
            graceful = True
        except BridgeError:
            pass
        t0 = time.time()
        while time.time() - t0 < timeout:
            gone = self.proc is None or self.proc.poll() is not None
            if gone and not self.client.alive():
                break
            time.sleep(1)
        else:
            for pid in (self.game_pid, self.proc.pid if self.proc else None):
                if pid:
                    _kill_tree(pid)
            graceful = False
        if self.proc is not None:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _kill_tree(self.proc.pid)
        self.proc, self.game_pid = None, None
        TOKEN_FILE.unlink(missing_ok=True)
        return {"stopped": True, "graceful": graceful}


# ---- viewpoints -------------------------------------------------------------------------------------------------

def look_at(name: str, cam: tuple, target: tuple, **extra) -> dict:
    """Viewpoint at cam (x, y, z) looking at target. Minecraft yaw: 0 = +z (south), 90 = -x (west); pitch + = down."""
    cam, target = tuple(float(v) for v in cam), tuple(float(v) for v in target)
    dx, dy, dz = (t - c for t, c in zip(target, cam))
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = math.degrees(math.atan2(-dy, math.hypot(dx, dz)))
    y = min(max(cam[1], Y_MIN), Y_MAX)
    return {"name": name, "x": round(cam[0], 2), "y": round(y, 2), "z": round(cam[2], 2),
            "yaw": round(yaw, 1), "pitch": round(pitch, 1), **extra}


def _box_mean(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window (edge-padded), via summed-area table."""
    p = np.pad(a.astype(np.float64), r + 1, mode="edge")
    s = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    w = s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]
    return (w / (k * k))[: a.shape[0], : a.shape[1]]


def _near_mask(m: np.ndarray, r: int) -> np.ndarray:
    return _box_mean(m, r) > 0


def auto_viewpoints(p: dict, f: dict) -> list[dict]:
    """4-8 views from params + fields (arrays indexed [z, x], world block = array cell):
    overview, river, mountain->valley, coast, meadow (ground level, plants), lake + tributary (if lakes/streams),
    cave (if caves)."""
    h = np.asarray(f["height"], dtype=np.float64)
    L, W = h.shape
    wl = float(p["water_level"])
    m = int(min(p.get("edge_falloff", 0) + 8, min(W, L) // 4))
    zz, xx = np.mgrid[0:L, 0:W]
    interior = (xx >= m) & (xx < W - m) & (zz >= m) & (zz < L - m)
    cx, cz = W / 2, L / 2
    hc = float(h[int(cz), int(cx)])
    views = []

    dist = min(0.35 * max(W, L), 120.0)  # keep the far edge inside render distance (16 chunks)
    views.append(look_at("overview", (cx, hc + dist * math.tan(math.radians(60)), cz + dist), (cx, hc, cz)))

    rd = np.asarray(f["river_dist"]) if "river_dist" in f else None
    river = (rd < 1.0) & interior if rd is not None else np.zeros_like(interior)
    rz, rx = np.nonzero(river)
    if len(rx) >= 8:
        pts = np.stack([rx, rz], 1).astype(np.float64)
        mean = pts.mean(0)
        axis = np.linalg.svd(pts - mean, full_matrices=False)[2][0]
        proj = (pts - mean) @ axis
        order = np.argsort(proj)
        a, b = pts[order[int(0.15 * len(order))]], pts[order[int(0.6 * len(order))]]
        views.append(look_at("river", (a[0], wl + 14, a[1]), (b[0], wl, b[1])))

    hi = np.where(interior, h, -np.inf)
    pz, px = np.unravel_index(int(np.argmax(hi)), h.shape)
    if len(rx):
        i = int(np.argmin((rx - px) ** 2 + (rz - pz) ** 2))
        tx, tz = float(rx[i]), float(rz[i])
    else:
        tx, tz = cx, cz
    # stand back from the peak (away from the valley) and above its trees
    d = math.hypot(tx - px, tz - pz) or 1.0
    bx, bz = px - 12 * (tx - px) / d, pz - 12 * (tz - pz) / d
    views.append(look_at("mountain", (bx + 0.5, h[pz, px] + 16, bz + 0.5), (tx, float(h[int(tz), int(tx)]), tz)))

    edge_d = np.minimum.reduce([xx, W - 1 - xx, zz, L - 1 - zz])
    shore = (h >= wl) & (h <= wl + 2) & (edge_d <= m + 24)
    if shore.any():
        inland = np.where(shore, _box_mean(h, 12), -np.inf)
        sz, sx = np.unravel_index(int(np.argmax(inland)), h.shape)
        k = int(np.argmin([sx, W - 1 - sx, sz, L - 1 - sz]))
        ox, oz = ((-1, 0), (1, 0), (0, -1), (0, 1))[k]  # outward = towards the nearest map edge
        views.append(look_at("coast", (sx + 50 * ox, wl + 22, sz + 50 * oz), (sx, wl + 4, sz)))

    slope = np.asarray(f["slope"]) if "slope" in f else np.zeros_like(h)
    meadow = interior & (h > wl + 2) & (slope < 12)
    if rd is not None:
        meadow &= (rd > 12) & (rd < 40)
    mz, mx = np.nonzero(meadow)
    if len(mx):
        i = int(np.argmin((mx - cx) ** 2 + (mz - cz) ** 2))
        gx, gz = int(mx[i]), int(mz[i])
        if len(rx):
            j = int(np.argmin((rx - gx) ** 2 + (rz - gz) ** 2))
            tx, tz = float(rx[j]), float(rz[j])
        else:
            tx, tz = cx, cz
        views.append(look_at("meadow", (gx + 0.5, h[gz, gx] + 1.2, gz + 0.5),
                             (tx, float(h[gz, gx]) - 1, tz)))

    if "water_level" in f:  # Faz 9: the biggest lake, and an upper stream looking down towards the river
        lw = (np.asarray(f["water_level"]) > 0) & (np.floor(h + 0.5) < f["water_level"]) & interior
        lake = lw & (_box_mean(lw, 3) > 0.6)
        if lake.any():
            lz, lx = np.unravel_index(int(np.argmax(np.where(lake, _box_mean(lake, 8), -1))), h.shape)
            lv = float(f["water_level"][lz, lx])
            views.append(look_at("lake", (lx + 0.5, lv + 30, lz + 36.5), (lx + 0.5, lv, lz + 0.5)))  # over the trees
        stream = lw & ~_near_mask(lake, 6) & (np.asarray(f["water_level"]) > wl + 3)
        sz, sx = np.nonzero(stream)
        if len(sx) and len(rx):
            k = len(sx) // 2
            gx, gz = float(sx[k]), float(sz[k])
            j = int(np.argmin((rx - gx) ** 2 + (rz - gz) ** 2))
            tx, tz = float(rx[j]), float(rz[j])
            d = math.hypot(tx - gx, tz - gz) or 1.0
            cxs, czs = gx - 16 * (tx - gx) / d, gz - 16 * (tz - gz) / d
            ci, cj = int(np.clip(czs, 0, L - 1)), int(np.clip(cxs, 0, W - 1))
            views.append(look_at("tributary", (cxs, max(float(h[ci, cj]), float(h[int(gz), int(gx)])) + 28, czs),
                                 (gx + (tx - gx) * 0.4, wl + 2, gz + (tz - gz) * 0.4)))

    if p.get("caves", 0) > 0 and "caverns" in f:
        cav = np.where(interior & (h > wl + 4), np.asarray(f["caverns"], dtype=np.float64), -1)
        if cav.max() > 0:
            vz, vx = np.unravel_index(int(np.argmax(_box_mean(cav, 8))), h.shape)  # middle of a big patch
            y = float(h[vz, vx]) - 14
            views.append(look_at("cave", (vx + 0.5, y, vz + 0.5), (vx + 20.5, y - 6, vz + 0.5), night_vision=True))
    return views


def find_cave(world_dir: Path, x: float, z: float, r: int = 32) -> tuple | None:
    """Centre of the roomiest underground air pocket (>= 8 below the surface) within r blocks of (x, z), plus a
    point to look at (air centroid around it). Reads the exported world (read-only). None if no cave air."""
    from terrain_engine.edit_world import Y_MIN, columns, load_area
    a = load_area(Path(world_dir), int(x) - r, int(z) - r, int(x) + r, int(z) + r)
    ids, pal = a["ids"], a["pal"]
    air = pal.lut(lambda n: n.endswith(("minecraft:air", "cave_air")))[ids]
    top = columns(ids, pal)["top"] - Y_MIN
    ys = np.arange(ids.shape[0])[:, None, None]
    air &= (ys <= top[None] - 8) & (ys > 4)
    zs, xs = np.mgrid[0:ids.shape[1], 0:ids.shape[2]]  # load_area rounds out to whole chunks: keep to the window
    air &= ((np.abs(xs + a["gx0"] - int(x)) <= r) & (np.abs(zs + a["gz0"] - int(z)) <= r))[None]
    if not air.any():
        return None
    k = 4  # roominess = air count in a 9^3 window (summed-volume table)
    s = np.pad(air.astype(np.int32), ((k + 1, k), (k + 1, k), (k + 1, k))).cumsum(0).cumsum(1).cumsum(2)
    n = 2 * k + 1
    room = (s[n:, n:, n:] - s[:-n, n:, n:] - s[n:, :-n, n:] - s[n:, n:, :-n]
            + s[:-n, :-n, n:] + s[:-n, n:, :-n] + s[n:, :-n, :-n] - s[:-n, :-n, :-n])
    room = np.where(air, room, -1)
    y, zi, xi = np.unravel_index(int(np.argmax(room)), room.shape)
    near = np.argwhere(air[max(y - 6, 0):y + 7, max(zi - 16, 0):zi + 17, max(xi - 16, 0):xi + 17])
    cy, cz_, cx_ = near.mean(0) + [max(y - 6, 0), max(zi - 16, 0), max(xi - 16, 0)]
    if math.hypot(cx_ - xi, cz_ - zi) < 2:
        cx_ = xi + 8.0
    g = lambda vx, vy, vz: (float(vx) + a["gx0"] + 0.5, float(vy) + Y_MIN, float(vz) + a["gz0"] + 0.5)  # noqa: E731
    return g(xi, y, zi), g(cx_, cy, cz_)


def validate_views(views: list) -> list[dict]:
    if not isinstance(views, list) or not views or len(views) > 12:
        raise ValueError("views must be a list of 1-12 viewpoints")
    out = []
    for v in views:
        if not isinstance(v, dict):
            raise ValueError("each view must be an object {name, x, y, z, yaw, pitch}")
        name = str(v.get("name", f"view{len(out)}"))
        if not name.replace("_", "").replace("-", "").isalnum() or len(name) > 32:
            raise ValueError(f"bad view name {name!r} (letters, digits, '_' or '-', <= 32)")
        try:
            nums = {k: float(v[k]) for k in ("x", "y", "z")} | {k: float(v.get(k, 0)) for k in ("yaw", "pitch")}
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"view {name!r}: x, y, z (and optional yaw, pitch) must be numbers") from None
        if not all(math.isfinite(n) for n in nums.values()):
            raise ValueError(f"view {name!r}: non-finite number")
        out.append({"name": name, **nums, "night_vision": bool(v.get("night_vision", False))})
    return out


# ---- tour -------------------------------------------------------------------------------------------------------

def _prepare_save(world_dir: Path) -> str:
    """Copy the world into bridge/run/saves under a fresh name, with commands allowed. Returns the save name."""
    import nbtlib
    base = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in world_dir.name)[:40]
    name = f"{base}_{time.strftime('%Y%m%d_%H%M%S')}"
    dst = RUN / "saves" / name
    if dst.exists():
        raise FileExistsError(f"save already exists: {dst}")
    shutil.copytree(world_dir, dst, ignore=shutil.ignore_patterns("session.lock", "level.dat.bak"))
    lvl = nbtlib.load(dst / "level.dat")
    lvl["Data"]["allowCommands"] = nbtlib.Byte(1)
    lvl.save()
    return name


SETUP_CMDS = ("time set noon", "gamerule doDaylightCycle false", "weather clear", "gamerule doWeatherCycle false",
              "gamerule doMobSpawning false")


def tour(world_dir: str | Path, viewpoints: list[dict], out_dir: str | Path, client: BridgeClient | None = None,
         keep_save: bool = False) -> list[dict]:
    """Open a copy of world_dir in the running dev client, screenshot each viewpoint into out_dir, quit the world."""
    c = client or BridgeClient()
    world_dir, out_dir = Path(world_dir), Path(out_dir)
    if not (world_dir / "level.dat").exists():
        raise FileNotFoundError(f"no level.dat in {world_dir}")
    views = validate_views(viewpoints)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _prepare_save(world_dir)
    shots = []
    try:
        c.open_world(name)
        for cmd in SETUP_CMDS:
            c.command(cmd)
        for v in views:
            if v["night_vision"]:
                c.command("effect give @s minecraft:night_vision infinite 0 true")
            cam = c.camera(v["x"], v["y"], v["z"], v["yaw"], v["pitch"])
            shot = c.screenshot(f"{name}_{v['name']}")
            if v["night_vision"]:
                c.command("effect clear @s")
            dst = out_dir / f"{v['name']}.png"
            shutil.move(shot["path"], dst)  # don't let bridge/run/screenshots pile up
            shots.append({"view": v["name"], "path": str(dst), "settled": cam["settled"], "chunks": cam["chunks"],
                          "size": [shot["width"], shot["height"]]})
    finally:
        try:
            c.quit_world()
        except BridgeError:
            pass
        if not keep_save:
            shutil.rmtree(RUN / "saves" / name, ignore_errors=True)
    return shots


def tour_export(export_dir: str | Path, views: list | None = None, client: BridgeClient | None = None) -> list[dict]:
    """Tour an exports/<name>-<key> dir; screenshots go to <export>/previews/ingame/."""
    out = Path(export_dir).resolve()
    p = json.loads((out / "params.json").read_text(encoding="utf-8"))
    world = out / "world" / p["name"]
    if views is None:
        views = auto_viewpoints(p, dict(np.load(out / "fields.npz")))
        for i, v in enumerate(views):  # put the cave camera into a real air pocket of the exported world
            if v["name"] == "cave":
                hit = find_cave(world, v["x"], v["z"])
                views[i] = look_at("cave", *hit, night_vision=True) if hit else None
        views = [v for v in views if v]
    return tour(world, views, out / "previews" / "ingame", client=client)


if __name__ == "__main__":  # python agent/bridge_client.py <export_dir>  -> start, tour, stop
    sys.path.insert(0, str(ROOT))
    g = Game()
    started = g.start()
    try:
        print(json.dumps(started))
        print(json.dumps(tour_export(sys.argv[1]), indent=1))
    finally:
        print(json.dumps(g.stop()))
