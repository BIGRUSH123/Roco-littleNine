"""定位「局内决策耗时暴涨」：一边推进对局，一边记录双方局内结构规模。

背景（2026-09-24 远端实测，sims=100）：绝大多数对局单次决策 0.1~0.5s，但个别对局
在局内某处暴涨到 23s（局 163：0.17→0.31→2.52→2.18→23.04，35 回合就撞 300s 预算）。
本脚本按真实回合推进，逐回合记录：
  - 本回合耗时（含双方 MCTS）
  - 双方全部精灵的 active_effects 条数 / counters 条数 / _modifiers 条数 / 技能状态条数
  - 全局：marks 数、team_counters、abnormal_stacks_battle、observer/registry 注册数
  - MCTS 每次仿真前拷贝的状态条目数（save_mutable_state）
→ 哪一个量在暴涨，就是"累积"的元凶。

用法: python -X utf8 probe_state_growth.py --indices 0-79 --sims 20 --budget-s 45
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # native/tools/ 上溯两级 = 仓库根
if not (ROOT / "backend").is_dir():          # 兜底：本地按绝对路径跑过
    ROOT = Path(r"D:\projects\Roco-LittleNine")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def parse_indices(spec: str) -> set[int] | None:
    if not spec.strip():
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.update(range(lo, hi + 1))
        elif part:
            out.add(int(part))
    return out


def world_metrics(battle) -> dict:
    """全局结构规模（不含精灵明细）。"""
    g = getattr(battle, "globals", None) or getattr(battle, "_globals", None)
    marks = 0
    if g is not None:
        for team, lst in (getattr(g, "mark_effects", {}) or {}).items():
            marks += len(lst)
    return {
        "marks": marks,
        "team_cnt": sum(len(v) for v in (getattr(battle, "team_counters", {}) or {}).values()),
        "log": len(getattr(battle, "log", []) or []),
        "snaps": len(battle.snapshots),
    }


def sprite_metrics(battle) -> dict:
    eff = cnt = mods = skills = 0
    for player in (battle.player_a, battle.player_b):
        for sp in player.team:
            eff += len(getattr(sp, "active_effects", []) or [])
            cnt += len(getattr(sp, "counters", {}) or {})
            mods += len(getattr(sp, "_modifiers", {}) or {})
            for sk in (sp.skills or []):
                skills += len(getattr(sk, "_modifiers", {}) or {})
    return {"effects": eff, "counters": cnt, "mods": mods, "skill_mods": skills}


def count_entries(state: dict) -> int:
    total = 0
    for sp in state.get("sprites", ()):
        total += len(sp.get("effects") or ())
        total += len(sp.get("counters") or {})
        total += len(sp.get("modifiers") or {})
        total += len(sp.get("skill_states") or ())
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", default="0-79")
    ap.add_argument("--games", type=int, default=200, help="套件规模（决定任务表/种子）")
    ap.add_argument("--sims", type=int, default=20)
    ap.add_argument("--budget-s", type=float, default=45.0)
    ap.add_argument("--max-turns", type=int, default=150)
    ap.add_argument("--ckpt", default="checkpoints/bc_fix/new10k.pt")
    ap.add_argument("--verbose-from", type=float, default=20.0,
                    help="本局耗时超过这个秒数才打印逐回合明细")
    ap.add_argument("--peak-from", type=float, default=3.0,
                    help="单回合耗时超过这个秒数也打印明细（抓局内暴涨）")
    args = ap.parse_args()

    import torch

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
    from backend.sim import battle as battle_mod
    from backend.sim.factory import SimFactory

    orig_save = battle_mod.Battle.save_mutable_state

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet.load(args.ckpt, device=dev)
    ev = TorchEvaluator(model, dev)
    factory = SimFactory()
    skills = _load_sprite_skills()
    tasks = _paired_eval_tasks(factory, skills, args.games, rng=_eval_roster_rng())
    wanted = parse_indices(args.indices)

    print(f"套件 {args.games}｜sims={args.sims}｜cap={args.max_turns}｜单局上限 {args.budget_s:.0f}s"
          f"｜测 {len(wanted) if wanted else '全部'} 局")

    slow: list[tuple[int, dict]] = []
    for g, matchup in tasks:
        if wanted is not None and g not in wanted:
            continue
        _seed_eval_game(g)
        battle = _build_eval_battle(factory, matchup)
        agent_a = MCTSAgent("A", battle.player_a, factory,
                            NetworkPolicyAgent(evaluator=ev, greedy=True), args.sims,
                            temperature=0.0, root_noise=0.0, record=False, evaluator=ev,
                            opp_greedy=True, max_turns=args.max_turns, leaf_value_weight=1.0)
        agent_b = MCTSAgent("B", battle.player_b, factory,
                            NetworkPolicyAgent(evaluator=ev, greedy=True), args.sims,
                            temperature=0.0, root_noise=0.0, record=False, evaluator=ev,
                            opp_greedy=True, max_turns=args.max_turns, leaf_value_weight=1.0)

        rows = []
        t_game = time.perf_counter()
        for turn in range(1, args.max_turns + 1):
            t0 = time.perf_counter()
            battle.execute_turn(agent_a, agent_b)
            turn_s = time.perf_counter() - t0
            state = orig_save(battle)
            rows.append({"turn": turn, "s": turn_s,
                         **sprite_metrics(battle), **world_metrics(battle),
                         "entries": count_entries(state)})
            if battle.is_finished or time.perf_counter() - t_game >= args.budget_s:
                break
        dt = time.perf_counter() - t_game
        turns = len(rows)
        peak = max(r["s"] for r in rows) if rows else 0.0
        names = " / ".join(x.get("name", "?") for x in matchup[0])[:34]
        interesting = dt >= args.verbose_from or peak >= args.peak_from
        tag = "  <<< 看明细" if interesting else ""
        print(f"  局 {g:>4d} {dt:7.1f}s  回合 {turns:>3d}  最慢回合 {peak:6.2f}s  {names}{tag}",
              flush=True)
        if interesting:
            slow.append((g, {"rows": rows}))
            print("        回合   本回合s   effects counters mods skillmods  marks tcnt entries snaps",
                  flush=True)
            step = max(1, turns // 12)
            for r in rows[::step] + [rows[-1]]:
                print(f"        {r['turn']:>4d} {r['s']:>9.2f} {r['effects']:>9d} "
                      f"{r['counters']:>8d} {r['mods']:>4d} {r['skill_mods']:>9d} "
                      f"{r['marks']:>6d} {r['team_cnt']:>4d} {r['entries']:>7d} {r['snaps']:>5d}",
                      flush=True)

    print(f"\n共测 {len(wanted) if wanted else len(tasks)} 局｜慢局（≥{args.verbose_from:.0f}s）"
          f"{len(slow)} 局：{[g for g, _ in slow]}")


if __name__ == "__main__":
    main()
