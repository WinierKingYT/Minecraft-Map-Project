"""Vectorized, seeded 2D gradient noise (Perlin) + fBm / ridged / domain warp.

All functions take *global* block coordinates, so any tile of a map evaluates
to exactly the same values as the full map (needed for chunked generation).
"""
import numpy as np

_GRADS = np.array([[np.cos(a), np.sin(a)] for a in np.linspace(0, 2 * np.pi, 16, endpoint=False)])


def _perm(seed: int) -> np.ndarray:
    p = np.random.Generator(np.random.PCG64(seed & 0xFFFFFFFF)).permutation(256)
    return np.concatenate([p, p]).astype(np.int64)


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def perlin(x: np.ndarray, z: np.ndarray, seed: int) -> np.ndarray:
    """Single octave gradient noise, roughly in [-1, 1]."""
    p = _perm(seed)
    xi0 = np.floor(x).astype(np.int64)
    zi0 = np.floor(z).astype(np.int64)
    xf, zf = x - xi0, z - zi0
    xi, zi = xi0 & 255, zi0 & 255
    xi1, zi1 = (xi + 1) & 255, (zi + 1) & 255

    def dot(ix, iz, dx, dz):
        g = _GRADS[p[p[ix] + iz] & 15]
        return g[..., 0] * dx + g[..., 1] * dz

    u, v = _fade(xf), _fade(zf)
    n00 = dot(xi, zi, xf, zf)
    n10 = dot(xi1, zi, xf - 1, zf)
    n01 = dot(xi, zi1, xf, zf - 1)
    n11 = dot(xi1, zi1, xf - 1, zf - 1)
    nx0 = n00 + u * (n10 - n00)
    nx1 = n01 + u * (n11 - n01)
    return (nx0 + v * (nx1 - nx0)) * 1.41421356


def fbm(x, z, seed, octaves=5, lacunarity=2.0, gain=0.5):
    """Fractional Brownian motion, normalized to roughly [-1, 1]."""
    total = np.zeros_like(x, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for o in range(octaves):
        total += amp * perlin(x * freq, z * freq, seed + 1013 * o)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def ridged(x, z, seed, octaves=4, lacunarity=2.0, gain=0.5, sharpness=1.0):
    """Ridged multifractal in [0, 1]. Low sharpness -> rounded ridges (no spikes)."""
    total = np.zeros_like(x, dtype=np.float64)
    amp, freq, norm, weight = 1.0, 1.0, 0.0, np.ones_like(x, dtype=np.float64)
    for o in range(octaves):
        n = 1.0 - np.abs(perlin(x * freq, z * freq, seed + 7919 * o))
        n = n ** (1.0 + sharpness)
        n *= weight
        weight = np.clip(n * 1.5, 0, 1)
        total += amp * n
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def warp(x, z, seed, scale, strength):
    """Domain warp: returns displaced coordinates (organic, non-grid shapes)."""
    if np.all(strength <= 0):  # strength may be a per-cell array (regions)
        return x, z
    wx = fbm(x / scale, z / scale, seed + 31, octaves=3)
    wz = fbm(x / scale + 5.2, z / scale + 1.3, seed + 57, octaves=3)
    return x + wx * strength, z + wz * strength
