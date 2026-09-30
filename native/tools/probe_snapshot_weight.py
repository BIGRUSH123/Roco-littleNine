"""真实对局每回合的 save_snapshot 有多重（内存/序列化成本），回合数增长它怎么涨。

这是唯一确认在“累积”的东西：battle._snapshots[turn] 每回合存一份整场序列化。
用法: python -X utf8 probe_snapshot_weight.py <局号> [sims]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\projects\Roco-LittleNine")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def main() -> None:

    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import (
        MCTSAgent,
        NetworkPolicyAgent,
        _build_eval_battle,
        _eval_roster_rng,
        _load_sprite_skills,
        _paired_eval_tasks,
        _seed_eval_game,
    )
    from backend.sim.factory import SimFactory

    game_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    sims = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    dev = "cpu"
    model = ModularBattleNet.load("checkpoints/bc_fix/new10k.pt", device=dev)
    ev = TorchEvaluator(model, dev)
    factory = SimFactory()
    skills = _load_sprite_skills()
    tasks = _paired_eval_tasks(factory, skills, 200, rng=_eval_roster_rng())
    matchup = next(m for g, m in tasks if g == game_index)

    _seed_eval_game(game_index)
    battle = _build_eval_battle(factory, matchup)
    agent_a = MCTSAgent("A", battle.player_a, factory, NetworkPolicyAgent(evaluator=ev, greedy=True),
                        sims, temperature=0.0, root_noise=0.0, record=False, evaluator=ev,
                        opp_greedy=True, max_turns=150, leaf_value_weight=1.0)
    agent_b = MCTSAgent("B", battle.player_b, factory, NetworkPolicyAgent(evaluator=ev, greedy=True),
                        sims, temperature=0.0, root_noise=0.0, record=False, evaluator=ev,
                        opp_greedy=True, max_turns=150, leaf_value_weight=1.0)

    total_bytes = 0
    t_ser = 0.0
    for turn in range(1, 151):
        battle.execute_turn(agent_a, agent_b)
        if battle.is_finished:
            break
    snaps = battle.snapshots
    t0 = time.perf_counter()
    for turn, snap in snaps.items():
        total_bytes += len(json.dumps(snap, default=str))
    t_ser = time.perf_counter() - t0
    n = len(snaps)
    print(f"局 {game_index}：{battle.turn} 回合｜快照 {n} 份"
          f"｜合计序列化 {total_bytes/1024/1024:.2f} MB"
          f"｜平均 {total_bytes/max(n,1)/1024:.0f} KB/份"
          f"｜再次全量序列化耗时 {t_ser:.2f}s")


if __name__ == "__main__":
    main()
