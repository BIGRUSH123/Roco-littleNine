# -*- coding: utf-8 -*-
"""probe_leaf_reward.py — 自博弈"奖励信号"体检：价值头有没有区分度、叶节点奖励有没有驱动搜索。

背景（2026-09-23）：用户反馈"当前自博弈没有效果，应该是奖励信号没有设置好"。本脚本把这句话
拆成三个可测的量，全部在**远端**跑（本机不跑实验）：

  1. **价值头区分度**：`V_net` 在同一局内的取值范围/标准差。若几乎不变，价值头对搜索无信息。
  2. **奖励信号是否改变搜索**：`mcts_search` 的 π 在 `leaf_value_weight=0`（纯网络价值）与
     `=1`（默认：纯手写局面分 `sim.value.state_value`）下的**总变差距离** TV(π_net, π_heur)，
     以及各自与先验的 TV(prior, π)。TV≈0 说明奖励信号对搜索没有影响 → 自博弈目标≈先验。
  3. **价值头校准**：从若干真实对局按结局分组，看 `V_net` 是否随胜负单调（AUC）。

用法（远端）:
  python -X utf8 native/tools/probe_leaf_reward.py --ckpt new_10000.pt --positions 6 --sims 32
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from backend.engine.ai.core.encoder import encode_battle_state  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.mcts import get_valid_actions, mcts_search  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team, strategy_from_team,
)
from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.value import state_value  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="new_10000.pt", help="BC/RL 权重（远端工作区里的 .pt）")
    ap.add_argument("--positions", type=int, default=6, help="取样局面数")
    ap.add_argument("--sims", type=int, default=32)
    ap.add_argument("--turns", type=int, default=12, help="先走多少回合到中局再取样")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--weights", default="0,0.5,1", help="叶子奖励混合权重档位（逗号分隔）")
    return ap.parse_args()


def tv(p, q) -> float:
    """总变差距离（概率分布）。"""
    return 0.5 * float(np.abs(np.asarray(p, dtype=np.float64) - np.asarray(q, dtype=np.float64)).sum())


def entropy(p) -> float:
    """香农熵（nat）。"""
    q = np.asarray(p, dtype=np.float64)
    q = q / max(q.sum(), 1e-12)
    q = q[q > 0]
    return float(-(q * np.log(q)).sum())


def top_share(p) -> float:
    """访问/概率最高的那个动作占比（1/mask 数 = 完全平坦）。"""
    q = np.asarray(p, dtype=np.float64)
    return float(q.max() / max(q.sum(), 1e-12))


def midgame(factory, teams, seed: int, turns: int):
    """用规则层专家走到中局，返回 (battle, 对手 agent, A 视角结局)。"""
    rng = random.Random(seed)
    t = rng.choice(teams)
    ta, _ = spec_from_team(t, rng)
    tb, _ = spec_from_team(t, rng)
    p1 = factory.build_player("A", ta, item=item_from_team(t, ta))
    p2 = factory.build_player("B", tb, item=item_from_team(t, tb))
    b = factory.build_battle(p1, p2)
    a1 = RuleAgentV2("A", p1, strategy=strategy_from_team(t, rng))
    a2 = RuleAgentV2("B", p2, strategy=strategy_from_team(t, rng))
    random.seed(seed + 500)
    for _ in range(turns):
        if b.is_finished:
            break
        b.execute_turn(a1, a2)
    b._agent_a, b._agent_b = a1, a2      # 力竭换宠时 MCTS 会用到
    return b, a2, a1


def main() -> None:
    args = parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet.load(args.ckpt, device=dev)
    ev = TorchEvaluator(model, dev)
    factory = SimFactory()
    teams = load_meta_teams()
    leaf = lambda bb: state_value(bb, "A")  # noqa: E731

    print(f"权重={args.ckpt} 设备={dev} 局面数={args.positions} sims={args.sims}")

    # ── 1+2：逐局面测价值头区分度 / 奖励信号对 π 的影响 ──
    weights = [float(x) for x in str(args.weights).split(",") if x.strip()]
    print(f"\n叶子奖励权重档位: {weights}（0 = 纯网络价值，1 = 纯手感局面分 state_value）")
    print(f"{'局面':>4s} {'V_A+V_B':>9s} {'Vnet 范围':>18s} {'Vnet std':>9s} "
          + ' '.join(f'{("TV(prior,πw=%g)" % w):>15s}' for w in weights)
          + ' '.join(f'{("TV(πw=%g,πw=1)" % w):>15s}' for w in weights if w != 1.0))
    vnet_std, tv_prior, tv_cross, sym, agree = [], {w: [] for w in weights}, {w: [] for w in weights}, [], 0
    vnet_by_outcome: dict[float, list[float]] = {}
    for i in range(args.positions):
        b, opp, _a1 = midgame(factory, teams, args.seed + i * 7, args.turns)
        if b.is_finished:
            continue
        st = encode_battle_state(b, perspective="A")
        st_b = encode_battle_state(b, perspective="B")
        _valid, mask = get_valid_actions(b.player_a, b)
        _valid_b, mask_b = get_valid_actions(b.player_b, b)
        v_a, prior = ev.evaluate(st, mask)
        v_b, _pb = ev.evaluate(st_b, mask_b)
        sym.append(float(v_a) + float(v_b))          # 无偏时应≈0
        # 必须显式传 evaluator（或 device）：mcts_search 默认 device="cpu"，
        # 会另建一个 CPU 评估器，而模型已 .to(cuda) → 输入/权重分居两处，跑起来就
        # RuntimeError: Expected all tensors to be on the same device（2026-09-23 踩）
        kw = dict(model=model, factory=factory, opponent_agent=opp, evaluator=ev, device=dev,
                  num_simulations=args.sims, root_noise=0.0,
                  max_turns=args.max_turns, draw_margin=0.15)
        pis = {}
        for w in weights:
            pis[w] = mcts_search(b, **kw, leaf_value_fn=leaf,
                                 leaf_value_weight=float(w), leaf_value_scale=0.5)

        # 走到终局拿 V_net 的校准样本（用同一局的后续状态，避免额外开销）
        seq = []
        for _ in range(args.max_turns):
            if b.is_finished:
                break
            st_i = encode_battle_state(b, perspective="A")
            _valid_i, mask_i = get_valid_actions(b.player_a, b)
            v_i, _p_i = ev.evaluate(st_i, mask_i)
            seq.append(float(v_i))
            a1 = RuleAgentV2("A", b.player_a, strategy=None)
            a2 = RuleAgentV2("B", b.player_b, strategy=None)
            random.seed(args.seed + i)
            b.execute_turn(a1, a2)
        b._agent_a, b._agent_b = None, None
        outcome_a, _reason = battle_outcome_a(b, args.max_turns, draw_margin=0.15)
        vnet_by_outcome.setdefault(round(outcome_a, 1), []).extend(seq)

        for w in weights:
            tv_prior[w].append(tv(prior, pis[w]))
            if w != 1.0:
                tv_cross[w].append(tv(pis[w], pis[1.0]))
        am = lambda p: int(np.argmax(p))  # noqa: E731
        agree += int(len({am(pis[w]) for w in weights}) == 1)
        vnet_std.append(statistics.pstdev(seq) if len(seq) > 1 else 0.0)
        # 搜索的"集中度"：π 的熵与最大占比对比先验 —— 若两者接近，说明搜索没在挑招
        ent_p, ent_pi = entropy(prior), entropy(pis[1.0])
        print(f"     熵: prior={ent_p:.2f} → π={ent_pi:.2f} nat；最大占比 prior={top_share(prior):.2f} "
              f"→ π={top_share(pis[1.0]):.2f}（动作空间 {len(prior)} 个合法手）")
        print(f"{args.seed + i * 7:>4d} {sym[-1]:>+9.3f} {min(seq):>+8.3f}~{max(seq):<+8.3f} "
              f"{vnet_std[-1]:>9.3f} "
              + ' '.join(f'{tv_prior[w][-1]:>15.4f}' for w in weights)
              + ' '.join(f'{tv_cross[w][-1]:>15.4f}' for w in weights if w != 1.0))

    if vnet_std:
        print(f"\n均值: V_net 局内 std={statistics.mean(vnet_std):.3f} | "
              + ' | '.join(f'TV(prior,πw={w})={statistics.mean(tv_prior[w]):.4f}' for w in weights)
              + ' | ' + ' | '.join(f'TV(πw={w},πw=1)={statistics.mean(tv_cross[w]):.4f}'
                                   for w in weights if w != 1.0))
        print(f"价值头视角对称性: V_A+V_B 均值={statistics.mean(sym):+.3f} "
              f"中位={statistics.median(sym):+.3f} n={len(sym)}（无偏应≈0）")
        print(f"各档位 argmax 全一致的局面: {agree}/{len(vnet_std)}")

    # ── 3：价值头校准（按结局分组）──
    print("\n价值头校准（A 视角结局 → 局内 V_net 均值±std）:")
    for out in sorted(vnet_by_outcome):
        vs = vnet_by_outcome[out]
        if vs:
            print(f"  outcome={out:+.1f}: n={len(vs):4d}  V_net={statistics.mean(vs):+.3f} ± {statistics.pstdev(vs):.3f}")


if __name__ == "__main__":
    main()
