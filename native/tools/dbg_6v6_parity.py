# -*- coding: utf-8 -*-
"""dbg_6v6_parity.py — 6v6 阵容下 py/rust 整局一致性抽查（复用门工具 runner）。"""
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

_log = io.open(ROOT / "native" / "tools" / "_6v6_parity_log.txt", "w", encoding="utf-8")


def out(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.write(s + "\n")
    _log.flush()


def main() -> None:
    import json
    import random

    import numpy as np

    from backend.engine.ai.train import _load_sprite_skills, _random_item, _random_teams
    from backend.engine.ai.rust_selfplay_hook import _build_spec_from_teams
    from backend.engine.test_rust_gate import _first_diff, run_python, run_rust
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    n_ok = 0
    n_seeds = 5
    for seed in (101, 102, 103, 104, 105):
        random.seed(seed)
        np.random.seed(seed)
        ta, tb, ia, ib = _random_teams(factory, sprite_skills)
        assert len(ta) == 6 and len(tb) == 6, f"team size {len(ta)}/{len(tb)}"
        spec = _build_spec_from_teams(seed, factory, ta, tb, ia, ib)

        py_digests, py_winner = run_python(spec)
        rust_digests, rust_winner = run_rust(spec)

        if py_winner == rust_winner and len(py_digests) == len(rust_digests) \
                and py_digests == rust_digests:
            n_ok += 1
            out(f"seed {seed}: ✓ 6v6 整局一致（{len(py_digests) - 1} turns, "
                f"winner={py_winner}）")
        else:
            out(f"seed {seed}: ✗ 不一致！ py_winner={py_winner} rust={rust_winner} "
                f"turns py={len(py_digests)} rust={len(rust_digests)}")
            for i, (pd, rd) in enumerate(zip(py_digests, rust_digests)):
                if pd != rd:
                    out(f"   首差 turn#{i}: {_first_diff(pd, rd)[:280]}")
                    break
    out(f"── {n_ok}/{n_seeds} 一致")


if __name__ == "__main__":
    main()
