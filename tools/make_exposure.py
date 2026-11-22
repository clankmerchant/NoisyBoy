"""Offline: build arena/exposure.npy (4 m grid, [j, i], 0 = hidden from everywhere, 1 = fully exposed).

For each open cell, the fraction of observer points (32 bearings x 4 ranges) that can see a mech
standing there. Usage: python3 tools/make_exposure.py <arena_dir> <out.npy>
"""
import math
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from world import World  # noqa: E402

STEP = 4.0
RANGES = (30.0, 60.0, 100.0, 150.0)
NDIR = 32


def main(arena, out):
    t0 = time.time()
    w = World(arena)
    nx4 = int(w.nx // STEP) + 1
    ny4 = int(w.ny // STEP) + 1
    xs = w.minx + STEP * np.arange(nx4)
    ys = w.miny + STEP * np.arange(ny4)
    X, Y = np.meshgrid(xs, ys)
    X = X.ravel()
    Y = Y.ravel()

    def idx(x, y):
        i = np.clip(np.rint(x - w.minx).astype(np.int32), 0, w.nx - 1)
        j = np.clip(np.rint(y - w.miny).astype(np.int32), 0, w.ny - 1)
        return j, i

    j, i = idx(X, Y)
    # usable = standable for the bot: on its (wall-inflated) nav grid, not just outside buildings
    nav = w.nav_np
    gi = np.clip(np.rint((X - w.minx) / 2.0).astype(np.int32), 0, w.NW - 1)
    gj = np.clip(np.rint((Y - w.miny) / 2.0).astype(np.int32), 0, w.NH - 1)
    open_ = nav[gj, gi]
    gz = w.surface[j, i]
    vis = np.zeros(X.shape, dtype=np.float32)
    cnt = np.zeros(X.shape, dtype=np.float32)
    sel = np.nonzero(open_)[0]
    tx, ty, tz = X[sel], Y[sel], gz[sel] + 6.0
    for k in range(NDIR):
        a = k * 2 * math.pi / NDIR
        ca, sa = math.cos(a), math.sin(a)
        for r in RANGES:
            ox, oy = tx + r * ca, ty + r * sa
            inmap = (ox > w.minx + 4) & (ox < w.minx + w.nx - 5) & (oy > w.miny + 4) & (oy < w.miny + w.ny - 5)
            oj, oi = idx(ox, oy)
            valid = inmap & ~w.blocked1[oj, oi]
            oz = w.surface[oj, oi] + 8.0
            n = int((r - 3.0) / 1.5) + 1
            seen = valid.copy()
            for s in range(n):
                f = (1.5 + s * 1.5) / r
                px = ox + (tx - ox) * f
                py = oy + (ty - oy) * f
                pz = oz + (tz - oz) * f
                pj, pi = idx(px, py)
                blk = (pz < w.roof[pj, pi]) | (pz < w.ground[pj, pi] - 0.3) | \
                      ((pz > w.bbase[pj, pi]) & (pz < w.btop[pj, pi]))
                seen &= ~blk
            vis[sel] += seen
            cnt[sel] += valid
    e = np.ones(X.shape, dtype=np.float32)
    has = cnt > 0
    e[has] = vis[has] / cnt[has]
    e = np.minimum(e, 0.998)  # open cells stay below the bot's 0.999 "unusable" cut
    e[~open_] = 1.0
    e = e.reshape(ny4, nx4)
    np.save(out, e)
    print(f"exposure {e.shape} open={open_.mean():.2f} mean={e[e < 1].mean():.3f} "
          f"p10={np.percentile(e[e < 1], 10):.3f} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
