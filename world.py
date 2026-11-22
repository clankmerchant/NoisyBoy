"""Static map model: terrain heights, clearance-inflated grid, 3D line of sight, A* pathing."""
import heapq
import json
import math
import sys
import time
from array import array

CELL = 3.0


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

        self._raster(w["prisms"])
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
        if gx < 0 or gy < 0 or gx >= self.W or gy >= self.H:
            return -1
        return gy * self.W + gx

    def center(self, c):
        gy, gx = divmod(c, self.W)
        return (self.ox + (gx + 0.5) * CELL, self.oy + (gy + 0.5) * CELL)

    def is_blocked(self, x, y):
        c = self.ci(x, y)
        return c < 0 or self.blocked[c] == 1

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
        for p in prisms:
            ring = p["ring"]
            if len(ring) < 6:
                continue
            xs = ring[0::2]
            ys = ring[1::2]
            gx0 = max(0, int((min(xs) - 3.0 - self.ox) / CELL))
            gx1 = min(self.W - 1, int((max(xs) + 3.0 - self.ox) / CELL))
            gy0 = max(0, int((min(ys) - 3.0 - self.oy) / CELL))
            gy1 = min(self.H - 1, int((max(ys) + 3.0 - self.oy) / CELL))

            holes = p.get("holes") or []
            if holes and isinstance(holes[0], (int, float)):
                holes = [holes]
            base, top = p["base"], p["top"]

            for gy in range(gy0, gy1 + 1):
                cy = self.oy + (gy + 0.5) * CELL
                for gx in range(gx0, gx1 + 1):
                    cx = self.ox + (gx + 0.5) * CELL
                    hit = False
                    for dx, dy in offs:
                        px, py = cx + dx, cy + dy
                        if self._inside(ring, px, py) and not any(self._inside(hh, px, py) for hh in holes):
                            hit = True
                            break
                    if not hit:
                        continue
                    g = self.h(cx, cy)
                    c = gy * self.W + gx
                    if base < g + 9.5 and top > g + 1.1:
                        self.blocked[c] = 1
                        if top > self.top[c]:
                            self.top[c] = top

    def _slopes(self):
        lim = 1.05
        for gy in range(self.H):
            cy = self.oy + (gy + 0.5) * CELL
            for gx in range(self.W):
                c = gy * self.W + gx
                if self.blocked[c]:
                    continue
                cx = self.ox + (gx + 0.5) * CELL
                dx = (self.h(cx + 2.0, cy) - self.h(cx - 2.0, cy)) / 4.0
                dy = (self.h(cx, cy + 2.0) - self.h(cx, cy - 2.0)) / 4.0
                if dx * dx + dy * dy > lim * lim:
                    self.blocked[c] = 1

    def _near(self):
        """Mark 2-ring wall proximity: 2 = adjacent to wall, 1 = 2 cells away."""
        W, H = self.W, self.H
        blocked = self.blocked
        near = self.near
        for gy in range(H):
            row = gy * W
            for gx in range(W):
                c = row + gx
                if blocked[c]:
                    continue
                adj1 = False
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    nx, ny = gx + dx, gy + dy
                    if nx < 0 or ny < 0 or nx >= W or ny >= H or blocked[ny * W + nx]:
                        adj1 = True
                        break
                if adj1:
                    near[c] = 2

        for gy in range(H):
            row = gy * W
            for gx in range(W):
                c = row + gx
                if blocked[c] or near[c]:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
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
        for i in range(1, n + 1):
            f = i * inv_n
            if self.is_blocked(x0 + dx * f, y0 + dy * f):
                return False
        return True

    def los(self, a, b, step=3.0):
        """True if nothing in the grid blocks the 3D segment a -> b."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        n = int(d / step)
        if n <= 0:
            return True
        top = self.top
        inv = 1.0 / (n + 1)
        for i in range(1, n + 1):
            f = i * inv
            x = a[0] + dx * f
            y = a[1] + dy * f
            c = self.ci(x, y)
            if c < 0:
                return False
            z = a[2] + dz * f
            if top[c] > z:
                return False
            if self.h(x, y) > z + 0.45:
                return False
        return True

    def nearest_free(self, c, rmax=12):
        if c >= 0 and not self.blocked[c]:
            return c
        if c < 0:
            return -1
        gy, gx = divmod(c, self.W)
        W, H = self.W, self.H
        blocked = self.blocked
        for r in range(1, rmax + 1):
            best = None
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    x, y = gx + dx, gy + dy
                    if 0 <= x < W and 0 <= y < H and not blocked[y * W + x]:
                        dd = dx * dx + dy * dy
                        if best is None or dd < best[0]:
                            best = (dd, y * W + x)
            if best:
                return best[1]
        return -1

    def plan(self, sx, sy, gx, gy, maxexp=8000, budget=0.018):
        tstart = time.perf_counter()
        s = self.nearest_free(self.ci(sx, sy))
        g = self.nearest_free(self.ci(gx, gy))
        if s < 0 or g < 0:
            return None
        if s == g:
            return [self.center(g)]

        W, H = self.W, self.H
        goal_y, goal_x = divmod(g, W)
        blocked, near = self.blocked, self.near
        gs = {s: 0.0}
        came = {}
        sy_, sx_ = divmod(s, W)
        h0 = self._oct(sx_, sy_, goal_x, goal_y)
        heap = [(h0, 0, s)]
        cnt = 0
        best = (h0, s)
        exp = 0
        closed = set()

        neighbors = (
            (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
            (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142),
        )

        while heap and exp < maxexp:
            _, _, c = heapq.heappop(heap)
            if c in closed:
                continue
            closed.add(c)
            if c == g:
                best = (0.0, c)
                break
            exp += 1
            if (exp & 255) == 0 and (time.perf_counter() - tstart) > budget:
                break

            cy, cx = divmod(c, W)
            gc = gs[c]
            for dx, dy, cost in neighbors:
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= W or ny >= H:
                    continue
                n = ny * W + nx
                if blocked[n]:
                    continue
                # Prevent diagonal corner-cutting across blocked cells
                if dx and dy and (blocked[cy * W + nx] or blocked[ny * W + cx]):
                    continue

                w_pen = 1.4 if near[n] == 2 else (0.4 if near[n] == 1 else 0.0)
                ng = gc + cost + w_pen
                if ng < gs.get(n, 1e18):
                    gs[n] = ng
                    came[n] = c
                    hh = self._oct(nx, ny, goal_x, goal_y)
                    if hh < best[0]:
                        best = (hh, n)
                if ng < gs.get(n, 1e18):
                    gs[n] = ng
                    came[n] = c
                    hh = self._oct(nx, ny, goal_x, goal_y)
                    if hh < best[0]:
                        best = (hh, n)
                    cnt += 1
                    heapq.heappush(heap, (ng + hh * 1.05, cnt, n))

        end = best[1]
        path = [end]
        while path[-1] in came:
            path.append(came[path[-1]])
        path.reverse()
        return [self.center(c) for c in path]

    @staticmethod
    def _oct(x0, y0, x1, y1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        return (dx + dy) + (1.4142 - 2.0) * min(dx, dy)
