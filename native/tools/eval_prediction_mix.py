# -*- coding: utf-8 -*-
"""native/tools/eval_prediction_mix.py — E4/E5 验收：概率预判 + 混合策略值不值。

三组配对对局（**相邻两局交换 A/B 抵消先手优势**，与门控口径一致）：

  1. **旧启发式 vs 期望值**：预判机制本身有没有让专家更强
     （`SpriteStrategy(ev_decide=False)` 是旧启发式，默认是期望值层）；
  2. **期望值镜像（T=0）**：基线方差（理论上应 ≈ 0.5）；
  3. **剥削者 vs 确定性 / 混合**：剥削者**算得出我按 T=0 会出什么**（完全信息），
     再按"我必出这一手"求最优应对。若我很可预测，它该赢；温度 > 0 时应回到 50% 附近。

对手是 `RuleAgentV2` 子类（`ExploiterAgent`），只注入退化信念，其余流程（斩杀/保命/
道具）完全复用 —— 它衡量的是"可预测性"，不是绝对棋力。

用法（项目根、项目解释器）：
    env\\python.exe native/tools/eval_prediction_mix.py --games 200 --temperature 0.35
"""
from __future__ import annotations

import argparse
import collections
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402
from backend.sim import agent_v2  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim import ev  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def _skill_kind(skill) -> str:
    if skill.is_attack:
        return "attack"
    if skill.is_defense:
        return "defense"
    return "status"


class ExploiterAgent(RuleAgentV2):
    """剥削者：预测对手（T=0 确定性）会出什么，再按"它必出这一手"求最优应对。

    只覆盖 `belief_provider`（信念注入点），硬规则/道具/斩杀全部复用基类。
    """

    def __init__(self, team, player, strategy, victim_strategy):
        super().__init__(team, player, strategy=strategy)
        self.victim_strategy = victim_strategy
        self.belief_provider = self._predict_victim_belief
        self.predictions = 0
        self.skipped = 0

    def _predict_victim_belief(self, battle):
        opp_player = battle.get_opponent(self.team)
        opp_team = "B" if self.team == "A" else "A"
        victim = RuleAgentV2(opp_team, opp_player, strategy=self.victim_strategy)
        action = victim.choose_action(battle)
        self.predictions += 1
        if action.kind == "item":
            # 道具不消耗回合 → 预测到"它用道具"没法翻译成列，本回合不注入（退回先验）
            self.skipped += 1
            return None
        if action.kind == "skill" and action.skill_index is not None:
            skills = opp_player.active.skills
            kind = (_skill_kind(skills[action.skill_index])
                    if 0 <= action.skill_index < len(skills) else "attack")
        else:
            kind = action.kind          # 'switch' / 'gather'
        scenario = kind if kind in ev.SCENARIOS else "attack"
        return {name: (1.0 if name == scenario else 0.0) for name in ev.SCENARIOS}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200, help="每组局数（偶数；相邻两局交换 A/B）")
    ap.add_argument("--temperature", type=float, default=0.35, help="混合策略温度（>0 才混合）")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--optimal-frac", type=float, default=0.95)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--group", default="all",
                    help="all | legacy | mirror | exploit0 | exploitT")
    ap.add_argument("--ab", default="", choices=("", "trade", "antiloop", "defend", "both"),
                    help="只改某一处规则的本轮改动，跑「旧口径 / 新口径」两臂对比")
    return ap.parse_args()


# 本轮改动的模块级常量（与 eval_expert_change.py 保持一致）：旧口径=会话前的值。
NEW_VALUES = {"trade": 0.15, "antiloop": True, "defend": 0.30}
OLD_VALUES = {"trade": 1e9, "antiloop": False, "defend": 0.0}


def _apply_ab(mode: str, new: bool) -> None:
    vals = NEW_VALUES if new else OLD_VALUES
    if mode in ("trade", "both"):
        agent_v2._TRADE_MARGIN = vals["trade"]
    if mode in ("antiloop", "both"):
        agent_v2._ANTI_SWITCH_LOOP = vals["antiloop"]
    if mode in ("defend", "both"):
        agent_v2._DEFEND_THRESHOLD = vals["defend"]


def _strategy_for(meta_team, temperature: float, legacy: bool = False) -> TeamStrategy:
    """meta 队 → 它自带的每只策略（只改温度/是否用旧启发式），随机队 → 默认策略。"""
    if meta_team is not None:
        base = strategy_from_team(meta_team, random.Random(0))
        sprites = {name: SpriteStrategy(
            role=sp.role, lead=sp.lead, preserve=sp.preserve,
            energy_hold=sp.energy_hold, threat_switch_hp=sp.threat_switch_hp,
            switch_hp=sp.switch_hp, ev_decide=not legacy,
            mix_temperature=temperature)
            for name, sp in base.sprites.items()}
        return TeamStrategy(name=base.name, sprites=sprites,
                            default=SpriteStrategy(ev_decide=not legacy,
                                                   mix_temperature=temperature))
    return TeamStrategy(default=SpriteStrategy(ev_decide=not legacy,
                                               mix_temperature=temperature))


def _play(factory, args, meta, rng, cand_team: str, temperature: float,
          opponent: str, stats: collections.Counter) -> tuple[int, int, int]:
    """打一局。cand_team 侧用期望值层（温度 temperature），另一侧按 opponent 类型。

    返回 (cand 是否胜, 是否平, 回合数)。
    """
    meta_i = rng.randrange(len(meta)) if (meta and rng.random() < args.meta_frac) else None
    if meta_i is not None:
        specs_a, _ = spec_from_team(meta[meta_i], rng)
        specs_b, _ = spec_from_team(meta[meta_i], rng)
        item_a = item_from_team(meta[meta_i], specs_a)
        item_b = item_from_team(meta[meta_i], specs_b)
        team_a = team_b = meta[meta_i]
    else:
        specs_a, specs_b, item_a, item_b = T._random_teams(
            factory, dict(SPRITE_RANDOM_POOL), optimal_frac=args.optimal_frac, meta_frac=0.0)
        team_a = team_b = None
    p1 = factory.build_player("A", specs_a, item=item_a)
    p2 = factory.build_player("B", specs_b, item=item_b)
    battle = factory.build_battle(p1, p2)

    cand_meta = team_a if cand_team == "A" else team_b
    opp_meta = team_b if cand_team == "A" else team_a
    cand_strategy = _strategy_for(cand_meta, temperature)
    cand_agent = RuleAgentV2(cand_team, p1 if cand_team == "A" else p2,
                             strategy=cand_strategy)

    if opponent == "legacy":
        opp_strategy = _strategy_for(opp_meta, 0.0, legacy=True)
        opp_agent = RuleAgentV2("B" if cand_team == "A" else "A",
                                p2 if cand_team == "A" else p1, strategy=opp_strategy)
    elif opponent == "mirror":
        opp_agent = RuleAgentV2("B" if cand_team == "A" else "A",
                                p2 if cand_team == "A" else p1,
                                strategy=_strategy_for(opp_meta, temperature))
    elif opponent == "exploiter":
        victim_t0 = _strategy_for(cand_meta, 0.0)          # 剥削者按"我 T=0"来预测
        opp_agent = ExploiterAgent("B" if cand_team == "A" else "A",
                                   p2 if cand_team == "A" else p1,
                                   strategy=TeamStrategy(default=SpriteStrategy(mix_temperature=0.0)),
                                   victim_strategy=victim_t0)
    else:
        raise ValueError(opponent)

    for team, agent in ((cand_team, cand_agent),
                        ("B" if cand_team == "A" else "A", opp_agent)):
        real = agent.choose_action

        def spy(b, _real=real, _t=team):
            act = _real(b)
            stats[f"{_t}:{act.kind}"] += 1
            return act
        agent.choose_action = spy

    turns = 0
    while not battle.is_finished and turns < args.max_turns:
        agent_a = cand_agent if cand_team == "A" else opp_agent
        agent_b = opp_agent if cand_team == "A" else cand_agent
        battle.execute_turn(agent_a, agent_b)
        turns += 1
    outcome, _reason = battle_outcome_a(battle, args.max_turns)
    if outcome == 0:
        return 0, 1, turns
    won = (outcome > 0) == (cand_team == "A")
    return (1 if won else 0), 0, turns


def _entropy(counter: collections.Counter) -> float:
    total = sum(counter.values()) or 1
    return -sum((c / total) * math.log(c / total) for c in counter.values() if c)


def _run(name: str, factory, args, meta, temperature: float, opponent: str) -> None:
    rng = random.Random(args.seed)
    random.seed(args.seed)
    stats: collections.Counter = collections.Counter()
    wins = draws = turns_sum = 0
    t0 = time.time()
    for g in range(args.games):
        cand_team = "A" if g % 2 == 0 else "B"      # 成对交替执 A/B
        w, d, turns = _play(factory, args, meta, rng, cand_team, temperature,
                            opponent, stats)
        wins += w
        draws += d
        turns_sum += turns
    decisive = max(1, args.games - draws)
    ent = {t: _entropy(collections.Counter(
        {k.split(":", 1)[1]: v for k, v in stats.items() if k.startswith(f"{t}:")}))
        for t in ("A", "B")}
    wr = wins / decisive
    # 95% 置信区间（正态近似，n 足够大时够用）
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / decisive)
    print(f"  {name:<28} 胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}] "
          f"(平 {draws}) 平均 {turns_sum / args.games:.1f} 回合 "
          f"动作熵 A={ent['A']:.3f} B={ent['B']:.3f} {time.time() - t0:.0f}s")


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    factory = SimFactory()
    meta = load_meta_teams()
    print(f"=== 每组 {args.games} 局，成对交替执 A/B；混合温度 T={args.temperature} ===")
    print("（胜率都是「被评估方」的视角：期望值层 / 被剥削方）")
    jobs = {
        "legacy": ("期望值 vs 旧启发式", 0.0, "legacy"),
        "mirror": ("期望值 T=0 镜像（基线）", 0.0, "mirror"),
        "exploit0": ("剥削者 vs 确定性 T=0", 0.0, "exploiter"),
        "exploitT": (f"剥削者 vs 混合 T={args.temperature}", args.temperature, "exploiter"),
    }
    for key, (name, temp, opp) in jobs.items():
        if args.group not in ("all", key):
            continue
        if not args.ab:
            _run(name, factory, args, meta, temp, opp)
            continue
        # A/B：同一 seed、同一批阵容跑两臂，只差模块级常量（`_run` 开头会重置随机流，
        # 所以两臂从同一状态出发）。被剥削方的口径要真变，剥削者的预测才对应得上。
        for is_new, arm in ((False, "旧口径"), (True, "新口径")):
            _apply_ab(args.ab, is_new)
            print(f"  ── A/B {args.ab}：{arm} ──")
            _run(name, factory, args, meta, temp, opp)
        _apply_ab(args.ab, True)   # 复位成当前口径
    print("读法：exploit0 越低说明确定性策略越容易被算准并针对；"
          "exploitT 应更接近 0.5（混合让预测落空）。")


if __name__ == "__main__":
    main()
