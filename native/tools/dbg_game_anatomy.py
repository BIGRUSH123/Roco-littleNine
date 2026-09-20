# -*- coding: utf-8 -*-
"""dbg_game_anatomy.py — 解剖打满回合的对局：行为构成 + 事件样本。"""
import collections
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
f = sorted(glob.glob(str(ROOT / "backend/engine/ai/log/exp21_smoke2/battles_*.jsonl")))[-1]
rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]


def turns_of(r):
    return r.get("turns", r.get("summary", {}).get("turns"))


games = [r for r in rows if turns_of(r) == 80]
print(f"80-turn games: {len(games)}")
g = games[0]
body = g.get("rounds") or g.get("summary", {}).get("rounds") or []
print("rounds:", len(body), " keys:", list(g.keys())[:10])

kinds_a, kinds_b = collections.Counter(), collections.Counter()
sk_counter = collections.Counter()
for r in body:
    aa = r.get("action_a") or {}
    ab = r.get("action_b") or {}
    kinds_a[aa.get("kind", "none")] += 1
    kinds_b[ab.get("kind", "none")] += 1
    if aa.get("kind") == "skill":
        sk_counter[aa.get("skill", "?")] += 1

print("action_a kinds:", dict(kinds_a))
print("action_b kinds:", dict(kinds_b))
print("top skills a:", sk_counter.most_common(8))
for r in body[:8]:
    aa = r.get("action_a") or {}
    ab = r.get("action_b") or {}
    ev = [e for e in (r.get("action_a", {}) or {}).get("events", [])][:2]
    print(f"  t{r.get('turn')}: A={aa.get('kind')}:{aa.get('skill')} "
          f"B={ab.get('kind')}:{ab.get('skill')} ev={ev}")
