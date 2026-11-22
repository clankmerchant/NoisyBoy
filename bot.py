import math
import os
import random
import sys
import time

import mechfight as mf

try:
    from world import World
except Exception:  # numpy missing etc.
    World = None

PI = math.pi
TAU = 2 * math.pi

UPGRADE_PRIORITY = [
    "armor", "plating", "laser_charge", "laser_damage", "nanites", "gun_damage", "gun_rate",
    "lightning_efficiency", "gyros", "gun_mag", "lightning_range", "reactor", "capacitor",
    "lightning_chain", "mortar_reload", "sensors", "jets", "servos", "gun_velocity",
    "mortar_velocity", "mortar_splash", "laser_pierce",
]
UPGRADE_PRIORITY_FARM = ["plating", "armor", "laser_charge", "nanites", "laser_damage", "gun_damage", "gun_rate",
                         "lightning_efficiency", "gyros", "gun_mag", "lightning_range", "reactor", "capacitor",
                         "lightning_chain", "mortar_reload", "sensors", "jets", "servos", "gun_velocity",
                         "mortar_velocity", "mortar_splash", "laser_pierce"]
WEAPON_VALUE = {"gun": 8.5, "laser": 9.5, "lightning": 7.0, "mortar": 3.0}
LASER_STANDOFF = float(os.environ.get("MF_STANDOFF", "150"))
LASER_STANDOFF_VS_LASER = float(os.environ.get("MF_STANDOFF_L", "100"))
LASER_SEEK = float(os.environ.get("MF_LASER_SEEK", "280"))
WEAPON_VALUE["mortar"] = float(os.environ.get("MF_MORTAR_V", "0"))
WEAPON_VALUE["lightning"] = float(os.environ.get("MF_LT_V", "7"))
FIGHT_PICKUP = os.environ.get("MF_FIGHT_PICKUP", "0") == "1"
HEAL_FIRST = os.environ.get("MF_HEAL_FIRST", "0") == "1"
COLLECT = os.environ.get("MF_COLLECT", "1") == "1"
COLLECT_R = float(os.environ.get("MF_COLLECT_R", "150"))
LDODGE = os.environ.get("MF_LDODGE", "0") == "1"
HIDE = os.environ.get("MF_STYLE", "roam") == "hide"
PROBE_T = float(os.environ.get("MF_PROBE_T", "0"))  # >0: probe that dies in the zone after this time
PACIFIST = os.environ.get("MF_PACIFIST", "0") == "1"  # probe: never shoot mechs
FFA_HIDE = os.environ.get("MF_FFA_HIDE", "1") == "1"
FFA_N = int(os.environ.get("MF_FFA_N", "3"))      # deaths we didn't cause ...
FFA_T = float(os.environ.get("MF_FFA_T", "120"))  # ... before this time => free-for-all  # switch to hiding once the match looks like a free-for-all
FARM = os.environ.get("MF_FARM", "1") == "1"
FARM2 = os.environ.get("MF_FARM2", "1") == "1"
FARM_T = float(os.environ.get("MF_FARM_T", "150"))
KITE = os.environ.get("MF_KITE", "1") == "1"
KITE_MIN = int(os.environ.get("MF_KITE_MIN", "2"))
KITE_D = float(os.environ.get("MF_KITE_D", "30"))
LASER_RUSH = os.environ.get("MF_LASER_RUSH", "0") == "1"
PEEK = os.environ.get("MF_PEEK", "1") == "1"
QUEST = os.environ.get("MF_QUEST", "1") == "1"
SEEK = os.environ.get("MF_SEEK", "0") == "1"
SELECT = os.environ.get("MF_SELECT", "1") == "1"
SEL_NEAR = float(os.environ.get("MF_SEL_NEAR", "35"))
SEL_AIM = float(os.environ.get("MF_SEL_AIM", "110"))
SEL_CLOSE = float(os.environ.get("MF_SEL_CLOSE", "120"))
STRICT = os.environ.get("MF_STRICT", "0") == "1"  # never start a fight with a non-hostile mech unless it is nearly dead
DROP_PRI = os.environ.get("MF_DROP_PRI", "1") == "1"
AGGRO = os.environ.get("MF_AGGRO", "0") == "1"
RETREAT = os.environ.get("MF_RETREAT", "race")
RACE_K = float(os.environ.get("MF_RACE_K", "0.7"))  # retreat when our time-to-kill > K x theirs (A/F used 0.85; higher = stay in fights longer)
HEAL_EXIT = float(os.environ.get("MF_HEAL_EXIT", "0.8"))
CAMP = os.environ.get("MF_CAMP", "0") == "1"
STYLE = os.environ.get("MF_STYLE", "roam")  # auto: camp until the match looks like a free-for-all
HEAL_HOLD = os.environ.get("MF_HEAL_HOLD", "1") == "1"


def wrap(a):
    return (a + PI) % TAU - PI


def hyp(x, y):
    return math.sqrt(x * x + y * y)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Bot:
    name = "oval-ace"

    def __init__(self, hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.slot = hello.get("slot", 0)
        random.seed(hello.get("seed", 0))
        self.world = None
        if World is not None:
            try:
                d = hello.get("map", {}).get("dir") or "/arena"
                if not os.path.exists(f"{d}/v2/world.json"):
                    here = os.path.dirname(os.path.abspath(__file__))
                    d = f"{here}/arena"
                self.world = World(d)
            except Exception as e:  # never die on map trouble
                log("world load failed:", repr(e))
                self.world = None
        w = cfg["weapons"]
        self.gun_cfg = w["gun"]
        self.laser_cfg = w["laser"]
        self.lt_cfg = w["lightning"]
        self.mortar_cfg = w["mortar"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.cube_reach = cfg["cubes"]["pickup_radius"]
        self.g = cfg["mech"]["gravity"]
        # state
        self.t = 0.0
        self.path = None
        self.path_goal = None
        self.path_t = -99
        self.stuck_n = 0
        self.unstick_until = -1
        self.unstick_dir = None
        self.strafe_sign = 1
        self.strafe_until = 0
        self.target_id = None
        self.last_damage = {}  # attacker id -> (t, bearing)
        self.dmg_bearing = None
        self.dmg_t = -99
        self.sound_look = None  # (bearing, t)
        self.last_enemy_t = -99
        self.scan_yaw = None
        self.scan_t = 0
        self.cover = None
        self.cover_t = -99
        self.retreating = False
        self.laser_hold_t = 0.0
        self.bad_pickups = {}
        self.pk_mem = {}
        self.camp = None
        self.camp_t = -99
        self.cube_mem = {}
        self.duck = None
        self.duck_t = -99
        self.peeking = False
        self.peek_pos = None
        self.seen_pos = {}
        self.seen_t = {}
        self.visited = {}
        self.explore = None
        self.explore_t = -99
        self.npc_mem = {}
        self.crate_mem = {}
        self.farm_goal = None
        self.my_kills = 0
        self.ffa = False
        self.hiding = HIDE
        self.expo = None
        try:
            import numpy as _np
            here = os.path.dirname(os.path.abspath(__file__))
            self.expo = _np.load(os.path.join(here, "arena", "exposure.npy")).astype(_np.float32)
        except Exception as e:
            log("no exposure map:", repr(e))
        self.crate_ban = {}
        self.last_pos = None
        self.ticks = 0
        self.maxdt = 0.0
        self.prev_hp = None
        self.dmg_window = []  # (t, amount)
        self.hit_window = []  # (t, target, amount)
        self.passive = False
        self.last_threat_t = -99
        self.attacks = []
        self.dmg_by = None

    # ------------------------------------------------------------------ helpers
    def zone_at(self, z, t):
        ss, se = z["shrink_start"], z["shrink_end"]
        if t <= ss or se <= ss:
            return z["center"], z["radius"]
        if t >= se:
            return z["next_center"], z["next_radius"]
        f = (t - ss) / (se - ss)
        c = [z["center"][0] + (z["next_center"][0] - z["center"][0]) * f,
             z["center"][1] + (z["next_center"][1] - z["center"][1]) * f]
        return c, z["radius"] + (z["next_radius"] - z["radius"]) * f

    def los(self, a, b):
        if self.world is None:
            return True
        return self.world.los(a, b)

    def weapon(self, kind):
        for i, w in enumerate(self.me["weapons"]):
            if w and w["kind"] == kind:
                return i, w
        return None, None

    def center_of(self, m):
        sc = 1 + 0.05 * m.get("cubes", 0) if m.get("kind") == "mech" else 1
        h = 4.5 * sc if m.get("kind") == "mech" else 1.75
        return [m["pos"][0], m["pos"][1], m["pos"][2] + h]

    def radius_of(self, m):
        if m.get("kind") == "mech":
            return 2.5 * (1 + 0.05 * m.get("cubes", 0))
        if m.get("kind") == "npc":
            return 1.8
        return 1.4

    def predict(self, m, dt):
        p = self.center_of(m)
        v = m.get("vel") or [0, 0, 0]
        x = p[0] + v[0] * dt
        y = p[1] + v[1] * dt
        z = p[2] + v[2] * dt
        if abs(v[2]) > 0.5:  # airborne: gravity
            z -= 0.5 * self.g * dt * dt
            if self.world is not None:
                floor = self.world.surface_at(x, y) + (p[2] - m["pos"][2])
                if z < floor:
                    z = floor
        return [x, y, z]

    def lead(self, m, speed, origin, extra=0.05):
        """Intercept point for a straight projectile of `speed`."""
        p = self.center_of(m)
        t = math.dist(origin, p) / speed + extra
        for _ in range(3):
            q = self.predict(m, t)
            t = math.dist(origin, q) / speed + extra
        return self.predict(m, t)

    def aim_to(self, origin, p):
        dx, dy, dz = p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]
        return [math.atan2(dy, dx), math.atan2(dz, hyp(dx, dy))]

    def aim_err(self, want):
        a = self.me["aim"]
        return abs(wrap(want[0] - a[0])), abs(want[1] - a[1])

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t0 = time.perf_counter()
        self.t = obs["t"]
        me = self.me = obs["self"]
        self.obs = obs
        self.ticks += 1
        act = {"tick": obs["tick"]}
        pos = me["pos"]
        st = me["stats"]
        self.eye = [pos[0], pos[1], pos[2] + st.get("eye_height", 8)]
        self.muzzle = [pos[0], pos[1], pos[2] + st.get("muzzle_height", 7)]
        self.hp_frac = me["hp"] / max(1.0, me["max_hp"])

        self.read_events(obs)
        if not self.ffa and self.t < FFA_T and (16 - obs.get("alive", 16)) - self.my_kills >= FFA_N:
            self.ffa = True
            log(f"t={self.t:.0f}: others are dying without us: free-for-all")
        self.squad = STYLE == "camp" or (STYLE == "auto" and not self.ffa)
        self.hiding = HIDE or (FFA_HIDE and self.ffa)
        if obs.get("offer"):
            act["pick"] = self.choose_upgrade(obs["offer"])

        enemies = [m for m in obs["mechs"] if m["kind"] == "mech" and m["id"] != me["id"]]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for e in enemies:
            e["_d"] = math.dist(e["pos"], pos)
            e["_los"] = self.los(self.muzzle, self.center_of(e))
            # can they see/shoot me?
            e["_threat"] = e["_d"] < 230 and self.los([e["pos"][0], e["pos"][1], e["pos"][2] + 8], [pos[0], pos[1], pos[2] + 6])
            rec = self.last_damage.get(e["id"])
            b = math.atan2(pos[1] - e["pos"][1], pos[0] - e["pos"][0])
            aiming = abs(wrap(e.get("aim", [99])[0] - b)) < 0.2
            v = e.get("vel") or [0, 0, 0]
            closing = (v[0] * math.cos(b) + v[1] * math.sin(b)) > 4 and e["_d"] < 160
            if SELECT:
                e["_hostile"] = bool((rec and self.t - rec[0] < 8) or e["_d"] < SEL_NEAR or (aiming and e["_d"] < SEL_AIM and e["_threat"])
                                     or (closing and aiming and e["_d"] < SEL_CLOSE))
            else:
                e["_hostile"] = bool((rec and self.t - rec[0] < 8) or e["_d"] < 65 or (aiming and e["_d"] < 230 and e["_threat"]) or (closing and aiming))
            if e["_hostile"] and e["_threat"]:
                self.last_threat_t = self.t
        if enemies:
            self.last_enemy_t = self.t
        for e in enemies:
            self.seen_pos[e["id"]] = list(e["pos"])
            self.seen_t[e["id"]] = self.t
        cell = (int(pos[0] // 50), int(pos[1] // 50))
        self.visited[cell] = self.t
        self.enemies = enemies
        self.npcs = npcs

        self.loot(obs, act)
        target, ttype = self.pick_target(enemies, npcs, obs)
        self.combat(target, ttype, act)
        self.movement(obs, target, ttype, act)

        if PROBE_T > 0 and self.t >= PROBE_T:
            # measurement probe: leave the circle and let the zone finish us
            zc = obs["zone"]["center"]
            a = math.atan2(pos[1] - zc[1], pos[0] - zc[0])
            mv = a
            if self.world is not None:
                for da in (0.0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8):
                    if self.world.open_at(pos[0] + 8 * math.cos(a + da), pos[1] + 8 * math.sin(a + da)):
                        mv = a + da
                        break
            act["move"] = [math.cos(mv), math.sin(mv)]
            act["boost"] = me["energy"] > 5
            act["fire"] = [False, False]
            act.pop("take", None)
        dt = time.perf_counter() - t0
        self.maxdt = max(self.maxdt, dt)
        if self.ticks % 300 == 0:
            log(f"t={self.t:.0f} hp={me['hp']:.0f} maxdt={self.maxdt * 1000:.1f}ms")
        return act

    # ------------------------------------------------------------------ events
    def read_events(self, obs):
        for e in obs.get("events", []):
            ty = e.get("type")
            if ty == "damaged":
                self.dmg_window.append((self.t, e.get("amount", 0)))
                if e.get("by") is not None:
                    self.last_damage[e["by"]] = (self.t, e.get("bearing"))
                if e.get("bearing") is not None and e.get("weapon") != "zone":
                    self.dmg_bearing = e["bearing"]
                    self.dmg_t = self.t
                    self.dmg_by = e.get("by")
                    self.attacks.append((self.t, e["bearing"], e.get("by"), e.get("weapon"), e.get("amount", 0)))
            elif ty == "kill":
                self.my_kills += 1
                vp = self.seen_pos.get(e.get("target"))
                if COLLECT and vp is not None:
                    self.cube_mem["k%s" % e.get("target")] = (vp, self.t)
            elif ty == "hit" and e.get("target") is not None and e["target"] < 16:
                self.hit_window.append((self.t, e["target"], e.get("amount", 0)))
            elif ty == "take_failed":
                self.bad_pickups[e.get("id")] = self.t
        self.dmg_window = [x for x in self.dmg_window if self.t - x[0] < 3.0]
        self.hit_window = [x for x in self.hit_window if self.t - x[0] < 4.0]
        self.attacks = [x for x in self.attacks if self.t - x[0] < 3.0]
        # sounds that hint at a nearby mech
        best = None
        for s in obs.get("sounds", []):
            k = s.get("kind")
            if k in ("step", "boost", "jet", "reload", "laser_charge") or (k in ("gun", "lightning", "laser") and s.get("dist") in ("near", "mid")):
                pri = {"near": 0, "mid": 1, "far": 2}.get(s.get("dist"), 2)
                if best is None or pri < best[0]:
                    best = (pri, s["bearing"])
        if best is not None:
            self.sound_look = (best[1], self.t)

    def choose_upgrade(self, offer):
        ids = [c["id"] for c in offer["choices"]]
        prio = UPGRADE_PRIORITY_FARM if FARM else UPGRADE_PRIORITY
        best, bi = 999, 0
        for i, c in enumerate(ids):
            r = prio.index(c) if c in prio else 500
            if r < best:
                best, bi = r, i
        return bi

    # ------------------------------------------------------------------ loot
    def held_kinds(self):
        return [w["kind"] if w else None for w in self.me["weapons"]]

    def pickup_gain(self, kind):
        """(gain, drop_slot) for taking a weapon of `kind`."""
        held = self.held_kinds()
        if kind in held:
            return 0, None
        if None in held:
            return WEAPON_VALUE.get(kind, 0), None
        # swap out the worst slot (never a laser mid-charge)
        worst, ws = 1e9, None
        for i, k in enumerate(held):
            w = self.me["weapons"][i]
            if k == "laser" and w.get("charge", 0) > 0:
                continue
            v = WEAPON_VALUE.get(k, 0)
            if v < worst:
                worst, ws = v, i
        if ws is None:
            return 0, None
        gain = WEAPON_VALUE.get(kind, 0) - worst
        return (gain if gain > 0.4 else 0), ws

    def loot(self, obs, act):
        me = self.me
        pos = me["pos"]
        self.loot_goal = None
        # take anything worthwhile within reach
        best = None
        # remember pickups we have seen (they are only listed while in view)
        vis = obs.get("pickups", [])
        vis_ids = set()
        for p in vis:
            vis_ids.add(p["id"])
            self.pk_mem[p["id"]] = (p["kind"], p["pos"], self.t)
        for e in obs.get("events", []):
            if e.get("type") in ("took", "take_failed") and e.get("id") in self.pk_mem:
                if e.get("type") == "took" or e.get("reason") in ("gone", "range"):
                    self.pk_mem.pop(e["id"], None)
        for pid in list(self.pk_mem):
            k, pp, ts = self.pk_mem[pid]
            if pid in vis_ids:
                continue
            dd = hyp(pp[0] - pos[0], pp[1] - pos[1])
            if self.t - ts > 150 or (dd < 30 and abs(pp[2] - pos[2]) < 6) or not self.in_safe_zone(pp):
                self.pk_mem.pop(pid, None)
        cands = [{"id": pid, "kind": v[0], "pos": v[1]} for pid, v in self.pk_mem.items()]
        has_laser = "laser" in self.held_kinds()
        for p in cands:
            if self.t - self.bad_pickups.get(p["id"], -99) < 3:
                continue
            gain, ds = self.pickup_gain(p["kind"])
            if gain <= 0:
                continue
            d = hyp(p["pos"][0] - pos[0], p["pos"][1] - pos[1])
            dz = abs(p["pos"][2] - pos[2])
            if d < self.reach - 0.3 and dz < 3.8:
                act["take"] = p["id"]
                if ds is not None:
                    act["drop"] = ds
                break
            if self.squad:
                reach = (150 if self.t < 60 else 70) if (p["kind"] == "laser" and not has_laser) else (45 if has_laser or self.t > 35 else 90)
            else:
                reach = LASER_SEEK if (p["kind"] == "laser" and not has_laser) else (70 if (CAMP and has_laser) else 140)
            if d > reach:
                continue
            score = gain * 12 - d + (60 if p["kind"] == "laser" else 0)
            if best is None or score > best[0]:
                best = (score, p, d)
        if best is not None:
            self.loot_goal = ("pickup", best[1]["pos"], best[2], best[1]["kind"])
        # drones and crates to farm (remembered: they are only listed while in view)
        if FARM:
            for n in obs.get("mechs", []):
                if n["kind"] == "npc":
                    self.npc_mem[n["id"]] = (n["pos"], self.t)
            for c in obs.get("crates", []):
                self.crate_mem[c["id"]] = (c["pos"], self.t)
            vis_ids = {n["id"] for n in obs.get("mechs", [])} | {c["id"] for c in obs.get("crates", [])}
            self.farm_goal = None
            bestf = None
            for mem in (self.npc_mem, self.crate_mem):
                for k in list(mem):
                    p, ts = mem[k]
                    dd = hyp(p[0] - pos[0], p[1] - pos[1])
                    if k not in vis_ids and (dd < 35 or self.t - ts > 120):
                        mem.pop(k, None)
                        continue
                    if not self.in_safe_zone(p):
                        continue
                    if FARM2 and any(hyp(sp[0] - p[0], sp[1] - p[1]) < 80 and self.t - self.seen_t.get(mid, -99) < 8
                                     for mid, sp in self.seen_pos.items()):
                        continue
                    if dd < 250 and (bestf is None or dd < bestf[0]):
                        bestf = (dd, p)
            if bestf is not None and self.farming():
                self.farm_goal = bestf[1]
        # cubes
        cb = None
        vis_c = obs.get("cubes", [])
        reach_c = (40 if self.squad and self.t > 15 else (60 if (CAMP and has_laser) else 90))
        if COLLECT:
            for c in vis_c:
                self.cube_mem[c["id"]] = (c["pos"], self.t)
            vset = {c["id"] for c in vis_c}
            for cid in list(self.cube_mem):
                cp, ts = self.cube_mem[cid]
                dd = hyp(cp[0] - pos[0], cp[1] - pos[1])
                gone = cid not in vset and dd < (25 if isinstance(cid, str) else 20)
                if isinstance(cid, str) and gone:
                    gone = not any(hyp(c["pos"][0] - cp[0], c["pos"][1] - cp[1]) < 10 for c in vis_c)
                if gone or self.t - ts > 90 or not self.in_safe_zone(cp):
                    self.cube_mem.pop(cid, None)
            if self.hp_frac > 0.45 and not self.retreating:
                reach_c = max(reach_c, COLLECT_R)
            vis_c = [{"id": k, "pos": v[0]} for k, v in self.cube_mem.items()]
        for c in vis_c:
            d = hyp(c["pos"][0] - pos[0], c["pos"][1] - pos[1])
            if d < reach_c and (cb is None or d < cb[1]):
                cb = (c, d)
        if cb is not None and (self.loot_goal is None or cb[1] < self.loot_goal[2] * 0.7):
            self.loot_goal = ("cube", cb[0]["pos"], cb[1])

    def in_safe_zone(self, p, dt=10.0, slack=0.0):
        z = self.obs["zone"]
        c, r = self.zone_at(z, self.t + dt)
        return hyp(p[0] - c[0], p[1] - c[1]) < max(r, z["next_radius"]) - 5 + slack

    # ------------------------------------------------------------------ targets
    def pick_target(self, enemies, npcs, obs):
        me = self.me
        pos = me["pos"]
        best = None
        any_hostile = any(e["_hostile"] for e in enemies)
        has_laser = self.weapon("laser")[1] is not None
        for e in enemies:
            d = e["_d"]
            if d > 320:
                continue
            hpf = e.get("hp", 1000) / max(1.0, e.get("max_hp", 1400))
            s = -d * 0.6 - hpf * 120
            if e["_los"]:
                s += 120
            if e["id"] == self.target_id:
                s += 50
            rec = self.last_damage.get(e["id"])
            if rec and self.t - rec[0] < 3:
                s += 80
            if QUEST and not has_laser and "laser" in (e.get("weapons") or []) and self.hp_frac > 0.5:
                s += 60  # killing it drops its laser
            if e["_hostile"]:
                s += 150
            if best is None or s > best[0]:
                best = (s, e)
        if best is not None:
            e = best[1]
            # don't start fights we don't need: a far, busy mech is left alone unless it is
            # an easy 1v1 (we're healthy, nobody else is on us, it's in good range)
            pick_ok = e["_hostile"] or (not STRICT and not any_hostile and self.hp_frac > 0.7 and e["_d"] < (170 if has_laser else 95))
            if self.hiding:
                rec2 = self.last_damage.get(e["id"])
                pick_ok = bool(rec2 and self.t - rec2[0] < 5) or e["_d"] < 50
            if AGGRO and e["_los"] and self.hp_frac > 0.5 and e["_d"] < (230 if has_laser else 150):
                pick_ok = True
            if e.get("hp", 1400) / max(1.0, e.get("max_hp", 1400)) < 0.3 and e["_d"] < 150:
                pick_ok = True
            self.target_id = e["id"]
            self.passive = not pick_ok
            if not (self.passive and (self.farming() or self.questing())):
                return e, "mech"
            alt = self.pick_loot_target(npcs, obs, True)
            if alt[0] is not None:
                self.passive = False
                return alt
            return e, "mech"
        self.target_id = None
        self.passive = False
        return self.pick_loot_target(npcs, obs, self.t - self.last_enemy_t > 3 or self.farming() or self.questing())

    def questing(self):
        """No laser yet: crates (half drop a weapon) and exploring are worth more than idling."""
        return QUEST and self.t < 120 and self.hp_frac > 0.4 and not self.retreating and \
            self.weapon("laser")[1] is None and not self.hiding

    def farming(self):
        return FARM and not self.hiding and self.t < FARM_T and self.hp_frac > 0.5 and not self.retreating

    def pick_loot_target(self, npcs, obs, calm):
        pos = self.me["pos"]
        lg = self.loot_goal
        if LASER_RUSH and lg is not None and lg[0] == "pickup" and len(lg) > 3 and lg[3] == "laser" and \
                self.weapon("laser")[1] is None:
            calm = False  # on the way to a laser: only shoot drones that shoot us
        farm = self.farming()
        # NPC drones shooting at us, or free cubes when calm
        bn = None
        for n in npcs:
            d = math.dist(n["pos"], pos)
            attacking = False
            rec = self.last_damage.get(n["id"])
            if rec and self.t - rec[0] < 4:
                attacking = True
            if d > (200 if farm else 150):
                continue
            if not (attacking or (calm and not self.hiding and self.hp_frac > (0.9 if HEAL_FIRST else 0.45) and not self.retreating)):
                continue
            if not self.los(self.muzzle, self.center_of(n)):
                continue
            s = -d + (100 if attacking else 0)
            if bn is None or s > bn[0]:
                bn = (s, n)
        if bn is not None:
            return bn[1], "npc"
        if calm and not self.hiding and not (HEAL_HOLD and self.retreating) and not (HEAL_FIRST and self.hp_frac < 0.9):
            bc = None
            for c in obs.get("crates", []):
                d = math.dist(c["pos"], pos)
                if d > (220 if self.questing() else (160 if farm else 110)) or self.t < self.crate_ban.get(c["id"], -1):
                    continue
                cp = [c["pos"][0], c["pos"][1], c["pos"][2] + 1.2]
                if not self.los(self.muzzle, cp):
                    continue
                if bc is None or d < bc[1]:
                    bc = (c, d)
            if bc is not None:
                c = dict(bc[0])
                c["kind"] = "crate"
                c["vel"] = [0, 0, 0]
                return c, "crate"
        return None, None

    # ------------------------------------------------------------------ combat
    def combat(self, target, ttype, act):
        me = self.me
        fire = [False, False]
        aim = None
        gs, gun = self.weapon("gun")
        ls, laser = self.weapon("laser")
        es, lt = self.weapon("lightning")
        ms, mortar = self.weapon("mortar")
        self.wants_charge = False
        if target is not None:
            if ttype == "crate":
                tc = [target["pos"][0], target["pos"][1], target["pos"][2] + 1.2]
                d = math.dist(self.muzzle, tc)
                tlos = True
                trad = 1.2
            else:
                tc = self.center_of(target)
                d = math.dist(self.muzzle, tc)
                tlos = target.get("_los") if ttype == "mech" else True
                trad = self.radius_of(target)
            # primary aim: gun lead (or direct for hitscan)
            if gun is not None and ttype != "crate":
                p = self.lead(target, gun["stats"]["speed"], self.muzzle)
            elif ttype == "crate":
                p = tc
            else:
                p = self.predict(target, 0.08)
            aim = self.aim_to(self.muzzle, p)
            ey, ep = self.aim_err(aim)
            tol_y = math.atan2(trad * 0.85, max(d, 1.0)) + 0.01
            tol_p = math.atan2(2.5 if ttype == "mech" else 1.2, max(d, 1.0)) + 0.01

            # laser: charge when we can hit, release when aimed and charged
            if laser is not None and (ttype in ("mech", "npc") or (FARM2 and ttype == "crate")) and d < laser["stats"]["range"]:
                lst = laser["stats"]
                charge = laser.get("charge", 0)
                hp_p = self.predict(target, 0.08)
                la = self.aim_to(self.muzzle, hp_p)
                ly, lp = self.aim_err(la)
                ltol = math.atan2(trad * 0.7, max(d, 1.0)) + 0.005
                if laser["cooldown"] <= 0.1 or charge > 0:
                    if charge <= 0:
                        if tlos or (PEEK and ttype == "mech" and self.peeking):
                            fire[ls] = True
                    elif charge < lst["charge"] - 0.02:
                        fire[ls] = True  # keep charging
                        self.wants_charge = True
                    else:
                        # full: release when aimed (or about to auto-fire)
                        held_full = charge >= lst["charge"] - 0.02
                        on = ly < ltol and lp < ltol * 1.5 and tlos
                        if on:
                            fire[ls] = False
                        elif held_full and self.laser_hold_t > lst.get("hold_s", 2) * 0.8:
                            fire[ls] = False
                        else:
                            fire[ls] = True
                        self.wants_charge = True
                    if gun is None or ttype != "mech":
                        aim = la
                if charge >= lst["charge"] - 0.02:
                    self.laser_hold_t += 0.1
                else:
                    self.laser_hold_t = 0
                # if we hold a gun too, aim for the laser while it is nearly charged
                if charge > lst["charge"] * 0.6:
                    aim = la
                    ey, ep = self.aim_err(aim)

            if gun is not None and tlos:
                gst = gun["stats"]
                if d < gst["range"] * 1.02 and gun.get("reload", 0) <= 0 and gun.get("ammo", 1) > 0:
                    slack = me["stats"].get("turn_rate", 4) * 0.05
                    if ey < tol_y + slack * 0.5 and ep < tol_p + 0.02:
                        fire[gs] = True
            if lt is not None and ttype in ("mech", "npc", "crate"):
                lst = lt["stats"]
                if d < lst["range"] * 0.95 and tlos and me["energy"] > lst.get("energy", 3) + (8 if ttype == "mech" else 30):
                    # lightning auto-targets inside the cone; aim yaw roughly on target
                    if ey < math.radians(lst["cone_deg"]) * 0.6:
                        fire[es] = True
            if mortar is not None and ttype in ("mech", "npc", "crate"):
                mst = mortar["stats"]
                hd = hyp(tc[0] - me["pos"][0], tc[1] - me["pos"][1])
                if 22 < hd < 225 and mortar["cooldown"] <= 0.1:
                    sol = self.mortar_solution(target, mst)
                    if sol is not None:
                        if gun is None and laser is None and lt is None:
                            aim = sol
                        my, mp = self.aim_err(sol)
                        if my < 0.03 and mp < 0.03:
                            fire[ms] = True
                        elif lt is None and (gun is None or not tlos or gun.get("reload", 0) > 0):
                            aim = sol  # nothing better to do: line up the mortar
            if ttype == "crate" and fire == [False, False]:
                # crate out of every weapon's reach: don't stare at it forever
                pass
        if self.passive and ttype == "mech":
            # watch it, don't poke it (a laser already charging still gets released on target)
            keep = fire[ls] if (laser is not None and laser.get("charge", 0) > 0) else False
            fire = [False, False]
            if ls is not None:
                fire[ls] = keep
        # healing: dealing damage restarts the regen timer, so stay quiet unless we're being
        # shot right now anyway or the target is about to die
        if HEAL_HOLD and self.retreating and target is not None and ttype == "mech":
            thpf = target.get("hp", 1400) / max(1.0, target.get("max_hp", 1400))
            if self.t - self.dmg_t > 0.6 and thpf > 0.15:
                keep = fire[ls] if (laser is not None and laser.get("charge", 0) > 0) else False
                fire = [False, False]
                if ls is not None:
                    fire[ls] = keep
        # reload when idle
        if gun is not None and target is None and gun.get("ammo", 12) < gun["stats"].get("mag", 12) * 0.6 and gun.get("reload", 0) <= 0:
            act["reload"] = True
        if laser is not None and target is None and laser.get("charge", 0) > 0:
            fire[ls] = False  # let go of a stale charge (it shoots wherever we aim)
        if PACIFIST and ttype == "mech":
            fire = [False, False]
        self.fire = fire
        act["fire"] = fire
        # an attacker we can't see: turn to face it (front armor, and to spot it)
        seen = {e["id"] for e in self.enemies}
        if self.dmg_bearing is not None and self.t - self.dmg_t < 1.5 and self.dmg_by not in seen and \
                self.dmg_by is not None and self.dmg_by < 100 and (target is None or self.passive or ttype != "mech"):
            aim = [self.dmg_bearing, 0.0]
        self.combat_aim = aim

    def mortar_solution(self, target, mst):
        me = self.me
        v = mst["speed"]
        g = mst.get("gravity", 22)
        tube = [me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("height", 9) + 0.5]
        p = self.center_of(target) if target.get("kind") != "crate" else [target["pos"][0], target["pos"][1], target["pos"][2] + 1]
        if target.get("kind") == "mech":
            p = [p[0], p[1], target["pos"][2] + 1.0]
        tflight = 1.5
        sol = None
        for _ in range(3):
            q = self.predict(target, tflight) if target.get("kind") != "crate" else p
            if target.get("kind") == "mech":
                q = [q[0], q[1], q[2] - 4.5 + 1.0]
            dx = hyp(q[0] - tube[0], q[1] - tube[1]) - 3.0
            dz = q[2] - tube[2]
            v2 = v * v
            disc = v2 * v2 - g * (g * dx * dx + 2 * dz * v2)
            if disc < 0 or dx <= 1:
                return None
            th = math.atan2(v2 - math.sqrt(disc), g * dx)
            tflight = dx / (v * math.cos(th)) + 0.05
            sol = [math.atan2(q[1] - tube[1], q[0] - tube[0]), th]
        pmin = me["stats"].get("min_pitch", -0.7)
        pmax = me["stats"].get("max_pitch", 1.3)
        if sol[1] < pmin or sol[1] > pmax:
            return None
        return sol

    # ------------------------------------------------------------------ movement
    def plan_to(self, goal, force=False):
        """Steering direction toward goal (x, y) using cached A* paths."""
        pos = self.me["pos"]
        if self.world is None:
            return math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        w = self.world
        if w.clear_line(pos, goal) and w.open_at(*pos[:2]):
            self.path = None
            return math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        need = force or self.path is None or self.path_goal is None or \
            math.dist(goal[:2], self.path_goal) > 6 or self.t - self.path_t > 2.5
        if need:
            r = w.astar(pos[0], pos[1], goal[0], goal[1], max_exp=4000)
            self.path_t = self.t
            self.path_goal = (goal[0], goal[1])
            self.path = r[0] if r else None
        if not self.path:
            return math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        path = self.path
        # drop passed waypoints
        k0 = 0
        bd = 1e9
        for k in range(min(len(path), 40)):
            d = hyp(path[k][0] - pos[0], path[k][1] - pos[1])
            if d < bd:
                bd, k0 = d, k
        if k0 > 0:
            del path[:k0]
        # farthest visible waypoint within a short horizon
        tgt = path[min(1, len(path) - 1)]
        for k in range(min(len(path) - 1, 16), 0, -1):
            if w.clear_line(pos, path[k]):
                tgt = path[k]
                break
        if len(path) <= 2:
            tgt = goal
        return math.atan2(tgt[1] - pos[1], tgt[0] - pos[0])

    def find_cover(self, threats):
        """Nearby open point hidden from every threat."""
        if self.world is None or not threats:
            return None
        me = self.me
        pos = me["pos"]
        z = self.obs["zone"]
        zc, zr = self.zone_at(z, self.t + 6)
        w = self.world
        best = None
        tl = threats[:3]
        for r in (9, 17, 26, 36):
            for k in range(16):
                a = k * TAU / 16
                x = pos[0] + r * math.cos(a)
                y = pos[1] + r * math.sin(a)
                if not w.open_at(x, y):
                    continue
                if hyp(x - zc[0], y - zc[1]) > zr - 4:
                    continue
                gz = w.ground_at(x, y)
                hidden = True
                closer = 0
                for e in tl:
                    ee = [e["pos"][0], e["pos"][1], e["pos"][2] + 8]
                    # the whole body must be hidden: top centre and both flanks
                    ba = math.atan2(y - e["pos"][1], x - e["pos"][0]) + PI / 2
                    ox, oy = 3.2 * math.cos(ba), 3.2 * math.sin(ba)
                    if w.los(ee, [x, y, gz + 8.5]) or w.los(ee, [x + ox, y + oy, gz + 6]) or w.los(ee, [x - ox, y - oy, gz + 6]):
                        hidden = False
                        break
                    # moving toward a threat is bad
                    if math.dist(e["pos"][:2], (x, y)) < e["_d"] - r * 0.6:
                        closer += 1
                if not hidden:
                    continue
                score = r + closer * 25
                if not w.clear_line(pos, (x, y)):
                    score += 15
                if best is None or score < best[0]:
                    best = (score, (x, y))
            if best is not None and r >= 17:
                break
        return best[1] if best else None

    def movement(self, obs, target, ttype, act):
        me = self.me
        pos = me["pos"]
        z = obs["zone"]
        t = self.t
        speed_now = hyp(me["vel"][0], me["vel"][1])
        boost = False
        jump = False
        move_yaw = None
        mag = 1.0

        # ---- zone safety
        zc, zr = z["center"], z["radius"]
        dz_now = hyp(pos[0] - zc[0], pos[1] - zc[1])
        nc, nr = z["next_center"], z["next_radius"]
        dnext = hyp(pos[0] - nc[0], pos[1] - nc[1])
        travel = max(0.0, dnext - nr * 0.75) / 11.0
        fc, fr = self.zone_at(z, t + 6 + travel)
        dfut = hyp(pos[0] - fc[0], pos[1] - fc[1])
        zone_goal = None
        zone_urgent = False
        if dz_now > zr - 3 or not me.get("in_zone", True):
            zone_urgent = True
        margin = min(25.0, max(6.0, fr * 0.15))
        zone_need = dfut > fr - margin
        if zone_need or (t > z["shrink_start"] - travel - 15 and dnext > nr * 0.8):
            # head toward a point well inside the next circle
            if dnext > 1:
                k = min(1.0, (nr * 0.55) / dnext)
                zone_goal = [nc[0] + (pos[0] - nc[0]) * k, nc[1] + (pos[1] - nc[1]) * k]
            else:
                zone_goal = nc
        if zone_urgent and zone_goal is None:
            zone_goal = nc

        def inside_safe(p, slack=0.0):
            c, r = self.zone_at(z, t + 10)
            return hyp(p[0] - c[0], p[1] - c[1]) < r - 8 + slack

        # ---- threats / retreat decision
        threats = []
        for e in self.enemies:
            rec = self.last_damage.get(e["id"])
            if e.get("_threat") or (rec and t - rec[0] < 1.5):
                threats.append(e)
        seen = {e["id"] for e in self.enemies}
        pseudo = {}
        for (ta, b, by, wk, amt) in self.attacks:
            if by is None or by >= 100 or by in seen or t - ta > 2.0:
                continue
            dd = 40.0 if wk == "lightning" else 120.0
            pseudo[by] = {"id": by, "pos": [pos[0] + dd * math.cos(b), pos[1] + dd * math.sin(b), pos[2]], "_d": dd, "vel": [0, 0, 0]}
        threats.extend(pseudo.values())
        threats.sort(key=lambda e: e["_d"])
        recent_dmg = sum(a for _, a in self.dmg_window)
        enemy_hpf = 1.0
        if ttype == "mech" and target is not None:
            enemy_hpf = target.get("hp", 1400) / max(1.0, target.get("max_hp", 1400))
        lose = (self.hp_frac < 0.3 and enemy_hpf > 0.25) or \
               (self.hp_frac < 0.5 and enemy_hpf > self.hp_frac + 0.3) or \
               (len(threats) >= 3 and self.hp_frac < 0.65) or \
               (len(threats) >= 2 and self.hp_frac < 0.45) or \
               (recent_dmg > 0.22 * me["max_hp"] and self.hp_frac < 0.75) or \
               (pseudo and self.hp_frac < 0.6)
        self.race = None
        if RETREAT == "race" and ttype == "mech" and target is not None and target.get("_los"):
            # damage race: keep fighting while we kill them before they kill us
            dealt = sum(a for (tt, tg, a) in self.hit_window if tg == target["id"])
            nominal = 95.0
            if self.weapon("laser")[1] is not None:
                nominal += 100
            if self.weapon("lightning")[1] is not None and target["_d"] < 45 and me["energy"] > 10:
                nominal += 120
            our_dps = max(nominal * 0.6, dealt / 4.0) if dealt > 0 else nominal
            their_dps = max(35.0 * max(1, len(threats)), recent_dmg / 3.0)
            t_us = target.get("hp", 1400) / our_dps
            t_them = me["hp"] / their_dps
            self.race = (t_us, t_them)
            lose = (t_us > t_them * RACE_K and self.hp_frac < 0.6) or self.hp_frac < 0.15 and t_us > 1.5 or \
                   (len(threats) >= 3 and self.hp_frac < 0.5)
        elif RETREAT == "race":
            lose = (self.hp_frac < 0.3) or (len(threats) >= 2 and self.hp_frac < 0.45) or (pseudo and self.hp_frac < 0.5)
        if RETREAT == "low":
            lose = (self.hp_frac < 0.3 and enemy_hpf > 0.25) or (self.hp_frac < 0.45 and len(threats) >= 2)
        elif RETREAT == "none":
            lose = False
        if self.retreating:
            if self.hp_frac > HEAL_EXIT or (not threats and self.hp_frac > 0.6 and t - self.dmg_t > 6) or \
                    (self.race is not None and self.race[0] < self.race[1] * 0.6):
                self.retreating = False
        elif lose and threats:
            self.retreating = True
            self.cover_t = -99
        goal = None
        mode = "idle"

        if self.retreating and threats and not zone_urgent:
            if self.cover is None or t - self.cover_t > 0.8:
                self.cover = self.find_cover(threats)
                self.cover_t = t
            if self.cover is not None:
                goal = self.cover
                mode = "cover"
            else:
                # run directly away from the nearest threat
                e = threats[0]
                a = math.atan2(pos[1] - e["pos"][1], pos[0] - e["pos"][0])
                goal = [pos[0] + 30 * math.cos(a), pos[1] + 30 * math.sin(a)]
                mode = "flee"
            boost = me["energy"] > 25
        elif self.retreating and not threats:
            mode = "hide"
            goal = None  # stay put and regen
        elif ttype == "mech" and target is not None and not self.passive:
            mode = "fight"
            d = target["_d"]
            has_lt = self.weapon("lightning")[1] is not None
            has_laser = self.weapon("laser")[1] is not None
            if has_lt and self.hp_frac > 0.6 and (enemy_hpf < self.hp_frac or d < 50):
                want = 30.0
            elif has_laser:
                # hitscan beats bullets at range: stand off unless they carry a laser too
                tw = target.get("weapons") or []
                want = LASER_STANDOFF_VS_LASER if "laser" in tw else LASER_STANDOFF
            else:
                want = 55.0
            if self.squad and self.camp is not None:
                # practice squad: let them come to us, fight from the spot
                spot = self.camp_spot(z) or self.camp
                if hyp(spot[0] - pos[0], spot[1] - pos[1]) > 6:
                    goal = spot
                mode = "fight"
            elif not target.get("_los"):
                # lost sight: close in carefully if they're weak, otherwise hold
                if enemy_hpf < 0.45 or (self.hp_frac > 0.7 and d < 120):
                    goal = target["pos"][:2]
                    mode = "hunt"
            else:
                a = math.atan2(target["pos"][1] - pos[1], target["pos"][0] - pos[0])
                if d > want + 15:
                    goal = [target["pos"][0] - want * 0.8 * math.cos(a), target["pos"][1] - want * 0.8 * math.sin(a)]
                elif d < want - 12 and not has_lt:
                    goal = [pos[0] - 20 * math.cos(a), pos[1] - 20 * math.sin(a)]
                mode = "fight"
            # laser peek: wait out the recharge behind cover, step out with a full charge
            self.peeking = False
            lsl, lw = self.weapon("laser")
            if PEEK and lw is not None and len(threats) <= 2 and d < lw["stats"]["range"] and self.t - self.seen_t.get(target["id"], -99) < 3:
                if target.get("_los"):
                    self.peek_pos = (pos[0], pos[1])
                full = lw["stats"]["charge"]
                ready = lw.get("charge", 0) >= full - 0.12
                duck = self.laser_duck(target)
                if duck is not None and self.peek_pos is not None and hyp(self.peek_pos[0] - pos[0], self.peek_pos[1] - pos[1]) < 14:
                    self.peeking = True
                    if ready:
                        goal = list(self.peek_pos) if not target.get("_los") else None
                        mode = "peek-out"
                    else:
                        goal = list(duck)
                        mode = "peek-in"
            # several on us: back away from the pack so they arrive one at a time
            if KITE and len(threats) >= KITE_MIN:
                cx = sum(e["pos"][0] for e in threats[:4]) / len(threats[:4])
                cy = sum(e["pos"][1] for e in threats[:4]) / len(threats[:4])
                a = math.atan2(pos[1] - cy, pos[0] - cx)
                best_k = None
                for da in (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5):
                    gx, gy = pos[0] + KITE_D * math.cos(a + da), pos[1] + KITE_D * math.sin(a + da)
                    if (self.world is None or self.world.open_at(gx, gy)) and inside_safe((gx, gy), 0):
                        best_k = (gx, gy)
                        break
                if best_k is not None:
                    goal = list(best_k)
                    mode = "kite"
            # a weapon upgrade (above all a laser) lying close by is worth a detour mid-fight
            lg = self.loot_goal
            rush = LASER_RUSH and lg is not None and lg[0] == "pickup" and lg[3] == "laser" and not has_laser and \
                lg[2] < 80 and d > 60 and self.hp_frac > 0.3
            if rush or FIGHT_PICKUP and lg is not None and lg[0] == "pickup" and lg[2] < 30 and self.hp_frac > 0.3 and \
                    lg[3] in ("laser", "lightning") and self.pickup_gain(lg[3])[0] >= 2:
                goal = lg[1][:2]
                mode = "fight-pick"
            # grab cubes lying close by while the fight allows it
            if self.loot_goal is not None and self.loot_goal[0] == "cube" and self.loot_goal[2] < 22 and \
                    (self.hp_frac > 0.4 or not threats):
                goal = self.loot_goal[1][:2]
                mode = "fight-cube"
        watch = None
        if ttype == "mech" and target is not None and self.passive and not self.retreating:
            watch = target
        if DROP_PRI and goal is None and mode == "idle" and not self.hiding:
            dg = self.drop_goal(obs)
            if dg is not None and (zone_goal is None or inside_safe(dg, 0)):
                goal = list(dg)
                mode = "drop"
        if goal is None and mode == "idle":
            lg = self.loot_goal
            if lg is not None and watch is not None:
                # don't walk loot-first into someone we're avoiding
                if math.dist(lg[1][:2], watch["pos"][:2]) < min(120.0, watch["_d"]):
                    lg = None
            if lg is not None and (zone_goal is None or inside_safe(lg[1], 10)):
                goal = self.loot_goal[1][:2]
                mode = "loot-" + self.loot_goal[0]
            elif watch is not None:
                mode = "watch"
                if self.squad and self.camp is not None:
                    spot = self.camp_spot(z) or self.camp
                    if hyp(spot[0] - pos[0], spot[1] - pos[1]) > 4:
                        goal = spot
            elif ttype == "npc" and target is not None and (zone_goal is None or inside_safe(target["pos"], 0)):
                d = math.dist(target["pos"], pos)
                if d > 60 and (self.farming() or not (CAMP and self.weapon("laser")[1] is not None)):
                    goal = target["pos"][:2]
                mode = "npc"
            elif ttype == "crate" and target is not None:
                d = math.dist(target["pos"], pos)
                if (self.weapon("gun")[1] is None and d > 35) or (self.farming() and d > 90):
                    goal = target["pos"][:2]
                mode = "crate"
            elif SEEK and self.seek_goal(z) is not None and (zone_goal is None or inside_safe(self.seek_goal(z), 0)):
                goal = list(self.seek_goal(z))
                mode = "seek"
            elif self.farm_goal is not None and (zone_goal is None or inside_safe(self.farm_goal, 0)):
                goal = self.farm_goal[:2]
                mode = "farm"
            elif FARM2 and (self.farming() or self.questing()) and (zone_goal is None or zone_urgent is False):
                ex = self.explore_goal(z)
                if ex is not None:
                    goal = ex
                    mode = "explore"
            else:
                dg = self.drop_goal(obs)
                if dg is not None and self.squad and hyp(dg[0] - pos[0], dg[1] - pos[1]) > 60:
                    dg = None
                if dg is not None and (zone_goal is None or inside_safe(dg, 0)):
                    goal = dg
                    mode = "drop"
        if zone_goal is not None and (zone_urgent or mode in ("idle", "loot-pickup", "loot-cube", "npc", "crate", "drop", "hunt", "watch", "farm", "explore", "seek")
                                      or (mode == "hide" and zone_need)):
            if goal is None or not inside_safe(goal, 0):
                goal = zone_goal
                mode = "zone"
        if zone_urgent:
            boost = me["energy"] > 10

        if self.squad:
            camping = t > 12 and (self.weapon("laser")[1] is not None or t > 35)
        else:
            camping = CAMP and (self.weapon("laser")[1] is not None or t > 45)
        if goal is None and mode == "idle" and camping:
            spot = self.camp_spot(z)
            if spot is not None:
                mode = "camp"
                if hyp(spot[0] - pos[0], spot[1] - pos[1]) > 2.5:
                    goal = spot
        elif goal is None and mode == "idle":
            # roam gently toward the safe circle's interior
            if dnext > nr * 0.4:
                goal = [nc[0] + (pos[0] - nc[0]) * 0.3, nc[1] + (pos[1] - nc[1]) * 0.3]
                mag = 0.6

        if self.hiding and not zone_urgent and mode in ("idle", "loot-pickup", "loot-cube", "npc", "crate", "drop", "watch", "zone", "camp", "hide"):
            # hider: only an early laser or cubes at our feet are worth moving for; otherwise sit in the best cover
            lg = self.loot_goal
            want_laser = lg is not None and lg[0] == "pickup" and lg[3] == "laser" and t < 40 and lg[2] < 120
            want_cube = lg is not None and lg[0] == "cube" and lg[2] < 30
            slow_ok = True
            if want_laser or want_cube:
                goal = lg[1][:2]
                mode = "loot-" + lg[0]
                slow_ok = False
            else:
                spot = self.camp_spot(z)
                if spot is not None:
                    goal = spot if hyp(spot[0] - pos[0], spot[1] - pos[1]) > 2.5 else None
                    mode = "hide-spot"
            if goal is not None and slow_ok:
                dist = hyp(goal[0] - pos[0], goal[1] - pos[1])
                deadline = (z["shrink_start"] if t < z["shrink_start"] else z["shrink_end"]) - t
                if dist / 4.4 < deadline - 10:
                    mag = 0.38  # under step_speed: no footstep sounds
            boost = False

        if goal is not None:
            if hyp(goal[0] - pos[0], goal[1] - pos[1]) > 1.5:
                move_yaw = self.plan_to(goal)

        # ---- strafing in a fight
        strafe = None
        if mode in ("fight", "cover", "flee", "hunt", "kite") and target is not None and ttype == "mech":
            if t > self.strafe_until:
                self.strafe_sign = -self.strafe_sign if random.random() < 0.7 else self.strafe_sign
                self.strafe_until = t + random.uniform(0.5, 1.3)
            a = math.atan2(target["pos"][1] - pos[1], target["pos"][0] - pos[0])
            strafe = a + self.strafe_sign * PI / 2
            if self.world is not None:
                sx = pos[0] + 5 * math.cos(strafe)
                sy = pos[1] + 5 * math.sin(strafe)
                if not self.world.open_at(sx, sy):
                    self.strafe_sign = -self.strafe_sign
                    strafe = a + self.strafe_sign * PI / 2
        # dodge a laser being charged at us
        for e in self.enemies:
            if e.get("charging") and e.get("_threat"):
                b = math.atan2(pos[1] - e["pos"][1], pos[0] - e["pos"][0])
                if abs(wrap(e["aim"][0] - b)) < 0.25:
                    duck = self.laser_duck(e) if LDODGE else None
                    if duck is not None:
                        move_yaw = math.atan2(duck[1] - pos[1], duck[0] - pos[0])
                        strafe = None
                        mode = "duck"
                    else:
                        a = b + PI
                        strafe = a + self.strafe_sign * PI / 2
                    boost = me["energy"] > 15
                    break
        # dodge incoming mortar shells
        for pr in self.obs.get("projectiles", []):
            if pr.get("kind") != "mortar":
                continue
            land = self.shell_landing(pr)
            if land is not None and hyp(land[0] - pos[0], land[1] - pos[1]) < 11:
                a = math.atan2(pos[1] - land[1], pos[0] - land[0])
                move_yaw = a
                strafe = None
                boost = me["energy"] > 10
                break

        if strafe is not None:
            if move_yaw is None:
                move_yaw = strafe
            else:
                mx = math.cos(move_yaw) + 0.8 * math.cos(strafe)
                my = math.sin(move_yaw) + 0.8 * math.sin(strafe)
                move_yaw = math.atan2(my, mx)

        # ---- unstick
        commanded = move_yaw is not None and mag > 0.3
        if commanded and me["grounded"] and speed_now < 2.0 and not self.wants_charge:
            self.stuck_n += 1
        else:
            self.stuck_n = max(0, self.stuck_n - 1)
        if self.stuck_n >= 6 and t > self.unstick_until:
            self.unstick_until = t + 1.6
            self.unstick_dir = (move_yaw or 0) + random.choice((-1, 1)) * random.uniform(0.3, 1.2)
            self.stuck_n = 0
            self.path = None
            self.path_t = -99
        if t < self.unstick_until:
            if me["grounded"] and t < self.unstick_until - 1.0:
                jump = True
            if not me["grounded"] and me["energy"] > 8:
                boost = True  # jet up and over
            if move_yaw is not None and t > self.unstick_until - 1.2:
                pass
            else:
                move_yaw = self.unstick_dir
        # climbing to a roof target (loot on a roof)
        if goal is not None and mode.startswith("loot") and self.loot_goal is not None:
            gz = self.loot_goal[1][2]
            hd = hyp(goal[0] - pos[0], goal[1] - pos[1])
            if gz > pos[2] + 2.5 and hd < 25:
                move_yaw = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
                if me["grounded"] and me["energy"] > 40:
                    jump = True
                if not me["grounded"] and pos[2] < gz + 3 and me["energy"] > 5:
                    boost = True

        if move_yaw is not None:
            act["move"] = [mag * math.cos(move_yaw), mag * math.sin(move_yaw)]
        else:
            act["move"] = [0.0, 0.0]
        # boosting on the ground costs energy; keep a reserve for lightning
        if boost and me["grounded"] and self.weapon("lightning")[1] is not None and mode == "fight":
            boost = me["energy"] > 50
        if LASER_RUSH and mode in ("loot-pickup", "fight-pick") and self.loot_goal is not None and \
                len(self.loot_goal) > 3 and self.loot_goal[3] == "laser" and self.weapon("laser")[1] is None and \
                me["energy"] > 25 and me["grounded"]:
            boost = True
        act["boost"] = bool(boost)
        act["jump"] = bool(jump)

        # ---- aim
        aim = self.combat_aim
        if aim is None:
            aim = self.idle_aim(move_yaw)
        act["aim"] = [wrap(aim[0]), max(me["stats"].get("min_pitch", -0.7), min(me["stats"].get("max_pitch", 1.3), aim[1]))]
        mf.draw.label(f"{mode} hp{me['hp']:.0f} e{me['energy']:.0f} {('T%s' % target.get('id')) if target else ''}")

    def idle_aim(self, move_yaw):
        t = self.t
        me = self.me
        if self.dmg_bearing is not None and t - self.dmg_t < 2.5:
            return [self.dmg_bearing, 0.0]
        # remembered enemies we can't see right now
        if self.sound_look is not None and t - self.sound_look[1] < 2.0:
            return [self.sound_look[0], 0.0]
        if t < 2.6:  # opening: spin once to see the loot around the spawn
            return [me["aim"][0] + 2.0, 0.0]
        # sweep: look around in 120 degree steps, biased toward movement
        if self.scan_yaw is None or t - self.scan_t > 0.9:
            base = move_yaw if move_yaw is not None else me["aim"][0]
            self.scan_yaw = base + random.choice((-2.1, 0.0, 2.1, 0.0))
            self.scan_t = t
        return [self.scan_yaw, 0.0]

    def seek_goal(self, z):
        """Go find the next kill: the freshest remembered house bot, if we're healthy and it's likely alone."""
        if self.hp_frac < 0.75 or self.retreating or self.hiding or self.t < 12:
            return None
        pos = self.me["pos"]
        recent = [(mid, sp) for mid, sp in self.seen_pos.items() if self.t - self.seen_t.get(mid, -99) < 30]
        best = None
        for mid, sp in recent:
            d = hyp(sp[0] - pos[0], sp[1] - pos[1])
            if d > 300 or d < 20:
                continue
            crowd = sum(1 for m2, sp2 in recent if m2 != mid and hyp(sp2[0] - sp[0], sp2[1] - sp[1]) < 120)
            if crowd >= 1:
                continue
            age = self.t - self.seen_t.get(mid, -99)
            sc = d + age * 4
            if best is None or sc < best[0]:
                best = (sc, sp)
        return (best[1][0], best[1][1]) if best else None

    def explore_goal(self, z):
        """An unvisited spot inside the coming circle, away from known mechs (to find drones and crates)."""
        pos = self.me["pos"]
        if self.explore is not None and hyp(self.explore[0] - pos[0], self.explore[1] - pos[1]) > 12 and \
                self.t - self.explore_t < 25:
            return self.explore
        cc, rr = z["next_center"], z["next_radius"]
        best = None
        for k in range(24):
            a = k * TAU / 24
            for r in (60.0, 110.0, 160.0):
                x, y = pos[0] + r * math.cos(a), pos[1] + r * math.sin(a)
                if hyp(x - cc[0], y - cc[1]) > rr * 0.85:
                    continue
                if self.world is not None and not self.world.open_at(x, y):
                    continue
                if (int(x // 50), int(y // 50)) in self.visited:
                    continue
                near = min([hyp(sp[0] - x, sp[1] - y) for mid, sp in self.seen_pos.items()
                            if self.t - self.seen_t.get(mid, -99) < 10] + [999.0])
                if near < 90:
                    continue
                sc = r * 0.4 - min(near, 250) * 0.3
                if best is None or sc < best[0]:
                    best = (sc, (x, y))
        self.explore = best[1] if best else None
        self.explore_t = self.t
        return self.explore

    def camp_spot(self, z):
        if self.expo is not None and self.world is not None:
            try:
                return self.camp_spot_expo(z)
            except Exception as e:
                log("camp_spot_expo failed", repr(e))
                self.expo = None
        return self.camp_spot_walls(z)

    def camp_spot_expo(self, z):
        """Best-hidden open spot (precomputed exposure map) inside the circle we'll need soon."""
        import numpy as _np
        pos = self.me["pos"]
        t = self.t
        if t + 30 > z["shrink_start"]:
            cc, rr = z["next_center"], z["next_radius"]
        else:
            cc, rr = self.zone_at(z, t + 30)
        lim = max(6.0, rr * 0.72 - 8)
        cs = self.camp
        if cs is not None and hyp(cs[0] - cc[0], cs[1] - cc[1]) < lim and t - self.camp_t < 90:
            return cs
        w = self.world
        dc = hyp(pos[0] - cc[0], pos[1] - cc[1])
        if dc > lim:
            k = (dc - lim * 0.6) / max(dc, 1)
            bx, by = pos[0] + (cc[0] - pos[0]) * k, pos[1] + (cc[1] - pos[1]) * k
        else:
            bx, by = pos[0], pos[1]
        R = 130.0
        g = self.expo
        ny, nx = g.shape
        i0 = max(0, int((bx - R - w.minx) / 4)); i1 = min(nx, int((bx + R - w.minx) / 4) + 1)
        j0 = max(0, int((by - R - w.miny) / 4)); j1 = min(ny, int((by + R - w.miny) / 4) + 1)
        sub = g[j0:j1, i0:i1]
        xs = w.minx + 4.0 * _np.arange(i0, i1)[None, :]
        ys = w.miny + 4.0 * _np.arange(j0, j1)[:, None]
        dcc = _np.hypot(xs - cc[0], ys - cc[1])
        dme = _np.hypot(xs - pos[0], ys - pos[1])
        score = sub * 100.0 + dme * 0.12 + dcc / max(lim, 1.0) * 6.0
        score = _np.where((sub < 0.999) & (dcc < lim), score, 1e9)
        for _ in range(6):
            k = int(_np.argmin(score))
            if score.flat[k] >= 1e9:
                return self.camp_spot_walls(z)
            jj, ii = divmod(k, score.shape[1])
            x, y = float(xs[0, ii]), float(ys[jj, 0])
            if w.open_at(x, y):
                self.camp = (x, y)
                self.camp_t = t
                self.camp_exp = float(sub[jj, ii])
                return self.camp
            score.flat[k] = 1e9
        return self.camp_spot_walls(z)

    def camp_spot_walls(self, z):
        """A quiet spot to wait in: inside the circle we'll need in ~30 s, with walls close by."""
        pos = self.me["pos"]
        t = self.t
        fc, fr = self.zone_at(z, t + 30)
        nc, nr = z["next_center"], z["next_radius"]
        # the circle that must contain us: the later of the two, shrunk for margin
        if t + 30 > z["shrink_start"]:
            cc, rr = nc, nr
        else:
            cc, rr = fc, fr
        lim = max(8.0, rr * 0.75 - 6)
        cs = self.camp
        if cs is not None and hyp(cs[0] - cc[0], cs[1] - cc[1]) < lim and t - self.camp_t < 60:
            return cs
        w = self.world
        if w is None:
            return None
        best = None
        dc = hyp(pos[0] - cc[0], pos[1] - cc[1])
        # search around us, pulled toward the circle when we're outside it
        if dc > lim:
            k = (dc - lim * 0.7) / max(dc, 1)
            base = (pos[0] + (cc[0] - pos[0]) * k, pos[1] + (cc[1] - pos[1]) * k)
        else:
            base = (pos[0], pos[1])
        for r in (0, 10, 20, 32, 45):
            for kk in range(1 if r == 0 else 12):
                a = kk * TAU / 12
                x = base[0] + r * math.cos(a)
                y = base[1] + r * math.sin(a)
                if hyp(x - cc[0], y - cc[1]) > lim or not w.open_at(x, y):
                    continue
                walls = 0
                for j in range(8):
                    b = j * PI / 4
                    for rr2 in (6.0, 11.0):
                        if w.is_solid(x + rr2 * math.cos(b), y + rr2 * math.sin(b)):
                            walls += 1
                            break
                score = -walls * 9 + hyp(x - pos[0], y - pos[1]) * 0.25 + hyp(x - cc[0], y - cc[1]) / max(lim, 1) * 10
                if best is None or score < best[0]:
                    best = (score, (x, y))
        if best is None:
            return None
        self.camp = best[1]
        self.camp_t = t
        return self.camp

    def laser_duck(self, e):
        """A spot within ~8 m that the charging shooter's muzzle cannot see (or None)."""
        w = self.world
        if w is None:
            return None
        pos = self.me["pos"]
        mz = [e["pos"][0], e["pos"][1], e["pos"][2] + 7 * (1 + 0.05 * e.get("cubes", 0))]
        if self.duck is not None and self.t - self.duck_t < 0.8:
            return self.duck
        best = None
        for r in (4.0, 7.0, 9.5):
            for k in range(12):
                a = k * TAU / 12
                x, y = pos[0] + r * math.cos(a), pos[1] + r * math.sin(a)
                if not w.open_at(x, y) or not w.clear_line(pos, (x, y)):
                    continue
                gz = w.surface_at(x, y)
                if w.los(mz, [x, y, gz + 4.5]) or w.los(mz, [x, y, gz + 8.5]):
                    continue
                if best is None or r < best[0]:
                    best = (r, (x, y))
            if best is not None:
                break
        self.duck = best[1] if best else None
        self.duck_t = self.t
        return self.duck

    def shell_landing(self, pr):
        p = pr["pos"]
        v = pr["vel"]
        g = self.mortar_cfg.get("gravity", 22)
        x, y, zz = p
        vx, vy, vz = v
        dt = 0.05
        for _ in range(140):
            x += vx * dt
            y += vy * dt
            zz += vz * dt
            vz -= g * dt
            if vz < 0:
                floor = self.world.surface_at(x, y) if self.world is not None else 0.0
                if zz <= floor:
                    return (x, y)
        return None

    def drop_goal(self, obs):
        pos = self.me["pos"]
        best = None
        for d in obs.get("drops", []):
            dist = hyp(d["pos"][0] - pos[0], d["pos"][1] - pos[1])
            eta = dist / 12.0
            if d.get("holder") is not None and d.get("holder") != self.me["id"] and d.get("progress", 0) > 1.5:
                continue
            if dist > (350 if DROP_PRI else 220) or eta < d.get("lands_in", 0) - 15:
                continue
            if self.hp_frac < 0.35:
                continue
            if best is None or dist < best[0]:
                best = (dist, d)
        if best is None:
            return None
        d = best[1]
        if best[0] < d["radius"] - 2.5:
            return pos[:2]  # inside: stand still
        return d["pos"][:2]


if __name__ == "__main__":
    mf.run(Bot)
