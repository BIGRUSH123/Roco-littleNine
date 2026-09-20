# -*- coding: utf-8 -*-
"""native/tools/summarize_selfplay_turns.py — 自博弈对局回合数与终局原因统计。

训练汇总只打「终局原因」计数，不打平均回合数。但 `max_turns_*` 占比高意味着
价值标签大量来自「打满回合的局面分」而不是真实击杀——价值信号被稀释、
policy 目标也偏向僵持动作，是判断样本可信度的第一道指标。

用法:
  python native/tools/summarize_selfplay_turns.py backend/engine/ai/log/exp23_bc/battles_run_*.jsonl

每行是一局：{"teams":..., "rounds":[...], "winner":..., "end_reason":..., "turns":N}
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path


def load_games(path: Path) -> list[dict]:
    """读一个 battles jsonl；容忍运行中被截断的最后一行。"""
    games: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # 写入中的半行
            if "turns" in rec:
                games.append(rec)
    return games


def pct(n: int, total: int) -> str:
    return f"{n / total:.1%}" if total else "-"


def report(tag: str, games: list[dict]) -> None:
    if not games:
        print(f"{tag}: 无对局记录")
        return
    turns = sorted(g["turns"] for g in games)
    n = len(turns)
    reasons = Counter(g.get("end_reason", "?") for g in games)
    cappe = sum(v for k, v in reasons.items() if k.startswith("max_turns"))
    draws = reasons.get("max_turns_draw", 0)
    decisive = sum(v for k, v in reasons.items() if k.startswith("decisive"))
    mean = sum(turns) / n
    print(f"\n=== {tag} ===")
    print(f"  对局 {n}  回合 均值 {mean:.1f} / 中位 {turns[n // 2]} / p90 "
          f"{turns[int(n * 0.9)] if n > 1 else turns[0]} / 最大 {turns[-1]}")
    print(f"  正常击杀 {decisive} ({pct(decisive, n)})   "
          f"打满回合 {cappe} ({pct(cappe, n)})   平局 {draws} ({pct(draws, n)})")
    print("  终局原因: " + "  ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    if cappe / n > 0.30:
        print(f"  !! 打满回合占比 {pct(cappe, n)} > 30%：价值标签多为局面分裁决，"
              f"考虑提高 --max-turns 或检查僵持动作")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="+", help="battles jsonl 路径或通配符")
    ap.add_argument("--per-file", action="store_true", help="每个文件单独出一份")
    args = ap.parse_args()

    files: list[Path] = []
    for pat in args.patterns:
        files.extend(Path(p) for p in sorted(glob.glob(pat)))
    if not files:
        print("没有匹配到日志文件")
        return 1

    all_games: list[dict] = []
    for path in files:
        games = load_games(path)
        all_games.extend(games)
        if args.per_file and games:
            report(str(path), games)
    report(f"合计 {len(files)} 个文件", all_games)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
