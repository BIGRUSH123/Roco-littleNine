# -*- coding: utf-8 -*-
"""native/tools/eval_fixed_rosters.py — **固定阵容**下的专家对比（当前 vs 会话前）。

为什么单独做：随机阵容时"阵容/克制"的方差会淹没 agent 差异（此前 3000 局随机阵容测到的
差异都在噪声内）。固定 N 套阵容、每套打满 K 局（两侧交替），组内只有对局 RNG 与决策差异，
能把 agent 的强弱看得更清；同时给出**逐阵容**战绩，看改动是普遍有效还是只在特定阵容里。

基线 = `git HEAD:backend/sim/agent_v2.py`（本会话开始前的版本），运行时从 git 取源码 exec。

用法：
    env\\python.exe native/tools/eval_fixed_rosters.py --games 1000 --rosters 20
    env\\python.exe native/tools/eval_fixed_rosters.py --games 1000 --rosters 20 --meta-only
"""
from __future__ import annotations

import argparse
import collections
import copy
import math
import random
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402


def _load_baseline() -> type:
    """会话前的 agent（HEAD 版）；`agent.py` 已不再导出 ELEMENTAL_BLOODLINES，改直连常量。"""
    out = subprocess.run(["git", "show", "HEAD:backend/sim/agent_v2.py"],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    src = out.stdout.replace("from .agent import ELEMENTAL_BLOODLINES",
                             "from backend.common.constants import ELEMENTAL_BLOODLINES")
    name = "backend.sim._baseline_fixed_roster"
    mod = types.ModuleType(name)
    mod.__package__ = "backend.sim"
    mod.__file__ = "<git HEAD:backend/sim/agent_v2.py>"
    sys.modules[name] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod.RuleAgentV2


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--rosters", type=int, default=20, help="固定阵容套数（每套打 games/rosters 局）")
    ap.add_argument("--seed", type=int, default=2026, help="抽阵容的种子")
    ap.add_argument("--meta-only", action="store_true", help="只用 meta 站队（不用随机阵容）")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=60)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    ensure_hash_seed()

    from backend.engine.ai.core.outcome import battle_outcome_a
    from backend.engine.ai.data.meta_teams import (item_from_team, load_meta_teams,
                                                   spec_from_team, strategy_from_team)
    from backend.engine.ai import train as T
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
    from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
    from backend.sim.factory import SimFactory

    BaselineAgentV2 = _load_baseline()
    factory = SimFactory()
    meta = load_meta_teams()

    # ── 抽固定阵容（每套 = 双方 spec + 道具 + 策略）──
    rng = random.Random(args.seed)
    rosters = []
    for _ in range(args.rosters):
        use_meta = bool(meta) and (args.meta_only or rng.random() < args.meta_frac)
        if use_meta:
            ia_, ib_ = rng.randrange(len(meta)), rng.randrange(len(meta))
            sa, _ = spec_from_team(meta[ia_], rng)
            sb, _ = spec_from_team(meta[ib_], rng)
            item_a, item_b = item_from_team(meta[ia_], sa), item_from_team(meta[ib_], sb)
            stra = strategy_from_team(meta[ia_], rng)
            strb = strategy_from_team(meta[ib_], rng)
            tag = f"meta:{meta[ia_]['name']} vs {meta[ib_]['name']}"
        else:
            # rng 必须显式传入：`_random_teams` 默认用**全局 random**，而这里的全局流
            # 由 OS 熵播种 → 同一命令两次运行抽到不同阵容，1000 局下能差 5 个百分点
            # （见 docs §4f）。
            sa, sb, item_a, item_b = T._random_teams(
                factory, dict(SPRITE_RANDOM_POOL), optimal_frac=0.95, meta_frac=0.0,
                rng=rng)
            stra = strb = TeamStrategy(default=SpriteStrategy())
            tag = "random:" + "/".join(s["name"] for s in sa)
        rosters.append({"specs_a": sa, "specs_b": sb, "item_a": item_a, "item_b": item_b,
                        "strat_a": stra, "strat_b": strb, "tag": tag})

    per_roster = max(2, args.games // len(rosters))
    total = per_roster * len(rosters)
    print(f"=== 固定 {len(rosters)} 套阵容 × 每套 {per_roster} 局 = {total} 局"
          f"（新口径在每套内交替执 A/B）===")
    wins = losses = draws = 0
    per_tag: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    side_decided: collections.Counter = collections.Counter()   # 只看"哪一侧赢"
    kinds: collections.Counter = collections.Counter()
    t0 = time.time()
    for idx, roster in enumerate(rosters):
        for g in range(per_roster):
            new_is_a = (g % 2 == 0)
            random.seed(args.seed * 1000003 + idx * 7919 + g)
            # 道具（Item）是**可变对象**（uses/last_use_turn），同一套阵容打多局必须 deepcopy，
            # 否则第一局用掉进化之力后，后面 49 局的道具状态是"已用"状态（会把对比跑歪）。
            p1 = factory.build_player("A", copy.deepcopy(roster["specs_a"]),
                                      item=copy.deepcopy(roster["item_a"]))
            p2 = factory.build_player("B", copy.deepcopy(roster["specs_b"]),
                                      item=copy.deepcopy(roster["item_b"]))
            battle = factory.build_battle(p1, p2)
            New, Old = RuleAgentV2, BaselineAgentV2
            a_cls, b_cls = (New, Old) if new_is_a else (Old, New)
            a1 = a_cls("A", p1, strategy=roster["strat_a"])
            a2 = b_cls("B", p2, strategy=roster["strat_b"])
            for team, agent in (("A", a1), ("B", a2)):
                tag = "new" if (team == "A") == new_is_a else "old"
                real = agent.choose_action

                def spy(b, _real=real, _t=tag):
                    act = _real(b)
                    kinds[f"{_t}:{act.kind}"] += 1
                    return act
                agent.choose_action = spy
            turns = 0
            while not battle.is_finished and turns < args.max_turns:
                battle.execute_turn(a1, a2)
                turns += 1
            outcome, _reason = battle_outcome_a(battle, args.max_turns)
            row = per_tag[roster["tag"]]
            if outcome == 0:
                draws += 1
                row[2] += 1
            elif (outcome > 0) == new_is_a:
                wins += 1
                row[0] += 1
            else:
                losses += 1
                row[1] += 1
            # 诊断：不看 agent 版本，只看"哪一侧赢"——若两侧各赢一半，说明胜负由阵容/随机数定
            if outcome > 0:
                side_decided[(roster["tag"], "A")] += 1
            elif outcome < 0:
                side_decided[(roster["tag"], "B")] += 1
    decisive = wins + losses
    wr = wins / max(1, decisive)
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / max(1, decisive))
    print(f"  **当前版胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}]** "
          f"({wins}胜 / {losses}负 / {draws}平)  {time.time() - t0:.0f}s")
    for side in ("new", "old"):
        sub = collections.Counter({k.split(":", 1)[1]: v for k, v in kinds.items()
                                   if k.startswith(f"{side}:")})
        tot = sum(sub.values()) or 1
        print(f"  {side}: " + "  ".join(f"{k} {v / tot:.1%}" for k, v in sub.most_common()))
    agent_decided = [t for t in per_tag if abs(per_tag[t][0] - per_tag[t][1]) >= 20]
    side_only = [t for t in per_tag
                 if t not in agent_decided
                 and abs(side_decided[(t, "A")] - side_decided[(t, "B")]) <= 10
                 and per_tag[t][2] < per_roster]
    print(f"  ── 拆分：{len(agent_decided)} 套阵容里胜负由**agent 版本**决定；"
          f"{len(side_only)} 套里由**哪一侧/随机数**决定（A/B 各赢一半，与版本无关）──")
    if agent_decided:
        print("  agent 决定胜负的阵容：")
        for tag in sorted(agent_decided, key=lambda t: -abs(per_tag[t][0] - per_tag[t][1])):
            w, l, d = per_tag[tag]
            print(f"    {w:>3}-{l:<3} (平{d}) {tag[:72]}")
    print("  ── 逐阵容（新口径胜/负/平）──")
    for tag, (w, l, d) in sorted(per_tag.items(), key=lambda kv: -(kv[1][0] - kv[1][1]))[:12]:
        print(f"    {w:>3}-{l:<3} (平{d}) {tag[:72]}")
    print("判定：CI 下界 > 0.5 才算当前版确实更强。")


if __name__ == "__main__":
    main()
