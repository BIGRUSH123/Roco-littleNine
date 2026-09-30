"""probe_eval_tail.py — 定位"评估卡在最后 1-2 局"的原因。

现象（2026-09-24 多次观测）：`evaluate_parallel` 的进度会长时间停在 N-1/N
（我见过的：198/200 停 5 分钟、199/200 停 10 分钟），然后才跳到 N。怀疑不是死锁，
而是：

  A. **固定阵容套件里有天然超长的对局**：`_eval_roster_rng()` 用固定种子生成同一批
     阵容、逐局用局号播种 → 每次测评都是同 100 对 matchup；其中某一对可能是"双方都
     磨"的组合，在 `--eval-max-turns 150` 下打满 150 回合，而每回合双方各做一次
     MCTS（100~400 sims）→ 单局 5~15 分钟。
  B. **静态任务表 + 无重平衡**：worker 领完就没了，剩最后一局在跑时其余 worker 空转，
     整体耗时 = 最慢那一局 → 观感上"卡住"。

本脚本用**单进程**逐局计时（同样的套件、同样的 `_play_one_eval_game`），把每局的
耗时、回合数、双方阵容打出来 → 直接判断 A 是否成立（哪几局慢、慢到多少、是不是打满）。

用法（远端）:
  python -X utf8 native/tools/probe_eval_tail.py --ckpt new_10000.pt --games 200 --sims 100 \
      --indices 0,1,2,98,99,196,197,198,199
  python -X utf8 native/tools/probe_eval_tail.py --ckpt new_10000.pt --games 200 --sims 200 \
      --indices 168-199        # 尾段（长尾嫌疑最大的就是最后领到任务的这些局）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="new_10000.pt")
    ap.add_argument("--games", type=int, default=200, help="套件规模（决定任务表/种子）")
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--best-sims", type=int, default=0,
                    help="对方（best）侧的 sims；0=与 --sims 相同。"
                         "复刻 sims 扫描臂要传 1（candidate=N vs policy）")
    ap.add_argument("--eval-max-turns", type=int, default=150)
    ap.add_argument("--indices", default="", help="只测这些局号（如 0,1,168-199）；空=全部")
    ap.add_argument("--budget-s", type=float, default=900.0, help="单局 wall-clock 上限")
    return ap.parse_args()


def install_decision_timer() -> type:
    """给 MCTSAgent.choose_action 套一层计时（不改远端代码）。

    `_play_one_eval_game` 在函数体里按模块全局名构造 MCTSAgent，所以替换
    `train.MCTSAgent` 就能生效。计时按**每次决策**（一回合两次：双方各一次），
    用来判断「同一局里是不是越打越慢」——若逐次决策耗时随局面推进单调上升，
    就是状态在局内累积（每仿真一次拷贝的状态越来越大）。
    """
    from backend.engine.ai import train as train_module

    original = train_module.MCTSAgent

    class _TimedMCTS(original):  # type: ignore[misc, valid-type]
        TIMES: list[float] = []

        def choose_action(self, battle):
            started = time.monotonic()
            try:
                return super().choose_action(battle)
            finally:
                _TimedMCTS.TIMES.append(time.monotonic() - started)

    train_module.MCTSAgent = _TimedMCTS
    return _TimedMCTS


def parse_indices(spec: str) -> set[int] | None:
    """'0,1,168-199' -> {0,1,168,...,199}；空串 -> None（全部）。"""
    if not spec.strip():
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return out


def bucket_profile(turn_s: list[float], buckets: int = 5) -> str:
    """把逐回合耗时压成 buckets 段均值，用来看「局内是否越打越慢」。"""
    if len(turn_s) < buckets * 2:
        return ""
    size = len(turn_s) / buckets
    means = []
    for i in range(buckets):
        lo, hi = int(i * size), int((i + 1) * size) if i < buckets - 1 else len(turn_s)
        chunk = turn_s[lo:hi] or [0.0]
        means.append(sum(chunk) / len(chunk))
    return " → ".join(f"{m:.2f}s" for m in means)


def main() -> None:
    args = parse_args()
    import torch

    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import (
        _eval_roster_rng,
        _load_sprite_skills,
        _paired_eval_tasks,
        _play_one_eval_game,
        _seed_eval_game,
    )
    from backend.sim.factory import SimFactory

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet.load(args.ckpt, device=dev)
    ev = TorchEvaluator(model, dev)
    factory = SimFactory()
    skills = _load_sprite_skills()

    tasks = _paired_eval_tasks(factory, skills, args.games, rng=_eval_roster_rng())
    wanted = parse_indices(args.indices)
    print(f"套件 {args.games} 局（{len(tasks)} 个任务）｜sims={args.sims}｜max_turns={args.eval_max_turns}"
          f"｜单局上限 {args.budget_s:.0f}s｜只测 {len(wanted) if wanted else '全部'} 局")

    timed = install_decision_timer()

    total = 0.0
    rows = []
    for g, matchup in tasks:
        if wanted is not None and g not in wanted:
            continue
        _seed_eval_game(g)
        stats: dict = {}
        timed.TIMES.clear()
        t0 = time.time()
        score = _play_one_eval_game(
            factory, skills, ev, ev, g, args.sims, args.eval_max_turns, 0.15,
            args.budget_s, 128, matchup=matchup,
            candidate_leaf_weight=1.0, best_leaf_weight=1.0,
            candidate_sims=args.sims,
            best_sims=args.best_sims or args.sims,
            stats_out=stats,
        )
        dt = time.time() - t0
        total += dt
        # EvalMatchup = (team_a, team_b, item_a, item_b)，队伍是 spec dict 列表
        names = (" / ".join(x.get("name", "?") for x in matchup[0])[:38] + "  ⚔  "
                 + " / ".join(x.get("name", "?") for x in matchup[1])[:38])
        rows.append((g, dt, score, names, stats))
        print(f"  局 {g:>4d}  {dt:6.1f}s  回合 {stats.get('turns', -1):>3d}"
              f"  {stats.get('end', '?'):<8s} 得分 {score:.2f}  {names[:60]}")
        profile = bucket_profile(list(timed.TIMES))
        if profile:
            print(f"        单次决策耗时（5 档均值）: {profile}")

    if rows:
        dts = sorted(r[1] for r in rows)
        turns = sorted(r[4].get("turns", 0) for r in rows)
        capped = sum(1 for r in rows if r[4].get("end") == "max_turns")
        wall = sum(1 for r in rows if r[4].get("end") == "wall")
        print(f"\n合计 {total:.1f}s｜中位 {dts[len(dts)//2]:.1f}s｜最慢 {dts[-1]:.1f}s"
              f"｜>60s 的局 {sum(1 for d in dts if d > 60)}/{len(dts)}"
              f"｜>300s 的局 {sum(1 for d in dts if d > 300)}/{len(dts)}")
        print(f"回合数：中位 {turns[len(turns)//2]}｜最大 {turns[-1]}"
              f"｜打满 {capped}/{len(rows)}｜撞 wall 预算 {wall}/{len(rows)}"
              f"｜分布 {turns}")


if __name__ == "__main__":
    main()
