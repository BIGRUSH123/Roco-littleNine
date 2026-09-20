# -*- coding: utf-8 -*-
"""native/tools/find_hang_block.py — 分块扫描定位卡死对局。

按 100 局一块单进程重跑同一 seed，给每块设墙钟超时；哪一块超时就说明
卡死对局落在该块内，并把该块内**逐局**重跑一次（带 faulthandler 栈转储）
以拿到卡住时的调用栈。

用法（pwsh）:
  python native/tools/find_hang_block.py [--seed 2026] [--games 2500]
                                         [--block 100] [--timeout 90]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

GEN = _ROOT / "native" / "tools" / "gen_bc_data.py"


def run_block(seed: int, start: int, end: int, timeout: float, dump_sec: int,
              log: Path, err: Path) -> tuple[str, float]:
    """跑 [start, end) 区间；返回 (状态, 耗时秒)。状态 ∈ ok/timeout/error。"""
    cmd = [
        sys.executable, "-u", str(GEN),
        "--games", str(end), "--start-game", str(start),
        "--meta-frac", "0.6", "--seed", str(seed),
        "--out", str(_ROOT / "checkpoints" / "bc_block.npz"),
        "--per-game-log", "--log-file", str(log),
    ]
    if dump_sec:
        cmd += ["--hang-dump-sec", str(dump_sec)]
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8") as lf, open(err, "w", encoding="utf-8") as ef:
        try:
            proc = subprocess.run(cmd, stdout=lf, stderr=ef, timeout=timeout,
                                  cwd=str(_ROOT))
        except subprocess.TimeoutExpired:
            return "timeout", time.perf_counter() - t0
    return ("ok" if proc.returncode == 0 else f"error({proc.returncode})",
            time.perf_counter() - t0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--games", type=int, default=2500)
    ap.add_argument("--block", type=int, default=100)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--dump-sec", type=int, default=20,
                    help="块内逐局重跑时的栈转储阈值")
    args = ap.parse_args()

    bad: list[tuple[int, int, str, float]] = []
    for start in range(0, args.games, args.block):
        end = min(start + args.block, args.games)
        status, dt = run_block(args.seed, start, end, args.timeout, args.dump_sec,
                              _ROOT / "_block.log", _ROOT / "_block_err.txt")
        print(f"  块 [{start:4d},{end:4d}) {status:12s} {dt:6.1f}s", flush=True)
        if status != "ok":
            bad.append((start, end, status, dt))
            print(f"    → 卡点在该块内；stderr 尾部：")
            print("      " + (_ROOT / "_block_err.txt").read_text(
                encoding="utf-8", errors="replace")[-800:].replace("\n", "\n      "))

    print()
    if not bad:
        print("所有块均通过：卡死不在单局内容里（更像多进程/序列化层问题）")
        return 0
    print(f"异常块 {len(bad)} 个：")
    for start, end, status, dt in bad:
        log = (_ROOT / "_block.log").read_text(encoding="utf-8", errors="replace")
        lines = log.strip().splitlines()
        last = lines[-1] if lines else "（无进度）"
        print(f"  [{start},{end}) {status} {dt:.1f}s；该块最后一条进度: {last.strip()}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
