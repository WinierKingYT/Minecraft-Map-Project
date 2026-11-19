"""Tributary streams and basin lakes (Faz 9).

One global pass on the coarse erosion grid (same grid and heights as the hydraulic pass): priority-flood the
heights towards the outlets (main river, sea band, map border) -> spill levels + a downhill flow tree. Basins
deeper than LAKE_MIN_DEPTH become lakes with a flat surface at their spill level; cells draining enough area
become streams, traced downhill until they reach the river, the sea or a stream already traced.
The noise + erosion heightfield has (almost) no closed basins (measured: 0 cells > 0.3 deep on the mountain
presets), so lake sites are scoured first: a smooth bowl on a flat bench along a drainage line (like a tarn);
the lake then fills it to its own spill level, so the water stays flat and lopsided bowls get one low shore.
Streams are
smoothed polylines in global block coordinates and every tile renders them (and the lake masks) pointwise,
so tiles still stitch bit-for-bit. Water surfaces are integers: `level` = y of the top water block (0 = none),
WorldPainter writes them per column (Dimension.setWaterLevelAt).
"""
import heapq

import numpy as np

EPS = 1e-3            # blocks added per flood step: flats drain, flow paths are strictly downhill
LAKE_MIN_DEPTH = 1.0  # blocks below the spill level
LAKE_MIN_AREA = 48    # blocks^2
LAKES_PER_256 = 2.0   # scoured lake sites per 256 x 256 blocks at lakes = 1 (sub-linear on bigger maps)
LAKE_SPACING = 64     # blocks between lake sites
LAKE_FLAT = 0.2       # max coarse slope (tangent) of a lake site
STREAM_AREA = (0.02, 0.004)  # drained area (map fraction) a stream starts from at tributaries = 0+ .. 1
STREAM_MIN_LEN = 24   # blocks; shorter head paths are dropped
INCISE = 1.0          # water surface below the filled (spill) height
HW_MIN, HW_MAX = 0.9, 2.4  # stream half width, blocks (grows with drained area)
BANK_MIN, BANK_K, BANK_MAX = 3.0, 2.0, 16.0  # bank width = 3 + 2 * height above water (gully walls <= ~37 deg)
_NB8 = [(dz, dx) for dz in (-1, 0, 1) for dx in (-1, 0, 1) if (dz, dx) != (0, 0)]


def _flood(E, outlet):
    """Priority flood from the outlet cells: filled heights F (>= E, strictly decreasing to an outlet)."""
    rows, cols = E.shape
    F = E.copy()
    done = outlet.copy()
    heap = [(float(E.flat[i]), int(i)) for i in np.flatnonzero(outlet)]
    heapq.heapify(heap)
    Ff = F.reshape(-1)
    df = done.reshape(-1)
    while heap:
        f, i = heapq.heappop(heap)
        z, x = divmod(i, cols)
        for dz, dx in _NB8:
            nz, nx = z + dz, x + dx
            if 0 <= nz < rows and 0 <= nx < cols:
                j = nz * cols + nx
                if not df[j]:
                    df[j] = True
                    Ff[j] = max(Ff[j], f + EPS)
                    heapq.heappush(heap, (Ff[j], j))
    return F


def _receivers(F, outlet):
    """Steepest-descent (D8) receiver of every cell (-1 for outlets)."""
    rows, cols = F.shape
    Fp = np.pad(F, 1, mode="constant", constant_values=np.inf)
    best = np.zeros(F.shape)
    rec = np.full(F.shape, -1, dtype=np.int64)
    zz, xx = np.mgrid[0:rows, 0:cols]
    for dz, dx in _NB8:
        drop = (F - Fp[1 + dz:1 + dz + rows, 1 + dx:1 + dx + cols]) / np.hypot(dz, dx)
        better = drop > best
        best = np.where(better, drop, best)
        rec = np.where(better, (zz + dz) * cols + xx + dx, rec)
    rec[outlet] = -1
    return rec.reshape(-1)


def _label(mask):
    """8-connected components: label grid (-1 = background) and count."""
    rows, cols = mask.shape
    lab = np.full(mask.shape, -1, dtype=np.int64)
    n = 0
    for start in np.flatnonzero(mask):
        if lab.flat[start] >= 0:
            continue
        stack = [int(start)]
        lab.flat[start] = n
        while stack:
            z, x = divmod(stack.pop(), cols)
            for dz, dx in _NB8:
                nz, nx = z + dz, x + dx
                if 0 <= nz < rows and 0 <= nx < cols and mask[nz, nx] and lab[nz, nx] < 0:
                    lab[nz, nx] = n
                    stack.append(nz * cols + nx)
        n += 1
    return lab, n


def _lakes(E, F, outlet, c, wl):
    """Lake surface level per coarse cell (0 = no lake): basins below their spill level."""
    depth = F - E
    lab, n = _label((depth > 0.3) & ~outlet)
    level = np.zeros(E.shape, dtype=np.int64)
    for k in range(n):
        cells = lab == k
        L = int(np.floor(F[cells].min()))
        core = cells & (E < L - 0.25)
        if L >= wl + 1 and core.sum() * c * c >= LAKE_MIN_AREA and (L - E[core]).max(initial=0) >= LAKE_MIN_DEPTH:
            level[core] = L
    return level


def _accumulate(F, rec):
    A = np.ones(F.size)
    for i in np.argsort(-F.reshape(-1), kind="stable"):
        if rec[i] >= 0:
            A[rec[i]] += A[i]
    return A


def bowls(sites, X, Z):
    """Depth of the scoured lake bowls at block coords: D * (1 - (d/R)^2)^2 inside R(angle), an irregular
    radius (two low harmonics with per-site phases), so shores are lobed, not circles."""
    out = np.zeros(np.shape(X))
    for x, z, r, d, a1, a2 in sites:
        th = np.arctan2(Z - z, X - x)
        R = r * (1 + 0.28 * np.sin(2 * th + a1) + 0.14 * np.sin(3 * th + a2))
        q = np.clip(1 - ((X - x) ** 2 + (Z - z) ** 2) / (R * R), 0, 1)
        out = np.maximum(out, d * q * q)
    return out


def _sites(E, A, c, ok, n, seed):
    """Lake sites: flat benches on drainage lines, greedy by drained area, LAKE_SPACING apart."""
    gz, gx = np.gradient(E / c)
    rng = np.random.Generator(np.random.PCG64(seed + 717))
    jitter = rng.uniform(0, 1, E.shape)
    cand = np.flatnonzero(ok & (np.hypot(gx, gz) < LAKE_FLAT))
    score = np.log(A[cand]) + 2 * jitter.flat[cand]
    sites, cols = [], E.shape[1]
    for i in cand[np.argsort(-score, kind="stable")]:
        if len(sites) >= n:
            break
        z, x = divmod(int(i), cols)
        bx, bz = x * c + (c - 1) / 2, z * c + (c - 1) / 2
        if all((bx - q[0]) ** 2 + (bz - q[1]) ** 2 >= LAKE_SPACING ** 2 for q in sites):
            u = float(jitter.flat[i])
            a1, a2 = (float(v) for v in rng.uniform(0, 2 * np.pi, 2))
            sites.append((bx, bz, max(14.0, 2.0 * c) * (0.8 + 0.5 * u), 3.0 + 2.0 * u, a1, a2))
    return sites


def _smooth(pts, passes=2):
    """5-point moving average along a path, endpoints fixed (removes the D8 zigzag)."""
    for _ in range(passes):
        if len(pts) < 5:
            break
        q = pts.copy()
        q[2:-2] = (pts[:-4] + pts[1:-3] + pts[2:-2] + pts[3:-1] + pts[4:]) / 5
        q[1] = (pts[0] + pts[1] + pts[2]) / 3
        q[-2] = (pts[-3] + pts[-2] + pts[-1]) / 3
        pts = q
    return pts


def network(E, c, outlet, inland, wl, tributaries, lakes, seed):
    """Global stream/lake network from coarse heights E (blocks, c blocks per cell); inland: cells where a lake
    site may go. Returns {"segs": (N, 8) [ax, az, bx, bz, ws_a, ws_b, hw_a, hw_b] in blocks, "lake": coarse
    levels, "sites": scoured bowls (x, z, r, depth), counts}."""
    rows, cols = E.shape
    sites = []
    if lakes > 0:
        F0 = _flood(E, outlet)
        n = max(1, int(round(lakes * LAKES_PER_256 * (rows * cols * c * c / 256 ** 2) ** 0.75)))
        sites = _sites(E, _accumulate(F0, _receivers(F0, outlet)), c, inland & (E > wl + 3), n, seed)
        X, Z = np.meshgrid(np.arange(cols) * c + (c - 1) / 2, np.arange(rows) * c + (c - 1) / 2)
        E = E - bowls(sites, X, Z)
    F = _flood(E, outlet)
    lake = _lakes(E, F, outlet, c, wl) if lakes > 0 else np.zeros(E.shape, dtype=np.int64)
    segs, n_streams = [], 0
    if tributaries > 0:
        rec = _receivers(F, outlet)
        A = _accumulate(F, rec)
        frac = STREAM_AREA[0] + (STREAM_AREA[1] - STREAM_AREA[0]) * tributaries
        thr = frac * rows * cols
        chan = (A >= thr) & ~outlet.reshape(-1)
        donors = np.bincount(rec[chan & (rec >= 0)], minlength=rows * cols)
        heads = np.flatnonzero(chan & (donors == 0))
        heads = heads[np.argsort(-F.reshape(-1)[heads], kind="stable")]
        seen = np.zeros(rows * cols, dtype=bool)
        Ff = F.reshape(-1)
        for h in heads:
            path, i = [int(h)], int(h)
            while True:
                j = int(rec[i])
                if j < 0:
                    break
                path.append(j)
                if outlet.flat[j] or seen[j]:
                    break
                i = j
            if (len(path) - 1) * c < STREAM_MIN_LEN:
                continue
            seen[path[:-1]] = True
            idx = np.array(path)
            zc, xc = np.divmod(idx, cols)
            pts = _smooth(np.stack([xc * c + (c - 1) / 2, zc * c + (c - 1) / 2], axis=1).astype(np.float64))
            ws = np.maximum(Ff[idx] - INCISE, wl)
            hw = HW_MIN + (HW_MAX - HW_MIN) * np.clip(np.log2(A[idx] / thr) / 4, 0, 1)
            run = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
            hw = hw * np.clip(0.55 + run / 32, 0, 1)  # springs start narrow
            segs.append(np.column_stack([pts[:-1], pts[1:], ws[:-1], ws[1:], hw[:-1], hw[1:]]))
            n_streams += 1
    _, n_lakes = _label(lake > 0)
    lp = np.pad(lake, 1)
    area = np.maximum.reduce([lp[1 + dz:1 + dz + rows, 1 + dx:1 + dx + cols] for dz, dx in _NB8 + [(0, 0)]])
    return {"segs": np.concatenate(segs) if segs else np.zeros((0, 8)), "lake": lake, "lake_area": area, "cell": c,
            "sites": sites, "streams": n_streams, "lakes": n_lakes}


def _lake_sample(lake, c, X, Z):
    """(indicator 0..1 bilinear, level of the nearest lake cell within 1 cell) at block coords."""
    rows, cols = lake.shape
    ind = (lake > 0).astype(np.float64)
    fx = np.clip((X - (c - 1) / 2) / c, 0, cols - 1)
    fz = np.clip((Z - (c - 1) / 2) / c, 0, rows - 1)
    ix = np.minimum(fx.astype(np.int64), cols - 2)
    iz = np.minimum(fz.astype(np.int64), rows - 2)
    u, v = fx - ix, fz - iz
    m = (ind[iz, ix] * (1 - u) * (1 - v) + ind[iz, ix + 1] * u * (1 - v)
         + ind[iz + 1, ix] * (1 - u) * v + ind[iz + 1, ix + 1] * u * v)
    lv = np.maximum.reduce([lake[iz, ix], lake[iz, ix + 1], lake[iz + 1, ix], lake[iz + 1, ix + 1]])
    return m, lv


def render(net, X, Z, h, wet):
    """Carve lakes and streams into block heights h (X, Z: global block coords, unit spacing; wet: columns already
    under the sea/river, never raised). Returns (new h, level): level = top water block y of lake/stream
    columns, 0 elsewhere."""
    level = np.zeros(h.shape, dtype=np.int64)
    c = net["cell"]
    x0, z0 = X[0, 0], Z[0, 0]
    rows, cols = h.shape
    near_sites = [q for q in net["sites"]
                  if x0 - 1.5 * q[2] <= q[0] <= x0 + cols + 1.5 * q[2] and z0 - 1.5 * q[2] <= q[1] <= z0 + rows + 1.5 * q[2]]
    h = np.where(wet, h, h - bowls(near_sites, X, Z))
    # shoreline = the block-level contour of the lake level inside the (one cell wider) coarse lake area
    m, lv = _lake_sample(net["lake_area"], c, X, Z)
    core = (m > 0.5) & (lv > 0) & (h < lv - 0.5)
    rim = (m > 0.02) & (lv > 0) & ~core & ~wet & (h < lv + 0.6)
    h = np.where(core, np.minimum(h, lv - 1.0), np.where(rim, lv + 0.6, h))  # >= 1 deep; rim above the water
    level[core] = lv[core]

    segs = net["segs"]
    if len(segs):
        R = HW_MAX + BANK_MAX + 1
        lo_x, hi_x = np.minimum(segs[:, 0], segs[:, 2]) - R, np.maximum(segs[:, 0], segs[:, 2]) + R
        lo_z, hi_z = np.minimum(segs[:, 1], segs[:, 3]) - R, np.maximum(segs[:, 1], segs[:, 3]) + R
        near = (hi_x >= x0) & (lo_x <= x0 + cols - 1) & (hi_z >= z0) & (lo_z <= z0 + rows - 1)
        key = np.full(h.shape, np.inf)
        dist = np.zeros(h.shape)
        hw = np.zeros(h.shape)
        ws = np.zeros(h.shape)
        for s, a, b, d in zip(segs[near], lo_x[near], hi_x[near], zip(lo_z[near], hi_z[near])):
            c0, c1 = max(int(np.ceil(a - x0)), 0), min(int(np.floor(b - x0)) + 1, cols)
            r0, r1 = max(int(np.ceil(d[0] - z0)), 0), min(int(np.floor(d[1] - z0)) + 1, rows)
            if c0 >= c1 or r0 >= r1:
                continue
            px, pz = X[r0:r1, c0:c1], Z[r0:r1, c0:c1]
            vx, vz = s[2] - s[0], s[3] - s[1]
            t = np.clip(((px - s[0]) * vx + (pz - s[1]) * vz) / max(vx * vx + vz * vz, 1e-9), 0, 1)
            dd = np.hypot(px - s[0] - t * vx, pz - s[1] - t * vz)
            ww = s[6] + t * (s[7] - s[6])
            k = dd - ww
            win = (slice(r0, r1), slice(c0, c1))
            better = k < key[win]
            key[win] = np.where(better, k, key[win])
            dist[win] = np.where(better, dd, dist[win])
            hw[win] = np.where(better, ww, hw[win])
            ws[win] = np.where(better, s[4] + t * (s[5] - s[4]), ws[win])
        wsf = np.floor(ws)
        bank = np.clip(BANK_MIN + BANK_K * (h - wsf), BANK_MIN, BANK_MAX)
        zone = (key < bank) & ~core
        t = np.clip(key / bank, 0, 1)
        sm = t * t * (3 - 2 * t)
        hb = np.where(h > wsf + 1, wsf + 1 + (h - wsf - 1) * sm, h)  # streams only cut down ...
        levee = (key >= 0) & (key < 1.5) & ~wet  # ... except a low rim on dry ground beside the water
        hb = np.where(levee, np.maximum(hb, wsf + 0.6), hb)
        bed = wsf - 1.0 - 0.8 * (1 - np.clip(dist / np.maximum(hw, 1e-6), 0, 1) ** 2)
        hb = np.where(key < 0, np.minimum(hb, bed), hb)
        h = np.where(zone, hb, h)
        sc = (key < 0) & ~core
        level[sc] = np.maximum(level[sc], wsf[sc].astype(np.int64))
    return h, level
