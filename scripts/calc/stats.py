#!/usr/bin/env python3
"""
scripts/calc/stats.py — 精灵六维属性计算器 CLI

根据精灵种类、性格、个体值、能力修正、特性，输出最终六项属性。

用法（CLI）：
  python scripts/calc/stats.py <精灵名> [--form <形态>] [--nature <性格>]
       [--iv-hp N --iv-atk N --iv-sp-atk N --iv-def N --iv-sp-def N --iv-speed N]
       [--mod "物攻+100%" "速度-30" ...]
       [--ability <特性名>] [--json]

数据来源：
  1. wiki/精灵图鉴/**/*.md 的 frontmatter（hp/atk/sp_atk/def/sp_def/speed）
  2. wiki/meta/sprites.csv（CSV 字段名 spd→speed）

退出码：
  0  正常
  1  精灵未找到 / 性格未识别 / 参数错误
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.common import (
    STAT_KEYS, STAT_LABELS, LABEL_TO_KEY,
    get_nature_coeff,
    half_round, apply_mods, StatsCalc,
    SpeciesStats, StatsResult,
)
from scripts.common.sprite_db import SpriteDB


# ═══════════════════════════════════════════════
# 4. 输出格式
# ═══════════════════════════════════════════════

def format_table(r: StatsResult) -> str:
    show_mods = bool(r.mods)
    cols = ['属性', '种族', '基础(IV后)', '性格后']
    if show_mods:
        cols.append('能力修正后')

    lines: list[str] = []
    name = r.species.display_name()
    lines.append(f"# {name}")
    if r.species.attributes:
        lines.append(f"- 属性：{r.species.attributes}")
    if r.species.ability:
        lines.append(f"- 特性：{r.species.ability}")
    if r.ability and r.ability != r.species.ability:
        lines.append(f"- 输入特性：{r.ability}")
    lines.append(f"- 性格：{r.nature or '（无）'}")
    iv_str = '/'.join(f"{STAT_LABELS[k]}{r.iv[k]}" for k in STAT_KEYS)
    lines.append(f"- 个体值：{iv_str}")
    if r.mods:
        lines.append(f"- 能力修正：{'、'.join(r.mods)}")
    lines.append('')

    lines.append('| ' + ' | '.join(cols) + ' |')
    lines.append('|' + '|'.join(['------'] * len(cols)) + '|')
    nat_coeff = get_nature_coeff(r.nature)
    for k in STAT_KEYS:
        marker = ''
        if nat_coeff[k] > 1.0:
            marker = '↑'
        elif nat_coeff[k] < 1.0:
            marker = '↓'
        row = [
            f"{STAT_LABELS[k]}{marker}",
            str(r.base_stats[k]),
            str(r.raw_stats[k]),
            str(r.nature_stats[k]),
        ]
        if show_mods:
            row.append(str(r.final_stats[k]))
        lines.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(lines)


def to_json(r: StatsResult) -> str:
    return json.dumps({
        'species': {
            'name': r.species.name, 'form': r.species.form,
            'attributes': r.species.attributes, 'ability': r.species.ability,
        },
        'nature': r.nature,
        'iv': r.iv,
        'mods': r.mods,
        'base_stats':    r.base_stats,
        'raw_stats':     r.raw_stats,
        'nature_stats':  r.nature_stats,
        'final_stats':   r.final_stats,
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════
# 5. CLI
# ═══════════════════════════════════════════════

def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_args(argv: list[str]) -> dict:
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    name = argv[0]
    args = argv[1:]
    out: dict = {
        'name': name, 'form': '',
        'nature': None,
        'iv': {k: 0 for k in STAT_KEYS},
        'mods': [],
        'ability': '',
        'json': False,
    }
    i = 0
    iv_alias = {
        '--iv-hp': 'hp', '--iv-atk': 'atk', '--iv-sp-atk': 'sp_atk',
        '--iv-def': 'def', '--iv-sp-def': 'sp_def', '--iv-speed': 'speed',
    }
    while i < len(args):
        a = args[i]
        if a == '--form' and i + 1 < len(args):
            out['form'] = args[i + 1]; i += 2
        elif a == '--nature' and i + 1 < len(args):
            out['nature'] = args[i + 1]; i += 2
        elif a == '--ability' and i + 1 < len(args):
            out['ability'] = args[i + 1]; i += 2
        elif a in iv_alias and i + 1 < len(args):
            try:
                out['iv'][iv_alias[a]] = int(args[i + 1])
            except ValueError:
                _err(f"[错误] {a} 需要整数")
                sys.exit(1)
            i += 2
        elif a == '--iv-all' and i + 1 < len(args):
            try:
                v = int(args[i + 1])
                for k in STAT_KEYS:
                    out['iv'][k] = v
            except ValueError:
                _err("[错误] --iv-all 需要整数")
                sys.exit(1)
            i += 2
        elif a == '--mod' and i + 1 < len(args):
            out['mods'].append(args[i + 1]); i += 2
        elif a == '--json':
            out['json'] = True; i += 1
        else:
            _err(f"[警告] 未识别参数：{a}")
            i += 1
    return out


def main() -> None:
    opts = parse_args(sys.argv[1:])
    db = SpriteDB(BASE)
    species = db.get(opts['name'], opts['form'])
    if not species:
        forms = db.list_forms(opts['name'])
        if forms:
            _err(f"[错误] 精灵 {opts['name']!r} 有多种形态，请用 --form 指定其一：{forms}")
        else:
            _err(f"[错误] 找不到精灵：{opts['name']!r}")
        sys.exit(1)

    calc = StatsCalc()
    try:
        result = calc.compute(
            species=species,
            nature=opts['nature'],
            iv=opts['iv'],
            mods=opts['mods'],
            ability=opts['ability'],
        )
    except ValueError as e:
        _err(f"[错误] {e}")
        sys.exit(1)

    print(to_json(result) if opts['json'] else format_table(result))


if __name__ == '__main__':
    main()
