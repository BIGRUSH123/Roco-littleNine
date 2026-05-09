#!/usr/bin/env python3
"""
scripts/calc/damage.py — 伤害计算器

公式：round((37/41) × 技能威力 × 攻击 / 防御)
攻击/防御由技能类型决定：物攻→物攻/物防，魔攻→魔攻/魔防。

用法：
  python scripts/calc/damage.py <攻击方> <技能> <防御方>
      [--form <形态>] [--nature <性格>] [--iv-* ...]
      [--atk-mod "物攻+20%" "速度-10%"]
      [--def-mod "物防+30%" "魔防-10%"]
      [--ability <特性>] [--json]
"""

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

from scripts.common import STAT_KEYS, STAT_LABELS, apply_mods, StatsCalc
from scripts.common.sprite_db import SpriteDB
from scripts.calc.state import SkillDB

# 技能类型 → (攻击键, 防御键)
TYPE_ATK_DEF: dict[str, tuple[str, str]] = {
    '物攻': ('atk', 'def'),
    '魔攻': ('sp_atk', 'sp_def'),
}


def calc_damage(power: int, atk: int, def_: int) -> int:
    """计算伤害。"""
    if power == 0 or def_ == 0:
        return 0
    return round((37 / 41) * power * atk / def_)


def format_output(
    skill_name: str, power: int, skill_type: str,
    atk_name: str, def_name: str,
    atk_stats: dict[str, int], def_stats: dict[str, int],
    atk_key: str, def_key: str,
    atk_mods: list[str], def_mods: list[str],
    damage: int,
) -> str:
    lines: list[str] = []

    atk_label = STAT_LABELS[atk_key]
    def_label = STAT_LABELS[def_key]
    atk_val = atk_stats[atk_key]
    def_val = def_stats[def_key]

    lines.append(f'# {atk_name} 使用 {skill_name} → {def_name}')
    lines.append('')
    lines.append(f'| 方 | 精灵 | {atk_label} | {def_label} | 威力 | 类型 |')
    lines.append('|------|------|------|------|------|------|')
    lines.append(f'| 攻 | {atk_name} | {atk_val} | — | {power} | {skill_type} |')
    lines.append(f'| 防 | {def_name} | — | {def_val} | | |')
    lines.append('')
    lines.append(f'伤害 = round((37/41) × {power} × {atk_val} ÷ {def_val}) = **{damage}**')

    if atk_mods:
        lines.append('')
        lines.append(f'攻击方修正：{"、".join(atk_mods)}')
    if def_mods:
        lines.append(f'防御方修正：{"、".join(def_mods)}')

    return '\n'.join(lines)


def parse_iv_args(args: list[str]) -> dict[str, int]:
    """解析 --iv-* 参数和 --nature 参数。"""
    iv = {k: 0 for k in STAT_KEYS}
    nature: Optional[str] = None
    form: str = ''
    ability: str = ''
    atk_mods: list[str] = []
    def_mods: list[str] = []

    iv_alias = {
        '--iv-hp': 'hp', '--iv-atk': 'atk', '--iv-sp-atk': 'sp_atk',
        '--iv-def': 'def', '--iv-sp-def': 'sp_def', '--iv-speed': 'speed',
    }

    i = 0
    while i < len(args):
        a = args[i]
        if a == '--form' and i + 1 < len(args):
            form = args[i + 1]; i += 2
        elif a == '--nature' and i + 1 < len(args):
            nature = args[i + 1]; i += 2
        elif a == '--ability' and i + 1 < len(args):
            ability = args[i + 1]; i += 2
        elif a in iv_alias and i + 1 < len(args):
            try:
                iv[iv_alias[a]] = int(args[i + 1])
            except ValueError:
                print(f'[错误] {a} 需要整数', file=sys.stderr)
                sys.exit(1)
            i += 2
        elif a == '--iv-all' and i + 1 < len(args):
            try:
                v = int(args[i + 1])
                for k in STAT_KEYS:
                    iv[k] = v
            except ValueError:
                print('[错误] --iv-all 需要整数', file=sys.stderr)
                sys.exit(1)
            i += 2
        elif a == '--atk-mod' and i + 1 < len(args):
            i += 1
            while i < len(args) and not args[i].startswith('--'):
                if re.match(r'^\d+$', args[i]):
                    break
                atk_mods.append(args[i])
                i += 1
        elif a == '--def-mod' and i + 1 < len(args):
            i += 1
            while i < len(args) and not args[i].startswith('--'):
                if re.match(r'^\d+$', args[i]):
                    break
                def_mods.append(args[i])
                i += 1
        else:
            i += 1

    return {'iv': iv, 'nature': nature, 'form': form, 'ability': ability,
            'atk_mods': atk_mods, 'def_mods': def_mods}


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    if len(args) < 3:
        print('[错误] 需要三个位置参数：攻击方 技能 防御方', file=sys.stderr)
        print('用法: python scripts/calc/damage.py <攻击方> <技能> <防御方> [选项]', file=sys.stderr)
        sys.exit(1)

    atk_name = args[0]
    skill_name = args[1]
    def_name = args[2]
    opts = parse_iv_args(args[3:])

    json_out = '--json' in args

    # 初始化数据库
    wiki_root = BASE / 'wiki'
    sprite_db = SpriteDB(wiki_root)
    skill_db = SkillDB(wiki_root)

    # 查找精灵
    atk_species = sprite_db.get(atk_name, opts['form'])
    if not atk_species:
        forms = sprite_db.list_forms(atk_name)
        if forms:
            print(f'[错误] 精灵 {atk_name!r} 有多种形态，请用 --form 指定：{forms}', file=sys.stderr)
        else:
            print(f'[错误] 找不到精灵：{atk_name!r}', file=sys.stderr)
        sys.exit(1)

    def_species = sprite_db.get(def_name)
    if not def_species:
        forms = sprite_db.list_forms(def_name)
        if forms:
            print(f'[错误] 精灵 {def_name!r} 有多种形态，请用 --form 指定：{forms}', file=sys.stderr)
        else:
            print(f'[错误] 找不到精灵：{def_name!r}', file=sys.stderr)
        sys.exit(1)

    # 查找技能
    skill = skill_db.get(skill_name)
    if not skill:
        print(f'[错误] 找不到技能：{skill_name!r}', file=sys.stderr)
        sys.exit(1)

    # 映射攻击/防御键
    mapping = TYPE_ATK_DEF.get(skill.skill_type)
    if not mapping:
        print(f'[提示] 技能类型为「{skill.skill_type}」(power={skill.power})，无直接伤害。', file=sys.stderr)
        if skill.power == 0:
            print('伤害 = 0（非攻击技能）')
            sys.exit(0)
        mapping = ('atk', 'def')  # fallback
    atk_key, def_key = mapping

    # 计算双精灵最终属性
    calc = StatsCalc()
    atk_result = calc.compute(
        atk_species, nature=opts['nature'], iv=opts['iv'],
        ability=opts['ability'],
    )
    def_result = calc.compute(
        def_species, iv={k: 0 for k in STAT_KEYS},
    )

    # 应用能力修正
    atk_final = apply_mods(atk_result.final_stats, opts['atk_mods'])
    def_final = apply_mods(def_result.final_stats, opts['def_mods'])

    # 计算伤害
    damage = calc_damage(skill.power, atk_final[atk_key], def_final[def_key])

    if json_out:
        print(json.dumps({
            'attacker': atk_name,
            'skill': skill_name,
            'defender': def_name,
            'skill_type': skill.skill_type,
            'power': skill.power,
            'atk_stat': atk_key,
            'def_stat': def_key,
            'atk_value': atk_final[atk_key],
            'def_value': def_final[def_key],
            'atk_mods': opts['atk_mods'],
            'def_mods': opts['def_mods'],
            'damage': damage,
        }, ensure_ascii=False, indent=2))
    else:
        print(format_output(
            skill_name, skill.power, skill.skill_type,
            atk_species.display_name(), def_species.display_name(),
            atk_final, def_final, atk_key, def_key,
            opts['atk_mods'], opts['def_mods'],
            damage,
        ))


if __name__ == '__main__':
    main()
