"""Grid helpers for world.py: polygon rasterization and disk dilation (numpy only).

Grid convention (matches world.py): cell (i, j) is the terrain vertex at
x = minx + i, y = miny + j (1 m step), arrays are indexed [j, i].
"""
import math

import numpy as np


def _inside(xs, ys, flat):
    """Even-odd point-in-polygon for grids xs (1, W) and ys (H, 1). flat = [x0, y0, x1, y1, ...]."""
    n = len(flat) // 2
    res = np.zeros((ys.shape[0], xs.shape[1]), dtype=bool)
    if n < 3:
        return res
    px = flat[0::2]
    py = flat[1::2]
    for k in range(n):
        x1, y1 = px[k], py[k]
        x2, y2 = px[(k + 1) % n], py[(k + 1) % n]
        if y1 == y2:
            continue
        # edges that straddle the row's y (half-open so shared vertices count once)
        cond = (y1 > ys) != (y2 > ys)  # (H, 1)
        if not cond.any():
            continue
        xint = x1 + (ys - y1) * (x2 - x1) / (y2 - y1)  # (H, 1)
        res ^= cond & (xs < xint)
    return res


def rasterize(ring, holes, minx, miny, nx, ny):
    """Cells whose vertex lies inside `ring` and outside every hole.

    Returns (j0, j1, i0, i1, mask) with mask shaped (j1 - j0, i1 - i0), or None if empty/off-map.
    `holes` may be None, a flat list, or a list of flat lists.
    """
    if not ring or len(ring) < 6:
        return None
    px = ring[0::2]
    py = ring[1::2]
    i0 = max(0, int(math.floor(min(px) - minx)))
    i1 = min(nx, int(math.ceil(max(px) - minx)) + 1)
    j0 = max(0, int(math.floor(min(py) - miny)))
    j1 = min(ny, int(math.ceil(max(py) - miny)) + 1)
    if i1 <= i0 or j1 <= j0:
        return None
    xs = (minx + np.arange(i0, i1, dtype=np.float64))[None, :]
    ys = (miny + np.arange(j0, j1, dtype=np.float64))[:, None]
    m = _inside(xs, ys, ring)
    if holes:
        hl = holes if isinstance(holes[0], (list, tuple)) else [holes]
        for h in hl:
            if h and len(h) >= 6:
                m &= ~_inside(xs, ys, h)
    if not m.any():
        return None
    return j0, j1, i0, i1, m


def dilate(mask, r):
    """Boolean dilation of a 2-D mask by a disk of radius r cells."""
    R = int(math.floor(r))
    H, W = mask.shape
    out = mask.copy()
    pad = np.zeros((H + 2 * R, W + 2 * R), dtype=bool)
    pad[R:R + H, R:R + W] = mask
    r2 = r * r
    for dj in range(-R, R + 1):
        for di in range(-R, R + 1):
            if (di or dj) and di * di + dj * dj <= r2:
                out |= pad[R + dj:R + dj + H, R + di:R + di + W]
    return out
