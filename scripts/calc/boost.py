#!/usr/bin/env python3
"""
scripts/calc/boost.py — 属性增幅/降幅计算器

以 10% 为基础单位，对六维分别增幅或降低，支持叠加。
支持百分比（"物攻+20%"）和固定值（"速度+120"）。

用法1（管道，接 calc_stats）：
  python scripts/calc/stats.py <精灵> [选项] --json | python scripts/calc/boost.py --mod "物攻+20%" "速度-10%"

用法2（直接输入六个值）：
  python scripts/calc/boost.py 400 250 150 180 160 220 --mod "物攻+20%" "速度-10%"

用法3（交互输入）：
  python scripts/calc/boost.py --mod "双攻+30%" "双防-20%"
  然后输入六维（空格分隔）。

mod 格式：属性 + 符号 + 数值 + 可选%，如 "物攻+20%" "速度+120" "双攻-50%"。
支持复合属性：双攻（物攻+魔攻）、双防（物防+魔防）。
"""

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.common import STAT_KEYS, STAT_LABELS, apply_mods

ORDER = ('hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed')


def format_table(original: dict[str, int], modified: dict[str, int]) -> str:
    lines: list[str] = []
    lines.append('| 属性 | 原始 | 修正 | 变化 |')
    lines.append('|------|------|------|------|')
    for k in ORDER:
        orig = original[k]
        mod  = modified[k]
        diff = mod - orig
        sign = '+' if diff >= 0 else ''
        lines.append(f'| {STAT_LABELS[k]} | {orig} | {mod} | {sign}{diff} |')
    return '\n'.join(lines)


def parse_stats_from_args(args: list[str]) -> dict[str, int] | None:
    """尝试从命令行参数解析六个数值。返回 None 表示参数不足。"""
    nums = []
    for a in args:
        try:
            nums.append(int(a))
        except ValueError:
            break
    if len(nums) == 6:
        return dict(zip(ORDER, nums))
    return None


def read_stats_from_stdin() -> dict[str, int] | None:
    """尝试从 stdin 读取 calc_stats.py --json 输出。"""
    if sys.stdin.isatty():
        return None
    try:
        data = json.load(sys.stdin)
        fs = data.get('final_stats', {})
        if all(k in fs for k in ORDER):
            return {k: fs[k] for k in ORDER}
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def prompt_stats() -> dict[str, int]:
    """交互输入六维。"""
    print('请输入六维属性（空格分隔，顺序：生命 物攻 魔攻 物防 魔防 速度）：', file=sys.stderr)
    line = sys.stdin.readline()
    parts = line.strip().split()
    if len(parts) != 6:
        print('[错误] 需要 6 个数值', file=sys.stderr)
        sys.exit(1)
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        print('[错误] 请输入整数', file=sys.stderr)
        sys.exit(1)
    return dict(zip(ORDER, nums))


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    # 分离 --mod 和位置参数
    mods: list[str] = []
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == '--mod':
            i += 1
            while i < len(args) and not args[i].startswith('--'):
                # 遇到纯数字则停止收集（这是位置参数，不是 mod）
                if re.match(r'^\d+$', args[i]):
                    break
                mods.append(args[i])
                i += 1
        elif not args[i].startswith('--'):
            positional.append(args[i])
            i += 1
        else:
            i += 1

    if not mods:
        print('[错误] 请用 --mod 指定增幅/降幅，如 --mod "物攻+20%" 或 --mod "速度+120"', file=sys.stderr)
        sys.exit(1)

    # 获取原始六维
    stats = parse_stats_from_args(positional)
    if stats is None:
        stats = read_stats_from_stdin()
    if stats is None:
        stats = prompt_stats()

    modified = apply_mods(stats, mods)

    # 输出
    print()
    print(format_table(stats, modified))
    print()

    # 列出应用的修正
    if mods:
        print(f'修正：{"、".join(mods)}')

    # JSON 输出（方便管道）
    if '--json' in args:
        deltas = {k: modified[k] - stats[k] for k in ORDER}
        print(json.dumps({
            'original': stats,
            'modified': modified,
            'mods': mods,
            'deltas': deltas,
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
