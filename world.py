"""Static map model: terrain heights, clearance-inflated grid, 3D line of sight, bounded A* pathing."""
import heapq
import json
import math
import sys
import time
from array import array

CELL = 3.0

_NB = (
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142),
)
# wall-proximity penalty indexed by self.near value (0 = open, 1 = two cells from wall, 2 = adjacent)
_PEN = (0.0, 0.4, 1.4)


class World:
    def __init__(self, mapdir):
        with open(mapdir + "/v2/world.json") as f:
            w = json.load(f)
        t = w["terrain"]
        self.tmx, self.tmy, self.tnx, self.tny = t["minX"], t["minY"], t["nx"], t["ny"]
        self.ter = array("h")
        with open(mapdir + "/v2/terrain.i16", "rb") as f:
            self.ter.frombytes(f.read())
        if sys.byteorder != "little":
            self.ter.byteswap()

        self.ox, self.oy = self.tmx, self.tmy
        self.W = int((self.tnx - 1) / CELL) + 1
        self.H = int((self.tny - 1) / CELL) + 1
        n = self.W * self.H
        self.blocked = bytearray(n)
        self.top = [-1e9] * n
        self.near = bytearray(n)

        self._raster(w.get("prisms") or [])
        self._slopes()
        self._near()

    # ---- terrain
    def h(self, x, y):
        fx = x - self.tmx
        fy = y - self.tmy
        if fx < 0.0:
            fx = 0.0
        elif fx > self.tnx - 1.001:
            fx = self.tnx - 1.001
        if fy < 0.0:
            fy = 0.0
        elif fy > self.tny - 1.001:
            fy = self.tny - 1.001

        i = int(fx)
        j = int(fy)
        ax = fx - i
        ay = fy - j
        k = j * self.tnx + i
        ter = self.ter
        a = ter[k] * (1.0 - ax) + ter[k + 1] * ax
        c = ter[k + self.tnx] * (1.0 - ax) + ter[k + self.tnx + 1] * ax
        return (a * (1.0 - ay) + c * ay) * 0.01

    # ---- grid
    def ci(self, x, y):
        gx = int((x - self.ox) / CELL)
        gy = int((y - self.oy) / CELL)
        if x < self.ox or y < self.oy or gx >= self.W or gy >= self.H:
            return -1
        return gy * self.W + gx

    def center(self, c):
        gy, gx = divmod(c, self.W)
        return (self.ox + (gx + 0.5) * CELL, self.oy + (gy + 0.5) * CELL)

    def is_blocked(self, x, y):
        c = self.ci(x, y)
        return c < 0 or self.blocked[c] == 1

    def snap(self, x, y, rmax=12):
        """Centre of the nearest walkable cell to (x, y); falls back to (x, y)."""
        c = self.nearest_free(self.ci(x, y), rmax)
        if c < 0:
            return (x, y)
        return self.center(c)

    @staticmethod
    def _inside(ring, x, y):
        n = len(ring) // 2
        ins = False
        j = n - 1
        for i in range(n):
            xi = ring[2 * i]
            yi = ring[2 * i + 1]
            xj = ring[2 * j]
            yj = ring[2 * j + 1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                ins = not ins
            j = i
        return ins

    def _raster(self, prisms):
        # 9-point mech hitbox footprint (2.1m radius) to prevent corner clipping
        r = 2.1
        d = r * 0.7071
        offs = (
            (0.0, 0.0),
            (r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r),
            (d, d), (d, -d), (-d, d), (-d, -d),
        )
        inside = self._inside
        hfn = self.h
        W, H, ox, oy = self.W, self.H, self.ox, self.oy
        blocked, topv = self.blocked, self.top

        for p in prisms:
            try:
                ring = p["ring"]
                if len(ring) < 6:
                    continue
                base, top = p["base"], p["top"]
            except Exception:
                continue
            xs = ring[0::2]
            ys = ring[1::2]
            rminx, rmaxx = min(xs), max(xs)
            rminy, rmaxy = min(ys), max(ys)
            gx0 = max(0, int((rminx - 3.0 - ox) / CELL))
            gx1 = min(W - 1, int((rmaxx + 3.0 - ox) / CELL))
            gy0 = max(0, int((rminy - 3.0 - oy) / CELL))
            gy1 = min(H - 1, int((rmaxy + 3.0 - oy) / CELL))

            holes = p.get("holes") or []
            if holes and isinstance(holes[0], (int, float)):
                holes = [holes]
            holes = [hh for hh in holes if len(hh) >= 6]

            for gy in range(gy0, gy1 + 1):
                cy = oy + (gy + 0.5) * CELL
                if cy < rminy - r or cy > rmaxy + r:
                    continue
                row = gy * W
                for gx in range(gx0, gx1 + 1):
                    cx = ox + (gx + 0.5) * CELL
                    if cx < rminx - r or cx > rmaxx + r:
                        continue
                    g = hfn(cx, cy)
                    if not (base < g + 9.5 and top > g + 1.1):
                        continue
                    hit = False
                    for dx, dy in offs:
                        px, py = cx + dx, cy + dy
                        if px < rminx or px > rmaxx or py < rminy or py > rmaxy:
                            continue
                        if inside(ring, px, py):
                            in_hole = False
                            for hh in holes:
                                if inside(hh, px, py):
                                    in_hole = True
                                    break
                            if not in_hole:
                                hit = True
                                break
                    if hit:
                        c = row + gx
                        blocked[c] = 1
                        if top > topv[c]:
                            topv[c] = top

    def _slopes(self):
        lim2 = 1.05 * 1.05
        hfn = self.h
        W, H, ox, oy = self.W, self.H, self.ox, self.oy
        blocked = self.blocked
        for gy in range(H):
            cy = oy + (gy + 0.5) * CELL
            row = gy * W
            for gx in range(W):
                c = row + gx
                if blocked[c]:
                    continue
                cx = ox + (gx + 0.5) * CELL
                dx = (hfn(cx + 2.0, cy) - hfn(cx - 2.0, cy)) * 0.25
                dy = (hfn(cx, cy + 2.0) - hfn(cx, cy - 2.0)) * 0.25
                if dx * dx + dy * dy > lim2:
                    blocked[c] = 1

    def _near(self):
        """Mark 2-ring wall proximity: 2 = adjacent to wall, 1 = 2 cells away."""
        W, H = self.W, self.H
        blocked = self.blocked
        near = self.near
        ring8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
        for gy in range(H):
            row = gy * W
            for gx in range(W):
                c = row + gx
                if blocked[c]:
                    continue
                for dx, dy in ring8:
                    nx, ny = gx + dx, gy + dy
                    if nx < 0 or ny < 0 or nx >= W or ny >= H or blocked[ny * W + nx]:
                        near[c] = 2
                        break

        ring4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
        for gy in range(H):
            row = gy * W
            for gx in range(W):
                c = row + gx
                if blocked[c] or near[c]:
                    continue
                for dx, dy in ring4:
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < W and 0 <= ny < H and near[ny * W + nx] == 2:
                        near[c] = 1
                        break

    # ---- queries
    def walk_line(self, x0, y0, x1, y1, step=1.2):
        dx, dy = x1 - x0, y1 - y0
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return not self.is_blocked(x0, y0)
        n = int(d / step) + 1
        inv_n = 1.0 / n
        ox, oy, W, H = self.ox, self.oy, self.W, self.H
        blocked = self.blocked
        for i in range(1, n + 1):
            f = i * inv_n
            ux = x0 + dx * f - ox
            uy = y0 + dy * f - oy
            if ux < 0.0 or uy < 0.0:
                return False
            gx = int(ux / CELL)
            gy = int(uy / CELL)
            if gx >= W or gy >= H or blocked[gy * W + gx]:
                return False
        return True

    def los(self, a, b, step=3.0):
        """True if nothing in the grid blocks the 3D segment a -> b."""
        ax, ay, az = a[0], a[1], a[2]
        dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
        d = math.hypot(dx, dy)
        n = int(d / step)
        if n <= 0:
            return True
        top = self.top
        hfn = self.h
        ci = self.ci
        inv = 1.0 / (n + 1)
        for i in range(1, n + 1):
            f = i * inv
            x = ax + dx * f
            y = ay + dy * f
            c = ci(x, y)
            if c < 0:
                return False
            z = az + dz * f
            if top[c] > z:
                return False
            if hfn(x, y) > z + 0.45:
                return False
        return True

    def nearest_free(self, c, rmax=12):
        if c < 0:
            return -1
        if not self.blocked[c]:
            return c
        gy, gx = divmod(c, self.W)
        W, H = self.W, self.H
        blocked = self.blocked
        for r in range(1, rmax + 1):
            best = None
            for dy in range(-r, r + 1):
                yy = gy + dy
                if yy < 0 or yy >= H:
                    continue
                edge_row = (dy == -r or dy == r)
                step = 1 if edge_row else 2 * r
                for dx in range(-r, r + 1, step):
                    xx = gx + dx
                    if 0 <= xx < W and not blocked[yy * W + xx]:
                        dd = dx * dx + dy * dy
                        if best is None or dd < best[0]:
                            best = (dd, yy * W + xx)
            if best:
                return best[1]
        return -1

    def plan(self, sx, sy, gx, gy, maxexp=3200, budget=0.007):
        """Bounded weighted A*. Returns a list of cell centres (possibly a partial
        path toward the goal if the node/time budget runs out), or None."""
        tstart = time.perf_counter()
        s = self.nearest_free(self.ci(sx, sy))
        g = self.nearest_free(self.ci(gx, gy), 20)
        if s < 0 or g < 0:
            return None
        if s == g:
            return [self.center(g)]

        W, H = self.W, self.H
        goal_y, goal_x = divmod(g, W)
        blocked, near = self.blocked, self.near
        pen = _PEN
        pc = time.perf_counter
        push, pop = heapq.heappush, heapq.heappop

        sy_, sx_ = divmod(s, W)
        ddx, ddy = abs(sx_ - goal_x), abs(sy_ - goal_y)
        h0 = ddx + ddy - 0.5858 * min(ddx, ddy)

        gs = {s: 0.0}
        came = {}
        closed = set()
        heap = [(h0 * 1.15, 0, s)]
        cnt = 0
        exp = 0
        best_h, best_c = h0, s

        while heap and exp < maxexp:
            _, _, c = pop(heap)
            if c in closed:
                continue
            closed.add(c)
            if c == g:
                best_c = c
                break
            exp += 1
            if (exp & 63) == 0 and (pc() - tstart) > budget:
                break

            cy, cx = divmod(c, W)
            gcur = gs[c]
            for dx, dy, cost in _NB:
                nx = cx + dx
                ny = cy + dy
                if nx < 0 or ny < 0 or nx >= W or ny >= H:
                    continue
                n = ny * W + nx
                if blocked[n] or n in closed:
                    continue
                # prevent diagonal corner-cutting across blocked cells
                if dx and dy and (blocked[cy * W + nx] or blocked[ny * W + cx]):
                    continue
                ng = gcur + cost + pen[near[n]]
                if ng < gs.get(n, 1e18):
                    gs[n] = ng
                    came[n] = c
                    ex = nx - goal_x
                    if ex < 0:
                        ex = -ex
                    ey = ny - goal_y
                    if ey < 0:
                        ey = -ey
                    hh = ex + ey - 0.5858 * (ex if ex < ey else ey)
                    if hh < best_h:
                        best_h = hh
                        best_c = n
                    cnt += 1
                    push(heap, (ng + hh * 1.15, cnt, n))

        path = [best_c]
        while path[-1] in came:
            path.append(came[path[-1]])
        path.reverse()
        return [self.center(c) for c in path]

    @staticmethod
    def _oct(x0, y0, x1, y1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        return (dx + dy) + (1.4142 - 2.0) * min(dx, dy)
