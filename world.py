"""Static map model: terrain heights, blocked-cell grid, line of sight, A* pathing."""
import heapq
import json
import math
import sys
from array import array

CELL = 4.0


class World:
    def __init__(self, mapdir):
        w = json.load(open(mapdir + "/v2/world.json"))
        t = w["terrain"]
        self.tmx, self.tmy, self.tnx, self.tny = t["minX"], t["minY"], t["nx"], t["ny"]
        self.ter = array("h")
        with open(mapdir + "/v2/terrain.i16", "rb") as f:
            self.ter.frombytes(f.read())
        if sys.byteorder != "little":
            self.ter.byteswap()
        b = w["bounds"]
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
        if fx < 0:
            fx = 0.0
        if fy < 0:
            fy = 0.0
        if fx > self.tnx - 1.001:
            fx = self.tnx - 1.001
        if fy > self.tny - 1.001:
            fy = self.tny - 1.001
        i = int(fx)
        j = int(fy)
        ax = fx - i
        ay = fy - j
        k = j * self.tnx + i
        ter = self.ter
        a = ter[k] * (1 - ax) + ter[k + 1] * ax
        c = ter[k + self.tnx] * (1 - ax) + ter[k + self.tnx + 1] * ax
        return (a * (1 - ay) + c * ay) * 0.01

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
        offs = ((0, 0), (1.6, 0), (-1.6, 0), (0, 1.6), (0, -1.6))
        for p in prisms:
            ring = p["ring"]
            if len(ring) < 6:
                continue
            xs = ring[0::2]
            ys = ring[1::2]
            gx0 = max(0, int((min(xs) - 2 - self.ox) / CELL))
            gx1 = min(self.W - 1, int((max(xs) + 2 - self.ox) / CELL))
            gy0 = max(0, int((min(ys) - 2 - self.oy) / CELL))
            gy1 = min(self.H - 1, int((max(ys) + 2 - self.oy) / CELL))
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
                    if base < g + 9.5 and top > g + 1.2:
                        self.blocked[c] = 1
                        if top > self.top[c]:
                            self.top[c] = top

    def _slopes(self):
        lim = 1.1
        for gy in range(self.H):
            cy = self.oy + (gy + 0.5) * CELL
            for gx in range(self.W):
                c = gy * self.W + gx
                if self.blocked[c]:
                    continue
                cx = self.ox + (gx + 0.5) * CELL
                dx = (self.h(cx + 2, cy) - self.h(cx - 2, cy)) / 4.0
                dy = (self.h(cx, cy + 2) - self.h(cx, cy - 2)) / 4.0
                if dx * dx + dy * dy > lim * lim:
                    self.blocked[c] = 1

    def _near(self):
        W, H = self.W, self.H
        for gy in range(H):
            for gx in range(W):
                c = gy * W + gx
                if self.blocked[c]:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    x, y = gx + dx, gy + dy
                    if x < 0 or y < 0 or x >= W or y >= H or self.blocked[y * W + x]:
                        self.near[c] = 1
                        break

    # ---- queries
    def walk_line(self, x0, y0, x1, y1, step=2.0):
        d = math.hypot(x1 - x0, y1 - y0)
        n = int(d / step) + 1
        for i in range(1, n + 1):
            f = min(1.0, i * step / d) if d > 0 else 1.0
            if self.is_blocked(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f):
                return False
        return True

    def los(self, a, b, step=4.0):
        """True if nothing in the grid blocks the segment a->b (3D points)."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        n = int(d / step)
        top = self.top
        for i in range(1, n + 1):
            f = i / (n + 1)
            x = a[0] + dx * f
            y = a[1] + dy * f
            c = self.ci(x, y)
            if c < 0:
                return False
            tp = top[c]
            z = a[2] + dz * f
            if tp > z:
                return False
            if self.h(x, y) > z + 0.5 and i % 2 == 0:
                return False
        return True

    def nearest_free(self, c, rmax=8):
        if c >= 0 and not self.blocked[c]:
            return c
        if c < 0:
            return -1
        gy, gx = divmod(c, self.W)
        for r in range(1, rmax + 1):
            best = None
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    x, y = gx + dx, gy + dy
                    if 0 <= x < self.W and 0 <= y < self.H and not self.blocked[y * self.W + x]:
                        dd = dx * dx + dy * dy
                        if best is None or dd < best[0]:
                            best = (dd, y * self.W + x)
            if best:
                return best[1]
        return -1

    def plan(self, sx, sy, gx, gy, maxexp=4000):
        s = self.nearest_free(self.ci(sx, sy))
        g = self.nearest_free(self.ci(gx, gy))
        if s < 0 or g < 0:
            return None
        if s == g:
            return [self.center(g)]
        W = self.W
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
        while heap and exp < maxexp:
            f, _, c = heapq.heappop(heap)
            if c in closed:
                continue
            closed.add(c)
            if c == g:
                best = (0, c)
                break
            exp += 1
            cy, cx = divmod(c, W)
            gc = gs[c]
            for dx, dy, cost in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                                 (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)):
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= W or ny >= self.H:
                    continue
                n = ny * W + nx
                if blocked[n]:
                    continue
                if dx and dy and (blocked[cy * W + nx] or blocked[ny * W + cx]):
                    continue
                ng = gc + cost + (0.6 if near[n] else 0.0)
                if ng < gs.get(n, 1e18):
                    gs[n] = ng
                    came[n] = c
                    hh = self._oct(nx, ny, goal_x, goal_y)
                    if hh < best[0]:
                        best = (hh, n)
                    cnt += 1
                    heapq.heappush(heap, (ng + hh, cnt, n))
        end = best[1]
        path = [end]
        while path[-1] in came:
            path.append(came[path[-1]])
        path.reverse()
        return [self.center(c) for c in path]

    @staticmethod
    def _oct(x0, y0, x1, y1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        return (dx + dy) + (1.414 - 2) * min(dx, dy)
