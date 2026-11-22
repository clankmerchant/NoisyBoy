"""Static map model: terrain, buildings, line of sight and A* navigation."""
import heapq
import math
import sys
import time

import numpy as np

from precompute import dilate, rasterize

SQ2 = math.sqrt(2.0)


class World:
    def __init__(self, arena_dir: str):
        import json

        t0 = time.time()
        w = json.load(open(f"{arena_dir}/v2/world.json"))
        t = w["terrain"]
        self.nx, self.ny = int(t["nx"]), int(t["ny"])
        self.minx, self.miny = float(t["minX"]), float(t["minY"])
        nx, ny = self.nx, self.ny
        ground = np.fromfile(f"{arena_dir}/v2/terrain.i16", dtype="<i2").reshape(ny, nx).astype(np.float32) * float(t.get("scale", 0.01))
        roof = np.full((ny, nx), -1000.0, dtype=np.float32)
        bbase = np.full((ny, nx), 1000.0, dtype=np.float32)
        btop = np.full((ny, nx), -1000.0, dtype=np.float32)
        for p in w["prisms"]:
            r = rasterize(p["ring"], p.get("holes"), self.minx, self.miny, nx, ny)
            if r is None:
                continue
            j0, j1, i0, i1, m = r
            if p["kind"] == "bridge":
                sb = bbase[j0:j1, i0:i1]
                st = btop[j0:j1, i0:i1]
                sb[m] = np.minimum(sb[m], p["base"])
                st[m] = np.maximum(st[m], p["top"])
            else:
                sr = roof[j0:j1, i0:i1]
                sr[m] = np.maximum(sr[m], p["top"])
        self.ground = ground
        self.roof = roof
        self.bbase = bbase
        self.btop = btop
        self.surface = np.maximum(ground, roof)
        solid = roof > ground + 1.2
        lowbridge = (btop > -999) & (bbase < ground + 9.5) & (btop > ground + 1.2)
        blocked = solid | lowbridge
        gy, gx = np.gradient(ground)
        blocked |= np.hypot(gx, gy) > math.tan(math.radians(47))
        blocked[:4, :] = True
        blocked[-4:, :] = True
        blocked[:, :4] = True
        blocked[:, -4:] = True
        self.blocked1 = blocked
        infl = dilate(blocked, 3.3)
        nav = ~infl[::2, ::2]
        self.NW = nav.shape[1]
        self.NH = nav.shape[0]
        self.nav_np = nav
        self.nav = bytearray(nav.astype(np.uint8).ravel().tobytes())
        # a softer cost: cells hugging walls cost a bit more so paths keep clear of corners
        near = dilate(blocked, 5.5)[::2, ::2]
        self.navcost = bytearray((near & nav).astype(np.uint8).ravel().tobytes())
        print(f"world loaded in {time.time() - t0:.2f}s", file=sys.stderr, flush=True)

    # ---------- height queries ----------
    def _ij(self, x, y):
        i = int(round(x - self.minx))
        j = int(round(y - self.miny))
        if i < 0:
            i = 0
        elif i >= self.nx:
            i = self.nx - 1
        if j < 0:
            j = 0
        elif j >= self.ny:
            j = self.ny - 1
        return i, j

    def ground_at(self, x, y):
        i, j = self._ij(x, y)
        return float(self.ground[j, i])

    def surface_at(self, x, y):
        i, j = self._ij(x, y)
        return float(self.surface[j, i])

    def is_solid(self, x, y):
        i, j = self._ij(x, y)
        return bool(self.blocked1[j, i])

    # ---------- line of sight (buildings, terrain, bridges; trees ignored) ----------
    def los(self, a, b, step=1.0, skip=1.5):
        ax, ay, az = a
        bx, by, bz = b
        dx, dy, dz = bx - ax, by - ay, bz - az
        L = math.sqrt(dx * dx + dy * dy + dz * dz)
        if L < 2 * skip + 0.5:
            return True
        n = int((L - 2 * skip) / step) + 1
        ts = (skip + np.arange(n) * step) / L
        xs = ax + ts * dx
        ys = ay + ts * dy
        zs = az + ts * dz
        i = np.rint(xs - self.minx).astype(np.int32)
        j = np.rint(ys - self.miny).astype(np.int32)
        np.clip(i, 0, self.nx - 1, out=i)
        np.clip(j, 0, self.ny - 1, out=j)
        if (zs < self.roof[j, i]).any():
            return False
        if (zs < self.ground[j, i] - 0.3).any():
            return False
        if ((zs > self.bbase[j, i]) & (zs < self.btop[j, i])).any():
            return False
        return True

    # ---------- navigation on a 2 m grid ----------
    def cell(self, x, y):
        gi = int(round((x - self.minx) / 2.0))
        gj = int(round((y - self.miny) / 2.0))
        gi = min(max(gi, 0), self.NW - 1)
        gj = min(max(gj, 0), self.NH - 1)
        return gj * self.NW + gi

    def cell_xy(self, c):
        return (self.minx + 2.0 * (c % self.NW), self.miny + 2.0 * (c // self.NW))

    def open_at(self, x, y):
        return self.nav[self.cell(x, y)] == 1

    def nearest_open(self, x, y, maxr=30):
        c = self.cell(x, y)
        if self.nav[c]:
            return c
        W = self.NW
        ci, cj = c % W, c // W
        for r in range(1, maxr + 1):
            best = None
            bd = 1e9
            for dj in range(-r, r + 1):
                for di in (-r, r) if abs(dj) != r else range(-r, r + 1):
                    i, j = ci + di, cj + dj
                    if 0 <= i < W and 0 <= j < self.NH and self.nav[j * W + i]:
                        d = di * di + dj * dj
                        if d < bd:
                            bd = d
                            best = j * W + i
            if best is not None:
                return best
        return None

    def clear_line(self, a, b):
        """Straight walk from a to b stays on open nav cells."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        n = int(L / 1.0) + 1
        nav = self.nav
        for k in range(1, n + 1):
            t = k / n
            if not nav[self.cell(a[0] + dx * t, a[1] + dy * t)]:
                return False
        return True

    def astar(self, sx, sy, gx, gy, max_exp=3000, weight=1.4, budget_s=0.012):
        s = self.nearest_open(sx, sy)
        g = self.nearest_open(gx, gy)
        if s is None or g is None:
            return None
        W = self.NW
        nav = self.nav
        cost = self.navcost
        gi, gj = g % W, g // W

        def h(c):
            di = abs(c % W - gi)
            dj = abs(c // W - gj)
            return (di + dj + (SQ2 - 2) * min(di, dj)) * weight

        nbrs = ((1, 1.0), (-1, 1.0), (W, 1.0), (-W, 1.0), (W + 1, SQ2), (W - 1, SQ2), (-W + 1, SQ2), (-W - 1, SQ2))
        gs = {s: 0.0}
        came = {s: -1}
        closed = set()
        heap = [(h(s), s)]
        best, besth = s, h(s)
        exp = 0
        push, pop = heapq.heappush, heapq.heappop
        N = len(nav)
        deadline = time.perf_counter() + budget_s
        while heap and exp < max_exp:
            if (exp & 255) == 255 and time.perf_counter() > deadline:
                break
            f, n = pop(heap)
            if n in closed:
                continue
            if n == g:
                best = g
                break
            closed.add(n)
            exp += 1
            hn = f - gs[n]
            if hn < besth:
                besth, best = hn, n
            gn = gs[n]
            for d, c in nbrs:
                m = n + d
                if m < 0 or m >= N or not nav[m] or m in closed:
                    continue
                ng = gn + c + (0.6 * c if cost[m] else 0.0)
                if ng < gs.get(m, 1e18):
                    gs[m] = ng
                    came[m] = n
                    push(heap, (ng + h(m), m))
        path = []
        n = best
        while n != -1:
            path.append(self.cell_xy(n))
            n = came[n]
        path.reverse()
        return path, best == g
