"""Survival-first mechfight bot: stay in the zone, loot lightning/laser, hide from squads, shoot with lead."""
import math
import random
import time
import mechfight as mf
from world import World

TAU = math.tau
UP_PREF = ["armor", "plating", "gun_damage", "gun_rate", "servos", "laser_damage", "laser_charge", "nanites", "gyros", "capacitor", "sensors", "reactor", "jets", "laser_damage", "laser_charge",
           "gun_mag", "gun_velocity", "lightning_range"]
WEAPON_RANK = {"laser": 2}
RUNNER = True  # experiment: no fighting, no looting, just flee and hold the zone edge


def norm_ang(a):
    return (a + math.pi) % TAU - math.pi


def unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 1e-6 else (0.0, 0.0)


class Bot:
    name = "runner-1"

    def __init__(self, hello):
        self.rng = random.Random(hello.get("seed", 1))
        self.cfg = hello["config"]
        self.world = None
        err = None
        for d in (hello["map"]["dir"], "/arena"):
            try:
                self.world = World(d)
                break
            except Exception as e:  # e.g. Windows check.py rewrites /arena to C:\arena
                err = e
        if self.world is None:
            raise err
        self.path = None
        self.path_goal = None
        self.path_tick = -999
        self.pi = 0
        self.pickups = {}
        self.cube_mem = {}
        self.hist = []
        self.unstick_until = -1
        self.unstick_dir = (1, 0)
        self.boost_lock = False
        self.cover = None
        self.cover_tick = -999
        self.strafe = 1
        self.strafe_flip = 0
        self.strafe_mag = 1.0
        self.last_dmg_t = -99
        self.last_dmg_bearing = 0.0
        self.last_threat_t = -99
        self.dead_drops = set()
        self.dbg = ""

    # ------------------------------------------------------------ helpers
    def zone_circle(self, z, t):
        if t <= z["shrink_start"]:
            return z["center"], z["radius"]
        if t >= z["shrink_end"]:
            return z["next_center"], z["next_radius"]
        f = (t - z["shrink_start"]) / max(1e-6, z["shrink_end"] - z["shrink_start"])
        c = (z["center"][0] + (z["next_center"][0] - z["center"][0]) * f,
             z["center"][1] + (z["next_center"][1] - z["center"][1]) * f)
        return c, z["radius"] + (z["next_radius"] - z["radius"]) * f

    def free_dir(self, x, y, dx, dy, dist=14.0):
        w = self.world
        for a in (0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, 1.4, -1.4, 1.75, -1.75, 2.1, -2.1, 2.5, -2.5, 3.14):
            ca, sa = math.cos(a), math.sin(a)
            rx, ry = dx * ca - dy * sa, dx * sa + dy * ca
            if w.walk_line(x, y, x + rx * dist, y + ry * dist):
                return rx, ry
        return dx, dy

    def go_to(self, me, goal, tick):
        """Return unit direction toward goal using cached A* path."""
        x, y = me[0], me[1]
        w = self.world
        d = math.hypot(goal[0] - x, goal[1] - y)
        if d < 6:
            return unit(goal[0] - x, goal[1] - y), True
        if d < 45 and w.walk_line(x, y, goal[0], goal[1]):
            self.path = None
            return unit(goal[0] - x, goal[1] - y), False
        replan = (self.path is None or self.path_goal is None
                  or math.hypot(goal[0] - self.path_goal[0], goal[1] - self.path_goal[1]) > 12
                  or tick - self.path_tick > 30)
        if replan:
            self.path = w.plan(x, y, goal[0], goal[1])
            self.path_goal = goal
            self.path_tick = tick
            self.pi = 0
        if not self.path:
            return unit(goal[0] - x, goal[1] - y), False
        p = self.path
        # nearest waypoint ahead
        while self.pi < len(p) - 1 and math.hypot(p[self.pi][0] - x, p[self.pi][1] - y) < 5:
            self.pi += 1
        tgt = p[self.pi]
        for j in range(min(len(p) - 1, self.pi + 8), self.pi, -1):
            if w.walk_line(x, y, p[j][0], p[j][1]):
                tgt = p[j]
                break
        return unit(tgt[0] - x, tgt[1] - y), False

    def lead(self, src, tpos, tvel, speed):
        px, py, pz = tpos
        for _ in range(3):
            tt = math.dist((px, py, pz), src) / speed
            px, py, pz = tpos[0] + tvel[0] * tt, tpos[1] + tvel[1] * tt, tpos[2] + tvel[2] * tt * 0.0
        return px, py, pz

    def find_cover(self, me, threats, C, R, t0):
        w = self.world
        x, y, z = me
        best = None
        eye_me = z + 7.0
        for dist in (8, 16, 26, 36):
            for k in range(14):
                a = TAU * k / 14 + self.rng.random() * 0.2
                cx, cy = x + math.cos(a) * dist, y + math.sin(a) * dist
                if w.is_blocked(cx, cy) or not w.walk_line(x, y, cx, cy, 3.0):
                    continue
                if math.hypot(cx - C[0], cy - C[1]) > R * 0.9:
                    continue
                gz = w.h(cx, cy)
                ok = True
                score = dist
                for th in threats:
                    tp = th["pos"]
                    if w.los((tp[0], tp[1], tp[2] + 8), (cx, cy, gz + 7)):
                        ok = False
                        break
                    # prefer farther from threats
                    score -= 0.15 * math.hypot(cx - tp[0], cy - tp[1])
                if ok and (best is None or score < best[0]):
                    best = (score, (cx, cy))
            if best and dist >= 16:
                break
            if time.perf_counter() - t0 > 0.03:
                break
        return best[1] if best else None

    # ------------------------------------------------------------ main
    def act(self, obs):
        t0 = time.perf_counter()
        me = obs["self"]
        tick = obs["tick"]
        t = obs["t"]
        pos = me["pos"]
        x, y, z = pos
        w = self.world
        act = {"tick": tick}
        weapons = me["weapons"]
        have = [wp["kind"] if wp else None for wp in weapons]
        stats = me["stats"]
        eye_z = z + stats.get("eye_height", 8)
        muzzle = stats.get("muzzle_height", 7)
        walk = stats.get("walk_speed", 11.7)

        # ---- upgrade offers
        if obs["offer"]:
            ch = [c["id"] for c in obs["offer"]["choices"]]
            pick = 0
            bestr = 999
            for i, cid in enumerate(ch):
                r = UP_PREF.index(cid) if cid in UP_PREF else 500
                if r < bestr:
                    bestr, pick = r, i
            act["pick"] = pick

        # ---- events
        took_fail = set()
        for e in obs["events"]:
            ty = e["type"]
            if ty == "damaged" and e.get("weapon") != "zone" and e.get("bearing") is not None:
                self.last_dmg_t = t
                self.last_dmg_bearing = e["bearing"]
            elif ty == "took":
                self.pickups.pop(e["id"], None)
            elif ty == "take_failed":
                if e["reason"] in ("gone", "have"):
                    self.pickups.pop(e["id"], None)
                took_fail.add(e["id"])
            elif ty == "pickup":
                pass

        # ---- memory of loot
        seen_ids = set()
        for p in obs["pickups"]:
            seen_ids.add(p["id"])
            self.pickups[p["id"]] = (p["kind"], p["pos"])
        for pid in list(self.pickups):
            if pid not in seen_ids:
                pp = self.pickups[pid][1]
                if math.hypot(pp[0] - x, pp[1] - y) < 25:
                    del self.pickups[pid]
        self.cube_mem = {c["id"]: c["pos"] for c in obs["cubes"]} | {
            k: v for k, v in self.cube_mem.items() if math.hypot(v[0] - x, v[1] - y) > 20 and k not in {c["id"] for c in obs["cubes"]}}

        # ---- zone
        zone = obs["zone"]
        use_next = t >= zone["shrink_start"] - 12
        if use_next:
            C, R = zone["next_center"], zone["next_radius"]
        else:
            C, R = zone["center"], zone["radius"]
        cC, cR = self.zone_circle(zone, t)
        dC = math.hypot(x - C[0], y - C[1])
        safe_r = max(R * 0.8, min(R, 18.0))
        zone_unsafe = (not me["in_zone"]) or dC > safe_r or math.hypot(x - cC[0], y - cC[1]) > cR - 6

        # ---- actors
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for m in mechs:
            m["d"] = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
        mechs.sort(key=lambda m: m["d"])
        threats = [m for m in mechs if m["d"] < 220]
        if threats:
            self.last_threat_t = t
        virtual = False
        if not threats and t - self.last_dmg_t < 3.0:
            b = self.last_dmg_bearing
            threats = [{"pos": [x + math.cos(b) * 60, y + math.sin(b) * 60, z], "vel": [0, 0, 0], "d": 60,
                        "id": -1, "hp": 1000, "weapons": [], "virtual": True}]
            virtual = True
        hp_frac = me["hp"] / max(1, me["max_hp"])

        # ---- visible/aim target
        target = None
        tkind = None
        for m in mechs:
            if m["d"] < 200:
                tp = m["pos"]
                if w.los((x, y, eye_z), (tp[0], tp[1], tp[2] + 4.5)) or m["d"] < 45:
                    target, tkind = m, "mech"
                    break
        if target is None:
            for n in npcs:
                nd = math.hypot(n["pos"][0] - x, n["pos"][1] - y)
                if (not RUNNER) and nd < 60 and hp_frac > 0.4:
                    n["d"] = nd
                    target, tkind = n, "npc"
                    break

        # ---- aggression plan: 1v1 engage at long range, retreat-while-shooting if not isolated
        n_all = len([m for m in mechs if m["d"] < 220])
        engage_ok = (not RUNNER) and (not virtual) and tkind == "mech" and n_all == 1 and hp_frac > 0.35 and t < 300
        npc_fight = (not RUNNER) and tkind == "npc" and not threats
        edge_r = max(R * 0.8, min(R, 15.0))
        ex, ey = unit(x - C[0], y - C[1]) if dC > 1 else (1.0, 0.0)
        edge_pt = (C[0] + ex * edge_r, C[1] + ey * edge_r)
        out_zone = (not me["in_zone"]) or math.hypot(x - cC[0], y - cC[1]) > cR - 6

        if t > self.strafe_flip:
            self.strafe = -self.strafe
            self.strafe_flip = t + 0.4 + self.rng.random() * 0.7
            self.strafe_mag = 0.75 + self.rng.random() * 0.25

        move = (0.0, 0.0)
        boost = False
        goal_label = ""
        loot_goal = None

        if out_zone:
            move, _ = self.go_to(pos, edge_pt, tick)
            boost = True
            goal_label = "zone"
        elif threats or npc_fight:
            tgt = target if target is not None else threats[0]
            th = tgt["pos"]
            d = tgt.get("d", math.hypot(th[0] - x, th[1] - y))
            ux, uy = unit(th[0] - x, th[1] - y)
            sm = self.strafe * self.strafe_mag
            if npc_fight:
                goal_label = "npc"
                rad = 1.0 if d > 30 else (-1.0 if d < 18 else 0.0)
            elif engage_ok:
                goal_label = "duel"
                rad = 1.0 if d > 110 else (-1.0 if d < 70 else 0.0)
            else:
                goal_label = "retreat"
                rad = -1.0
            mx = ux * rad - uy * sm * 0.9
            my = uy * rad + ux * sm * 0.9
            if goal_label == "retreat":
                # run away from the pack, but stay on the inland side of the zone edge
                cxu, cyu = unit(C[0] - x, C[1] - y)
                if dC > edge_r:
                    mx, my = mx + cxu * 0.8, my + cyu * 0.8
                if len(threats) > 1:
                    ax = sum(m["pos"][0] for m in threats) / len(threats)
                    ay = sum(m["pos"][1] for m in threats) / len(threats)
                    ux2, uy2 = unit(ax - x, ay - y)
                    mx = -ux2 + (-uy2 * sm * 0.7)
                    my = -uy2 + (ux2 * sm * 0.7)
                    if dC > edge_r:
                        mx, my = mx + cxu * 0.8, my + cyu * 0.8
            elif dC > edge_r * 1.1:
                cxu, cyu = unit(C[0] - x, C[1] - y)
                mx, my = mx + cxu * 0.6, my + cyu * 0.6
            move = self.free_dir(x, y, *unit(mx, my), 12.0)
            boost = goal_label == "retreat" or (goal_label == "duel" and d > 140)
        elif RUNNER:
            goal_label = "edge"
            if dC > edge_r * 1.1 or dC < edge_r * 0.7:
                move, _ = self.go_to(pos, edge_pt, tick)
                move = (move[0] * 0.8, move[1] * 0.8)
        else:
            # ---- loot / objectives
            goal = None
            # supply drops
            for dr in obs["drops"]:
                dp = dr["pos"]
                dd = math.hypot(dp[0] - x, dp[1] - y)
                if dr["id"] in self.dead_drops:
                    continue
                if math.hypot(dp[0] - cC[0], dp[1] - cC[1]) > cR:
                    continue
                if dd < 250 and (dr["lands_in"] <= dd / walk + 6):
                    goal = (dp[0], dp[1])
                    goal_label = "drop"
                    if dd < dr["radius"] * 0.5:
                        goal = None
                        move = (0.0, 0.0)
                        goal_label = "capture"
                    break
            if goal is None and goal_label != "capture":
                wants = []
                for pid, (kind, pp) in self.pickups.items():
                    if kind not in WEAPON_RANK or pid in took_fail:
                        continue
                    cur1 = have[1] if len(have) > 1 else None
                    if kind in have:
                        continue
                    pd = math.hypot(pp[0] - x, pp[1] - y)
                    if pd > 110 or pp[2] > z + 6 or math.hypot(pp[0] - cC[0], pp[1] - cC[1]) > cR * 0.95:
                        continue
                    wants.append((pd - 30 * WEAPON_RANK[kind], pid, pp, kind))
                if wants:
                    wants.sort()
                    _, pid, pp, kind = wants[0]
                    goal = (pp[0], pp[1])
                    goal_label = "weapon " + kind
                    if math.hypot(pp[0] - x, pp[1] - y) < 3.3 and abs(pp[2] - z) < 3.5:
                        act["take"] = pid
                        if len(weapons) > 1 and weapons[1] is not None:
                            act["drop"] = 1
            if goal is None and goal_label != "capture":
                best = None
                for cid, cp in self.cube_mem.items():
                    cd = math.hypot(cp[0] - x, cp[1] - y)
                    if cd < 60 and abs(cp[2] - z) < 5 and math.hypot(cp[0] - cC[0], cp[1] - cC[1]) < cR * 0.95:
                        if best is None or cd < best[0]:
                            best = (cd, cp)
                if best:
                    goal = (best[1][0], best[1][1])
                    goal_label = "cube"
            crate = None
            if goal is None and goal_label != "capture" and hp_frac > 0.5:
                for c in obs["crates"]:
                    cd = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
                    if cd < 55 and abs(c["pos"][2] - z) < 6 and math.hypot(c["pos"][0] - C[0], c["pos"][1] - C[1]) < R * 0.9:
                        if crate is None or cd < crate[0]:
                            crate = (cd, c)
                if crate:
                    cd, c = crate
                    cp = c["pos"]
                    goal_label = "crate"
                    if cd > 22:
                        goal = (cp[0], cp[1])
                    else:
                        move = (0.0, 0.0)
                    target, tkind = {"pos": cp, "vel": [0, 0, 0], "d": cd, "kind": "crate"}, "crate"
            if goal is not None:
                move, arrived = self.go_to(pos, goal, tick)
                if arrived:
                    move = (0.0, 0.0)
                boost = (math.hypot(goal[0] - x, goal[1] - y) > 60 and me["energy"] > 70)
            elif goal_label == "":
                # idle: hold position, drift toward zone center slowly if far
                goal_label = "edge"
                if dC > edge_r * 1.1 or dC < edge_r * 0.7:
                    move, _ = self.go_to(pos, edge_pt, tick)
                    move = (move[0] * 0.8, move[1] * 0.8)

        # ---- stuck handling
        self.hist.append((t, x, y))
        if len(self.hist) > 12:
            self.hist.pop(0)
        wants_move = math.hypot(*move) > 0.5
        jump = False
        if t < self.unstick_until:
            move = self.unstick_dir
            jump = True
            boost = me["energy"] > 15
            self.path = None
        elif wants_move and len(self.hist) >= 10:
            t_old, x_old, y_old = self.hist[0]
            if t - t_old >= 0.8 and math.hypot(x - x_old, y - y_old) < 2.5:
                a = math.atan2(move[1], move[0]) + self.rng.choice((-1, 1)) * self.rng.random() * 1.2
                self.unstick_dir = (math.cos(a), math.sin(a))
                self.unstick_until = t + 1.0
                self.hist.clear()

        # ---- boost hysteresis (keep energy for lightning)
        if me["energy"] < 12:
            self.boost_lock = True
        elif me["energy"] > 40:
            self.boost_lock = False
        if self.boost_lock:
            boost = False
        if jump and me["grounded"] is False:
            boost = me["energy"] > 10

        # ---- aiming + firing
        fire = [False, False]
        aim_yaw, aim_pitch = me["aim"]
        if target is not None:
            tp = target["pos"]
            tv = target.get("vel", [0, 0, 0])
            tz = tp[2] + (4.5 if tkind == "mech" else 1.8 if tkind == "npc" else 1.5)
            gun = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
            sp = gun["stats"]["speed"] if gun else 252
            lp = self.lead((x, y, z + muzzle), (tp[0], tp[1], tz), (tv[0], tv[1], 0), sp)
            hd = math.hypot(lp[0] - x, lp[1] - y)
            aim_yaw = math.atan2(lp[1] - y, lp[0] - x)
            aim_pitch = max(-0.7, min(1.3, math.atan2(lp[2] - (z + muzzle), max(hd, 1e-3))))
            err = abs(norm_ang(me["aim"][0] - aim_yaw))
            d = target["d"]
            tol = math.atan2(2.2, max(d, 5)) + 0.03
            clear = w.los((x, y, z + muzzle), (tp[0], tp[1], tz)) or d < 20
            for i, wp in enumerate(weapons):
                if not wp:
                    continue
                k = wp["kind"]
                if k == "gun":
                    if clear and err < tol and d < 190 and wp.get("ammo", 0) > 0 and wp.get("reload", 0) == 0:
                        fire[i] = True
                elif k == "lightning":
                    if tkind != "crate" and d < wp["stats"].get("range", 45) - 3 and err < 0.35 and me["energy"] > 8 and clear:
                        fire[i] = True
                elif k == "laser":
                    if tkind == "mech" and d < 240 and clear:
                        ch = wp.get("charge", 0)
                        if ch >= 0.72 and err >= tol * 1.2 and ch < 1.4:
                            fire[i] = True
                        elif ch >= 0.72:
                            fire[i] = False
                        elif wp["cooldown"] < 0.15 and ch < 0.72:
                            fire[i] = True
        else:
            # scan along movement / toward heard sounds
            snd = None
            for s in obs["sounds"]:
                if s["kind"] in ("gun", "laser", "lightning", "mortar", "explosion") and s["dist"] != "far":
                    snd = s
                    break
            if virtual:
                aim_yaw = self.last_dmg_bearing
            elif snd:
                aim_yaw = snd["bearing"]
            elif math.hypot(*move) > 0.3:
                aim_yaw = math.atan2(move[1], move[0])
            else:
                aim_yaw = me["aim"][0] + 0.15
            aim_pitch = 0.0
        gun_w = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
        if gun_w and not any(fire) and (gun_w.get("ammo", 12) == 0 or
                                        (gun_w.get("ammo", 12) < 6 and not threats)) and gun_w.get("reload", 0) == 0:
            act["reload"] = True

        mv = math.hypot(*move)
        if mv > 1:
            move = (move[0] / mv, move[1] / mv)
        act.update(move=[round(move[0], 3), round(move[1], 3)], boost=bool(boost), jump=bool(jump),
                   aim=[round(aim_yaw, 4), round(aim_pitch, 4)], fire=fire)
        mf.draw.label(f"{goal_label} hp{me['hp']:.0f} e{me['energy']:.0f} th{len(threats)}")
        if self.path and goal_label not in ("hide", "fight"):
            mf.draw.line([x, y, z + 1], [self.path[-1][0], self.path[-1][1], z + 1], "#40c0ff")
        return act


mf.run(Bot)
