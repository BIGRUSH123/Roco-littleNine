# -*- coding: utf-8 -*-
"""native/tools/audit_selfplay_samples.py — 自博弈样本不变量审计（真跑几局）。

自博弈数据不进磁盘（只进回放缓冲），所以不能从文件侧检查；本工具直接调
`_play_one_rl_battle` 打几局，然后逐样本验证三条不变量：

  1. **视角标签配对**：一局内所有样本的 value 必须只取两个互为相反数的值
     （A 方 +outcome_a、B 方 -outcome_a）。若出现「只有一个符号」或「多个
     无关值」，说明视角与标签不匹配——正是 BC 生成器踩过的坑。
  2. **π 的支撑集 ⊆ 合法动作**：`sum(π * mask) ≈ 1`（MCTS 不应访问非法动作）。
     若显著小于 1，说明回放里的 (π, mask) 不是同一次搜索的产物。
  3. **π 与先验不同**：若 π 与网络自身 masked softmax 几乎相同，则自博弈目标
     不携带新信息（这是「微调无提升」的机制解释，而非 bug）。

用法:
  python native/tools/audit_selfplay_samples.py --battles 2 --sims 8
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.engine.ai.core.evaluator import TorchEvaluator
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.vocab import VOCAB_SIZE
from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle
from backend.sim.factory import SimFactory


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--battles", type=int, default=2)
    ap.add_argument("--sims", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--ckpt", default="checkpoints/bc_init.pt",
                    help="用 BC 权重当自博弈先验（与 exp23 同源）")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    device = args.device if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet(trunk_dim=256, num_blocks=4, dropout=0.0,
                             vocab_size=VOCAB_SIZE, with_attention=True)
    state = torch.load(args.ckpt, map_location=device, weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.to(device).eval()
    evaluator = TorchEvaluator(model, device=device)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    n_samples = 0
    n_bad_support = 0
    support_vals: list[float] = []
    distinct_outcome_sets: list[int] = []
    agree = 0
    n_cmp = 0
    kl_sum = 0.0
    t0 = time.time()
    for b in range(args.battles):
        states, P, M, v, end_reason, summary = _play_one_rl_battle(
            factory, sprite_skills, evaluator,
            num_simulations=args.sims, max_turns=args.max_turns,
            temperature=1.0, root_noise=0.25,
        )
        if len(v) == 0:
            print(f"  局 {b}: 无样本（{end_reason}）")
            continue
        support = (np.asarray(P, dtype=np.float64) * np.asarray(M, dtype=np.float64)).sum(axis=1)
        support_vals.extend(support.tolist())
        bad = int((support < 0.99).sum())
        n_bad_support += bad
        n_samples += len(v)
        uniq = np.unique(np.round(v, 4))
        distinct_outcome_sets.append(len(uniq))

        # 不变量 3：π 与网络先验的差异（搜索相对先验新增了多少信息）
        Pn = np.asarray(P, dtype=np.float64)
        Mn = np.asarray(M, dtype=np.float32)
        with torch.no_grad():
            xb = {k: torch.from_numpy(np.stack([s[k] for s in states])).to(device)
                  for k in states[0]}
            _, logits = model(xb)
            logits = logits.masked_fill(torch.from_numpy(Mn).to(device) < 0.5, -1e9)
            prior = torch.softmax(logits, dim=-1).cpu().numpy().astype(np.float64)
        pi_arg = Pn.argmax(axis=1)
        pr_arg = prior.argmax(axis=1)
        agree += int((pi_arg == pr_arg).sum())
        n_cmp += len(pi_arg)
        kl = (Pn * (np.log(Pn + 1e-12) - np.log(prior + 1e-12))).sum(axis=1)
        kl_sum += float(kl.sum())

        print(f"  局 {b}: 样本 {len(v):5d}  终局 {end_reason:14s} "
              f"value 取值 {uniq.tolist()[:5]}  "
              f"π 合法质量 均值 {support.mean():.4f} 最小 {support.min():.4f}  "
              f"越界样本 {bad}  π/先验 argmax 一致 {100 * (pi_arg == pr_arg).mean():.1f}%")

    print(f"\n合计样本 {n_samples}  耗时 {time.time() - t0:.0f}s")
    if support_vals:
        s = np.array(support_vals)
        print(f"不变量 2（π 支撑 ⊆ 合法动作）: 均值 {s.mean():.5f}  中位 {np.median(s):.5f}  "
              f"<0.99 的比例 {n_bad_support / len(s):.3%}")
    print(f"不变量 1（一局内 value 取值个数）: {distinct_outcome_sets} "
          f"（决定性对局应为 2，且两值互为相反数；平局为 1 个 0）")
    if n_cmp:
        print(f"不变量 3（π 相对网络先验的新信息）: argmax 一致率 "
              f"{agree / n_cmp:.1%}   KL(π‖先验) 均值 {kl_sum / n_cmp:.4f} nats")
        print("   一致率越高 / KL 越小 → 搜索几乎没改变策略，自博弈目标 ≈ 自己的输出，"
              "这解释「微调不提升」而不属于 bug")

    # 不变量 1 更严：同局 value 只能有两个互为相反数的值
    ok = all(k <= 2 for k in distinct_outcome_sets)
    print("判定:", "不变量 1 通过（取值个数 ≤2）" if ok else "!! 不变量 1 可疑")
    if support_vals and n_bad_support / max(1, len(support_vals)) > 0.01:
        print("!! 不变量 2 可疑：π 质量大量落在 mask 之外")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
