# -*- coding: utf-8 -*-
"""native/tools/eval_vs_previous_agent.py — 当前专家 vs **本会话改动前**的专家。

基线的定义：**git HEAD 里的 `backend/sim/agent_v2.py`**（本会话开始时该文件未改动）。
工具在运行时用 `git show` 取源码、在 `backend.sim` 包命名空间里 exec 成模块（不落临时文件），
需要时把"进化之力 `turn <= 2`"这一行去掉即可得到"只修进化之力"的变体。

当前版本与之的差异：道具口径（进化之力不限回合 / 愿力按换血技能伤害）、战术预判
（先手值/印记 tick 致死/星陨组合杀/换人安全/交换价值/防空转）、期望值层（默认关闭）。
详见 `docs/博弈-概率预判口径.md` 与 `docs/培养方案-pvp口径.md`。

用法：
    env\\python.exe native/tools/eval_vs_previous_agent.py --games 3000 --seeds 2026,7,99,11,222
    env\\python.exe native/tools/eval_vs_previous_agent.py --new baseline_evolve --old baseline --games 2000
"""
from __future__ import annotations

import argparse
import collections
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

# 注意：这里必须在**导入其余 backend 模块之前**重执行，让 hash seed 从进程启动就固定。
ensure_hash_seed()

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

BASELINE_PATH = "backend/sim/agent_v2.py"
_CACHE: dict[str, type] = {}


def _baseline_source(evolve_any_turn: bool) -> str:
    """HEAD 版源码（`agent.py` 里 `ELEMENTAL_BLOODLINES` 已不再导出 → 改直连常量）。"""
    try:
        out = subprocess.run(["git", "show", f"HEAD:{BASELINE_PATH}"],
                             capture_output=True, text=True, encoding="utf-8", check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"取不到 HEAD 版 {BASELINE_PATH}（需要 git 且该文件在 HEAD 里）：{exc}")
    src = out.stdout.replace("from .agent import ELEMENTAL_BLOODLINES",
                             "from backend.common.constants import ELEMENTAL_BLOODLINES")
    if evolve_any_turn:
        src = src.replace(
            "item.name == '进化之力' and battle.turn <= 2 and s.bloodline == '首领'",
            "item.name == '进化之力' and s.bloodline == '首领'")
    return src


def _load_baseline(evolve_any_turn: bool) -> type:
    key = "baseline_evolve" if evolve_any_turn else "baseline"
    if key not in _CACHE:
        mod_name = f"backend.sim._{key}"
        mod = types.ModuleType(mod_name)
        mod.__package__ = "backend.sim"          # 让 `from .agent import …` 这类相对导入可用
        mod.__file__ = f"<git HEAD:{BASELINE_PATH}>"
        # 必须登记到 sys.modules：`@dataclass` 会按 `cls.__module__` 找回模块命名空间
        sys.modules[mod_name] = mod
        try:
            exec(compile(_baseline_source(evolve_any_turn), mod.__file__, "exec"), mod.__dict__)
        except BaseException:
            sys.modules.pop(mod_name, None)
            raise
        _CACHE[key] = mod.RuleAgentV2
    return _CACHE[key]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2000, help="总对局数（一半新口径执 A、一半执 B）")
    ap.add_argument("--seeds", default="2026,7,99", help="多 seed（阵容/对局随机）")
    ap.add_argument("--new", default="current",
                    choices=("current", "baseline", "baseline_evolve"),
                    help="新口径：current=当前专家（默认）｜baseline=HEAD 版｜baseline_evolve=只去掉进化之力回合限制")
    ap.add_argument("--old", default="baseline",
                    choices=("current", "baseline", "baseline_evolve"),
                    help="旧口径（默认 HEAD 版）")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--optimal-frac", type=float, default=0.95)
    ap.add_argument("--max-turns", type=int, default=60)
    return ap.parse_args()


def _agent_class(kind: str):
    if kind == "current":
        return RuleAgentV2
    return _load_baseline(kind == "baseline_evolve")


def _play(factory, args, meta, rng, new_is_a: bool,
          stats: collections.Counter) -> tuple[bool, bool, int]:
    """打一局；返回 (新口径是否胜, 是否平, 回合数)。两队阵容/道具完全相同，只差 agent 代码。"""
    meta_i = rng.randrange(len(meta)) if (meta and rng.random() < args.meta_frac) else None
    if meta_i is not None:
        sa, _ = spec_from_team(meta[meta_i], rng)
        sb, _ = spec_from_team(meta[meta_i], rng)
        ia, ib = item_from_team(meta[meta_i], sa), item_from_team(meta[meta_i], sb)
        st_a = st_b = strategy_from_team(meta[meta_i], rng)
    else:
        sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                         optimal_frac=args.optimal_frac, meta_frac=0.0)
        st_a = st_b = TeamStrategy(default=SpriteStrategy())
    p1 = factory.build_player("A", sa, item=ia)
    p2 = factory.build_player("B", sb, item=ib)
    battle = factory.build_battle(p1, p2)

    a_cls, b_cls = _agent_class(args.new), _agent_class(args.old)
    if not new_is_a:
        a_cls, b_cls = b_cls, a_cls
    a1 = a_cls("A", p1, strategy=st_a)
    a2 = b_cls("B", p2, strategy=st_b)
    for team, agent in (("A", a1), ("B", a2)):
        tag = ("new" if (team == "A") == new_is_a else "old")
        real = agent.choose_action

        def spy(b, _real=real, _tag=tag):
            act = _real(b)
            stats[f"{_tag}:{act.kind}"] += 1
            return act
        agent.choose_action = spy

    turns = 0
    while not battle.is_finished and turns < args.max_turns:
        battle.execute_turn(a1, a2)
        turns += 1
    outcome, _reason = battle_outcome_a(battle, args.max_turns)
    if outcome == 0:
        return False, True, turns
    new_won = (outcome > 0) == new_is_a
    return new_won, False, turns


def main() -> None:
    args = parse_args()
    factory = SimFactory()
    meta = load_meta_teams()
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()] or [2026]
    per_seed = max(2, args.games // len(seeds))
    wins = losses = draws = turns_sum = games = 0
    stats: collections.Counter = collections.Counter()
    t0 = time.time()
    for seed in seeds:
        rng = random.Random(seed)
        random.seed(seed)
        for g in range(per_seed):
            new_won, draw, turns = _play(factory, args, meta, rng, g % 2 == 0, stats)
            games += 1
            turns_sum += turns
            if draw:
                draws += 1
            elif new_won:
                wins += 1
            else:
                losses += 1
    decisive = wins + losses
    wr = wins / max(1, decisive)
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / max(1, decisive))
    print(f"=== 当前专家 vs 会话前专家：{games} 局（种子 {seeds}），平 {draws} ===")
    print(f"  当前版胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}]  "
          f"({wins}胜 / {losses}负)  平均 {turns_sum / max(1, games):.1f} 回合  {time.time() - t0:.0f}s")
    kinds = collections.Counter({k.split(":", 1)[1]: v for k, v in stats.items()})
    total = sum(kinds.values()) or 1
    print("  动作分布(两侧合计):", {k: round(v / total, 3) for k, v in kinds.most_common()})
    for side in ("new", "old"):
        sub = collections.Counter({k.split(":", 1)[1]: v for k, v in stats.items()
                                   if k.startswith(f"{side}:")})
        tot = sum(sub.values()) or 1
        print(f"  {side}:", {k: round(v / tot, 3) for k, v in sub.most_common()})
    print("判定：CI 下界 > 0.5 才算当前版确实更强。")


if __name__ == "__main__":
    main()
