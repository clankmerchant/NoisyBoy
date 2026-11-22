"""Top-16 Meta Mech Royale Bot: Autocannon+Laser, Outer-Ring Surfing, Reactive Dash-Kite, Cube Snowball."""
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
    "capacitor", "reactor", "gun_mag", "sensors", "jets"
]


def norm_ang(a):
    return (a + math.pi) % TAU - math.pi


def unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 1e-6 else (0.0, 0.0)


class Bot:
    name = "apex-v2-clean"

    def __init__(self, hello):
        self.rng = random.Random(hello.get("seed", 1))
        self.cfg = hello.get("config", {})
        self.world = None
        err = None
        for d in (hello["map"]["dir"], "/arena"):
            try:
                self.world = World(d)
                break
            except Exception as e:
                err = e
        if self.world is None:
            raise err

        self.path = None
        self.path_goal = None
        self.path_tick = -999
        self.pi = 0
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
        self.dash_dir = (0.0, 0.0)
        self.slow_until = -1

        gc.collect()
        gc.freeze()
        gc.disable()

    def zone_circle(self, z, t):
        zcx, zcy = z["center"]
        ncx, ncy = z["next_center"]
        if t <= z["shrink_start"]:
            return (zcx, zcy), z["radius"]
        if t >= z["shrink_end"]:
            return (ncx, ncy), z["next_radius"]
        f = (t - z["shrink_start"]) / max(1e-6, z["shrink_end"] - z["shrink_start"])
        return (zcx + (ncx - zcx) * f, zcy + (ncy - zcy) * f), z["radius"] + (z["next_radius"] - z["radius"]) * f

    def zone_clamp_dir(self, x, y, dx, dy, cC, cR, margin=12.0):
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
            1.40, -1.40, 1.80, -1.80, 2.30, -2.30, 3.14
        )
        for a in angles:
            ca, sa = math.cos(a), math.sin(a)
            rx, ry = dx * ca - dy * sa, dx * sa + dy * ca
            if w.walk_line(x, y, x + rx * dist, y + ry * dist, step=1.5):
                return rx, ry
        return dx, dy

    def go_to(self, me, goal, tick):
        x, y, _ = me
        gx, gy = goal
        w = self.world
        d = math.hypot(gx - x, gy - y)

        if d < 5.0:
            return unit(gx - x, gy - y), True

        if d < 40.0 and w.walk_line(x, y, gx, gy, step=1.5):
            self.path = None
            return unit(gx - x, gy - y), False

        plan_gx, plan_gy = gx, gy
        if d > 105.0:
            ux, uy = unit(gx - x, gy - y)
            plan_gx, plan_gy = x + ux * 105.0, y + uy * 105.0

        replan = self.path is None or self.path_goal is None or tick - self.path_tick > 26
        if not replan and self.path_goal is not None:
            pgx, pgy = self.path_goal
            if math.hypot(plan_gx - pgx, plan_gy - pgy) > 14.0:
                replan = True
        if replan and tick < self.slow_until and self.path:
            replan = False

        if replan:
            self.path = w.plan(x, y, plan_gx, plan_gy, maxexp=3200, budget=0.007)
            self.path_goal = (plan_gx, plan_gy)
            self.path_tick = tick
            self.pi = 0

        if not self.path:
            return self.free_dir(x, y, *unit(gx - x, gy - y)), False

        p = self.path
        while self.pi < len(p) - 1:
            wx, wy = p[self.pi]
            if math.hypot(wx - x, wy - y) < 5.5:
                self.pi += 1
            else:
                break

        tgt_x, tgt_y = p[self.pi]
        for j in range(min(len(p) - 1, self.pi + 7), self.pi, -1):
            jx, jy = p[j]
            if w.walk_line(x, y, jx, jy, step=1.5):
                tgt_x, tgt_y = jx, jy
                break

        return unit(tgt_x - x, tgt_y - y), False

    def lead(self, src, tpos, tvel, speed):
        tx, ty, tz = tpos
        vx, vy, vz = tvel if len(tvel) >= 3 else (0.0, 0.0, 0.0)
        px, py, pz = tx, ty, tz
        for _ in range(3):
            tt = math.dist((px, py, pz), src) / max(speed, 1.0) + 0.045
            px = tx + vx * tt
            py = ty + vy * tt
            pz = tz + vz * tt * 0.35
        return px, py, pz

    def find_cover(self, me, threats, cC, cR, t0):
        w = self.world
        x, y, _ = me
        ccx, ccy = cC
        best_score = 1e18
        best_pt = None
        for dist in (12, 22):
            for k in range(10):
                a = TAU * k / 10 + self.rng.random() * 0.3
                cx, cy = x + math.cos(a) * dist, y + math.sin(a) * dist
                if w.is_blocked(cx, cy) or not w.walk_line(x, y, cx, cy, step=2.0):
                    continue
                if math.hypot(cx - ccx, cy - ccy) > cR - 10.0:
                    continue
                gz = w.h(cx, cy)
                ok = True
                score = dist - 0.25 * gz
                for th in threats:
                    tpx, tpy, tpz = th["pos"]
                    if w.los((tpx, tpy, tpz + 7.5), (cx, cy, gz + 6.5)):
                        ok = False
                        break
                    score -= 0.18 * math.hypot(cx - tpx, cy - tpy)
                if ok and score < best_score:
                    best_score = score
                    best_pt = (cx, cy)
            if best_pt is not None:
                break
            if time.perf_counter() - t0 > 0.005:
                break
        return best_pt
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
        eye_z = z + stats.get("eye_height", 8.0)
        muzzle = stats.get("muzzle_height", 7.0)
        walk = stats.get("walk_speed", 11.7)
        hp_frac = me["hp"] / max(1.0, me["max_hp"])

        if tick % 250 == 0 and tick > 0:
            gc.collect(0)

        # 1. Upgrade selection
        if obs["offer"]:
            choices = [c["id"] for c in obs["offer"]["choices"]]
            pick = 0
            best_rank = 999
            for i, cid in enumerate(choices):
                r = UP_PREF.index(cid) if cid in UP_PREF else 500
                if r < best_rank:
                    best_rank, pick = r, i
            act["pick"] = pick

        # 2. Events & Reactive Dash
        took_fail = set()
        for e in obs["events"]:
            ty = e["type"]
            if ty == "damaged" and e.get("weapon") != "zone" and e.get("bearing") is not None:
                self.last_dmg_t = t
                b = e["bearing"]
                self.last_dmg_bearing = b
                away_ang = b + math.pi + self.strafe * 0.45
                self.dash_dir = (math.cos(away_ang), math.sin(away_ang))
                self.dash_until = t + 0.45
            elif ty == "took":
                self.pickups.pop(e["id"], None)
            elif ty == "take_failed":
                if e.get("reason") in ("gone", "have"):
                    self.pickups.pop(e["id"], None)
                took_fail.add(e["id"])

        # 3. Loot & Cube Memory
        seen_ids = set()
        for p in obs["pickups"]:
            seen_ids.add(p["id"])
            self.pickups[p["id"]] = (p["kind"], p["pos"])
        for pid in list(self.pickups):
            if pid not in seen_ids:
                _, pp = self.pickups[pid]
                px, py, _ = pp
                if math.hypot(px - x, py - y) < 30.0:
                    del self.pickups[pid]

        cur_cubes = {c["id"]: c["pos"] for c in obs["cubes"]}
        self.cube_mem = cur_cubes | {
            k: v for k, v in self.cube_mem.items()
            if k not in cur_cubes and math.hypot(v[0] - x, v[1] - y) > 25.0
        }

        # 4. Outer-Ring Zone Surfing
        zone = obs["zone"]
        cC, cR = self.zone_circle(zone, t)
        ccx, ccy = cC
        ncx, ncy = zone["next_center"]
        nR = zone["next_radius"]

        vx_z, vy_z = unit(ccx - ncx, ccy - ncy)
        if math.hypot(vx_z, vy_z) < 0.1:
            vx_z, vy_z = unit(x - ccx, y - ccy)
        surf_r = max(nR * 0.75, cR - 14.0)
        surf_pt = (ccx + vx_z * surf_r, ccy + vy_z * surf_r)

        dist_cur_center = math.hypot(x - ccx, y - ccy)
        zone_unsafe = (not me["in_zone"]) or dist_cur_center > (cR - 8.5)

        # 5. Enemy & NPC Scan
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for m in mechs:
            mx, my, _ = m["pos"]
            m["d"] = math.hypot(mx - x, my - y)
        mechs.sort(key=lambda m: m["d"])

        close_threats = [m for m in mechs if m["d"] < 145.0]
        virtual = False
        if not close_threats and (t - self.last_dmg_t) < 2.2:
            b = self.last_dmg_bearing
            close_threats = [{
                "pos": [x + math.cos(b) * 85.0, y + math.sin(b) * 85.0, z],
                "vel": [0.0, 0.0, 0.0],
                "d": 85.0,
                "id": -1,
                "hp": 1000,
                "weapons": [],
                "virtual": True,
            }]
            virtual = True

        # 6. Target Selection
        target = None
        tkind = None
        for m in mechs:
            if m["d"] < 215.0:
                tpx, tpy, tpz = m["pos"]
                if w.los((x, y, eye_z), (tpx, tpy, tpz + 5.5)) or m["d"] < 38.0:
                    target, tkind = m, "mech"
                    break

        if target is None:
            best_npc = None
            for n in npcs:
                nx, ny, _ = n["pos"]
                nd = math.hypot(nx - x, ny - y)
                if nd < 85.0 and hp_frac > 0.25:
                    n["d"] = nd
                    if best_npc is None or nd < best_npc["d"]:
                        best_npc = n
            if best_npc is not None:
                target, tkind = best_npc, "npc"

        # 7. Combat Decision & Fast Strafe
        fight = False
        if tkind == "mech" and not virtual:
            near_cnt = sum(1 for m in mechs if m["d"] < 95.0)
            enemy_hp = target.get("hp", 1000)
            if near_cnt <= 2 and hp_frac > 0.30 and target["d"] < 185.0:
                fight = True
            elif enemy_hp < me["hp"] * 0.65 and hp_frac > 0.20:
                fight = True
        elif tkind == "npc":
            fight = True

        if t > self.strafe_flip:
            self.strafe = -self.strafe
            self.strafe_flip = t + 0.22 + self.rng.random() * 0.26

        # 8. Movement State Machine
        move = (0.0, 0.0)
        boost = False
        goal_label = ""

        if zone_unsafe:
            move, _ = self.go_to(pos, surf_pt, tick)
            boost = me["energy"] > 30
            goal_label = "zone"

        elif t < self.dash_until and close_threats:
            ddx, ddy = self.dash_dir
            cdx, cdy = self.zone_clamp_dir(x, y, ddx, ddy, cC, cR, margin=12.0)
            move = self.free_dir(x, y, cdx, cdy, dist=10.0)
            boost = me["energy"] > 25
            goal_label = "dash"

        elif close_threats and fight and target is not None and tkind == "mech":
            goal_label = "kite_fight"
            thx, thy, _ = target["pos"]
            d = target["d"]
            ux, uy = unit(thx - x, thy - y)
            pref = 82.0
            rad = 1.0 if d > pref + 10.0 else (-1.25 if d < pref - 10.0 else 0.0)
            mx = ux * rad - uy * self.strafe * 1.15
            my = uy * rad + ux * self.strafe * 1.15
            rdx, rdy = unit(mx, my)
            cdx, cdy = self.zone_clamp_dir(x, y, rdx, rdy, cC, cR, margin=12.0)
            move = self.free_dir(x, y, cdx, cdy, dist=10.0)
            if d < 48.0 and me["energy"] > 35:
                boost = True

        elif close_threats and not fight:
            goal_label = "retreat"
            thr = close_threats[:3]
            if (self.cover is None or tick - self.cover_tick > 10) and tick >= self.slow_until:
                self.cover = self.find_cover(pos, thr, cC, cR, t0)
                self.cover_tick = tick
            first_th = close_threats[0]
            if self.cover is not None and math.hypot(self.cover[0] - x, self.cover[1] - y) > 3.0:
                move, _ = self.go_to(pos, self.cover, tick)
                boost = first_th["d"] < 70.0 and me["energy"] > 30
            else:
                thx, thy, _ = first_th["pos"]
                sx, sy = surf_pt
                ax, ay = unit(x - thx, y - thy)
                cx, cy = unit(sx - x, sy - y)
                stx, sty = -ay * self.strafe * 0.5, ax * self.strafe * 0.5
                rdx, rdy = unit(ax + 0.6 * cx + stx, ay + 0.6 * cy + sty)
                cdx, cdy = self.zone_clamp_dir(x, y, rdx, rdy, cC, cR, margin=12.0)
                move = self.free_dir(x, y, cdx, cdy)
                boost = first_th["d"] < 60.0 and me["energy"] > 35

        else:
            goal = None

            # 5a. Laser Pickup
            if "laser" not in have:
                best_laser = None
                cur1 = have[1] if len(have) > 1 else None
                for pid, (kind, pp) in self.pickups.items():
                    if kind != "laser" or pid in took_fail:
                        continue
                    if cur1 is not None and cur1 != "lightning":
                        continue
                    px, py, pz = pp
                    pd = math.hypot(px - x, py - y)
                    if pd > 155.0 or pz > z + 7.0 or math.hypot(px - ccx, py - ccy) > cR - 9.0:
                        continue
                    if best_laser is None or pd < best_laser[0]:
                        best_laser = (pd, pid, px, py, pz)
                if best_laser is not None:
                    pd, pid, px, py, pz = best_laser
                    goal = (px, py)
                    goal_label = "get_laser"
                    if pd < 3.5 and abs(pz - z) < 3.5:
                        act["take"] = pid
                        if len(weapons) > 1 and weapons[1] is not None:
                            act["drop"] = 1

            # 5b. XP Cubes
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
            # 5c. Supply Drops
            if goal is None:
                for dr in obs["drops"]:
                    did = dr["id"]
                    if did in self.dead_drops:
                        continue
                    dp = dr["pos"]
                    dd = math.hypot(dp[0] - x, dp[1] - y)
                    if math.hypot(dp[0] - cC[0], dp[1] - cC[1]) > cR - 10.0:
                        continue
                    if dd < 190.0 and dr["lands_in"] <= (dd / walk + 6.0):
                        if dd < dr["radius"] * 0.65:
                            self.drop_dwell[did] = self.drop_dwell.get(did, 0) + 1
                            if self.drop_dwell[did] > 45:
                                self.dead_drops.add(did)
                                continue
                            ux, uy = unit(dp[0] - x, dp[1] - y)
                            move = self.free_dir(x, y, *unit(-uy * self.strafe, ux * self.strafe), dist=6.0)
                            goal_label = "capture"
                        else:
                            goal = (dp[0], dp[1])
                            goal_label = "drop"
                        break

            # 5d. Breakable Crates
            if goal is None and goal_label != "capture" and hp_frac > 0.35:
                best_crate = None
                for c in obs["crates"]:
                    cp = c["pos"]
                    cd = math.hypot(cp[0] - x, cp[1] - y)
                    if cd < 90.0 and abs(cp[2] - z) < 6.5 and math.hypot(cp[0] - cC[0], cp[1] - cC[1]) < cR - 10.0:
                        if best_crate is None or cd < best_crate[0]:
                            best_crate = (cd, cp)
                if best_crate:
                    cd, cp = best_crate
                    goal_label = "crate"
                    if cd > 26.0:
                        goal = (cp[0], cp[1])
                    else:
                        ux, uy = unit(cp[0] - x, cp[1] - y)
                        move = self.free_dir(x, y, *unit(-uy * self.strafe, ux * self.strafe), dist=6.0)
                    if target is None:
                        target, tkind = {"pos": cp, "vel": [0.0, 0.0, 0.0], "d": cd, "kind": "crate"}, "crate"

            # 5e. Farmable NPCs
            if goal is None and goal_label == "" and target is not None and tkind == "npc":
                goal = (target["pos"][0], target["pos"][1])
                goal_label = "npc"

            # 5f. Outer-Ring Surf -> Silent Hold (Stop making footsteps once positioned!)
            if goal is not None:
                move, arrived = self.go_to(pos, goal, tick)
                if arrived:
                    move = (0.0, 0.0)
                boost = math.hypot(goal[0] - x, goal[1] - y) > 70.0 and me["energy"] > 75
            elif goal_label == "":
                d_surf = math.hypot(surf_pt[0] - x, surf_pt[1] - y)
                if d_surf > 16.0:
                    move, _ = self.go_to(pos, surf_pt, tick)
                    goal_label = "surf_edge"
                else:
                    # Top-16 stealth hold: stand still on the outer edge so we emit zero footstep audio
                    move = (0.0, 0.0)
                    goal_label = "silent_hold"

        # 9. Anti-stuck
        self.hist.append((t, x, y))
        if len(self.hist) > 12:
            self.hist.pop(0)

        wants_move = math.hypot(*move) > 0.4
        jump = False
        if t < self.unstick_until:
            move = self.unstick_dir
            jump = True
            boost = me["energy"] > 20
            self.path = None
        elif wants_move and len(self.hist) >= 10:
            t_old, x_old, y_old = self.hist[0]
            if (t - t_old) >= 0.75 and math.hypot(x - x_old, y - y_old) < 2.2:
                a = math.atan2(move[1], move[0]) + self.rng.choice((-1.0, 1.0)) * (0.9 + self.rng.random() * 0.8)
                self.unstick_dir = (math.cos(a), math.sin(a))
                self.unstick_until = t + 0.85
                self.hist.clear()

        if me["energy"] < 15:
            self.boost_lock = True
        elif me["energy"] > 45:
            self.boost_lock = False
        if self.boost_lock:
            boost = False
        if jump and me["grounded"] is False:
            boost = me["energy"] > 15

        # 10. Center-Mass Aiming & Firing (Autocannon + Laser Combo)
        fire = [False, False]
        aim_yaw, aim_pitch = me["aim"][0], me["aim"][1]

        if target is not None:
            tp = target["pos"]
            tv = target.get("vel", [0.0, 0.0, 0.0])
            tz = tp[2] + (5.5 if tkind == "mech" else 2.2 if tkind == "npc" else 1.5)

            gun = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
            sp = gun["stats"]["speed"] if gun else 252.0
            lp = self.lead((x, y, z + muzzle), (tp[0], tp[1], tz), tv, sp)

            hd = math.hypot(lp[0] - x, lp[1] - y)
            aim_yaw = math.atan2(lp[1] - y, lp[0] - x)
            aim_pitch = max(-0.75, min(1.35, math.atan2(lp[2] - (z + muzzle), max(hd, 1e-3))))

            err = abs(norm_ang(me["aim"][0] - aim_yaw))
            d = target["d"]
            tol = math.atan2(3.4, max(d, 5.0)) + 0.055
            clear = w.los((x, y, z + muzzle), (tp[0], tp[1], tz)) or d < 22.0

            for i, wp in enumerate(weapons):
                if not wp:
                    continue
                k = wp["kind"]
                if k == "gun":
                    if clear and err < tol and d < 205.0 and wp.get("ammo", 0) > 0 and wp.get("reload", 0) == 0:
                        fire[i] = True
                elif k == "laser":
                    if tkind == "mech" and d < 245.0 and clear:
                        ch = wp.get("charge", 0.0)
                        if ch >= 0.72:
                            fire[i] = False if err < tol * 1.4 else True
                        elif wp.get("cooldown", 0.0) < 0.15:
                            fire[i] = True
        else:
            # Top-16 360-degree continuous scan while holding silent position
            snd = next(
                (s for s in obs["sounds"] if s["kind"] in ("gun", "laser", "lightning", "mortar", "explosion") and s["dist"] != "far"),
                None,
            )
            if virtual:
                aim_yaw = self.last_dmg_bearing
            elif snd:
                aim_yaw = snd["bearing"]
            elif goal_label == "silent_hold":
                aim_yaw = norm_ang(me["aim"][0] + 0.42)
            elif math.hypot(*move) > 0.3:
                aim_yaw = math.atan2(move[1], move[0])
            else:
                aim_yaw = norm_ang(me["aim"][0] + 0.25)
            aim_pitch = 0.0

        gun_w = weapons[0] if weapons and weapons[0] and weapons[0]["kind"] == "gun" else None
        if gun_w and not any(fire) and gun_w.get("reload", 0) == 0:
            ammo = gun_w.get("ammo", 12)
            if ammo == 0 or (ammo <= 5 and (target is None or target["d"] > 70.0)):
                act["reload"] = True

        mv = math.hypot(*move)
        if mv > 1.0:
            move = (move[0] / mv, move[1] / mv)

        act.update(
            move=[round(move[0], 3), round(move[1], 3)],
            boost=bool(boost),
            jump=bool(jump),
            aim=[round(aim_yaw, 4), round(aim_pitch, 4)],
            fire=fire,
        )

        mf.draw.label(f"{goal_label} hp:{me['hp']:.0f} e:{me['energy']:.0f} th:{len(close_threats)}")
        if self.path and goal_label not in ("kite_fight", "dash", "retreat"):
            mf.draw.line([x, y, z + 1.0], [self.path[-1][0], self.path[-1][1], z + 1.0], "#40c0ff")

        if (time.perf_counter() - t0) > 0.015:
            self.slow_until = tick + 6

        return act


mf.run(Bot)
