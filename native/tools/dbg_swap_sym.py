# -*- coding: utf-8 -*-
"""dbg_swap_sym.py — 编码器视角对称性探针。

验证：encode(state, perspective="B") 是否等于
     swap(state.players) 后 encode(swap_state, perspective="A")。
若有差 → A/B 双视角样本给价值头的是互相矛盾的特征（真实病根）；
若全等 → 双视角数据天然对称，换边增强无额外收益。
"""
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

_log = io.open(ROOT / "native" / "tools" / "_swap_sym_log.txt", "w", encoding="utf-8")


def out(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.write(s + "\n")
    _log.flush()


def main() -> None:
    import json
    import random

    import numpy as np

    from backend.engine.ai.core.encoder import encode_battle_state
    from backend.engine.test_rust_gate import battle_from_spec
    from backend.sim.agent import RuleAgent

    KEYS = [
        "sprite_stats", "sprite_elements", "sprite_states",
        "skill_stats", "skill_elements", "skill_states",
        "global_stats", "global_elements", "ast_tokens", "ast_values",
    ]

    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    random.seed(spec["seed"] + 1)
    np.random.seed(spec["seed"] + 1)

    battle = battle_from_spec(spec)
    agents = (RuleAgent("A", battle.player_a), RuleAgent("B", battle.player_b))

    def probe(turn: int) -> None:
        ea = encode_battle_state(battle, perspective="A")
        eb = encode_battle_state(battle, perspective="B")
        battle.player_a, battle.player_b = battle.player_b, battle.player_a
        try:
            ea_swapped = encode_battle_state(battle, perspective="A")
        finally:
            battle.player_a, battle.player_b = battle.player_b, battle.player_a

        bad_total = 0
        for k in KEYS:
            a1 = np.asarray(eb[k], dtype=np.float32)
            a2 = np.asarray(ea_swapped[k], dtype=np.float32)
            if a1.shape != a2.shape:
                out(f"  [turn {turn}] {k}: 形状不同 {a1.shape} vs {a2.shape}")
                bad_total += 1
                continue
            d = ~np.isclose(a1, a2, rtol=0, atol=1e-6)
            if d.any():
                idx = np.argwhere(d)
                r, c = idx[0]
                bad_total += int(d.sum())
                out(f"  [turn {turn}] {k}: {int(d.sum())} 处不等 "
                    f"首处@{tuple(int(x) for x in idx[0])} "
                    f"B视角={a1[r, c]!r} swap后A视角={a2[r, c]!r}")
        if bad_total == 0:
            out(f"  [turn {turn}] ✓ 全部 10 数组严格对称")

    # 每回合行动后探针一次，覆盖开局/中期/残局
    turn = 0
    while not battle.is_finished and turn < 30:
        battle.execute_turn(agents[0], agents[1])
        turn += 1
        if turn in (1, 2, 3, 5, 8, 12, 16, 20, 25, 30):
            probe(turn)
    out(f"对局结束: turn={turn} finished={battle.is_finished} winner={battle.winner}")


if __name__ == "__main__":
    main()
