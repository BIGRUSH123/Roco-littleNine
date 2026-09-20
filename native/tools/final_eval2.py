# -*- coding: utf-8 -*-
"""final_eval2.py — 终评：exp19_best vs exp17_best / vs exp13_best（各配对 200 局）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))


def run(cand_path: str, ref_path: str, tag: str) -> None:
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import _load_sprite_skills, evaluate_parallel
    from backend.sim.factory import SimFactory

    cand = ModularBattleNet.load(str(ROOT / cand_path), device="cuda")
    ref = ModularBattleNet.load(str(ROOT / ref_path), device="cuda")
    factory = SimFactory()
    skills = _load_sprite_skills()
    wr = evaluate_parallel(
        cand, ref, factory, skills,
        n_games=200, num_workers=16, device="cuda",
        inference_batch_size=256, inference_timeout_ms=5.0,
        num_simulations=100, max_turns=150, draw_margin=0.15,
        progress_every=50, stall_timeout_s=900.0, leaf_batch_size=128,
        verbose=False,
    )
    print(f"FINAL {tag}: win_rate = {wr:.4f}", flush=True)


if __name__ == "__main__":
    which = sys.argv[1]
    if which == "vs17":
        run("checkpoints/exp19_deliver/model_rl_best.pt",
            "checkpoints/exp17_deliver/model_rl_best.pt", "exp19_best vs exp17_best")
    elif which == "vs13":
        run("checkpoints/exp19_deliver/model_rl_best.pt",
            "checkpoints/exp13_long_a/model_rl_best.pt", "exp19_best vs exp13_best")
