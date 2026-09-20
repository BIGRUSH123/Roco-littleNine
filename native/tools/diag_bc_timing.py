# -*- coding: utf-8 -*-
"""native/tools/diag_bc_timing.py — 定位 BC 数据生成的慢对局。

对 meta 分支与随机分支各跑若干局，逐局打印墙钟耗时/回合数/结束原因，
并把耗时最高的对局（队伍构成）写进 UTF-8 报告，供定位卡死/超时来源。
"""
from __future__ import annotations

import argparse
import importlib.util
import random
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.bc_record import run_recorded_battle  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.engine.ai.train import _random_teams  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

spec = importlib.util.spec_from_file_location("gen_bc", _ROOT / "native/tools/gen_bc_data.py")
gen_bc = importlib.util.module_from_spec(spec)
sys.modules["gen_bc"] = gen_bc
spec.loader.exec_module(gen_bc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta-games", type=int, default=10)
    ap.add_argument("--random-games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    factory = SimFactory()
    sprite_skills = dict(SPRITE_RANDOM_POOL)
    meta = load_meta_teams()
    rng = random.Random(args.seed)
    rows: list[tuple[float, str, int, str]] = []

    print(f"meta 队 {len(meta)} 支；池 {len(sprite_skills)} 只")
    for label, n in (("meta", args.meta_games), ("random", args.random_games)):
        for g in range(n):
            if label == "meta" and meta:
                i_a = rng.randrange(len(meta))
                i_b = rng.randrange(len(meta))
                team_a, _ = spec_from_team(meta[i_a], rng)
                team_b, _ = spec_from_team(meta[i_b], rng)
                st_a = strategy_from_team(meta[i_a], rng)
                st_b = strategy_from_team(meta[i_b], rng)
                it_a, it_b = item_from_team(meta[i_a]), item_from_team(meta[i_b])
                tag = f"meta:{meta[i_a]['name']} vs {meta[i_b]['name']} 魔法={it_a.name}/{it_b.name}"
            else:
                team_a, team_b, it_a, it_b = _random_teams(factory, sprite_skills)
                st_a = gen_bc.jittered_default_strategy(rng)
                st_b = gen_bc.jittered_default_strategy(rng)
                tag = "random: " + "|".join(sp["name"] for sp in team_a)
            t0 = time.perf_counter()
            samples, _outcome, reason, turns = run_recorded_battle(
                factory, team_a, team_b,
                lambda tg, pl: RuleAgentV2(tg, pl, strategy=st_a),
                lambda tg, pl: RuleAgentV2(tg, pl, strategy=st_b),
                item_a=it_a, item_b=it_b, game_id=g,
            )
            dt = time.perf_counter() - t0
            rows.append((dt, label, turns, reason))
            print(f"  [{label} {g + 1}/{n}] {dt:6.2f}s turns={turns:3d} "
                  f"{reason:14s} samples={len(samples)}  {tag[:70]}")

    slow = sorted(rows, key=lambda r: -r[0])[:5]
    lines = ["== 最慢对局 =="] + [f"  {dt:7.2f}s {lab} turns={t} {r}" for dt, lab, t, r in slow]
    for label in ("meta", "random"):
        subset = [r[0] for r in rows if r[1] == label]
        if subset:
            lines.append(f"{label}: n={len(subset)} 总计 {sum(subset):.1f}s "
                         f"平均 {sum(subset) / len(subset):.2f}s 最大 {max(subset):.2f}s")
    out = _ROOT / "_bc_timing.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
