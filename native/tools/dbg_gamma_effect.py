# -*- coding: utf-8 -*-
"""dbg_gamma_effect.py — 分文件分析 exp21 两次启动的对局长度与行为构成。"""
import collections
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for f in sorted(glob.glob(str(ROOT / "backend/engine/ai/log/exp21_6v6/battles_*.jsonl"))):
    rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    ts = [r.get("turns", r.get("summary", {}).get("turns")) for r in rows]
    ts = [t for t in ts if t is not None]
    if not ts:
        continue
    capped = sum(1 for t in ts if t >= 60)
    # 行为构成（用最长局的 rounds）
    g = max(rows, key=lambda r: r.get("turns", r.get("summary", {}).get("turns")) or 0)
    rounds = g.get("rounds") or g.get("summary", {}).get("rounds") or []
    ka = collections.Counter((r.get("action_a") or {}).get("kind", "none") for r in rounds)
    kb = collections.Counter((r.get("action_b") or {}).get("kind", "none") for r in rounds)
    na, nb = max(sum(ka.values()), 1), max(sum(kb.values()), 1)
    print(f"{Path(f).name}: games={len(ts)} mean={sum(ts)/len(ts):.0f} "
          f"median={sorted(ts)[len(ts)//2]} capped@60={capped} ({capped/len(ts):.0%})")
    print(f"   最长局行为: A switch={ka.get('switch',0)/na:.0%} skill={ka.get('skill',0)/na:.0%} | "
          f"B switch={kb.get('switch',0)/nb:.0%} skill={kb.get('skill',0)/nb:.0%}")
