"""Hydraulic (droplet) erosion as one global pass on a coarse grid.

Droplet erosion is order dependent and non-local, so it cannot run per tile.
Instead the whole map's pre-erosion heights are eroded once on a coarse grid
(cell_size() blocks per cell, growing with the map: droplets see the same number of
cells on any map size, so erosion is a fixed fraction of the relief); the result is a delta field that every tile samples bilinearly at its
global coordinates, so tiles still stitch bit-for-bit. Droplets run in batches,
vectorised with numpy (no numba needed); seeded, so the result is deterministic.
"""
import numpy as np

CELL = 2              # blocks per erosion cell on maps up to CELL_REF blocks
CELL_REF = 256        # map size (max side) at which the cell is CELL blocks; cell doubles per doubling
MAX_CELL = 16         # divides every legal map size (multiples of 16)
DROPS_PER_CELL = 2.0  # droplets per cell at hydraulic = 1
LIFETIME = 32         # max steps per droplet
INERTIA = 0.1
CAPACITY = 4.0
MIN_SLOPE = 0.05      # sediment still carried on near-flat ground -> fans, not flat fill
ERODE = 0.35
DEPOSIT = 0.1
DEATH_DEPOSIT = 0.3   # share of the load dropped where a droplet dies (rest = silt washed away)
EVAPORATE = 0.03
GRAVITY = 4.0
RAMP = 10             # steps until full capacity: water gathers downslope, crests barely erode
BRUSH = 2             # erosion brush radius, cells
BATCH_DIV = 8         # batch = cells / BATCH_DIV (fewer droplets writing the same cell at once)
BATCH_RELAX = 3       # thermal steps after each batch: gully walls slump as they form
RELAX_TALUS = 0.8     # angle of repose as a tangent (~39 deg) at CELL; lower for coarser cells (relax_talus)
TALUS_EXP = 0.7       # talus ~ cell^-0.7 (tuned: slopes drop at 512^2..4096^2)
FINE_TALUS = 0.6      # ~31 deg on the 2-block grid (maps <= 256^2; coarser grids unchanged)
FINE_RELAX = 4        # thermal steps per batch on the 2-block grid
HARD_FROM = 70.0      # height percentile where summit hardness starts
HARD_CAP = 0.8        # erodibility lost at the highest summit
SMOOTH = 2            # blur passes on the final delta (droplet-scale ripples)


def cell_size(width: int, length: int) -> int:
    """Blocks per erosion cell: CELL up to CELL_REF, then doubling with the map (power of two, <= MAX_CELL)."""
    c = CELL
    while c < MAX_CELL and max(width, length) > CELL_REF * c // CELL:
        c *= 2
    return c


def relax_talus(cell: int) -> float:
    """Coarse-grid angle of repose. A coarse cell hides more sub-cell relief (added back at block
    scale), so gully walls on coarser grids must stay gentler for block slopes to drop.
    On the finest grid (maps <= CELL_REF) walls are gentler still (FINE_TALUS): its p90 slope was above the
    hydraulic-off p90 (0.10.0: fantasy 256^2 57.2 vs 56.7)."""
    return FINE_TALUS if cell <= CELL else RELAX_TALUS * (CELL / cell) ** TALUS_EXP


def relax_steps(cell: int) -> int:
    return FINE_RELAX if cell <= CELL else BATCH_RELAX


def _brush(r):
    offs = [(dz, dx) for dz in range(-r, r + 1) for dx in range(-r, r + 1)]
    w = np.array([max(0.0, r + 0.5 - np.hypot(dz, dx)) for dz, dx in offs])
    keep = w > 0
    return np.array(offs)[keep], w[keep]


def _bilinear(H, ix, iz, u, v):
    h00, h10, h01, h11 = H[iz, ix], H[iz, ix + 1], H[iz + 1, ix], H[iz + 1, ix + 1]
    gx = (h10 - h00) * (1 - v) + (h11 - h01) * v
    gz = (h01 - h00) * (1 - u) + (h11 - h10) * u
    h = h00 * (1 - u) * (1 - v) + h10 * u * (1 - v) + h01 * (1 - u) * v + h11 * u * v
    return h, gx, gz


def erode(h: np.ndarray, strength: float, seed: int, erodibility: np.ndarray, relax=None,
          cell: int = CELL) -> np.ndarray:
    """Erode coarse heights h (blocks, `cell` blocks per cell); erodibility in [0, 1] scales
    how much each cell can lose. Returns the eroded copy."""
    rows, cols = h.shape
    H = h / cell  # cell units: height differences are slope tangents
    flat = H.reshape(-1)
    boffs, bw = _brush(BRUSH)
    bwn = bw / bw.sum()
    rng = np.random.Generator(np.random.PCG64(seed + 909))
    total = int(round(strength * DROPS_PER_CELL * rows * cols))
    batch = max(256, rows * cols // BATCH_DIV)
    lo, hi = H.min(), H.max()
    for start in range(0, total, batch):
        k = min(batch, total - start)
        x = rng.uniform(0, cols - 1, k)
        z = rng.uniform(0, rows - 1, k)
        dx = np.zeros(k)
        dz = np.zeros(k)
        speed = np.ones(k)
        water = np.ones(k)
        sed = np.zeros(k)
        for step in range(LIFETIME):
            ix = np.minimum(x.astype(np.int64), cols - 2)
            iz = np.minimum(z.astype(np.int64), rows - 2)
            u, v = x - ix, z - iz
            hc, gx, gz = _bilinear(H, ix, iz, u, v)
            dx = dx * INERTIA - gx * (1 - INERTIA)
            dz = dz * INERTIA - gz * (1 - INERTIA)
            norm = np.hypot(dx, dz)
            moving = norm > 1e-9
            norm = np.where(moving, norm, 1.0)
            dx, dz = dx / norm, dz / norm
            nx, nz = x + dx, z + dz
            inside = moving & (nx >= 0) & (nx < cols - 1) & (nz >= 0) & (nz < rows - 1)
            nxs, nzs = np.where(inside, nx, x), np.where(inside, nz, z)
            nix = np.minimum(nxs.astype(np.int64), cols - 2)
            niz = np.minimum(nzs.astype(np.int64), rows - 2)
            nh = _bilinear(H, nix, niz, nxs - nix, nzs - niz)[0]
            dh = np.where(inside, nh - hc, 0.0)
            cap = np.maximum(-dh, MIN_SLOPE) * speed * water * CAPACITY * min((step + 1) / RAMP, 1.0)
            up = dh > 0
            dep = np.where(up, np.minimum(dh, sed), np.maximum(sed - cap, 0.0) * DEPOSIT)
            dep = np.where(inside, dep, sed * DEATH_DEPOSIT)
            cx, cz = np.rint(x).astype(np.int64), np.rint(z).astype(np.int64)
            ero = np.where(inside & ~up & (sed < cap), np.minimum((cap - sed) * ERODE, -dh), 0.0) * erodibility[cz, cx]
            dep = np.where(ero > 0, 0.0, dep)
            # deposit on the 4 corners of the current cell, bilinear weights
            i0 = iz * cols + ix
            flat += np.bincount(np.concatenate([i0, i0 + 1, i0 + cols, i0 + cols + 1]),
                                np.concatenate([dep * (1 - u) * (1 - v), dep * u * (1 - v),
                                                dep * (1 - u) * v, dep * u * v]), flat.size)
            # erode with a radial brush around the nearest cell (avoids digging single-cell pits)
            bz = np.clip(cz[:, None] + boffs[None, :, 0], 0, rows - 1)
            bx = np.clip(cx[:, None] + boffs[None, :, 1], 0, cols - 1)
            flat -= np.bincount((bz * cols + bx).ravel(), (ero[:, None] * bwn[None, :]).ravel(), flat.size)
            sed = sed + ero - dep
            speed = np.sqrt(np.maximum(speed * speed - dh * GRAVITY, 0.0))
            water = water * (1 - EVAPORATE)
            keep = inside & (water > 0.05)
            if not keep.all():
                if not keep.any():
                    break
                x, z, dx, dz, speed, water, sed = (a[keep] for a in (nx, nz, dx, dz, speed, water, sed))
            else:
                x, z = nx, nz
        # droplets of one batch act in parallel; never let them dig/stack beyond the input range
        np.clip(H, lo, hi, out=H)
        if relax is not None:
            H[:] = relax(H, relax_steps(cell))  # slumped wall material is carried off by later droplets
    return H * cell
