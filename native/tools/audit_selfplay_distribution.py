# -*- coding: utf-8 -*-
"""探针：从自博弈对局日志体检「训练分布是否真的用的是 meta 队/道具/首领形态」。

背景：BC 数据是 60% meta 队（含 20 支首领血脉进化流 + 携带进化之力/愿力），
但 BC 数据的优势要能传递到自博弈，自博弈这一侧必须跑在**同分布**上。若
`--meta-frac 0.6` 没接进自博弈采样，则：
  - 队伍全走随机池 → 与 BC 先验分布不一致；
  - 首领血脉进化流（动作 17-21）几乎不出现 → 动作空间扩展在训练里形同死代码。
本探针直接统计实际日志，而不是读代码猜。
"""
from __future__ import annotations

import json
import sys
import glob as _glob
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_meta_roster_universes() -> list[set[str]]:
    """每支 meta 队的「正选 + alts 替补」名字全集。

    spec_from_entry 会按概率把某个精灵位替换成 alts 变体，所以日志里的阵容
    往往不等于正选名单——必须按部分匹配判定，否则会严重低估 meta 占比。
    """
    meta = json.loads((ROOT / "backend" / "engine" / "ai" / "data" / "meta_teams.json")
                      .read_text(encoding="utf-8"))
    teams = meta if isinstance(meta, list) else meta.get("teams", [])
    universes: list[set[str]] = []
    for t in teams:
        names: set[str] = set()
        for sp in t.get("sprites", []):
            names.add(sp["name"])
            for alt in sp.get("alts") or []:
                names.add(alt["name"])
        universes.append(names)
    return universes


def matched_universe(team_names: list[str], universes: list[set[str]], need: int = 4) -> bool:
    """阵容中至少 need 只来自同一支 meta 队（含替补）→ 判为 meta 队。"""
    have = set(team_names)
    return any(len(have & u) >= need for u in universes)


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else \
        "backend/engine/ai/log/exp23_bc/battles_run_*.jsonl"
    files = [Path(p) for p in sorted(_glob.glob(str(ROOT / pattern))) +
             sorted(_glob.glob(pattern))]
    if not files:
        print("没找到日志:", pattern, "ROOT =", ROOT)
        return 1
    meta_rosters = load_meta_roster_universes()

    kinds = Counter()
    item_skills = Counter()
    n_games = 0
    meta_side = 0
    meta_games = 0
    mirrored = 0
    leader_form_hits = 0
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "teams" not in rec:
                    continue
                n_games += 1
                ta = list(rec["teams"].get("A", []))
                tb = list(rec["teams"].get("B", []))
                if sorted(ta) == sorted(tb):
                    mirrored += 1
                a_meta = matched_universe(ta, meta_rosters)
                b_meta = matched_universe(tb, meta_rosters)
                meta_side += int(a_meta) + int(b_meta)
                if a_meta or b_meta:
                    meta_games += 1
                for rnd in rec.get("rounds", []):
                    for side in ("a", "b"):
                        act = rnd.get(f"action_{side}") or {}
                        kind = act.get("kind", "?")
                        kinds[kind] += 1
                        if kind == "item":
                            item_skills[act.get("skill", "?")] += 1
                            payload = act.get("skill", "")
                            if "进化" in payload or "首领" in payload:
                                leader_form_hits += 1

    print(f"对局 {n_games}  镜像局 {mirrored} ({mirrored / max(1, n_games):.1%})")
    print(f"含 meta 队的对局 {meta_games} ({meta_games / max(1, n_games):.1%})"
          f"  ← 每侧 _META_FRAC=0.6，含至少一侧期望 ≈84%")
    print(f"meta 侧占比 {meta_side / max(1, 2 * n_games):.1%}  ← 期望 ≈60%")
    print(f"\n动作分布: " + "  ".join(f"{k}={v}" for k, v in kinds.most_common()))
    total_acts = sum(kinds.values())
    for k, v in kinds.most_common():
        print(f"  {k:<8} {v:>7} ({v / max(1, total_acts):.2%})")
    print(f"\n道具动作明细（前 15）：")
    for name, cnt in item_skills.most_common(15):
        print(f"  {cnt:>6}  {name}")
    print(f"\n首领进化（进化之力/首领形态）出现次数: {leader_form_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
