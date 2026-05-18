#!/usr/bin/env python3
"""scripts/build/build_skills.py — 一次性预处理：wiki/CSV → data/skills/*.json

用法:
  python scripts/build/build_skills.py            # 生成全部技能 JSON
  python scripts/build/build_skills.py --limit 10 # 仅生成前 N 个（调试用）
"""

import csv
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent

# ── 正则：复用现有解析逻辑 ──

_STAT_MOD_RE = re.compile(
    r'(双攻|双防|物攻|魔攻|物防|魔防|速度|威力|先手|能耗)\s*([+\-])\s*(\d+)\s*(%?)'
)

# 复杂描述中的特殊效果
_POWER_MULT_RE = re.compile(r'威力变为\s*(\d+(?:\.\d+)?)\s*倍')
_MULTI_HIT_RE = re.compile(r'变为\s*(\d+)\s*连击')

# 中文分数："M分之N" → N/M。用于两侧技能威力动态计算。
_PARSE_FRACTION = re.compile(
    r'两侧.*?威力和的([一二三四五六七八九十\d]+)分之([一二三四五六七八九十\d]+)'
)

_CN_NUM: dict[str, int] = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}


def _fraction_value(m: re.Match) -> float | None:
    """从正则匹配提取中文分数值。den=分母, num=分子。返回 num/den。"""
    den_str = m.group(1)
    num_str = m.group(2)
    try:
        den = int(den_str) if den_str.isdigit() else _CN_NUM.get(den_str)
        num = int(num_str) if num_str.isdigit() else _CN_NUM.get(num_str)
        if den and num and den > 0:
            return num / den
    except (ValueError, TypeError):
        pass
    return None


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
        is_mark = (
            mark_name in _MARK_NAMES
            or mark_name.endswith('印记')
            or mark_name in ('棘刺', '迟缓', '风起', '减速')
        )
        if is_mark:
            target = 'own_team' if default_target == 'self' else 'opp_team'
            results.append({
                'kind': 'mark', 'target': target,
                'name': mark_name, 'stacks': stacks,
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

    # 3. 检测天气
    m_weather = re.search(r'天气[变为改]{1,2}(\S+)', eff)
    if m_weather:
        weather_raw = m_weather.group(1)
        # 归一化天气名
        weather_map = {
            '雨天': 'rain', '下雨': 'rain',
            '沙暴': 'sand', '沙尘暴': 'sand',
            '暴风雪': 'snow', '雪天': 'snow', '冰雹': 'snow',
        }
        for key, val in weather_map.items():
            if key in weather_raw:
                results.append({
                    'kind': 'weather', 'weather': val,
                    'turns': 8,
                })
                return results
        # 兜底：直接使用原文
        results.append({
            'kind': 'weather', 'weather': weather_raw,
            'turns': 8,
        })
        return results

    # 4. 检测属性变化
    scope = 'persistent' if '永久' in eff else 'battlefield'
    clean = eff.replace('永久', '').replace('提升', '+').replace('降低', '-')

    m = _STAT_MOD_RE.search(clean)
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

    # 5. 检测回复 / 能量操作
    m_heal_pct = re.search(r'回复(\d+)%生命', eff)
    if m_heal_pct:
        results.append({
            'kind': 'special', 'name': 'heal',
            'value': int(m_heal_pct.group(1)) / 100.0,
        })
        return results

    m_heal_flat = re.search(r'回复(\d+)点生命', eff)
    if m_heal_flat:
        results.append({
            'kind': 'special', 'name': 'direct_heal',
            'amount': int(m_heal_flat.group(1)),
        })
        return results

    # "偷取敌方N点能量" / "偷取N点能量" / "偷取敌方N能量"
    m_steal = re.search(r'偷取.*?(\d+)点?能量', eff)
    if m_steal:
        results.append({
            'kind': 'special', 'name': 'steal_energy',
            'amount': int(m_steal.group(1)),
        })
        return results

    # "敌方失去N点能量" / "失去N点能量" / "敌方失去N能量"
    m_lose_energy = re.search(r'失去(\d+)点?能量', eff)
    if m_lose_energy:
        results.append({
            'kind': 'special', 'name': 'steal_energy',
            'amount': int(m_lose_energy.group(1)),
        })
        return results

    # "回复N点能量" / "自己回复N能量" (self energy gain)
    m_gain_energy = re.search(r'(?:自己)?回复(\d+)点?能量', eff)
    if m_gain_energy:
        results.append({
            'kind': 'special', 'name': 'gain_energy',
            'amount': int(m_gain_energy.group(1)),
        })
        return results

    # 连击数修正（仅匹配绝对数值，排除百分数如"连击数+100%"）
    m_combo_mod = re.search(r'连击数([+\-])(\d+)(?![%\d])', eff)
    if m_combo_mod:
        sign, num = m_combo_mod.group(1), m_combo_mod.group(2)
        value = int(num)
        if sign == '-':
            value = -value
        results.append({
            'kind': 'stat', 'target': default_target,
            'stat': 'combo', 'steps': value, 'scope': 'battlefield',
        })
        return results

    # 连击数百分比："连击数+100%" → combo_mult stat (1step=100%)
    m_combo_pct = re.search(r'连击数([+\-])(\d+)%', eff)
    if m_combo_pct:
        sign, num = m_combo_pct.group(1), m_combo_pct.group(2)
        pct = int(num)
        steps = max(1, pct // 100)
        if sign == '-':
            steps = -steps
        results.append({
            'kind': 'stat', 'target': default_target,
            'stat': 'combo_mult', 'steps': steps, 'scope': 'battlefield',
        })
        return results

    # "N%吸血" → life_drain stat (1step=10%)
    m_life_drain = re.search(r'(\d+)%吸血', eff)
    if m_life_drain:
        pct = int(m_life_drain.group(1))
        steps = max(1, pct // 10)
        results.append({
            'kind': 'stat', 'target': default_target,
            'stat': 'life_drain', 'steps': steps, 'scope': 'battlefield',
        })
        return results

    # 5a. 检测驱散
    m_dispel = re.search(r'驱散(?:敌方|自己)的?(所有|一种|1种)?(增益|减益)', eff)
    if m_dispel:
        count = -1 if (m_dispel.group(1) and '所有' in m_dispel.group(1)) else 1 if m_dispel.group(1) else -1
        is_positive = m_dispel.group(2) == '增益'
        name = 'dispel_positive' if is_positive else 'dispel_negative'
        tgt = 'self' if '自己' in eff else 'opp'
        results.append({'kind': 'special', 'name': name, 'amount': count, 'target': tgt})
        return results

    # 5b. 检测翻倍
    m_double = re.search(r'(增益|减益).*翻倍|翻倍.*(增益|减益)', eff)
    if m_double:
        is_positive = (m_double.group(1) or m_double.group(2)) == '增益'
        name = 'double_positive' if is_positive else 'double_negative'
        tgt = 'self' if '自己' in eff else 'opp'
        results.append({'kind': 'special', 'name': name, 'target': tgt})
        return results

    # 6. 检测动态威力/能量（基于敌方技能总能耗）
    m_pow_energy = re.search(r'总能耗的?(\d+)倍', eff)
    if m_pow_energy:
        results.append({
            'kind': 'special', 'name': 'power_by_enemy_energy',
            'value': float(m_pow_energy.group(1)),
        })
        return results
    if '总能耗的一半' in eff:
        results.append({
            'kind': 'special', 'name': 'gain_energy_by_enemy', 'value': 0.5,
        })
        return results
    m_energy_frac = re.search(r'总能耗的?(\d+)分之(\d+)', eff)
    if m_energy_frac:
        den, num = int(m_energy_frac.group(1)), int(m_energy_frac.group(2))
        results.append({
            'kind': 'special', 'name': 'gain_energy_by_enemy',
            'value': num / den,
        })
        return results
    # 动态威力：两侧技能威力和的N分之一
    m_adj_pow = _PARSE_FRACTION.search(eff)
    if m_adj_pow:
        val = _fraction_value(m_adj_pow)
        if val is not None:
            results.append({
                'kind': 'special', 'name': 'power_by_adjacent',
                'value': val,
            })
            return results

    # 7. 检测特殊效果
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

    # "下个入场精灵继承增益" → escape_inherit
    if '下个入场' in eff and '继承' in eff:
        results.append({'kind': 'special', 'name': 'escape_inherit'})
        return results

    # "自己返场" → return_self
    if '自己返场' in eff or ('返场' in eff and '下回合' in eff):
        results.append({'kind': 'special', 'name': 'return_self'})
        return results

    # "使敌方精灵返场" → force_return
    if '敌方' in eff and '返场' in eff:
        results.append({'kind': 'special', 'name': 'force_return'})
        return results

    # "脱离" / "折返" → escape
    if '脱离' in eff or '折返' in eff:
        results.append({'kind': 'special', 'name': 'escape'})
        return results

    # "打断" → interrupt (硬门)
    if '打断' in eff:
        results.append({'kind': 'special', 'name': 'interrupt'})
        return results

    # "造成N威力物伤/魔伤" → counter_damage
    m_cnt_dmg = re.search(r'造成(\d+)威力(物|魔)伤', eff)
    if m_cnt_dmg:
        results.append({
            'kind': 'special', 'name': 'counter_damage',
            'value': float(m_cnt_dmg.group(1)),
        })
        return results

    # "交换生命比例" → exchange_hp_ratio
    if '交换生命比例' in eff:
        results.append({'kind': 'special', 'name': 'exchange_hp_ratio'})
        return results

    # "交换增益和减益" → exchange_effects
    if '交换增益' in eff and '减益' in eff:
        results.append({'kind': 'special', 'name': 'exchange_effects'})
        return results

    # "N次随机奉献" (输入可能是 "次随机奉献×N" 已被 _parse_text_to_effects 转换)
    m_dev = re.search(r'(\d+)次随机奉献|次随机奉献×(\d+)', eff)
    if m_dev:
        amount = int(m_dev.group(1) or m_dev.group(2))
        results.append({
            'kind': 'special', 'name': 'random_devotion',
            'amount': amount,
        })
        return results

    # "每回合随机变成己方队伍中其他精灵的技能" → borrow_skill
    if '随机变成' in eff and '技能' in eff:
        results.append({'kind': 'special', 'name': 'borrow_skill'})
        return results

    # "不受自身属性减益和敌方属性增益影响" → ignore_mods
    if '不受自身' in eff and '减益' in eff and '增益' in eff:
        results.append({'kind': 'special', 'name': 'ignore_mods'})
        return results

    # "交换携带的技能" → exchange_skills
    if '交换' in eff and '技能' in eff:
        results.append({'kind': 'special', 'name': 'exchange_skills'})
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


def _parse_counter_from_desc(description: str, skill_type: str = '') -> tuple[list[dict], list[dict] | None, bool, int]:
    """从描述中提取效果。返回 (base_effects, counter_then, is_extra, combo)。

    is_extra: True = 应对是"额外"效果（base 保留）；False = 效果应移入 conditional。
    """
    base_effects: list[dict] = []
    counter_then: list[dict] | None = None
    is_extra = '额外' in description

    # 0. 默认目标：攻击技能 → opp，状态/防御 → self
    default_target = 'opp' if skill_type in ('物攻', '魔攻', '动态攻击') else 'self'

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

    # 提取静态连击数
    combo = 1
    m_combo = re.search(r'(\d+)连击', pre_counter)
    if m_combo:
        combo = int(m_combo.group(1))

    # 提取 pre_counter 中的无条件效果（去除 "造成物伤/魔伤" 前缀 + "N连击"）
    pre_clean = re.sub(r'^造成[物魔]+伤[，,。.]*\s*', '', pre_counter)
    pre_clean = re.sub(r'\d+连击', '', pre_clean).strip('，,。.')
    has_non_attack = pre_clean and pre_clean != pre_counter  # "造成X伤" 前缀被移除了
    no_counter = not cm                                      # 无应对从句
    if pre_clean and (has_non_attack or no_counter):
        for part in re.split(r'[，,。.]', pre_clean):
            part = part.strip()
            if part:
                parsed = _parse_text_to_effects(part, default_target)
                for e in parsed:
                    # 只添加非 damage_reduction 的效果（DR 已单独处理）
                    if not (e.get('kind') == 'special' and e.get('name') == 'damage_reduction'):
                        base_effects.append(e)

    # 3. 提取「应对XX：ZZ」
    if cm:
        extra = description[cm.end():].strip().rstrip('。.')
        counter_then = []
        for part in re.split(r'[，,。.]', extra):
            part = part.strip()
            if part:
                counter_then.extend(_parse_text_to_effects(part, 'opp'))

    return base_effects, counter_then, is_extra, combo


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

    combo = 1
    # 描述 → base effects + counter conditional，然后调和
    effects: list[dict]
    if description:
        base_from_desc, counter_then, is_extra, combo = _parse_counter_from_desc(description, skill_type)
        effects = _reconcile_effects(unconditional, base_from_desc, counter_then, is_extra)
    else:
        effects = unconditional

    # dedup: escape_inherit 覆盖 escape（击鼓传花）
    if any(e.get('name') == 'escape_inherit' for e in effects):
        effects = [e for e in effects if e.get('name') != 'escape']

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
        'combo': combo,
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


# ═══════════════════════════════════════════════════════════════
# 输出校验
# ═══════════════════════════════════════════════════════════════

_VALID_EFFECT_KINDS: frozenset[str] = frozenset({
    'stat', 'abnormal', 'mark', 'weather', 'special', 'conditional',
})

# 从 parse 阶段已知的合法 special name（与 effects.SpecialName 对应）
_VALID_SPECIAL_NAMES: frozenset[str] = frozenset({
    'power_bonus', 'power_mult', 'damage_mult', 'damage_reduction', 'multi_hit',
    'heal', 'direct_heal', 'gain_energy', 'steal_energy', 'life_drain',
    'gain_energy_by_enemy', 'burst', 'charge', 'interrupt', 'reflect_damage',
    'counter_damage', 'escape', 'escape_inherit', 'force_return', 'return_self',
    'dispel_positive', 'dispel_negative', 'double_positive', 'double_negative',
    'exchange_hp_ratio', 'exchange_effects', 'exchange_skills',
    'power_by_enemy_energy', 'power_by_adjacent', 'adjacent_power_bonus',
    'priority_bonus', 'ignore_mods', 'random_devotion', 'borrow_skill',
})


def _validate_skill_json(name: str, data: dict) -> list[str]:
    """校验单个技能 JSON 数据。返回问题描述列表（空=无问题）。"""
    issues: list[str] = []

    # 必填字段
    for field in ('name', 'skill_type', 'power', 'energy_cost', 'counter'):
        if field not in data:
            issues.append(f'缺少必填字段: {field}')

    # 校验 effects 数组
    for i, effect in enumerate(data.get('effects', [])):
        kind = effect.get('kind', '')
        if kind not in _VALID_EFFECT_KINDS:
            issues.append(f'effects[{i}]: 无效 kind={kind!r}')
            continue

        if kind == 'stat':
            if not effect.get('stat'):
                issues.append(f'effects[{i}] stat: 缺少 stat 字段')
            if not effect.get('target'):
                issues.append(f'effects[{i}] stat: 缺少 target 字段')
            if 'steps' not in effect:
                issues.append(f'effects[{i}] stat: 缺少 steps 字段')

        elif kind == 'special':
            sname = effect.get('name', '')
            if sname not in _VALID_SPECIAL_NAMES:
                issues.append(f'effects[{i}] special: 未知 name={sname!r}')

        elif kind == 'conditional':
            when = effect.get('when') or {}
            when_kind = when.get('kind', '')
            valid_cond_kinds = {
                'counter_succeeded', 'is_first', 'opp_switched', 'hp_below',
                'has_abnormal', 'weather_is', 'counter_ge', 'and', 'or',
            }
            if when_kind not in valid_cond_kinds:
                issues.append(f'effects[{i}] conditional.when: 未知 kind={when_kind!r}')

    return issues


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

    # 2.5 校验
    total_issues = 0
    for name, data in skills.items():
        issues = _validate_skill_json(name, data)
        if issues:
            total_issues += len(issues)
            print(f'[校验] {name}:', file=sys.stderr)
            for issue in issues:
                print(f'  ! {issue}', file=sys.stderr)
    if total_issues:
        print(f'[校验] 共 {total_issues} 个问题', file=sys.stderr)
    else:
        print(f'[校验] 全部通过', file=sys.stderr)

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
