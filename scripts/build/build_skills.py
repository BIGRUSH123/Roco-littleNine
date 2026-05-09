#!/usr/bin/env python3
"""scripts/build_skills.py — 一次性预处理：wiki/CSV → data/skills/*.json

用法:
  python scripts/build_skills.py            # 生成全部技能 JSON
  python scripts/build_skills.py --limit 10 # 仅生成前 N 个（调试用）
"""

import csv
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# ── 正则：复用现有解析逻辑 ──

_STAT_MOD_RE = re.compile(
    r'(双攻|双防|物攻|魔攻|物防|魔防|速度|威力|先手|能耗)\s*([+\-])\s*(\d+)\s*(%?)'
)

# 复杂描述中的特殊效果
_POWER_MULT_RE = re.compile(r'威力变为\s*(\d+(?:\.\d+)?)\s*倍')
_MULTI_HIT_RE = re.compile(r'变为\s*(\d+)\s*连击')

_STAT_LABEL_MAP: dict[str, list[str]] = {
    '物攻': ['atk'], '魔攻': ['sp_atk'],
    '物防': ['def'], '魔防': ['sp_def'],
    '速度': ['speed'],
    '双攻': ['atk', 'sp_atk'],
    '双防': ['def', 'sp_def'],
    '威力': ['power'],
    '先手': ['priority'],
    '能耗': ['energy_cost'],
}

# 每步对应的实际数值
_STEP_UNITS: dict[str, int] = {
    'power': 10, 'priority': 1, 'energy_cost': 1,
}
_SPEED_STEP = 10
_STEP_PCT = 10

# 异常状态关键词 → (name, scope)
_ABNORMAL_MAP: dict[str, tuple[str, str]] = {
    '中毒': ('中毒', 'battlefield'),
    '灼烧': ('灼烧', 'battlefield'),
    '冻结': ('冻结', 'persistent'),
    '冰冻': ('冻结', 'persistent'),
    '寄生': ('寄生', 'battlefield'),
    '萌化': ('萌化', 'persistent'),
    '眩晕': ('眩晕', 'battlefield'),
    '晕眩': ('眩晕', 'battlefield'),
}

# 印记名集合（从 common.constants）
_MARK_NAMES: frozenset[str] = frozenset({
    '星陨印记', '光合印记', '降临印记', '润泽印记', '蓄势印记', '蓄电印记',
    '龙式印记', '中毒印记', '减速印记', '棘刺', '迟缓', '风起', '攻击印记',
    '减速',  # 减速印记的简写
})

# 类型归一化
_SKILL_TYPE_NORMALIZE: dict[str, str] = {
    '攻击': '动态攻击',
}


def _parse_frontmatter(text: str) -> dict | None:
    """解析 YAML frontmatter，返回字段 dict。"""
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 3)
    if end == -1:
        return None
    fm_raw = text[4:end]
    data: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            data[k.strip()] = v.strip()
    return data


def _parse_yaml_list(value: str) -> list[str]:
    """解析 frontmatter 中的内联 YAML 列表。"""
    value = value.strip()
    if not value.startswith('[') or not value.endswith(']'):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    items = []
    for part in inner.split(','):
        item = part.strip().strip('"').strip("'")
        if item:
            items.append(item)
    return items


def _is_mark_effect(effect_name: str) -> bool:
    """判断效果名是否为印记。"""
    # "星陨印记×2" → 提取 "星陨印记"
    m = re.match(r'^(.+?)×\d+$', effect_name)
    name = m.group(1) if m else effect_name
    return name in _MARK_NAMES


def _parse_effect_string(
    eff: str, default_target: str, from_opp_buffs: bool = False,
) -> list[dict]:
    """将单个效果字符串解析为效果 dict 列表。"""
    results: list[dict] = []

    # 1. 检测印记
    m_mark = re.match(r'^(.+?)(?:×(\d+))?$', eff)
    if m_mark:
        mark_name = m_mark.group(1)
        stacks = int(m_mark.group(2)) if m_mark.group(2) else 1
        if mark_name in _MARK_NAMES:
            target = 'own_team' if default_target == 'self' else 'opp_team'
            # 有些印记对自己是 positive，对敌人是 negative
            results.append({
                'kind': 'mark', 'target': target,
                'name': mark_name, 'stacks': stacks,
            })
            return results
        # "减速" (简写) — 视为印记
        if mark_name == '减速':
            results.append({
                'kind': 'mark', 'target': 'opp_team',
                'name': '减速', 'stacks': 1,
            })
            return results

    # 2. 检测异常状态
    for keyword, (ab_name, scope) in _ABNORMAL_MAP.items():
        if keyword in eff:
            # "冻结×3" 提取层数
            m_stack = re.search(rf'{keyword}×(\d+)', eff)
            stacks = int(m_stack.group(1)) if m_stack else 1
            results.append({
                'kind': 'abnormal', 'target': default_target,
                'name': ab_name, 'scope': scope, 'stacks': stacks,
            })
            return results

    # 3. 检测属性变化
    scope = 'persistent' if '永久' in eff else 'battlefield'
    clean = eff.replace('永久', '').replace('提升', '+').replace('降低', '-')

    m = _STAT_MOD_RE.match(clean)
    if m:
        label, sign, num_s, is_pct = m.group(1), m.group(2), m.group(3), m.group(4)
        value = int(num_s)
        if sign == '-':
            value = -value
        if from_opp_buffs:
            # opp_buffs: 正效果在对方身上，对我方是负面的
            # 保持原意：这是敌方身上的 buff
            pass

        stat_keys = _STAT_LABEL_MAP.get(label, [])
        for key in stat_keys:
            if key in _STEP_UNITS:
                step_unit = _STEP_UNITS[key]
            elif key == 'speed':
                step_unit = _SPEED_STEP
            elif is_pct:
                step_unit = _STEP_PCT
            else:
                step_unit = _SPEED_STEP

            steps = value // step_unit
            if steps == 0:
                continue

            results.append({
                'kind': 'stat', 'target': default_target,
                'stat': key, 'steps': steps, 'scope': scope,
            })
        return results

    # 4. 检测复杂描述中的特殊效果
    # "本技能变为被应对的技能" → reflect_damage
    if '变为被应对' in eff:
        results.append({'kind': 'special', 'name': 'reflect_damage'})
        return results

    # "威力变为N倍" → power_mult
    m_pow_mult = _POWER_MULT_RE.search(eff)
    if m_pow_mult:
        results.append({
            'kind': 'special', 'name': 'power_mult',
            'value': float(m_pow_mult.group(1)),
        })
        return results

    # "威力翻倍" → power_mult = 2
    if '威力翻倍' in eff:
        results.append({
            'kind': 'special', 'name': 'power_mult', 'value': 2.0,
        })
        return results

    # "变为N连击" → multi_hit
    m_multi = _MULTI_HIT_RE.search(eff)
    if m_multi:
        results.append({
            'kind': 'special', 'name': 'multi_hit',
            'value': float(m_multi.group(1)),
        })
        return results

    # 未识别
    return results


def _effect_fingerprint(eff: dict) -> str:
    """生成效果的匹配指纹，用于去重/比对。"""
    kind = eff.get('kind', '')
    if kind == 'stat':
        return f'stat:{eff.get("stat")}:{eff.get("target","")}'
    if kind == 'abnormal':
        return f'abnormal:{eff.get("name")}:{eff.get("target","")}'
    if kind == 'mark':
        return f'mark:{eff.get("name")}:{eff.get("target","")}'
    if kind == 'weather':
        return f'weather:{eff.get("weather")}'
    return ''


def _parse_text_to_effects(text: str, default_target: str) -> list[dict]:
    """将自由文本解析为效果 dict 列表。处理各种描述句式。"""
    # 预处理：清理前缀
    clean = text
    for prefix in ['被应对技能', '额外使', '敌方获得', '获得', '使', '全技能']:
        clean = clean.replace(prefix, '')
    # "N层X" → "X×N" 或 "N层XX印记" → "XX印记×N"
    clean = re.sub(r'(\d+)层\s*', r'\1', clean)
    # 末尾可能有量词
    m_stack = re.match(r'(\d+)\s*(.+)', clean.strip())
    if m_stack:
        clean = f'{m_stack.group(2)}×{m_stack.group(1)}'
    # 直接尝试解析
    return _parse_effect_string(clean, default_target)


def _parse_counter_from_desc(description: str) -> tuple[list[dict], list[dict] | None, bool]:
    """从描述中提取效果。返回 (base_effects, counter_then, is_extra)。

    is_extra: True = 应对是"额外"效果（base 保留）；False = 效果应移入 conditional。
    """
    base_effects: list[dict] = []
    counter_then: list[dict] | None = None
    is_extra = '额外' in description

    # 1. 提取 base 减伤（始终在「应对」之前，始终无条件）
    base_dr = re.match(r'^减伤(\d+)%', description)
    if base_dr:
        reduction_pct = int(base_dr.group(1)) / 100.0
        base_effects.append({
            'kind': 'special', 'name': 'damage_reduction', 'value': reduction_pct,
        })

    # 2. 描述中非应对部分的无条件效果
    # 模式: "敌方获得5层冻结，应对防御：额外获得5层"
    #       └─ pre_counter ─┘  └─ counter clause ─┘
    counter_pat = re.compile(r'应对(攻击|防御|状态)[：:]')
    cm = counter_pat.search(description)
    pre_counter = description[:cm.start()].strip() if cm else description

    # 提取 pre_counter 中的无条件效果（去除 "造成物伤/魔伤" 前缀）
    pre_clean = re.sub(r'^造成[物魔]+伤[，,。.]*\s*', '', pre_counter)
    if pre_clean and pre_clean != description:  # 有非攻击描述
        for part in re.split(r'[，,。.]', pre_clean):
            part = part.strip()
            if part:
                parsed = _parse_text_to_effects(part, 'opp')
                for e in parsed:
                    # 只添加非 damage_reduction 的效果（DR 已单独处理）
                    if not (e.get('kind') == 'special' and e.get('name') == 'damage_reduction'):
                        base_effects.append(e)

    # 3. 提取「应对XX：ZZ」
    if cm:
        extra = description[cm.end():].strip().rstrip('。.')
        counter_then = _parse_text_to_effects(extra, 'opp')

    return base_effects, counter_then, is_extra


def _reconcile_effects(
    unconditional: list[dict],
    base_from_desc: list[dict],
    counter_then: list[dict] | None,
    is_extra: bool,
) -> list[dict]:
    """调和 wiki 无条件效果与描述中的 counter 条件效果。

    规则：
    - is_extra=True → counter 是对 base 的追加 → 无条件保留 + counter conditional
    - is_extra=False → counter 中的效果应从无条件中移除 → 移入 conditional
    - base_from_desc 中的效果（如 damage_reduction）始终无条件
    """
    result: list[dict] = list(base_from_desc)

    if not counter_then:
        # 无 counter 效果 → 所有无条件效果保留
        result.extend(unconditional)
        return result

    # 生成 counter 效果的指纹集合
    counter_fps = {_effect_fingerprint(e) for e in counter_then}
    counter_fps.discard('')  # 移除空指纹

    if is_extra:
        # "额外" → 无条件全部保留 + counter conditional 追加
        result.extend(unconditional)
        result.append({
            'kind': 'conditional',
            'when': {'kind': 'counter_succeeded'},
            'then': list(counter_then),
        })
    else:
        # 非"额外" → 从无条件中移除匹配项，移入 counter conditional
        remaining: list[dict] = []
        removed: list[dict] = []
        for eff in unconditional:
            fp = _effect_fingerprint(eff)
            if fp and fp in counter_fps:
                removed.append(eff)
            else:
                remaining.append(eff)
        result.extend(remaining)
        if removed:
            result.append({
                'kind': 'conditional',
                'when': {'kind': 'counter_succeeded'},
                'then': removed + [e for e in counter_then if _effect_fingerprint(e) not in {_effect_fingerprint(r) for r in removed}],
            })
        else:
            # 无条件中没有匹配项 → counter_then 效果是纯条件追加
            result.append({
                'kind': 'conditional',
                'when': {'kind': 'counter_succeeded'},
                'then': list(counter_then),
            })

    return result


def _build_skill_data(
    name: str, attribute: str, skill_type: str, power: int,
    energy_cost: int, counter: str, description: str,
    self_buffs: list[str], opp_debuffs: list[str],
    self_debuffs: list[str], opp_buffs: list[str],
) -> dict:
    """组装完整的技能 JSON dict。"""
    unconditional: list[dict] = []

    # self_buffs → effects (target=self)
    for eff_str in self_buffs:
        unconditional.extend(_parse_effect_string(eff_str, 'self'))

    # self_debuffs → effects (target=self, 负面)
    for eff_str in self_debuffs:
        unconditional.extend(_parse_effect_string(eff_str, 'self'))

    # opp_debuffs → effects (target=opp)
    for eff_str in opp_debuffs:
        unconditional.extend(_parse_effect_string(eff_str, 'opp'))

    # opp_buffs → effects (target=opp, 正面效果在对手)
    for eff_str in opp_buffs:
        unconditional.extend(_parse_effect_string(eff_str, 'opp', from_opp_buffs=True))

    # 描述 → base effects + counter conditional，然后调和
    effects: list[dict]
    if description:
        base_from_desc, counter_then, is_extra = _parse_counter_from_desc(description)
        effects = _reconcile_effects(unconditional, base_from_desc, counter_then, is_extra)
    else:
        effects = unconditional

    # 类型归一化
    normalized_type = _SKILL_TYPE_NORMALIZE.get(skill_type, skill_type)

    # 提取 element（属性去除"系"后缀）
    element = attribute.replace('系', '') if attribute else ''

    return {
        'name': name,
        'element': element,
        'skill_type': normalized_type,
        'power': power,
        'energy_cost': energy_cost,
        'counter': counter if counter else '无',
        'priority': 0,
        'effects': effects,
    }


def _process_wiki_skills(wiki_root: Path) -> dict[str, dict]:
    """从 wiki/技能图鉴/**/*.md 提取技能数据。"""
    skill_dir = wiki_root / '技能图鉴'
    skills: dict[str, dict] = {}

    if not skill_dir.is_dir():
        print(f'[警告] 技能图鉴目录不存在: {skill_dir}', file=sys.stderr)
        return skills

    for md in skill_dir.rglob('*.md'):
        if md.name.startswith('_'):
            continue
        try:
            text = md.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue

        fm = _parse_frontmatter(text)
        if not fm:
            continue

        name = fm.get('name', '').strip().strip('"').strip("'")
        if not name:
            continue

        try:
            desc_raw = fm.get('description', '')
            data = _build_skill_data(
                name=name,
                attribute=fm.get('attribute', ''),
                skill_type=fm.get('type', ''),
                power=int(fm.get('power', '0').strip('"').strip("'")),
                energy_cost=int(fm.get('energy_cost', '0').strip('"').strip("'")),
                counter=fm.get('counter', '无').strip('"').strip("'"),
                description=desc_raw.strip().strip('"').strip("'"),
                self_buffs=_parse_yaml_list(fm.get('self_buffs', '[]')),
                opp_debuffs=_parse_yaml_list(fm.get('opp_debuffs', '[]')),
                self_debuffs=_parse_yaml_list(fm.get('self_debuffs', '[]')),
                opp_buffs=_parse_yaml_list(fm.get('opp_buffs', '[]')),
            )
            skills[name] = data
        except (ValueError, TypeError) as e:
            print(f'[跳过] {name}: {e}', file=sys.stderr)

    print(f'[wiki] 加载 {len(skills)} 个技能', file=sys.stderr)
    return skills


def _process_csv_skills(csv_path: Path, existing: dict[str, dict]) -> dict[str, dict]:
    """从 skills_all.csv 补充 wiki 中缺失的技能。"""
    if not csv_path.is_file():
        print(f'[CSV] 未找到: {csv_path}', file=sys.stderr)
        return {}

    skills: dict[str, dict] = {}
    try:
        with open(csv_path, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                name = row.get('技能名', '').strip()
                if not name or name in existing:
                    continue
                try:
                    skill_type = row.get('类型', '')
                    data = _build_skill_data(
                        name=name,
                        attribute=row.get('属性', '').replace('系', ''),
                        skill_type=skill_type,
                        power=int(row.get('威力', '0') or '0'),
                        energy_cost=int(row.get('耗能', '0') or '0'),
                        counter='无',
                        description=row.get('效果描述', ''),
                        self_buffs=[],
                        opp_debuffs=[],
                        self_debuffs=[],
                        opp_buffs=[],
                    )
                    skills[name] = data
                except (ValueError, TypeError) as e:
                    print(f'[CSV跳过] {name}: {e}', file=sys.stderr)
    except OSError as e:
        print(f'[CSV] 读取失败: {e}', file=sys.stderr)
        return {}

    print(f'[CSV] 补充 {len(skills)} 个技能', file=sys.stderr)
    return skills


def main() -> None:
    limit = None
    args = sys.argv[1:]
    if '--limit' in args:
        idx = args.index('--limit')
        if idx + 1 < len(args):
            limit = int(args[idx + 1])

    wiki_root = BASE / 'wiki'
    csv_path = wiki_root / 'meta' / 'skills_all.csv'
    out_dir = BASE / 'data' / 'skills'

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 从 wiki 加载
    skills = _process_wiki_skills(wiki_root)

    # 2. 从 CSV 补充
    csv_skills = _process_csv_skills(csv_path, skills)
    skills.update(csv_skills)

    # 3. 写入 JSON
    index: dict[str, str] = {}
    count = 0
    for name, data in skills.items():
        # 文件名：使用技能名，但需要处理非法字符
        safe_name = name.replace('/', '_').replace('\\', '_').replace(':', '_')
        filename = f'{safe_name}.json'
        filepath = out_dir / filename

        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        index[name] = filename
        count += 1
        if limit and count >= limit:
            break

    # 4. 写入索引
    index_path = out_dir / '_index.json'
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    print(f'[完成] 生成 {count} 个技能 JSON → {out_dir}')
    print(f'[索引] {index_path}')


if __name__ == '__main__':
    main()
