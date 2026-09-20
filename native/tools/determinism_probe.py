# -*- coding: utf-8 -*-
"""native/tools/determinism_probe.py — 同种子可复现性探针（逐回合轨迹 + 状态指纹）。

背景：`eval_fixed_rosters.py` 同命令同 seed 多次运行给出 0.612 / 0.564 / 0.573（1000 局），
差了 5 个百分点——这在"全部 seed 固定"的前提下不该发生。本工具把一次评测压成
**可 diff 的逐回合轨迹**，用来定位第一处分歧。

用法：
    env\\python.exe native/tools/determinism_probe.py --games 20 --out run1.jsonl
    env\\python.exe native/tools/determinism_probe.py --games 20 --out run2.jsonl
    env\\python.exe native/tools/determinism_probe.py --games 20 --inner-repeat 2
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--out", default="", help="轨迹输出文件（JSONL）")
    ap.add_argument("--inner-repeat", type=int, default=1,
                    help=">1 时在同一进程内把每局重复跑 N 次并比较（隔离地址顺序/跨局泄漏）")
    return ap.parse_args()


def _sprite_fingerprint(sprite) -> dict:
    """精灵级状态指纹：血量/能量/属性修饰/效果层数——只放可比较的标量。"""
    return {
        "hp": sprite.current_hp,
        "energy": sprite.energy,
        "mods": sorted((str(k), round(float(v), 6)) for k, v in
                       getattr(sprite, "_modifiers", {}).items()),
        "effects": sorted((getattr(e, "name", "") or type(e).__name__,
                           int(getattr(e, "stacks", 0) or 0),
                           int(getattr(e, "ttl", 0) or 0))
                          for e in getattr(sprite, "active_effects", [])),
        "cd": [int(getattr(sk, "cooldown", 0) or 0) for sk in (sprite.skills or [])],
    }


def _state_fingerprint(battle) -> dict:
    """整场状态指纹：双方 12 只的血/能/修饰 + 印记 + 天气 + 当前场上。"""
    out = {"active": [battle.player_a.active_index, battle.player_b.active_index],
           "lives": [battle.player_a.lives, battle.player_b.lives],
           "weather": [battle.globals.weather, battle.globals.weather_turns],
           "night": bool(getattr(battle.globals, "night", False)),
           "sprites": []}
    for player in (battle.player_a, battle.player_b):
        for sprite in player.team:
            out["sprites"].append(_sprite_fingerprint(sprite))
    marks = {}
    for team, lst in battle.globals.mark_effects.items():
        marks[team] = sorted((m.name, int(getattr(m, "stacks", 0) or 0)) for m in lst)
    out["marks"] = marks
    return out


def _digest(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _build_rosters(args, rng, meta, factory, T, pool):
    from backend.engine.ai.data.meta_teams import (item_from_team, spec_from_team,
                                                   strategy_from_team)
    rosters = []
    for _ in range(args.games):
        use_meta = bool(meta) and (args.meta_frac >= 1.0 or rng.random() < args.meta_frac)
        if use_meta:
            ia_, ib_ = rng.randrange(len(meta)), rng.randrange(len(meta))
            sa, _ = spec_from_team(meta[ia_], rng)
            sb, _ = spec_from_team(meta[ib_], rng)
            item_a, item_b = item_from_team(meta[ia_], sa), item_from_team(meta[ib_], sb)
            stra = strategy_from_team(meta[ia_], rng)
            strb = strategy_from_team(meta[ib_], rng)
            tag = f"meta:{meta[ia_]['name']} vs {meta[ib_]['name']}"
        else:
            sa, sb, item_a, item_b = T._random_teams(
                factory, dict(pool), optimal_frac=0.95, meta_frac=0.0, rng=rng)
            stra = strb = None
            tag = "random:" + "/".join(s["name"] for s in sa)
        rosters.append({"specs_a": sa, "specs_b": sb, "item_a": item_a, "item_b": item_b,
                        "strat_a": stra, "strat_b": strb, "tag": tag})
    return rosters


def _run_game(args, roster, factory, AgentCls, TeamStrategy, SpriteStrategy, gidx, trace):
    from backend.engine.ai.core.outcome import battle_outcome_a
    random.seed(args.seed * 1000003 + gidx * 7919)
    p1 = factory.build_player("A", copy.deepcopy(roster["specs_a"]),
                              item=copy.deepcopy(roster["item_a"]))
    p2 = factory.build_player("B", copy.deepcopy(roster["specs_b"]),
                              item=copy.deepcopy(roster["item_b"]))
    battle = factory.build_battle(p1, p2)
    stra = roster["strat_a"] or TeamStrategy(default=SpriteStrategy())
    strb = roster["strat_b"] or stra
    a1 = AgentCls("A", p1, strategy=stra)
    a2 = AgentCls("B", p2, strategy=strb)
    turns = 0
    while not battle.is_finished and turns < args.max_turns:
        battle.execute_turn(a1, a2)
        turns += 1
        rec = battle.log[-1] if battle.log else None
        trace.append({
            "t": turns,
            "first": getattr(rec, "first_team", ""),
            "hdr": getattr(rec, "_header", ""),
            "ev": (list(getattr(rec, "turn_start_events", []) or [])
                   + list(getattr(rec, "faint_check_events", []) or [])
                   + list(getattr(rec, "turn_end_events", []) or [])),
            "state": _state_fingerprint(battle),
        })
    outcome, _reason = battle_outcome_a(battle, args.max_turns)
    return outcome


def main() -> None:
    args = parse_args()
    ensure_hash_seed()

    from backend.engine.ai import train as T
    from backend.engine.ai.data.meta_teams import load_meta_teams
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
    from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    meta = load_meta_teams()
    rng = random.Random(args.seed)
    rosters = _build_rosters(args, rng, meta, factory, T, SPRITE_RANDOM_POOL)

    print(f"=== 探针：{args.games} 局，seed={args.seed}，inner_repeat={args.inner_repeat} ===")
    t0 = time.time()
    lines: list[str] = []
    game_digests: list[str] = []
    mismatch_report: list[str] = []
    for gidx, roster in enumerate(rosters):
        runs = []
        for rep in range(args.inner_repeat):
            trace: list[dict] = []
            outcome = _run_game(args, roster, factory, RuleAgentV2,
                                TeamStrategy, SpriteStrategy, gidx, trace)
            runs.append((outcome, trace))
        digest = _digest(runs[0])  # 只取第一遍 → inner-repeat 不影响指纹
        game_digests.append(digest)
        if args.inner_repeat > 1:
            base_outcome, base_trace = runs[0]
            for rep in range(1, len(runs)):
                o, tr = runs[rep]
                if o == base_outcome and tr == base_trace:
                    continue
                first = next((i for i in range(max(len(base_trace), len(tr)))
                              if i >= len(base_trace) or i >= len(tr)
                              or base_trace[i] != tr[i]), None)
                detail = ""
                if first is not None and first < len(base_trace) and first < len(tr):
                    a, b = base_trace[first], tr[first]
                    detail = (f" 首异回合{first + 1}：\n"
                              f"   rep0 {a['hdr']}\n   rep{rep} {b['hdr']}\n"
                              f"   rep0 ev={a['ev']}\n   rep{rep} ev={b['ev']}")
                mismatch_report.append(
                    f"  局{gidx} ({roster['tag'][:48]}) rep0={base_outcome} rep{rep}={o}"
                    f" len {len(base_trace)}/{len(tr)}{detail}")
        lines.append(json.dumps({"game": gidx, "tag": roster["tag"],
                                 "digest": digest, "outcome": runs[0][0],
                                 "turns": len(runs[0][1])}, ensure_ascii=False))
    total = _digest(game_digests)
    print(f"  **总指纹 {total}**（{len(game_digests)} 局，{time.time() - t0:.1f}s）")
    print("  逐局：" + " ".join(d[:8] for d in game_digests[:12]) + (" …" if len(game_digests) > 12 else ""))
    if mismatch_report:
        print(f"  **进程内重复不一致：{len(mismatch_report)} 局**")
        for line in mismatch_report[:6]:
            print(line)
    elif args.inner_repeat > 1:
        print("  进程内重复完全一致（→ 多进程运行间的差异来自跨进程环境/地址顺序）")

    if args.out:
        out = Path(args.out)
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"# digest={total}\n")
            for line in lines:
                f.write(line + "\n")
        print(f"  轨迹已写入 {out}")


if __name__ == "__main__":
    main()
