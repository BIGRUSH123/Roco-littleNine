# -*- coding: utf-8 -*-
"""final_eval.py — 终评①：exp17_best vs exp13_best（基座）配对 200 局。

注意：必须用 if __name__ == "__main__" 守卫 —— Windows spawn 子进程会
重新导入本模块，顶层执行会自我递归导致 stall。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import _load_sprite_skills, evaluate_parallel
    from backend.sim.factory import SimFactory

    cand = ModularBattleNet.load(
        str(ROOT / "checkpoints/exp17_deliver/model_rl_best.pt"), device="cuda")
    base = ModularBattleNet.load(
        str(ROOT / "checkpoints/exp13_long_a/model_rl_best.pt"), device="cuda")
    factory = SimFactory()
    skills = _load_sprite_skills()

    wr = evaluate_parallel(
        cand, base, factory, skills,
        n_games=200, num_workers=16, device="cuda",
        inference_batch_size=256, inference_timeout_ms=5.0,
        num_simulations=100, max_turns=150, draw_margin=0.15,
        progress_every=25, stall_timeout_s=900.0, leaf_batch_size=128,
    )
    print(f"FINAL paired 200 games: exp17_best win_rate = {wr:.4f} vs exp13_best",
          flush=True)


if __name__ == "__main__":
    main()
