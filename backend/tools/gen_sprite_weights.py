#!/usr/bin/env python3
"""backend/tools/gen_sprite_weights.py — 生成 data/sprites/_weights.json（精灵体重 sidecar）

数据源：线上 nrc wiki 的 Lua 模块缓存 `backend/engine/ai/data/nrc_cache/Catalog.lua`
（与线上逐字符一致；每条精灵条目带 `title="水灵"` / `weight="77~85.5KG"` 区间字符串）。

口径（见 data/IR_GUIDE.md §1.2 `weight`）：
  - **区间取中点**（模拟口径）：`weight = (lo + hi) / 2`，保留 3 位小数。
    游戏内个体体重在区间内浮动，本引擎不建模个体体重，取中点作为确定值。
  - 单位 KG。

name/form → Catalog title 的对应规则（按优先级，逐条幂等可复现）：
  1. `(number, 展示名)` 精确命中 —— 展示名与 `SpriteDB._display_key()` 同规则
     （外观优先 `名字（外观）` → 形态 `名字（首领形态）` → 纯名）；
  2. `(number, 纯名字)` —— 覆盖「本来的样子」这类**数据侧默认外观**（Catalog 里
     默认外观不带后缀）与「首领形态（无外观）」；
  3. 同 number 下按**基名**（去掉 `（…）` 后的部分）匹配 —— 覆盖首领形态不带外观、
     Catalog 只登记了外观变体的条目（鸭吉吉国王/钻石蜗/加油蟹…）；
  4. NFKC 归一后再比 —— 覆盖全角/半角差异（`权杖-Ⅴ` ↔ `权杖-V`）。

用法（幂等，可重复运行；输出不含时间戳，两次运行字节一致）：
  env\\python.exe -X utf8 backend/tools/gen_sprite_weights.py            # 写入
  env\\python.exe -X utf8 backend/tools/gen_sprite_weights.py --dry-run  # 只报告
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

CATALOG = BASE / "backend" / "engine" / "ai" / "data" / "nrc_cache" / "Catalog.lua"
SPRITES_DIR = BASE / "data" / "sprites"
OUT_PATH = SPRITES_DIR / "_weights.json"

_RE_SUFFIX = re.compile(r'（([^）]+)）$')


# ── Catalog 解析 ──

def _pet_entries(text: str) -> list[dict]:
    """抽取 Catalog.lua 里所有 `pet_NNNNNN={...}` 条目（花括号配平）。"""
    out: list[dict] = []
    for m in re.finditer(r'pet_(\d{6})=\{', text):
        start = m.end() - 1
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[start:i + 1]

        def g(key: str) -> str:
            mm = re.search(r'(?:^|[,{])' + key + r'="([^"]*)"', body)
            return mm.group(1) if mm else ''

        out.append({
            'id': 'pet_' + m.group(1),
            'number': g('number').lstrip('0') or '0',
            'title': g('title'),
            'weight_raw': g('weight'),
        })
    return out


def parse_weight(raw: str) -> tuple[float, float] | None:
    """`"77~85.5KG"` → (77.0, 85.5)；无法解析返回 None。"""
    m = re.match(r'^\s*([0-9]+(?:\.[0-9]+)?)\s*[~～-]\s*([0-9]+(?:\.[0-9]+)?)\s*KG\s*$',
                 raw, re.IGNORECASE)
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def midpoint(lo: float, hi: float) -> float:
    """区间中点（模拟口径）。"""
    return round((lo + hi) / 2.0, 3)


# ── 数据侧展示键（与 SpriteDB._display_key 同规则）──

def _normalize_form_appearance(data: dict) -> tuple[str, str]:
    form = str(data.get('form', '') or '').strip()
    appearance = str(data.get('appearance', '') or '').strip()
    if not appearance and form and '首领' not in form:
        appearance, form = form, ''
    return form, appearance


def display_key(name: str, form: str = '', appearance: str = '') -> str:
    if appearance:
        return f'{name}（{appearance}）'
    if form:
        return f'{name}（{form}）'
    return name


def base_name(title: str) -> str:
    """去掉 `（…）` 后缀的基名。"""
    return _RE_SUFFIX.sub('', title).strip()


def norm_key(text: str) -> str:
    """NFKC 归一 + 去掉空白/短横，用于兜底比较（全角罗马数字等）。"""
    s = unicodedata.normalize('NFKC', text)
    return re.sub(r'[\s\-－—]', '', s)


# ── 主流程 ──

def build_table() -> tuple[dict, dict]:
    """返回 (weights, stats)。weights: 展示键 → kg。"""
    text = CATALOG.read_text(encoding='utf-8')
    entries = _pet_entries(text)

    by_num_title: dict[tuple[str, str], dict] = {}
    by_num: dict[str, list[dict]] = {}
    unparsed: list[dict] = []
    for e in entries:
        lo_hi = parse_weight(e['weight_raw'])
        if lo_hi is None:
            unparsed.append(e)
            e['kg'] = None
        else:
            e['kg'] = midpoint(*lo_hi)
        by_num_title[(e['number'], e['title'])] = e
        by_num.setdefault(e['number'], []).append(e)

    stats = {
        'catalog_entries': len(entries),
        'catalog_unparsed_weight': len(unparsed),
        'matched_exact_display': 0,
        'matched_plain_name': 0,
        'matched_base_name': 0,
        'matched_normalized': 0,
        'unmatched': 0,
        # 同 number 多候选（外观变体）里按**字典序第一个**取（用户 2026-09-22 确认的口径）
        'base_name_policy': '歧义取字典序第一个（用户 2026-09-22 确认）',
        'base_name_ambiguous': [],
        'unmatched_entries': [],
    }
    weights: dict[str, float] = {}

    for jf in sorted(SPRITES_DIR.glob('*.json')):
        if jf.name.startswith('_'):
            continue
        try:
            data = json.loads(jf.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(data.get('name', '')).strip()
        number = str(data.get('number', '')).strip().lstrip('0') or '0'
        if not name or not number:
            continue
        form, appearance = _normalize_form_appearance(data)
        disp = display_key(name, form, appearance)

        hit = by_num_title.get((number, disp))
        step = 'exact_display'
        if hit is None and disp != name:
            hit = by_num_title.get((number, name))
            step = 'plain_name'
        if hit is None:
            cands = [e for e in by_num.get(number, []) if base_name(e['title']) == name]
            if cands:
                exact_plain = [e for e in cands if e['title'] == name]
                if exact_plain:
                    hit = exact_plain[0]
                else:
                    cands_sorted = sorted(cands, key=lambda e: e['title'])
                    hit = cands_sorted[0]
                    kilos = {e['kg'] for e in cands_sorted}
                    if len(kilos) > 1:
                        stats['base_name_ambiguous'].append({
                            'file': jf.name, 'number': number, 'name': name,
                            'picked': hit['title'], 'kg': hit['kg'],
                            'candidates': [{'title': e['title'], 'kg': e['kg']}
                                           for e in cands_sorted],
                        })
                step = 'base_name'
        if hit is None:
            want = norm_key(disp)
            for e in sorted(by_num.get(number, []), key=lambda e: e['title']):
                if norm_key(e['title']) == want:
                    hit = e
                    step = 'normalized'
                    break
        if hit is None or hit['kg'] is None:
            stats['unmatched'] += 1
            stats['unmatched_entries'].append({
                'file': jf.name, 'number': number, 'name': name,
                'form': form, 'appearance': appearance, 'display': disp,
            })
            continue

        stats[f'matched_{step}'] += 1
        # 同一展示键应唯一：文件名不同但展示键相同（重复数据）时取排序后的第一个，
        # 保证不同机器/不同扫描顺序下结果一致。
        weights.setdefault(disp, hit['kg'])

    return weights, stats


def main() -> None:
    dry_run = '--dry-run' in sys.argv
    weights, stats = build_table()

    print(f'Catalog: {CATALOG}')
    print(f'  条目 {stats["catalog_entries"]}，体重不可解析 {stats["catalog_unparsed_weight"]}')
    print('匹配分档：')
    for step in ('exact_display', 'plain_name', 'base_name', 'normalized'):
        print(f'  {step:16s} {stats["matched_" + step]}')
    print(f'  未匹配          {stats["unmatched"]}')
    if stats['base_name_ambiguous']:
        print(f'⚠ 基名匹配存在同 number 多候选权重不一致 {len(stats["base_name_ambiguous"])} 条：')
        for a in stats['base_name_ambiguous']:
            print('   ', a['file'], '→', a['picked'], a['kg'], a['candidates'])
    for u in stats['unmatched_entries']:
        print('   UNMATCHED', u['file'], u['display'], 'number=', u['number'])
    print(f'总键数 {len(weights)}')

    payload = {
        '_meta': {
            'source': 'backend/engine/ai/data/nrc_cache/Catalog.lua',
            'generator': 'backend/tools/gen_sprite_weights.py',
            'policy': '区间取中点（模拟口径）；游戏内个体体重在区间内浮动',
            'unit': 'kg',
            'catalog_entries': stats['catalog_entries'],
            'catalog_unparsed_weight': stats['catalog_unparsed_weight'],
            'matched': {step: stats[f'matched_{step}']
                        for step in ('exact_display', 'plain_name', 'base_name', 'normalized')},
            'unmatched': stats['unmatched'],
            'unmatched_entries': stats['unmatched_entries'],
            'base_name_policy': stats['base_name_policy'],
            'base_name_ambiguous': stats['base_name_ambiguous'],
        },
        'weights': {k: weights[k] for k in sorted(weights)},
    }

    if dry_run:
        print('--dry-run：未写入')
        return
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'已写入 {OUT_PATH}（{len(weights)} 条）')


if __name__ == '__main__':
    main()
