"""Offline stress: crowded, losing fights at random open spots on the real map.

Usage: python3 tools/stress.py <bot_dir> <kit_dir> [scenarios]
Prints act() time percentiles. Same seeds for every bot dir, so runs compare.
"""
import copy
import gzip
import json
import math
import random
import sys
import time

bot_dir, kit = sys.argv[1], sys.argv[2]
NS = int(sys.argv[3]) if len(sys.argv) > 3 else 40
sys.path.insert(0, bot_dir)
import bot as B  # noqa: E402
import mechfight as mf  # noqa: E402

L = [json.loads(x) for x in gzip.open(f"{kit}/check/stream.jsonl.gz", "rt")]
hello = L[0]
hello["map"]["dir"] = f"{kit}/arena"
base = [x for x in L if x.get("type") == "obs"][100]
bot = B.Bot(hello)
w = bot.world
rng = random.Random(7)


def open_pt():
    while True:
        x, y = rng.uniform(-400, 450), rng.uniform(-250, 300)
        if w is None or w.open_at(x, y):
            return x, y


times = []
tick = 1000
for sc in range(NS):
    cx, cy = open_pt()
    gz = w.surface_at(cx, cy) if w else 0
    ne = rng.randint(4, 10)
    foes = []
    for k in range(ne):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(30, 190)
        x, y = cx + d * math.cos(a), cy + d * math.sin(a)
        foes.append([x, y, w.surface_at(x, y) if w else 0, a])
    hp = rng.uniform(250, 900)
    for step in range(40):
        o = copy.deepcopy(base)
        tick += 2
        o["tick"] = tick
        o["t"] = 70 + step * 0.1
        o["alive"] = 12
        me = o["self"]
        me["pos"] = [cx, cy, gz]
        me["hp"] = hp
        me["weapons"] = [me["weapons"][0], {"kind": "laser", "cooldown": 0, "charge": 0.3 * (step % 3),
                                            "stats": {"damage": 315, "charge": 0.75, "range": 280, "hold_s": 2,
                                                      "min_charge": 0.15}}]
        mechs = []
        for k, (x, y, z, a) in enumerate(foes):
            b = math.atan2(cy - y, cx - x)
            mechs.append({"id": 20 + k if k < 8 else 4 + k, "kind": "mech", "pos": [x, y, z], "vel": [3, -2, 0],
                          "aim": [b, 0], "hp": 900, "max_hp": 1400, "cubes": 2, "weapons": ["gun", "laser"],
                          "charging": step % 4 == 0})
        o["mechs"] = mechs
        o["events"] = [{"type": "damaged", "amount": 60, "weapon": "gun", "bearing": foes[0][3] + math.pi,
                        "by": 20}, {"type": "damaged", "amount": 45, "weapon": "laser", "bearing": 1.0, "by": 9}]
        o["zone"] = {"center": [cx + 50, cy], "radius": 300, "next_center": [cx + 80, cy + 40], "next_radius": 160,
                     "shrink_start": 60, "shrink_end": 110, "dps": 20}
        o["projectiles"] = [{"kind": "mortar", "pos": [cx + 30, cy, gz + 40], "vel": [-10, 0, 5]}]
        mf.draw.clear()
        t0 = time.perf_counter()
        bot.act(o)
        times.append(time.perf_counter() - t0)
        # drift a bit so paths and cover get recomputed
        cx += rng.uniform(-2, 2)
        cy += rng.uniform(-2, 2)
        if w is not None and not w.open_at(cx, cy):
            cx, cy = open_pt()
            gz = w.surface_at(cx, cy)

ts = sorted(times)
n = len(ts)
print(f"world={'on' if w else 'OFF'} ticks={n} ms p50={ts[n // 2] * 1e3:.2f} p90={ts[int(n * .9)] * 1e3:.2f} "
      f"p99={ts[int(n * .99)] * 1e3:.2f} max={ts[-1] * 1e3:.2f} over15={sum(t > .015 for t in ts)} "
      f"over50={sum(t > .05 for t in ts)}")
