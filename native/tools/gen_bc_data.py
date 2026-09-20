# -*- coding: utf-8 -*-
"""native/tools/gen_bc_data.py — BC 专家数据生成 CLI。

组队策略（与选队解耦）：
  - meta_frac 概率使用 meta_teams.json 中的原型队（双方各抽一队，
    IV/性格/换宠阈值逐局扰动），其余走 train._random_teams 随机阵容；
  - 随机阵容的配装由 build_from_reference 生成：optimal_frac（默认 0.95）概率
    整队走**最优培养**（技能/血脉/性格/天赋取 wiki PVP 推荐的合规最优），其余
    整队按 wiki 占比抽样（带冲突修复）；队级道具按血脉决定（有首领 → 进化之力）；
  - 双方均由 RuleAgentV2（可挂 TeamStrategy）驱动，无 MCTS，单局亚秒级。

输出：npz（状态/动作/掩码/胜负/game_id/队伍标记）+ json sidecar（队伍元信息）。
用法:
  python native/tools/gen_bc_data.py --games 2500 --meta-frac 0.6 \
      --out checkpoints/bc_data.npz --workers 0

并行度（2026-09-21 实测，i5-14600KF：6 P 核 + 8 E 核 / 20 线程）:
  | 模式                  | workers | 吞吐         | 说明                        |
  |-----------------------|---------|--------------|-----------------------------|
  | 规划层专家 plan_depth=1| 6~12   | ~3.9 局/s    | 6 P 核跑满即到顶，加核无益  |
  | 规划层专家 plan_depth=1| 19      | 3.1 局/s     | 多出来的进程落在 E 核上互相挤 |
  | 纯规则 plan_depth=0    | 19      | 59 局/s      | 单局便宜，核越多越好        |
  `--workers 0` 会按「是否用规划层」自动选（见 `_auto_workers`）。

父进程侧（采集/堆叠/落盘）实测只占 0.3%（单局结果序列化中位 2.9ms vs 打局 976ms），
所以别再打这块的主意；要更快只能减规划层的 rollout 数或换机器。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.engine.ai.bc_record import run_recorded_battle
from backend.engine.ai.data.meta_teams import (
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
    validate_meta_teams,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.engine.ai.train import _random_teams
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.factory import SimFactory


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2500)
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--meta-file", default=None)
    ap.add_argument("--out", default="checkpoints/bc_data.npz")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--draw-margin", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--mirror-frac", type=float, default=0.15,
                    help="meta 对局中双方同队的镜像比例")
    ap.add_argument("--optimal-frac", type=float, default=0.95,
                    help="随机阵容里按「最优培养」整队出装的占比（其余按 wiki 占比抽样）")
    ap.add_argument("--per-game-log", action="store_true",
                    help="逐局打印耗时/队伍（定位慢对局，默认每 100 局汇总）")
    ap.add_argument("--log-file", default="",
                    help="把进度直接写入该文件并 flush（长跑时绕过 shell 缓冲）")
    ap.add_argument("--compress", action="store_true",
                    help="写 savez_compressed（体积小 ~30%%，但大样本量下极慢）")
    ap.add_argument("--workers", type=int, default=1,
                    help="并行 worker 进程数（1 = 单进程；0 = 自动：规划层 → 8，"
                         "纯规则 → cpu-1。实测规划层在 6 P 核上就到顶，见模块 docstring）")
    ap.add_argument("--start-game", type=int, default=0,
                    help="从第 N 局开始打（仍会完整产队，保证与整跑一致；用于复现慢局）")
    ap.add_argument("--hang-dump-sec", type=int, default=0,
                    help="单局超过 N 秒就把调用栈打到 stderr（定位死循环，0=关）")
    ap.add_argument("--stall-warn-sec", type=int, default=60,
                    help="多进程模式下多久没有新结果就打印剩余局号（定位卡死，0=关）")
    return ap.parse_args()


def _log(msg: str, args) -> None:
    """进度输出：同时进 stdout 与（可选）直接 flush 的日志文件。

    长跑时 stdout 常被 shell 管道缓冲，看不出进度；--log-file 绕过该问题。
    """
    print(msg, flush=True)
    path = getattr(args, "log_file", "")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")


def jittered_default_strategy(rng: random.Random) -> TeamStrategy:
    """随机阵容对局也做阈值抖动，避免确定性复读。"""
    return TeamStrategy(default=SpriteStrategy(
        threat_switch_hp=rng.uniform(0.8, 1.0),
        switch_hp=rng.uniform(0.25, 0.45),
    ))


# ═══════════════════════════════════════════════════════════════════
# 对局计划 / 打局（父进程产队，worker 只打局）
# ═══════════════════════════════════════════════════════════════════

def _build_plans(args, meta_teams, sprite_skills, rng, team_game_counts) -> list[dict]:
    """父进程按局号产出全部对局计划（唯一 RNG 流 → 与 worker 数无关的确定性）。

    随机阵容这一支的配装由 `_random_teams` 生成：`--optimal-frac` 概率整队走
    **最优培养**（BC 预训练口径），其余整队按 wiki 占比抽样。meta 抽取由本函数
    自己决定，故显式传 `meta_frac=0.0` 关掉 `_random_teams` 内部的 meta 混合
    ——两条路径叠加会把 meta 占比变成 1-(1-m1)(1-m2)，与命令行所见不一致。
    """
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    plans: list[dict] = []
    for g in range(args.games):
        is_meta = 0
        team_ids = (-1, -1)
        n_optimal = 0
        if meta_teams and rng.random() < args.meta_frac:
            is_meta = 1
            i_a = rng.randrange(len(meta_teams))
            mirror = len(meta_teams) == 1 or rng.random() < args.mirror_frac
            i_b = i_a if mirror else rng.choice(
                [i for i in range(len(meta_teams)) if i != i_a])
            team_a, _ = spec_from_team(meta_teams[i_a], rng)
            team_b, _ = spec_from_team(meta_teams[i_b], rng)
            strat_a = strategy_from_team(meta_teams[i_a], rng)
            strat_b = strategy_from_team(meta_teams[i_b], rng)
            item_a = item_from_team(meta_teams[i_a], team_a)
            item_b = item_from_team(meta_teams[i_b], team_b)
            team_ids = (i_a, i_b)
            for idx in (i_a, i_b):
                nm = meta_teams[idx]["name"]
                team_game_counts[nm] = team_game_counts.get(nm, 0) + 1
            tag = f"meta {meta_teams[i_a]['name']} vs {meta_teams[i_b]['name']}"
        else:
            team_a, team_b, item_a, item_b = _random_teams(
                factory, sprite_skills,
                optimal_frac=args.optimal_frac, meta_frac=0.0,
            )
            strat_a = jittered_default_strategy(rng)
            strat_b = jittered_default_strategy(rng)
            n_optimal = sum(1 for t in (team_a, team_b)
                            if t and all(s.get("_mode") == "optimal" for s in t))
            tag = "random " + "|".join(s["name"] for s in team_a)
        plans.append({
            "g": g, "team_a": team_a, "team_b": team_b,
            "item_a": item_a, "item_b": item_b,
            "strat_a": strat_a, "strat_b": strat_b,
            "team_ids": team_ids, "is_meta": is_meta, "tag": tag,
            "n_optimal": n_optimal,
            "max_turns": args.max_turns, "draw_margin": args.draw_margin,
            "hang_dump_sec": args.hang_dump_sec,
            # 每局独立派生的随机种子：引擎掷骰走全局 random，若不显式播种，
            # 同一 seed 在不同进程/不同 worker 数下结果不同（曾导致串并行数据集不一致）
            "rng_seed": _game_rng_seed(args.seed, g),
        })
    return plans


def _game_rng_seed(base_seed: int, game_index: int) -> int:
    """(实验种子, 局号) → 该局的对局随机种子（稳定、可复现）。"""
    return (int(base_seed) * 1_000_003 + int(game_index) * 7919) & 0x7FFFFFFF


def _auto_workers(plans: list[dict]) -> int:
    """自动并行度（--workers 0）。

    规划层专家每次决策要做「候选 ≤7 × 响应 ≤3」个真实 headless 回合，单局 CPU 是
    纯规则的 ~20 倍、内存/cache 压力也大：实测 6~12 worker 都是 ~3.9 局/s（6 个 P 核
    跑满即到顶），19 worker 反而掉到 3.1 局/s（多出的进程落在 E 核上互相挤）。
    纯规则单局便宜，核越多越好（19 worker 59 局/s）。
    """
    cores = os.cpu_count() or 2
    planner = False
    for p in plans:
        for strat in (p.get("strat_a"), p.get("strat_b")):
            if strat is None:
                continue
            candidates = [getattr(strat, "default", None),
                          *getattr(strat, "sprites", {}).values()]
            if any(getattr(s, "plan_depth", 0) > 0 for s in candidates if s is not None):
                planner = True
                break
        if planner:
            break
    if planner:
        return max(2, min(8, cores // 2))
    return max(1, cores - 1)


def _init_worker() -> None:
    """worker 初始化：限制底层线程数 + 预热 SimFactory（避免每局重建）。"""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    _worker_factory()


def _worker_factory():
    """worker 进程内复用同一个 SimFactory（跨局复用，避免重复建池）。"""
    global _WORKER_FACTORY
    if _WORKER_FACTORY is None:
        from backend.sim.factory import SimFactory
        _WORKER_FACTORY = SimFactory()
    return _WORKER_FACTORY


_WORKER_FACTORY = None


def _play_plan(plan: dict) -> tuple:
    """打一局并返回 (g, samples, outcome_a, end_reason, turns)（可在子进程执行）。

    看门狗：单局超过 --hang-dump-sec 秒就把该 worker 的调用栈写到 stderr
    （faulthandler），用于定位「某一局卡在某个回合」——recorder 的墙钟护栏只
    在回合之间检查，单个回合内部死循环时它永远不触发。
    """
    import faulthandler

    from backend.engine.ai.bc_record import run_recorded_battle

    dump_sec = plan.get("hang_dump_sec", 0)
    if dump_sec:
        faulthandler.dump_traceback_later(dump_sec, repeat=True, exit=False)
    try:
        random.seed(plan["rng_seed"])  # 引擎掷骰的确定性来源
        factory = _worker_factory()
        strat_a, strat_b = plan["strat_a"], plan["strat_b"]
        samples, outcome_a, end_reason, turns = run_recorded_battle(
            factory, plan["team_a"], plan["team_b"],
            lambda tag, player: RuleAgentV2(tag, player, strategy=strat_a),
            lambda tag, player: RuleAgentV2(tag, player, strategy=strat_b),
            item_a=plan["item_a"], item_b=plan["item_b"],
            max_turns=plan["max_turns"], draw_margin=plan["draw_margin"],
            game_id=plan["g"],
        )
    finally:
        if dump_sec:
            faulthandler.cancel_dump_traceback_later()
    return plan["g"], samples, outcome_a, end_reason, turns


def _run_pool(args, play_plans: list[dict], collect) -> None:
    """多进程打局：父进程按局号收结果，并对「长时间无进展」告警。

    不用 imap_unordered —— 它的迭代器在收满 _length 个结果前会**无限阻塞**，
    单个 worker 卡在某局时父进程静默假死（实测停在 [2400/2500]、其余 worker
    空闲 0% CPU、只能靠逐 60s 的栈转储才定位到）。改为 apply_async 逐个跟踪：
    卡住时直接把剩余局号与队伍打出来，事故现场一眼可复现。
    """
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    print(f"多进程生成: {args.workers} 个 worker（父进程产队，样本按局号重排）")
    with ctx.Pool(args.workers, initializer=_init_worker) as pool:
        pending = {p["g"]: (p, pool.apply_async(_play_plan, (p,)))
                   for p in play_plans}
        last_progress = time.time()
        while pending:
            finished = [g for g, (_plan, res) in pending.items() if res.ready()]
            if finished:
                for g in finished:
                    _plan, res = pending.pop(g)
                    collect(res.get())  # worker 侧异常在这里原样抛出
                last_progress = time.time()
                continue
            idle = time.time() - last_progress
            if args.stall_warn_sec and idle >= args.stall_warn_sec:
                sample = sorted(pending)[:3]
                detail = "; ".join(
                    f"局号 {g} [{pending[g][0]['tag'][:60]}]" for g in sample)
                _log(f"  !! {idle:.0f}s 无进展：剩余 {len(pending)} 局 —— {detail}"
                     + ("（还有更多）" if len(pending) > len(sample) else ""), args)
                last_progress = time.time()
            time.sleep(0.05)


def _ensure_hash_seed() -> None:
    """强制 PYTHONHASHSEED=0（数据集可复现的前提）。

    引擎里存在依赖 set/dict 迭代顺序的逻辑：hash 随机化会让同一 seed 跑出
    不同对局（表现为偶尔卡死在某局、以及 --workers 前后数据集不一致）。
    这里在进程启动早期用子进程重跑自己，并把 hash seed 传给全部 worker。

    只有「本文件就是被直接执行的脚本」时才重跑：被 import 调用（如测试里
    加载 main()）时 sys.argv[0] 不是本文件，重跑会去执行一个不存在的路径。
    """
    if os.environ.get("PYTHONHASHSEED") == "0" or os.environ.get("ROCO_KEEP_HASH_SEED"):
        return
    me = Path(__file__).resolve()
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is None or not argv0.is_file() or argv0.resolve() != me:
        print("提示：非脚本方式调用，跳过 PYTHONHASHSEED=0 重跑"
              "（数据集不保证可复现）", flush=True)
        return
    import subprocess

    print("以 PYTHONHASHSEED=0 重跑（保证数据集可复现；"
          "设 ROCO_KEEP_HASH_SEED=1 可跳过）", flush=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    proc = subprocess.run([sys.executable, "-u", str(me), *sys.argv[1:]], env=env)
    raise SystemExit(proc.returncode)


def main() -> None:
    _ensure_hash_seed()
    args = parse_args()
    auto_workers = args.workers == 0
    random.seed(args.seed)
    rng = random.Random(args.seed)
    sys.stdout.reconfigure(encoding="utf-8")

    factory = SimFactory()
    sprite_skills = dict(SPRITE_RANDOM_POOL)

    meta_teams = load_meta_teams(args.meta_file)
    if args.meta_frac > 0 and not meta_teams:
        print("!! meta_teams.json 不存在，退化为纯随机阵容生成")
        args.meta_frac = 0.0
    if meta_teams:
        problems = validate_meta_teams(meta_teams, sprite_skills)
        if problems:
            print("!! meta 队伍校验失败：")
            for p in problems[:20]:
                print("   -", p)
            raise SystemExit(1)
        print(f"meta 队伍 {len(meta_teams)} 支: {[t['name'] for t in meta_teams]}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_states: list[dict[str, np.ndarray]] = []
    all_actions: list[int] = []
    all_masks: list[np.ndarray] = []
    all_game_ids: list[int] = []
    all_team_ids: list[int] = []
    all_is_meta: list[int] = []
    all_outcomes: list[float] = []           # 逐样本、按视角取反
    game_signs: dict[int, set[int]] = {}     # 局号 → 该局出现过的标签符号（自检用）

    team_game_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    turn_sum = 0
    t0 = time.time()

    # ── 第一阶段：父进程产队（消耗唯一 RNG 流 → 与 --workers 无关的确定性） ──
    plans = _build_plans(args, meta_teams, sprite_skills, rng, team_game_counts)
    n_rand_plans = sum(1 for p in plans if not p["is_meta"])
    n_opt_teams = sum(p["n_optimal"] for p in plans)
    opt_rate = n_opt_teams / max(1, 2 * n_rand_plans)
    _log(f"随机阵容 {n_rand_plans} 局：最优培养 {n_opt_teams}/{2 * n_rand_plans} 队 = "
         f"{opt_rate:.1%}（目标 --optimal-frac {args.optimal_frac:.2f}）", args)

    # ── 第二阶段：打局（单进程 或 多进程池） ──
    # 结果先按局号收齐再按序写入：imap_unordered 的完成顺序与局号无关，
    # 直接按完成顺序写会让数据集行序随 worker 数变化（内容相同但不可复现）。
    if auto_workers:
        args.workers = _auto_workers(plans)
        _log(f"自动并行度: {args.workers} workers"
             f"（{'规划层 → 6 P 核到顶' if args.workers <= 8 else '纯规则 → 越多越好'}；"
             f"可用 --workers N 覆盖）", args)
    play_plans = [p for p in plans if p["g"] >= args.start_game]
    collected: dict[int, tuple] = {}

    def collect(result) -> None:
        nonlocal turn_sum
        g, samples, outcome_a, end_reason, turns = result
        collected[g] = result
        plan = plans[g]
        turn_sum += turns
        done = len(collected)
        if args.per_game_log:
            _log(f"  [{done}/{len(play_plans)}] {time.time() - t0:7.1f}s turns={turns:3d} "
                 f"{end_reason:14s}  {plan['tag'][:80]}", args)
        elif done % 100 == 0:
            reasons = [r[3] for r in collected.values()]
            n_dec = sum(1 for r in reasons if r.startswith("decisive"))
            n_samples = sum(len(r[1]) for r in collected.values())
            _log(f"  [{done}/{len(play_plans)}] samples={n_samples} "
                 f"decisive={n_dec}/{done} "
                 f"avg_turns={turn_sum / done:.1f} "
                 f"({time.time() - t0:.0f}s)", args)

    if args.workers > 1:
        _run_pool(args, play_plans, collect)
    else:
        for plan in play_plans:
            collect(_play_plan(plan))

    for g in sorted(collected):
        result = collected.get(g)
        if result is None:  # 理论上不会发生（worker 异常会抛出）
            raise RuntimeError(f"第 {g} 局没有结果")
        _, samples, outcome_a, end_reason, _turns = result
        plan = plans[g]
        team_ids, is_meta = plan["team_ids"], plan["is_meta"]
        for state, action_idx, mask, game_id, side in samples:
            all_states.append(state)
            all_actions.append(action_idx)
            all_masks.append(mask)
            all_game_ids.append(game_id)
            all_team_ids.append(team_ids[0] if side == "A" else team_ids[1])
            all_is_meta.append(is_meta)
            # 标签按视角取反：状态是按 side 视角编码的（encode_battle_state
            # perspective），value 头学的是「己方视角胜负」，故 B 方必须取负
            # ——与自博弈 train.py 的 +outcome_a / -outcome_a 同约定。
            value = outcome_a if side == "A" else -outcome_a
            all_outcomes.append(value)
            if outcome_a:
                game_signs.setdefault(game_id, set()).add(1 if value > 0 else -1)
        reason_counts[end_reason] = reason_counts.get(end_reason, 0) + 1

    outcomes = np.asarray(all_outcomes, dtype=np.float32)

    # 视角标签自检：决定性对局里 A/B 双方样本必须异号。全同号说明漏了取反
    # （2026-09-20 实测 2500 局里 2294 局全同号，value 头等于喂了一半反号标签）。
    sign_ok = sum(1 for s in game_signs.values() if len(s) > 1)
    sign_bad = sorted(g for g, s in game_signs.items() if len(s) == 1)
    if sign_bad:
        _log(f"!! 视角标签自检：{len(sign_bad)} 局只出现单侧符号"
             f"（例：{sign_bad[:5]}）——value 标签可能未按视角取反", args)

    stacked: dict[str, np.ndarray] = {}
    for key in all_states[0]:
        arr = np.stack([s[key] for s in all_states])
        if arr.dtype == np.float32:
            arr = arr.astype(np.float16)
        elif key == "ast_tokens":
            arr = arr.astype(np.int16)
        stacked[key] = arr

    savez = {
        **stacked,
        "action": np.asarray(all_actions, dtype=np.int64),
        "mask": np.asarray(all_masks, dtype=np.float32),
        "outcome": outcomes,
        "game_id": np.asarray(all_game_ids, dtype=np.int64),
        "team_id": np.asarray(all_team_ids, dtype=np.int64),
        "is_meta": np.asarray(all_is_meta, dtype=np.int8),
    }
    # float16 已把体积压到一半；zlib 再压 ~30% 却要几十倍时间（单核、数 GB 临时内存）
    # —— 大样本量下 savez_compressed 会成为主要耗时（实测 2500 局 > 10 分钟），
    # 默认写无压缩 npz，需要小体积时用 --compress 或离线再压。
    if args.compress:
        np.savez_compressed(out_path, **savez)
    else:
        np.savez(out_path, **savez)

    sidecar = {
        "games": args.games,
        "meta_frac": args.meta_frac,
        "optimal_frac": args.optimal_frac,
        "optimal_team_rate": opt_rate,
        "seed": args.seed,
        "max_turns": args.max_turns,
        "draw_margin": args.draw_margin,
        "samples": int(len(all_actions)),
        "decisive_rate": sum(v for k, v in reason_counts.items()
                             if k.startswith("decisive")) / max(1, args.games),
        "mean_turns": turn_sum / max(1, args.games),
        "reason_counts": reason_counts,
        "team_game_counts": team_game_counts,
        # 决定性对局中「A/B 视角标签异号」的比例（1.0 = 标签约定正确）
        "perspective_sign_ok_rate": sign_ok / max(1, sign_ok + len(sign_bad)),
        "holdout_hint": "整队留出验证用 team_id（meta 队索引）；留出最后 --holdout-teams 支",
    }
    sidecar_path = out_path.with_suffix(".json")
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")

    n_meta = sum(all_is_meta)
    n_dec = sum(v for k, v in reason_counts.items() if k.startswith("decisive"))
    print(f"\n完成: {out_path}  samples={len(all_actions)} "
          f"(meta {n_meta} / random {len(all_actions) - n_meta})")
    print(f"decisive={n_dec}/{args.games}  avg_turns={turn_sum / max(1, args.games):.1f}  "
          f"耗时 {time.time() - t0:.0f}s")
    print(f"视角标签自检: {sign_ok}/{sign_ok + len(sign_bad)} 局异号 "
          f"({sign_ok / max(1, sign_ok + len(sign_bad)):.1%})")
    print("end_reasons:", dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])))
    print(f"sidecar: {sidecar_path}")


if __name__ == "__main__":
    main()
