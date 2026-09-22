# -*- coding: utf-8 -*-
"""统计随机对局里的"空过"与"打满回合"占比（训练数据质量指标）。

口径：
  · 空过动作 = 该行动的事件列表为空（借用/复写在 engine 里即空操作；聚能满能量也是 +0E）
  · 满能量聚能 = 行动是 gather 且放招时能量已达上限（严格空操作）
  · 打满回合 = 60 回合内无人力竭（结局按 HP 差判定或平局）

用法: python stall_stats.py --games 200 --meta-frac 0.6 [--json-out stall.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("ROCO_REMOTE_ROOT", "/mnt/workspace/roco_remote"))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team, strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()

from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--mirror-frac", type=float, default=0.15,
                    help="meta 局里镜像（两侧同队）的比例；与 gen_bc_data 口径一致")
    ap.add_argument("--optimal-frac", type=float, default=0.95)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    factory = SimFactory()
    meta = load_meta_teams()
    rng = random.Random(a.seed)
    random.seed(a.seed)

    acts = collections.Counter()
    noop_kinds = collections.Counter()
    turns_total = 0
    capped = 0
    draw = 0
    per_team_noop: dict[str, list[int]] = {}
    t0 = time.time()
    for g in range(a.games):
        team_name = "随机阵容"
        if meta and rng.random() < a.meta_frac:
            i = rng.randrange(len(meta))
            mirror = rng.random() < a.mirror_frac
            j = i if mirror else rng.randrange(len(meta))
            team_a, team_b = meta[i], meta[j]
            team_name = team_a.get("name") or f"#{i}"
            sa, _ = spec_from_team(team_a, rng)
            sb, _ = spec_from_team(team_b, rng)
            ia, ib = item_from_team(team_a, sa), item_from_team(team_b, sb)
            st_a = strategy_from_team(team_a, rng)
            st_b = strategy_from_team(team_b, rng)
        else:
            sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                             optimal_frac=a.optimal_frac, meta_frac=0.0,
                                             rng=rng)
            st_a = st_b = TeamStrategy(default=SpriteStrategy())
        p1 = factory.build_player("A", sa, item=ia)
        p2 = factory.build_player("B", sb, item=ib)
        battle = factory.build_battle(p1, p2)
        a1 = RuleAgentV2("A", p1, strategy=st_a)
        a2 = RuleAgentV2("B", p2, strategy=st_b)
        turns = 0
        while not battle.is_finished and turns < a.max_turns:
            rec = battle.execute_turn(a1, a2)
            turns += 1
            for tag, player in (("A", p1), ("B", p2)):
                ar = rec.action_a if tag == "A" else rec.action_b
                if ar is None:
                    continue
                acts[ar.kind] += 1
                if not ar.events and ar.status == "ok":
                    noop_kinds[ar.kind] += 1
                    per_team_noop.setdefault(team_name, [0, 0])[0] += 1
                per_team_noop.setdefault(team_name, [0, 0])[1] += 1
        turns_total += turns
        if turns >= a.max_turns:
            capped += 1
        outcome, _ = battle_outcome_a(battle, a.max_turns)
        if outcome == 0:
            draw += 1
    total_acts = sum(acts.values()) or 1
    noop_total = sum(noop_kinds.values())
    out = {
        "games": a.games, "mean_turns": turns_total / max(1, a.games),
        "capped_games": capped, "capped_rate": capped / max(1, a.games),
        "draws": draw, "draw_rate": draw / max(1, a.games),
        "actions": dict(acts),
        "noop_actions": dict(noop_kinds),
        "noop_rate": noop_total / total_acts,
        "noop_action_share": {k: round(v / total_acts, 5) for k, v in noop_kinds.items()},
        "per_team_noop_rate": {k: round(v[0] / max(1, v[1]), 4)
                               for k, v in sorted(per_team_noop.items(),
                                                  key=lambda kv: -(kv[1][0] / max(1, kv[1][1])))
                               if v[1] >= 40},
        "seconds": round(time.time() - t0, 1),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                    encoding="utf-8")


if __name__ == "__main__":
    main()
