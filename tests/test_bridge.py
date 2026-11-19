"""Bridge driver tests: viewpoints, token/loopback enforcement, error handling (fake HTTP server).
The real game test runs only with RUN_GAME=1 (launches the Loom dev client, a few minutes)."""
import json
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from agent import bridge_client as B

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "f" * 32


class _Fake(BaseHTTPRequestHandler):
    """Mimics the mod: token header required, JSON envelope with ok/error."""

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.seen.append((self.command, self.path, dict(self.headers), body))
        if self.headers.get(B.TOKEN_HEADER) != TOKEN:
            return self._reply(401, {"ok": False, "error": "missing or bad token"})
        if self.path == "/status":
            return self._reply(200, {"ok": True, "screen": "TitleScreen", "loading": False, "in_world": False, "pid": 1})
        if self.path == "/command":
            return self._reply(200, {"ok": True, "feedback": [f"ran {body['cmd']}"]})
        if self.path == "/broken":
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nnot")
            return None
        if self.path == "/slow":
            threading.Event().wait(2)
            return self._reply(200, {"ok": True})
        return self._reply(404, {"ok": False, "error": f"unknown endpoint {self.path}"})

    do_GET = do_POST = _handle


@pytest.fixture()
def fake(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = tmp_path / "bridge_token.txt"
    tok.write_text(TOKEN + "\n25580\n", encoding="ascii")
    yield srv, B.BridgeClient(port=srv.server_address[1], token_file=tok, timeout=5)
    srv.shutdown()


def test_client_sends_token_and_parses(fake):
    srv, c = fake
    assert c.status()["screen"] == "TitleScreen"
    assert c.command("/time set noon") == ["ran /time set noon"]
    method, path, headers, body = srv.seen[-1]
    assert (method, path, body) == ("POST", "/command", {"cmd": "/time set noon"})
    assert headers[B.TOKEN_HEADER] == TOKEN


def test_bad_token_is_rejected(fake, tmp_path):
    srv, c = fake
    c.token_file.write_text("0" * 32, encoding="ascii")
    with pytest.raises(B.BridgeError, match="401"):
        c.status()
    assert c.alive() is False


def test_missing_token_file(tmp_path):
    c = B.BridgeClient(token_file=tmp_path / "nope.txt")
    with pytest.raises(B.BridgeError, match="not running"):
        c.status()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_only_loopback_hosts(host):
    with pytest.raises(ValueError, match="loopback"):
        B.BridgeClient(host=host)


def test_errors_are_bridge_errors(fake, tmp_path):
    srv, c = fake
    with pytest.raises(B.BridgeError, match="404 unknown endpoint"):
        c.call("/nothing", {})
    with pytest.raises(B.BridgeError, match="invalid JSON|not reachable"):
        c.call("/broken")
    with pytest.raises(B.BridgeError, match="not reachable"):
        c.call("/slow", timeout=0.3)
    port = srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    dead = B.BridgeClient(port=port, token_file=c.token_file, timeout=1)
    with pytest.raises(B.BridgeError, match="not reachable"):
        dead.status()


def test_token_never_in_error_text(fake):
    srv, c = fake
    for path in ("/nothing", "/slow"):
        with pytest.raises(B.BridgeError) as e:
            c.call(path, {}, timeout=0.3)
        assert TOKEN not in str(e.value)


def test_look_at_yaw_pitch():
    v = B.look_at("s", (0, 100, 0), (0, 90, 10))  # south (+z), 45 deg down
    assert v["yaw"] == 0 and v["pitch"] == 45
    assert B.look_at("w", (0, 64, 0), (-5, 64, 0))["yaw"] == 90  # west
    assert B.look_at("n", (0, 64, 0), (0, 74, -10))["pitch"] == -45  # up
    assert B.look_at("hi", (0, 999, 0), (0, 0, 1))["y"] == B.Y_MAX


def _fields(n=128):
    zz, xx = np.mgrid[0:n, 0:n]
    river_x = n / 2 + 6 * np.sin(zz / 15)
    rd = np.abs(xx - river_x)
    h = 62 + np.minimum(rd, 60) * 0.8
    h[:, :10] = 55  # sea along the west edge, then a beach
    h[:, 10:13] = 63
    h[30, 90] = 140  # peak
    cav = np.zeros((n, n))
    cav[64, 100] = 8
    return {"height": h.astype(np.float32), "river_dist": rd.astype(np.float32),
            "slope": np.full((n, n), 5, np.float32), "caverns": cav}


def test_auto_viewpoints_cover_features():
    f = _fields()
    p = {"water_level": 62, "edge_falloff": 10, "caves": 0.3}
    views = B.auto_viewpoints(p, f)
    names = [v["name"] for v in views]
    assert names == ["overview", "river", "mountain", "coast", "meadow", "cave"]
    by = {v["name"]: v for v in views}
    assert by["overview"]["pitch"] == 60 and by["overview"]["y"] > 140
    assert by["mountain"]["y"] > 150 and math.hypot(by["mountain"]["x"] - 90.5, by["mountain"]["z"] - 30.5) < 14
    assert by["mountain"]["pitch"] > 0  # looking down from behind the peak
    assert abs(by["river"]["x"] - 64) < 8 and by["river"]["pitch"] > 0  # on the river, looking down-stream
    cx, cz = int(by["coast"]["x"]), int(by["coast"]["z"])
    offshore = not (0 <= cx < 128 and 0 <= cz < 128) or f["height"][cz, cx] < 62
    assert offshore and by["coast"]["pitch"] > 0  # over the sea, looking down at the shore
    assert by["cave"]["night_vision"] and by["cave"]["y"] < f["height"][64, 100]
    assert all(math.isfinite(v[k]) and type(v[k]) is float for v in views for k in ("x", "y", "z", "yaw", "pitch"))
    assert B.validate_views(views)[0]["name"] == "overview"


def test_auto_viewpoints_without_river_or_caves():
    f = _fields()
    del f["river_dist"]
    views = B.auto_viewpoints({"water_level": 62, "caves": 0}, f)
    names = {v["name"] for v in views}
    assert {"overview", "mountain"} <= names and "river" not in names and "cave" not in names and len(views) >= 3


@pytest.mark.parametrize("views", [[], "x", [{"name": "a b", "x": 0, "y": 0, "z": 0}], [{"x": "1", "y": None, "z": 0}],
                                   [{"x": float("nan"), "y": 0, "z": 0}], [{"x": 0, "y": 0, "z": 0}] * 13])
def test_validate_views_rejects(views):
    with pytest.raises(ValueError):
        B.validate_views(views)


def test_write_options_merges(tmp_path):
    (tmp_path / "options.txt").write_text("version:4189\nrenderDistance:2\n", encoding="utf-8")
    text = B.write_options(tmp_path).read_text(encoding="utf-8")
    assert "version:4189" in text and "renderDistance:16" in text and "pauseOnLostFocus:false" in text


def test_tour_rejects_missing_world(tmp_path):
    with pytest.raises(FileNotFoundError):
        B.tour(tmp_path, [{"x": 0, "y": 0, "z": 0}], tmp_path / "out", client=object())


@pytest.mark.skipif(os.environ.get("RUN_GAME") != "1", reason="launches Minecraft; set RUN_GAME=1")
def test_real_game_tour():
    exports = sorted((ROOT / "exports").glob("fantasy_valley_256-*/world/fantasy_valley_256/level.dat"),
                     key=lambda p: p.stat().st_mtime)
    if not exports:
        pytest.skip("no fantasy_valley_256 export")
    g = B.Game()
    g.start()
    try:
        bad = B.BridgeClient(token_file=ROOT / "tests" / "_no_token.txt")
        tok = ROOT / "logs" / "_bad_token.txt"
        tok.write_text("0" * 32, encoding="ascii")
        with pytest.raises(B.BridgeError, match="401"):
            B.BridgeClient(token_file=tok).status()
        tok.unlink()
        with pytest.raises(B.BridgeError):
            bad.status()
        shots = B.tour_export(exports[-1].parent.parent.parent)
        assert len(shots) >= 4 and all(Path(s["path"]).stat().st_size > 50_000 for s in shots)
    finally:
        g.stop()


def test_find_cave_in_real_export():
    exports = [p.parent for p in (ROOT / "exports").glob("fantasy_valley_256-*/fields.npz")
               if (p.parent / "world" / "fantasy_valley_256" / "region").is_dir()]
    if not exports:
        pytest.skip("no fantasy_valley_256 export")
    out = max(exports, key=lambda p: p.stat().st_mtime)
    p = json.loads((out / "params.json").read_text(encoding="utf-8"))
    cave = [v for v in B.auto_viewpoints(p, dict(np.load(out / "fields.npz"))) if v["name"] == "cave"]
    if not cave:
        pytest.skip("export has no caves")
    (x, y, z), target = B.find_cave(out / "world" / p["name"], cave[0]["x"], cave[0]["z"])
    assert -60 < y < 60 and abs(x - cave[0]["x"]) <= 33 and abs(z - cave[0]["z"]) <= 33 and target != (x, y, z)
