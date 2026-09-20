"""generate_fixtures — 生成金标准对局录制。

用法（在项目根，项目解释器，PYTHONHASHSEED=0）：
    $env:PYTHONHASHSEED='0'
    env\\python.exe -m backend.engine.differential.generate_fixtures --seeds 1-16
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .recorder import run_recorded


def parse_seeds(spec: str) -> list[int]:
    """解析 '1-16,23,32' 形式的种子列表。"""
    seeds: list[int] = []
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            lo, hi = part.split('-', 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        elif part:
            seeds.append(int(part))
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description='生成金标准对局 fixtures')
    parser.add_argument('--seeds', default='1-16', help='种子列表，如 1-16 或 1,2,5-8')
    parser.add_argument('--out', default='backend/engine/differential/fixtures',
                        help='输出目录')
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for seed in parse_seeds(args.seeds):
        t0 = time.perf_counter()
        data = run_recorded(seed)
        dt = time.perf_counter() - t0
        path = out_dir / f'battle_{seed:04d}.json'
        path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')),
            encoding='utf-8',
        )
        print(f'seed={seed:<4d} winner={data["winner"] or "draw":<4s} '
              f'turns={data["turns_count"]:<3d} {dt:.2f}s -> {path}')


if __name__ == '__main__':
    main()
