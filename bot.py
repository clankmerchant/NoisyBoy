"""apex-v3: Autocannon + Laser, outer-ring zone surfing, reactive dash-kite, cube snowball."""
import gc
import math
import random
import time
import mechfight as mf
from world import World

TAU = math.tau

UP_PREF = [
    "armor", "plating", "nanites", "gun_damage", "laser_damage",
    "laser_charge", "gun_rate", "servos", "gyros", "gun_velocity",
    "capacitor", "reactor", "gun_mag", "sensors", "jets",
]
UP_RANK = {k: i for i, k in enumerate(UP_PREF)}
SOUND_KINDS = ("gun", "laser", "lightning", "mortar", "explosion")


def norm_ang(a):
    return (a + math.pi) % TAU - math.pi


def unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 1e-6 else (0.0, 0.0)


def xyz(p):
    if len(p) > 2:
        return p[0], p[1], p[2]
    return p[0], p[1], 0.0


def zone_circle(zc, zr, nc, nr, ss, se, t):
    if t <= ss:
        return zc, zr
    if t >= se:
        return nc, nr
    f = (t - ss) / max(1e-6, se - ss)
    return (zc[0] + (nc[0] - zc[0]) * f, zc[1] + (nc[1] - zc[1]) * f), zr + (nr - zr) * f


class Bot:
    name = "apex-v3"

    def __init__(self, hello):
        self.rng = random.Random(hello.get("seed", 1))
        self.cfg = hello.get("config", {}) or {}
        self.world = None
        err = None
        dirs = []
        try:
            dirs.append(hello["map"]["dir"])
        except Exception:
            pass
        dirs.append("/arena")
        for d in dirs:
            try:
                self.world = World(d)
                break
            except Exception as e:
                err = e
        if self.world is None:
            raise err

        self.t0 = time.perf_counter()
        self.path = None
        self.path_goal = None
        self.path_tick = -999
        self.pi = 0
        self.next_plan_tick = 0
        self.bad_goals = {}
        self.hist = []
        self.unstick_until = -1.0
        self.unstick_dir = (1.0, 0.0)
        self.boost_lock = False
        self.strafe = 1
        self.strafe_flip = 0.0

        self.pickups = {}
        self.cube_mem = {}
        self.dead_drops = set()
        self.drop_dwell = {}
        self.cover = None
        self.cover_tick = -999

        self.last_dmg_t = -99.0
        self.last_dmg_bearing = 0.0
        self.dash_until = -1.0
        self.dash_ready_t = -1.0
        self.dash_dir = (0.0, 0.0)
        self.slow_until = -1

        self.surf_pt = None
        self.surf_tick = -999

        gc.collect()
        gc.freeze()
        gc.disable()

    # ------------------------------------------------------------------ helpers
    def zone_clamp_dir(self, x, y, dx, dy, cC, cR, margin=12.0):
        """If moving along (dx, dy) would leave the safe disc, slide tangentially instead."""
        ccx, ccy = cC
        tx, ty = x + dx * 10.0, y + dy * 10.0
        if math.hypot(tx - ccx, ty - ccy) <= cR - margin:
            return dx, dy
        rx, ry = unit(x - ccx, y - ccy)
        t1x, t1y = -ry, rx
        t2x, t2y = ry, -rx
        if (t1x * dx + t1y * dy) >= (t2x * dx + t2y * dy):
            return unit(t1x - rx * 0.35, t1y - ry * 0.35)
        return unit(t2x - rx * 0.35, t2y - ry * 0.35)

    def free_dir(self, x, y, dx, dy, dist=11.0):
        w = self.world
        angles = (
            0.0, 0.35, -0.35, 0.70, -0.70, 1.05, -1.05,
            1.40, -1.40, 1.80, -1.80, 2.30, -2.30, 3.14,
        )
        for a in angles:
            ca, sa = math.cos(a), math.sin(a)
            rx, ry = dx * ca - dy * sa, dx * sa + dy * ca
            if w.walk_line(x, y, x + rx * dist, y + ry * dist, 1.5):
                return rx, ry
        return dx, dy

    def go_to(self, x, y, goal, tick):
        w = self.world
        gx, gy = goal
        d = math.hypot(gx - x, gy - y)

        if d < 5.0:
            return unit(gx - x, gy - y), True

        if d < 40.0 and w.walk_line(x, y, gx, gy, 1.5):
            self.path = None
            return unit(gx - x, gy - y), False

        key = (int(gx // 9.0), int(gy // 9.0))
        if self.bad_goals.get(key, -1) > tick:
            return self.free_dir(x, y, *unit(gx - x, gy - y)), False

        plan_gx, plan_gy = gx, gy
        if d > 105.0:
            ux, uy = unit(gx - x, gy - y)
            plan_gx, plan_gy = x + ux * 105.0, y + uy * 105.0

        replan = self.path is None or self.path_goal is None or tick - self.path_tick > 26
        if not replan:
            pgx, pgy = self.path_goal
            if math.hypot(plan_gx - pgx, plan_gy - pgy) > 14.0:
                replan = True
        if replan and (tick < self.next_plan_tick or time.perf_counter() - self.t0 > 0.006):
            replan = False

        if replan:
            path = w.plan(x, y, plan_gx, plan_gy, maxexp=3200, budget=0.007)
            self.path_goal = (plan_gx, plan_gy)
            self.path_tick = tick
            self.pi = 0
            self.next_plan_tick = tick + 4
            if path is None or len(path) < 2:
                self.path = None
                self.bad_goals[key] = tick + 40
                self.next_plan_tick = tick + 8
            else:
                self.path = path
                ex, ey = path[-1]
                if math.hypot(ex - x, ey - y) < 9.0:
                    self.bad_goals[key] = tick + 30
            if len(self.bad_goals) > 64:
                self.bad_goals = {k: v for k, v in self.bad_goals.items() if v > tick}

        p = self.path
        if not p:
            return self.free_dir(x, y, *unit(gx - x, gy - y)), False

        n = len(p)
        while self.pi < n - 1:
            wx, wy = p[self.pi]
            if math.hypot(wx - x, wy - y) < 5.5:
                self.pi += 1
            else:
                break

        tx, ty = p[self.pi]
        for j in range(min(n - 1, self.pi + 7), self.pi, -1):
            jx, jy = p[j]
            if w.walk_line(x, y, jx, jy, 1.5):
                tx, ty = jx, jy
                break

        if self.pi >= n - 1 and math.hypot(tx - x, ty - y) < 6.0 and d > 8.0:
            self.path = None
            return self.free_dir(x, y, *unit(gx - x, gy - y)), False

        return unit(tx - x, ty - y), False

    def lead(self, src, tpos, tvel, speed):
        tx, ty, tz = tpos
        if len(tvel) >= 3:
            vx, vy, vz = tvel[0], tvel[1], tvel[2]
        else:
            vx, vy, vz = 0.0, 0.0, 0.0
        px, py, pz = tx, ty, tz
        for _ in range(3):
            tt = math.dist((px, py, pz), src) / max(speed, 1.0) + 0.045
            px = tx + vx * tt
            py = ty + vy * tt
            pz = tz + vz * tt * 0.35
        return px, py, pz

    def find_cover(self, x, y, threats, cC, cR):
        w = self.world
        ccx, ccy = cC
        best_score = 1e18
        best_pt = None
        t0 = self.t0
        for dist in (14.0, 24.0):
            for k in range(8):
                if time.perf_counter() - t0 > 0.007:
                    return best_pt
                a = TAU * k / 8 + self.rng.random() * 0.3
                cx, cy = x + math.cos(a) * dist, y + math.sin(a) * dist
                if w.is_blocked(cx, cy) or not w.walk_line(x, y, cx, cy, 2.0):
                    continue
                if math.hypot(cx - ccx, cy - ccy) > cR - 10.0:
                    continue
                gz = w.h(cx, cy)
                ok = True
                score = dist
                for th in threats:
                    tpx, tpy, tpz = xyz(th["pos"])
                    if w.los((tpx, tpy, tpz + 7.5), (cx, cy, gz + 6.5), 4.0):
                        ok = False
                        break
                    score -= 0.18 * math.hypot(cx - tpx, cy - tpy)
                if ok and score < best_score:
                    best_score = score
                    best_pt = (cx, cy)
            if best_pt is not None:
                break
        return best_pt

    def pick_surf(self, x, y, cC, cR, nc, margin):
        """Point on the inner edge of the CURRENT zone, on the side nearest next_center."""
        ccx, ccy = cC
        ux, uy = unit(nc[0] - ccx, nc[1] - ccy)
        if ux == 0.0 and uy == 0.0:
            ux, uy = unit(x - ccx, y - ccy)
            if ux == 0.0 and uy == 0.0:
                ux = 1.0
        r0 = max(cR - margin, cR * 0.5)
        for k in range(5):
            r = max(r0 - 6.0 * k, 0.0)
            px, py = self.world.snap(ccx + ux * r, ccy + uy * r)
            if math.hypot(px - ccx, py - ccy) <= cR - 6.0:
                return (px, py)
        return (ccx, ccy)

    # ------------------------------------------------------------------ main
    def act(self, obs):
        self.t0 = time.perf_counter()
        try:
            return self._act(obs)
        except Exception:
            return self._fallback(obs)

    def _fallback(self, obs):
        tick = 0
        try:
            tick = obs.get("tick", 0)
        except Exception:
            pass
        out = {
            "tick": tick, "move": [0.0, 0.0], "boost": False, "jump": False,
            "aim": [0.0, 0.0], "fire": [False, False],
        }
        try:
            me = obs["self"]
            out["aim"] = [float(me["aim"][0]), float(me["aim"][1])]
            if obs.get("offer"):
                out["pick"] = 0
            x, y, _ = xyz(me["pos"])
            zn = obs["zone"]
            cx, cy = zn["center"][0], zn["center"][1]
            if math.hypot(cx - x, cy - y) > zn["radius"] * 0.6:
                mx, my = unit(cx - x, cy - y)
                out["move"] = [round(mx, 3), round(my, 3)]
        except Exception:
            pass
        return out

    def _act(self, obs):
        t0 = self.t0
        w = self.world
        me = obs["self"]
        tick = obs["tick"]
        t = obs["t"]
        x, y, z = xyz(me["pos"])

        out = {"tick": tick}
        weapons = me.get("weapons") or []
        have = [wp["kind"] if wp else None for wp in weapons]
        stats = me.get("stats") or {}
        eye_z = z + stats.get("eye_height", 8.0)
        muzzle = stats.get("muzzle_height", 7.0)
        walk = stats.get("walk_speed", 11.7)
        hp = me.get("hp", 100.0)
        hp_frac = hp / max(1.0, me.get("max_hp", 100.0))
        energy = me.get("energy", 100.0)
        grounded = me.get("grounded", True)
        in_zone = me.get("in_zone", True)
        cur_yaw, cur_pitch = me["aim"][0], me["aim"][1]

        if tick % 250 == 0 and tick > 0:
            gc.collect(0)

        # 1. Upgrade selection
        offer = obs.get("offer")
        if offer:
            pick = 0
            best_rank = 999
            for i, c in enumerate(offer.get("choices") or []):
                r = UP_RANK.get(c.get("id"), 500)
                if r < best_rank:
                    best_rank, pick = r, i
            out["pick"] = pick

        # 2. Events & reactive dash
        took_fail = set()
        for e in obs.get("events") or []:
            ty = e.get("type")
            if ty == "damaged":
                b = e.get("bearing")
                if e.get("weapon") != "zone" and b is not None:
                    self.last_dmg_t = t
                    self.last_dmg_bearing = b
                    if t >= self.dash_ready_t:
                        away = b + math.pi + self.strafe * 0.55
                        self.dash_dir = (math.cos(away), math.sin(away))
                        self.dash_until = t + 0.45
                        self.dash_ready_t = t + 1.0
            elif ty == "took":
                self.pickups.pop(e.get("id"), None)
            elif ty == "take_failed":
                if e.get("reason") in ("gone", "have"):
                    self.pickups.pop(e.get("id"), None)
                took_fail.add(e.get("id"))

        # 3. Loot & cube memory
        seen_ids = set()
        for p in obs.get("pickups") or []:
            seen_ids.add(p["id"])
            self.pickups[p["id"]] = (p["kind"], xyz(p["pos"]))
        for pid in list(self.pickups):
            if pid not in seen_ids:
                px, py, _ = self.pickups[pid][1]
                if math.hypot(px - x, py - y) < 30.0:
                    del self.pickups[pid]

        cur_cubes = {c["id"]: xyz(c["pos"]) for c in obs.get("cubes") or []}
        mem = {}
        for k, v in self.cube_mem.items():
            if k not in cur_cubes and math.hypot(v[0] - x, v[1] - y) > 25.0:
                mem[k] = v
        mem.update(cur_cubes)
        self.cube_mem = mem

        # 4. Zone: current circle + outer-ring surf point
        zone = obs["zone"]
        zc = zone["center"]
        zr = zone["radius"]
        nc = zone.get("next_center") or zc
        nr = zone.get("next_radius")
        if nr is None:
            nr = zr
        ss = zone.get("shrink_start", 1e9)
        se = zone.get("shrink_end", 1e9)
        cC, cR = zone_circle(zc, zr, nc, nr, ss, se, t)
        ccx, ccy = cC
        shrinking = (ss - 6.0) <= t <= se
        margin = 18.0 if shrinking else 14.0
        if self.surf_pt is None or tick - self.surf_tick >= 8:
            self.surf_pt = self.pick_surf(x, y, cC, cR, nc, margin)
            self.surf_tick = tick
        surf_pt = self.surf_pt
        surf_x, surf_y = surf_pt
        zone_unsafe = (not in_zone) or math.hypot(x - ccx, y - ccy) > cR - 8.5

        # 5. Enemy & NPC scan
        mechs = []
        npcs = []
        for m in obs.get("mechs") or []:
            mx, my, _ = xyz(m["pos"])
            m["d"] = math.hypot(mx - x, my - y)
            if m.get("kind") == "npc":
                npcs.append(m)
            else:
                mechs.append(m)
        mechs.sort(key=lambda m: m["d"])

        close_threats = [m for m in mechs if m["d"] < 145.0]
        virtual = False
        if not close_threats and (t - self.last_dmg_t) < 2.2:
            b = self.last_dmg_bearing
            close_threats = [{
                "pos": [x + math.cos(b) * 85.0, y + math.sin(b) * 85.0, z],
                "vel": [0.0, 0.0, 0.0],
                "d": 85.0, "id": -1, "hp": 1000, "weapons": [], "virtual": True,
            }]
            virtual = True

        # 6. Target selection (LOS checks capped for tick time)
        target = None
        tkind = None
        los_checks = 0
        for m in mechs:
            d = m["d"]
            if d >= 215.0:
                break
            tpx, tpy, tpz = xyz(m["pos"])
            if d < 38.0:
                target, tkind = m, "mech"
                break
            if los_checks >= 4:
                break
            los_checks += 1
            if w.los((x, y, eye_z), (tpx, tpy, tpz + 5.5)):
                target, tkind = m, "mech"
                break

        if target is None and hp_frac > 0.25:
            best_npc = None
            for n in npcs:
                if n["d"] < 85.0 and (best_npc is None or n["d"] < best_npc["d"]):
                    best_npc = n
            if best_npc is not None:
                target, tkind = best_npc, "npc"

        # 7. Combat decision & fast strafe clock
        fight = False
        if tkind == "mech" and not virtual:
            near_cnt = sum(1 for m in mechs if m["d"] < 95.0)
            enemy_hp = target.get("hp", 1000)
            if near_cnt <= 2 and hp_frac > 0.30 and target["d"] < 185.0:
                fight = True
            elif enemy_hp < hp * 0.65 and hp_frac > 0.20:
                fight = True
        elif tkind == "npc":
            fight = True

        if t > self.strafe_flip:
            self.strafe = -self.strafe
            self.strafe_flip = t + 0.22 + self.rng.random() * 0.26

        # 8. Movement state machine
        move = (0.0, 0.0)
        boost = False
        goal_label = ""

        if zone_unsafe:
            move, _ = self.go_to(x, y, surf_pt, tick)
            boost = energy > 30
            goal_label = "zone"

        elif t < self.dash_until and close_threats:
            ddx, ddy = self.dash_dir
            cdx, cdy = self.zone_clamp_dir(x, y, ddx, ddy, cC, cR, 12.0)
            move = self.free_dir(x, y, cdx, cdy, 10.0)
            boost = energy > 25
            goal_label = "dash"

        elif close_threats and fight and target is not None and tkind == "mech":
            goal_label = "kite_fight"
            thx, thy, _ = xyz(target["pos"])
            d = target["d"]
            ux, uy = unit(thx - x, thy - y)
            pref = 82.0
            rad = 1.0 if d > pref + 10.0 else (-1.25 if d < pref - 10.0 else 0.0)
            mx = ux * rad - uy * self.strafe * 1.15
            my = uy * rad + ux * self.strafe * 1.15
            rdx, rdy = unit(mx, my)
            cdx, cdy = self.zone_clamp_dir(x, y, rdx, rdy, cC, cR, 12.0)
            move = self.free_dir(x, y, cdx, cdy, 10.0)
            if d < 48.0 and energy > 35:
                boost = True

        elif close_threats and not fight:
            goal_label = "retreat"
            first_th = close_threats[0]
            if (self.cover is None or tick - self.cover_tick > 15) and tick >= self.slow_until:
                self.cover = self.find_cover(x, y, close_threats[:2], cC, cR)
                self.cover_tick = tick
            if self.cover is not None and math.hypot(self.cover[0] - x, self.cover[1] - y) > 3.0:
                move, _ = self.go_to(x, y, self.cover, tick)
                boost = first_th["d"] < 70.0 and energy > 30
            else:
                thx, thy, _ = xyz(first_th["pos"])
                ax, ay = unit(x - thx, y - thy)
                cx, cy = unit(surf_x - x, surf_y - y)
                stx, sty = -ay * self.strafe * 0.5, ax * self.strafe * 0.5
                rdx, rdy = unit(ax + 0.6 * cx + stx, ay + 0.6 * cy + sty)
                cdx, cdy = self.zone_clamp_dir(x, y, rdx, rdy, cC, cR, 12.0)
                move = self.free_dir(x, y, cdx, cdy)
                boost = first_th["d"] < 60.0 and energy > 35

        else:
            goal = None
            cur1 = have[1] if len(have) > 1 else None

            # 8a. Weapon pickups: only Autocannon / Laser (never Lightning)
            wanted = [k for k in ("laser", "gun") if k not in have]
            if wanted:
                best_w = None
                for pid, (kind, pp) in self.pickups.items():
                    if kind not in wanted or pid in took_fail:
                        continue
                    px, py, pz = pp
                    pd = math.hypot(px - x, py - y)
                    if pd > 155.0 or pz > z + 7.0 or math.hypot(px - ccx, py - ccy) > cR - 9.0:
                        continue
                    if best_w is None or pd < best_w[0]:
                        best_w = (pd, pid, px, py, pz, kind)
                if best_w is not None:
                    pd, pid, px, py, pz, kind = best_w
                    goal = (px, py)
                    goal_label = "get_" + kind
                    if pd < 3.5 and abs(pz - z) < 3.5:
                        out["take"] = pid
                        slot = 1 if kind == "laser" else 0
                        if len(weapons) > slot and weapons[slot] is not None:
                            out["drop"] = slot

            # 8b. XP cubes (farm whenever no enemy within 145m)
            if goal is None:
                best_cube = None
                for cid, cp in self.cube_mem.items():
                    cx, cy, cz = cp
                    cd = math.hypot(cx - x, cy - y)
                    if cd < 160.0 and abs(cz - z) < 7.0 and math.hypot(cx - ccx, cy - ccy) < cR - 9.0:
                        if best_cube is None or cd < best_cube[0]:
                            best_cube = (cd, cx, cy)
                if best_cube is not None:
                    _, cx, cy = best_cube
                    goal = (cx, cy)
                    goal_label = "cube"

            # 8c. Supply drops
            if goal is None:
                for dr in obs.get("drops") or []:
                    did = dr["id"]
                    if did in self.dead_drops:
                        continue
                    dpx, dpy, _ = xyz(dr["pos"])
                    dd = math.hypot(dpx - x, dpy - y)
                    if math.hypot(dpx - ccx, dpy - ccy) > cR - 10.0:
                        continue
                    if dd < 190.0 and dr.get("lands_in", 0) <= (dd / walk + 6.0):
                        if dd < dr.get("radius", 10.0) * 0.65:
                            self.drop_dwell[did] = self.drop_dwell.get(did, 0) + 1
                            if self.drop_dwell[did] > 45:
                                self.dead_drops.add(did)
                                continue
                            ux, uy = unit(dpx - x, dpy - y)
                            move = self.free_dir(x, y, *unit(-uy * self.strafe, ux * self.strafe), 6.0)
                            goal_label = "capture"
                        else:
                            goal = (dpx, dpy)
                            goal_label = "drop"
                        break

            # 8d. Breakable crates
            if goal is None and goal_label == "" and hp_frac > 0.35:
                best_crate = None
                for c in obs.get("crates") or []:
                    cpx, cpy, cpz = xyz(c["pos"])
                    cd = math.hypot(cpx - x, cpy - y)
                    if cd < 90.0 and abs(cpz - z) < 6.5 and math.hypot(cpx - ccx, cpy - ccy) < cR - 10.0:
                        if best_crate is None or cd < best_crate[0]:
                            best_crate = (cd, (cpx, cpy, cpz))
                if best_crate is not None:
                    cd, cp = best_crate
                    cpx, cpy, _ = cp
                    goal_label = "crate"
                    if cd > 26.0:
                        goal = (cpx, cpy)
                    else:
                        ux, uy = unit(cpx - x, cpy - y)
                        move = self.free_dir(x, y, *unit(-uy * self.strafe, ux * self.strafe), 6.0)
                    if target is None:
                        target = {"pos": cp, "vel": [0.0, 0.0, 0.0], "d": cd}
                        tkind = "crate"

            # 8e. Farmable NPCs
            if goal is None and goal_label == "" and target is not None and tkind == "npc":
                nx, ny, _ = xyz(target["pos"])
                goal = (nx, ny)
                goal_label = "npc"

            # 8f. Go to surf edge, else silent hold
            if goal is not None:
                move, arrived = self.go_to(x, y, goal, tick)
                if arrived:
                    move = (0.0, 0.0)
                gx, gy = goal
                boost = math.hypot(gx - x, gy - y) > 70.0 and energy > 75
            elif goal_label == "":
                if math.hypot(surf_x - x, surf_y - y) > 16.0:
                    move, _ = self.go_to(x, y, surf_pt, tick)
                    goal_label = "surf_edge"
                else:
                    move = (0.0, 0.0)
                    goal_label = "silent_hold"

        # 9. Anti-stuck
        self.hist.append((t, x, y))
        if len(self.hist) > 12:
            self.hist.pop(0)

        mvx, mvy = move
        wants_move = math.hypot(mvx, mvy) > 0.4
        jump = False
        if t < self.unstick_until:
            move = self.unstick_dir
            jump = True
            boost = energy > 20
            self.path = None
        elif wants_move and len(self.hist) >= 10:
            t_old, x_old, y_old = self.hist[0]
            if (t - t_old) >= 0.75 and math.hypot(x - x_old, y - y_old) < 2.2:
                a = math.atan2(mvy, mvx) + self.rng.choice((-1.0, 1.0)) * (0.9 + self.rng.random() * 0.8)
                self.unstick_dir = (math.cos(a), math.sin(a))
                self.unstick_until = t + 0.85
                self.hist.clear()

        if energy < 15:
            self.boost_lock = True
        elif energy > 45:
            self.boost_lock = False
        if self.boost_lock:
            boost = False
        if jump and grounded is False:
            boost = energy > 15 and not self.boost_lock

        # 10. Aiming & firing
        fire = [False, False]
        aim_yaw, aim_pitch = cur_yaw, cur_pitch

        if target is not None:
            tpx, tpy, tpz = xyz(target["pos"])
            tv = target.get("vel") or [0.0, 0.0, 0.0]
            tz = tpz + (5.5 if tkind == "mech" else 2.2 if tkind == "npc" else 1.5)

            gun = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
            sp = ((gun.get("stats") or {}).get("speed", 252.0)) if gun else 252.0
            lpx, lpy, lpz = self.lead((x, y, z + muzzle), (tpx, tpy, tz), tv, sp)

            hd = math.hypot(lpx - x, lpy - y)
            aim_yaw = math.atan2(lpy - y, lpx - x)
            aim_pitch = max(-0.75, min(1.35, math.atan2(lpz - (z + muzzle), max(hd, 1e-3))))

            err = abs(norm_ang(cur_yaw - aim_yaw))
            d = target["d"]
            tol = math.atan2(3.4, max(d, 5.0)) + 0.055
            clear = d < 22.0 or w.los((x, y, z + muzzle), (tpx, tpy, tz))

            for i, wp in enumerate(weapons):
                if not wp:
                    continue
                k = wp["kind"]
                if k == "gun":
                    if clear and err < tol and d < 205.0 and wp.get("ammo", 1) > 0 and not wp.get("reload"):
                        fire[i] = True
                elif k == "laser":
                    if tkind == "mech" and d < 245.0 and clear:
                        ch = wp.get("charge", 0.0)
                        if ch >= 0.72:
                            # release the trigger to fire once aligned; keep holding otherwise
                            fire[i] = False if err < tol * 1.4 else True
                        elif wp.get("cooldown", 0.0) < 0.15:
                            fire[i] = True
                elif k == "lightning":
                    rng = (wp.get("stats") or {}).get("range", 45.0)
                    if tkind != "crate" and d < rng - 2.0 and err < 0.40 and energy > 8 and clear:
                        fire[i] = True
        else:
            snd = None
            for s in obs.get("sounds") or []:
                if s.get("kind") in SOUND_KINDS and s.get("dist") != "far" and s.get("bearing") is not None:
                    snd = s
                    break
            mvx, mvy = move
            if virtual:
                aim_yaw = self.last_dmg_bearing
            elif snd is not None:
                aim_yaw = snd["bearing"]
            elif goal_label == "silent_hold":
                aim_yaw = norm_ang(cur_yaw + 0.42)
            elif math.hypot(mvx, mvy) > 0.3:
                aim_yaw = math.atan2(mvy, mvx)
            else:
                aim_yaw = norm_ang(cur_yaw + 0.25)
            aim_pitch = 0.0

        gun_w = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
        if gun_w and not any(fire) and not gun_w.get("reload"):
            ammo = gun_w.get("ammo", 12)
            if ammo == 0 or (ammo <= 5 and (target is None or target["d"] > 70.0)):
                out["reload"] = True

        mvx, mvy = move
        mv = math.hypot(mvx, mvy)
        if mv > 1.0:
            mvx, mvy = mvx / mv, mvy / mv

        out.update(
            move=[round(mvx, 3), round(mvy, 3)],
            boost=bool(boost),
            jump=bool(jump),
            aim=[round(aim_yaw, 4), round(aim_pitch, 4)],
            fire=fire,
        )

        try:
            mf.draw.label(f"{goal_label} hp:{hp:.0f} e:{energy:.0f} th:{len(close_threats)}")
        except Exception:
            pass

        if (time.perf_counter() - t0) > 0.015:
            self.slow_until = tick + 6

        return out


mf.run(Bot)
