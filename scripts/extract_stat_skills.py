#!/usr/bin/env python3
"""
scripts/extract_stat_skills.py — 从技能描述中提取所有六维属性变化效果

扫描 wiki/技能图鉴/**/*.md，直接解析 description 文字，
识别六维属性变化（含百分比和固定值），按效果方向分类输出。

用法：
  python scripts/extract_stat_skills.py > wiki/meta/stat_skills.md

输出字段：
  - 自身增益：描述中"自己/自身获得…"
  - 对手削弱：描述中"敌方/对手获得…"（含减益）
  - 自身减益：描述中"自己/自身…降低/减少"
"""

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# ── 六维标签 ──
STAT_TYPES = ['生命', '物攻', '魔攻', '物防', '魔防', '双攻', '双防', '速度']
STAT_PAT = '|'.join(STAT_TYPES)


def parse_stat_changes(text: str) -> list[str]:
    """
    从文本中提取属性变化。返回如 ["物攻+100%", "速度-30", "双攻-50%"]。
    """
    results: list[str] = []

    # 格式 A：stat1和stat2 共享符号值，如 "双攻和双防-100%"
    for m in re.finditer(
        rf'({STAT_PAT})和({STAT_PAT})([+\-]\d+(?:\.\d+)?%?)', text
    ):
        s1, s2, val = m.group(1), m.group(2), m.group(3)
        results.append(f'{s1}{val}')
        results.append(f'{s2}{val}')

    # 格式 B：单个属性，如 "物攻+100%"、"速度+120"、"魔防-20%"
    for m in re.finditer(
        rf'({STAT_PAT})([+\-]\d+(?:\.\d+)?%?)', text
    ):
        stat, val = m.group(1), m.group(2)
        entry = f'{stat}{val}'
        if entry not in results:
            # 过滤：数字不能紧跟前缀（排除 "威力135" 被误识别为 "135"）
            # 检查捕获位置之前是否有 "威力" / "power" 等
            pos = m.start()
            before = text[max(0, pos - 8):pos]
            if re.search(r'(?:威力|power|耗能|HP|能量)\s*$', before, re.IGNORECASE):
                continue
            results.append(entry)

    return results


def classify_from_text(description: str) -> dict[str, list[str]]:
    """
    从技能描述中提取并分类六维效果。
    不依赖 frontmatter 预计算字段，直接从文字判断。
    """
    out: dict[str, list[str]] = {
        'self_buffs': [], 'opp_debuffs': [],
        'self_debuffs': [], 'opp_buffs': [],
    }
    d = description

    # ── 1. 自己/自身/我方 获得/提升 → self_buffs ──
    for m in re.finditer(r'(?:自己|自身|我方|全体)(?:.*?)(?:获得|提升)(.{1,60}?)(?:[。；，]|$)', d):
        changes = parse_stat_changes(m.group(1))
        for c in changes:
            m2 = re.match(rf'({STAT_PAT})(.+)', c)
            if not m2:
                continue
            sign = m2.group(2)
            if sign.startswith('+'):
                out['self_buffs'].append(c)
            else:
                out['self_debuffs'].append(c)

    # ── 2. 敌方/对手 获得/降低 → opp_debuffs ──
    for m in re.finditer(r'(?:使|令|让)?(?:敌方|对手|对方)(?:精灵)?(?:.*?)(?:获得|降低|下降|减少)(.{1,60}?)(?:[。；，]|$)', d):
        changes = parse_stat_changes(m.group(1))
        for c in changes:
            m2 = re.match(rf'({STAT_PAT})(.+)', c)
            if not m2:
                continue
            sign = m2.group(2)
            if sign.startswith('-'):
                out['opp_debuffs'].append(c)
            else:
                out['opp_buffs'].append(c)

    # ── 3. 自残效果：自己…降低/减少/下降 ──
    for m in re.finditer(r'(?:自己|自身|使用后)(?:.*?)(?:降低|减少|下降|减弱)(.{1,60}?)(?:[。；，]|$)', d):
        changes = parse_stat_changes(m.group(1))
        for c in changes:
            if c not in out['self_debuffs'] and c not in out['self_buffs']:
                out['self_debuffs'].append(c)

    # ── 4. "自己和敌方获得…" 双方效果 ──
    for m in re.finditer(r'自己和敌方获得(.{1,60}?)(?:[。；，]|$)', d):
        changes = parse_stat_changes(m.group(1))
        for c in changes:
            m2 = re.match(rf'({STAT_PAT})(.+)', c)
            if not m2:
                continue
            sign = m2.group(2)
            if sign.startswith('+'):
                out['self_buffs'].append(c)
                out['opp_buffs'].append(c)
            else:
                out['self_debuffs'].append(c)
                out['opp_debuffs'].append(c)

    # ── 5. 应对效果 ──
    for m in re.finditer(r'应对.*?(?:使|令)(?:敌方|对手)(?:.*?)(?:获得|降低)(.{1,40}?)(?:[。；，]|$)', d):
        changes = parse_stat_changes(m.group(1))
        for c in changes:
            if c not in out['opp_debuffs']:
                out['opp_debuffs'].append(c)

    # 去重
    for k in out:
        seen = set()
        out[k] = [x for x in out[k] if not (x in seen or seen.add(x))]
    return out


def main() -> None:
    skill_dir = BASE / 'wiki' / '技能图鉴'
    if not skill_dir.is_dir():
        print(f'[错误] 目录不存在: {skill_dir}', file=sys.stderr)
        sys.exit(1)

    results: list[dict] = []
    warned: list[str] = []

    for md in sorted(skill_dir.rglob('*.md')):
        if md.name.startswith('_'):
            continue
        text = md.read_text(encoding='utf-8', errors='ignore')
        if not text.startswith('---'):
            continue
        end = text.find('\n---', 3)
        if end == -1:
            continue

        fm: dict[str, str] = {}
        for line in text[4:end].splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip().strip('"').strip("'")

        name = fm.get('name', md.stem)
        attr = fm.get('attribute', '')
        stype = fm.get('type', '')
        power = fm.get('power', '')
        cost = fm.get('energy_cost', '')
        desc = fm.get('description', '')

        if not desc:
            continue

        effects = classify_from_text(desc)

        # 检查 frontmatter 预计算字段与文字解析是否一致，不一致则警告
        for key in effects:
            stored_raw = fm.get(key, '')
            if stored_raw and stored_raw != '[]':
                # 简单比较
                stored = [s.strip().strip('"').strip("'")
                         for s in stored_raw.strip('[]').split(',') if s.strip()]
                if sorted(effects[key]) != sorted(stored):
                    warned.append(f'  [{name}] {key}: desc→{effects[key]} frontmatter→{stored}')

        if any(effects[k] for k in effects):
            results.append({
                'name': name, 'attr': attr, 'type': stype,
                'power': power, 'cost': cost, 'effects': effects,
            })

    # ── 输出警告 ──
    if warned:
        print('<!--', file=sys.stderr)
        print('## ⚠ 描述解析与 frontmatter 不一致', file=sys.stderr)
        for w in warned:
            print(w, file=sys.stderr)
        print('-->', file=sys.stderr)
        print(file=sys.stderr)

    # ── 输出 markdown ──
    print('# 六维属性影响技能汇总')
    print()
    print('> 直接从技能描述文字中解析。共 {} 个技能。'.format(len(results)))
    print()

    # 按方向分表
    sections = [
        ('self_buffs',   '## 自身增益'),
        ('opp_debuffs',  '## 对手削弱'),
        ('self_debuffs', '## 自身减益'),
        ('opp_buffs',    '## 对手增益'),
    ]
    for key, heading in sections:
        subset = [r for r in results if r['effects'][key]]
        if not subset:
            continue
        print(heading)
        print()
        print('| 技能 | 系别 | 类型 | 威力 | 耗能 | 描述摘要 | 效果 |')
        print('|------|------|------|------|------|----------|------|')
        for r in subset:
            eff_str = '、'.join(r['effects'][key])
            # 从 description 中取前60字作为摘要
            print(f'| {r["name"]} | {r["attr"]} | {r["type"]} | {r["power"]} '
                  f'| {r["cost"]} | … | {eff_str} |')
        print()

    # 完整汇总表
    print('## 全部技能一览')
    print()
    print('| 技能 | 系别 | 类型 | 威力 | 耗能 | 自身增益 | 对手削弱 | 自身减益 | 对手增益 |')
    print('|------|------|------|------|------|----------|----------|----------|----------|')
    for r in results:
        def _f(k): return '、'.join(r['effects'].get(k, [])) or '—'
        print(f'| {r["name"]} | {r["attr"]} | {r["type"]} | {r["power"]} '
              f'| {r["cost"]} | {_f("self_buffs")} | {_f("opp_debuffs")} '
              f'| {_f("self_debuffs")} | {_f("opp_buffs")} |')
    print()

    print(f'*共 {len(results)} 个技能，数据来源：wiki/技能图鉴 frontmatter description 字段*')


if __name__ == '__main__':
    main()
