# -*- coding: utf-8 -*-
"""单局镜像追踪：逐回合打印动作/事件/血线，用来诊断"打满回合无人力竭"的僵局。

用法:
    python trace_mirror.py --team 魔偶雨天队 --turns 6
    python trace_mirror.py --team 星陨队 --turns 6 --events      # 连每个动作的事件一起打
    python trace_mirror.py --team 纯受向毒0517 --turns 80 --full # 打满整局看结局
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()

from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team, strategy_from_team,
)
from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def _sprite_line(tag: str, sprite, lives: int) -> str:
    effects = getattr(sprite, "effects", None)
    n_eff = len(effects) if isinstance(effects, (list, tuple, dict)) else "-"
    return (f"      {tag} {sprite.name[:14]:16s} HP {sprite.current_hp:4d}/{sprite.max_hp:4d} "
            f"E {getattr(sprite, 'energy', -1):2d} 效果 {n_eff} | 余 {lives}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--events", action="store_true", help="打印动作事件明细")
    ap.add_argument("--full", action="store_true", help="打满 60 回合")
    ap.add_argument("--why-from", type=int, default=0,
                    help="从第 N 回合起，对选择聚能的一侧打印「为什么不用攻击」的伤害表")
    args = ap.parse_args()

    factory = SimFactory()
    meta = load_meta_teams()
    hits = [t for t in meta if args.team in (t.get("name") or "")]
    if not hits:
        raise SystemExit(f"没有匹配 {args.team!r} 的 meta 阵容")
    team = hits[0]
    rng = random.Random(args.seed)
    random.seed(args.seed)
    sa, _ = spec_from_team(team, rng)
    sb, _ = spec_from_team(team, rng)
    ia, ib = item_from_team(team, sa), item_from_team(team, sb)
    st = strategy_from_team(team, rng)
    p1 = factory.build_player("A", sa, item=ia)
    p2 = factory.build_player("B", sb, item=ib)
    battle = factory.build_battle(p1, p2)
    a1 = RuleAgentV2("A", p1, strategy=st)
    a2 = RuleAgentV2("B", p2, strategy=st)

    print(f"=== {team.get('name')} 镜像 @seed={args.seed} 道具 A={getattr(ia, 'name', ia)} "
          f"B={getattr(ib, 'name', ib)} ===")
    print("    A 队:", [s.name for s in p1.team])
    print("    B 队:", [s.name for s in p2.team])
    limit = 60 if args.full else args.turns
    turn = 0
    while not battle.is_finished and turn < limit:
        # 先按当前状态算一遍"能打的手"，用于解释聚能
        pre = {}
        if args.why_from:
            for tag, agent, player in (("A", a1, p1), ("B", a2, p2)):
                try:
                    pre[tag] = agent._attack_table(battle, player.active,
                                                   battle.get_opponent(tag).active)
                except Exception as exc:  # 诊断工具不因内部接口变动而崩
                    pre[tag] = f"<{exc}>"
        rec = battle.execute_turn(a1, a2)
        turn += 1
        if args.events:
            print(rec.to_message())
        else:
            print(f"  {rec.summary()}")
        print(_sprite_line("A", p1.active, p1.lives))
        print(_sprite_line("B", p2.active, p2.lives))
        if args.why_from and turn >= args.why_from:
            for tag, player, opp, agent in (("A", p1, p2, a1), ("B", p2, p1, a2)):
                act = rec.action_a if tag == "A" else rec.action_b
                if act is None or act.kind not in ("gather", "switch"):
                    continue
                s, o = player.active, opp.active
                thr = 0.12 * o.current_hp
                rows = sorted(pre.get(tag, []), key=lambda x: -x[1]) if isinstance(pre.get(tag), list) else []
                detail = ", ".join(
                    f"{s.skills[i].name}(耗{cost},打{dmg})" for i, dmg, cost in rows[:5])
                print(f"      [为什么{act.kind} {tag}] 能量 {getattr(s,'energy','-')} → 阈值 "
                      f"{thr:.0f}（对手 HP {o.current_hp}）；可打: {detail or '无合法攻击'}")
                info = getattr(agent, "last_plan", None) or {}
                vals = info.get("values") or {}
                if vals:
                    ordered = sorted(vals.items(), key=lambda kv: -kv[1])
                    shown = " | ".join(f"{k}: {v:.4f}" for k, v in ordered[:6])
                    print(f"      规划层打分: {shown}")
                    print(f"      规划层选中: {info.get('best')}（候选 {info.get('n_candidates')} × "
                          f"响应 {info.get('n_responses')}）")
    print(f"结束: finished={battle.is_finished} turns={turn} "
          f"剩 A={sum(1 for s in p1.team if s.current_hp > 0)} "
          f"B={sum(1 for s in p2.team if s.current_hp > 0)}")


if __name__ == "__main__":
    main()
