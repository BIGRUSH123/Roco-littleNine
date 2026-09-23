# -*- coding: utf-8 -*-
"""BC 数据集逐臂对照：世界质量（决定性/回合数/终止原因）+ 标签动作直方图。

动作空间见 `backend/engine/ai/core/mcts.py`: 0-9 技能 / 10-14 换宠 / 15 聚能 /
16 道具（愿力）/ 17-21 首领形态。**聚能占比是解僵局最直接的标签侧指标**——
修复前"能量已满仍聚能"是严格劣的空过，会被规则层选中并写进标签。

用法: python bc_arm_stats.py <a.npz> [b.npz ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

GROUPS = (("技能", 0, 10), ("换宠", 10, 15), ("聚能", 15, 16),
          ("道具", 16, 17), ("首领形态", 17, 22))


def report(path: str) -> dict:
    sidecar = Path(path).with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    with np.load(path, allow_pickle=False) as z:
        act = np.asarray(z["action"]).astype(np.int64)
        roster = {k: np.asarray(z[k]).copy() for k in ("game_id", "team_id", "is_meta")}
    n = len(act)
    games = max(1, int(meta["games"]))
    hist = np.bincount(act, minlength=22)
    reasons = meta["reason_counts"]
    stall = sum(v for k, v in reasons.items() if k.startswith("max_turns"))
    print(f"== {path}")
    print(f"   局数 {games}  样本 {n}  样本/局 {n / games:.2f}")
    print(f"   决定性 {meta['decisive_rate']:.3f}  打满回合 {stall / games:.3f}"
          f"  平均回合 {meta['mean_turns']:.1f}")
    print(f"   终止原因 {dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}")
    if meta.get("nonfinite_clipped"):
        print(f"   !! 非有限夹取 {meta['nonfinite_clipped']}")
    out = {"path": path, "samples": n, "games": games,
           "decisive_rate": float(meta["decisive_rate"]),
           "max_turns_rate": stall / games, "mean_turns": float(meta["mean_turns"]),
           "roster": roster}
    for name, lo, hi in GROUPS:
        c = int(hist[lo:hi].sum())
        out[name] = c / n
        print(f"   {name:<8} {c:>9}  {c / n:6.2%}")
    print("   技能槽: " + " ".join(f"{i}:{hist[i] / n:.1%}" for i in range(10)))
    print("   换宠桶: " + " ".join(f"{i - 10}:{hist[i] / n:.1%}" for i in range(10, 15)))
    return out


def _roster_map(roster: dict) -> dict[int, set[int]]:
    """局号 → 该局出现过的队伍编号集合（随机阵容局恒为 {-1}，只对 meta 局有效）。"""
    out: dict[int, set[int]] = {}
    for g, t in zip(roster["game_id"].tolist(), roster["team_id"].tolist()):
        out.setdefault(int(g), set()).add(int(t))
    return out


def main() -> None:
    rows = [report(p) for p in sys.argv[1:]]
    if len(rows) == 2:
        a, b = rows
        print("\n== 差值（后 - 前）")
        print(f"   样本/局 {b['samples'] / b['games'] - a['samples'] / a['games']:+.2f}"
              f"   决定性 {b['decisive_rate'] - a['decisive_rate']:+.3f}"
              f"   打满 {b['max_turns_rate'] - a['max_turns_rate']:+.3f}"
              f"   平均回合 {b['mean_turns'] - a['mean_turns']:+.1f}")
        for name, _lo, _hi in GROUPS:
            print(f"   {name:<8} {b[name] - a[name]:+.2%}")
        # 阵容对照：两臂同种子产队 → 每局的队伍编号集合应完全相同
        # （否则两臂打的不是同一批对手，top-1 之差就不能归因于教师）
        ma, mb = _roster_map(a["roster"]), _roster_map(b["roster"])
        shared = set(ma) & set(mb)
        diff = [g for g in sorted(shared) if ma[g] != mb[g]]
        print(f"   阵容对照: 共同局号 {len(shared)}/{len(ma)}，队伍编号不一致 {len(diff)} 局"
              + (f"（前几个 {diff[:5]}）" if diff else " → 两臂对手一致"))


if __name__ == "__main__":
    main()
