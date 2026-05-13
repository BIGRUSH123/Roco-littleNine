"""assign_ids.py — 为技能和特性分配数字编号，写入 JSON + 生成索引。

运行: python scripts/build/assign_ids.py

编号规则：
  技能 ID 从 10001 开始，按系别分组（wiki/技能图鉴/_index.json 顺序）
  特性 ID 从 20001 开始，按拼音排序
  测试技能 90001+
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = BASE / 'data' / 'skills'
TRAIT_DIR = BASE / 'data' / 'traits'
SPRITE_DIR = BASE / 'data' / 'sprites'
WIKI_SKILL_INDEX = BASE / 'wiki' / '技能图鉴' / '_index.json'

SKILL_ID_START = 10001
TRAIT_ID_START = 20001
TEST_ID_START = 90001
SLOTS_PER_ELEMENT = 50  # 每系预留槽位


def is_test_skill(name: str) -> bool:
    return name.startswith('test_') or name.startswith('debug_')


def assign_skill_ids() -> dict[str, int]:
    """返回 {name: id} 映射。"""
    # 1. 读取 wiki 索引获取系别分组
    with open(WIKI_SKILL_INDEX, encoding='utf-8') as f:
        wiki = json.load(f)

    # 2. 扫描 data/skills/ 下所有 JSON 文件
    all_files = sorted([f.stem for f in SKILL_DIR.glob('*.json')
                        if not f.name.startswith('_')])

    wiki_names: set[str] = set()
    assigned: dict[str, int] = {}
    next_id = SKILL_ID_START

    # 3. 按系别顺序分配（wiki 中的技能）
    for element, info in wiki['by_element'].items():
        for name in info['names']:
            wiki_names.add(name)
            if name in assigned:
                continue
            if name not in all_files:
                print(f'  [warn] wiki 中存在但无 JSON 文件: {name!r}')
                continue
            assigned[name] = next_id
            next_id += 1
        # 预留槽位：跳到下一个 50 倍数的区间
        block_start = ((next_id - SKILL_ID_START - 1) // SLOTS_PER_ELEMENT + 1) * SLOTS_PER_ELEMENT + SKILL_ID_START
        next_id = block_start

    # 4. 非 wiki 中的正式技能（如水枪、火柱）→ 追加到末尾
    extra = [n for n in all_files if n not in assigned and not is_test_skill(n)]
    for name in sorted(extra):
        assigned[name] = next_id
        next_id += 1
        print(f'  [info] 非 wiki 技能: {name!r} → {assigned[name]}')

    # 5. 测试技能 → 9xxxx
    test_id = TEST_ID_START
    for name in sorted(all_files):
        if is_test_skill(name):
            assigned[name] = test_id
            test_id += 1

    return assigned


def _scan_python_trait_names() -> set[str]:
    """扫描 Python 注册的特性名（无 JSON 文件的 @register 特性）。"""
    import re
    names: set[str] = set()
    traits_dir = BASE / 'scripts' / 'sim' / 'traits'
    for py_file in traits_dir.glob('*.py'):
        text = py_file.read_text(encoding='utf-8')
        for m in re.finditer(r'@register\("([^"]+)"\)', text):
            names.add(m.group(1))
    return names


def assign_trait_ids() -> dict[str, int]:
    """返回 {name: id} 映射，包含 JSON 和 Python 注册特性。"""
    # JSON 文件中的特性
    json_names = sorted([f.stem for f in TRAIT_DIR.glob('*.json')
                         if not f.name.startswith('_')])
    # Python 注册的特性
    python_names = _scan_python_trait_names()
    # Python only (无 JSON)
    python_only = sorted(python_names - set(json_names))

    assigned: dict[str, int] = {}
    next_id = TRAIT_ID_START

    # JSON 特性按拼音排序先分配
    for name in sorted(json_names):
        assigned[name] = next_id
        next_id += 1

    # Python only 特性随后分配
    for name in python_only:
        assigned[name] = next_id
        next_id += 1
        print(f'  [info] Python only 特性: {name!r} → {assigned[name]}')

    return assigned


def write_json_id(path: Path, new_id: int) -> None:
    """为单个 JSON 文件写入 id 字段（保持其他字段不变）。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    old_id = data.get('id', None)
    if old_id == new_id:
        return  # 已正确，跳过

    # 插入 id 作为第一个字段（保留顺序美观）
    new_data = {'id': new_id}
    new_data.update(data)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def write_sprites_ability_id(ability_map: dict[str, int]) -> None:
    """为精灵 JSON 写入 ability_id 字段。"""
    count = 0
    for path in SPRITE_DIR.glob('*.json'):
        if path.name.startswith('_'):
            continue
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        ability = data.get('ability', '').strip()
        if not ability:
            continue
        trait_id = ability_map.get(ability)
        if trait_id is None:
            continue
        old = data.get('ability_id', 0)
        if old == trait_id:
            continue
        new_data = {}
        for k, v in data.items():
            new_data[k] = v
            if k == 'ability':
                new_data['ability_id'] = trait_id
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        count += 1
    print(f'  精灵 ability_id: {count} 个已更新')


def generate_constants(skill_map: dict[str, int],
                       trait_map: dict[str, int]) -> str:
    """生成 skill_trait_ids.py 内容。"""
    lines = [
        '# -*- coding: utf-8 -*-',
        '"""技能和特性数字 ID 常量。由 scripts/build/assign_ids.py 自动生成。"""',
        '',
        '# ── 技能 ID ──',
    ]
    for name, sid in sorted(skill_map.items(), key=lambda x: x[1]):
        safe = name.replace('(', '_').replace(')', '_').replace('（', '_').replace('）', '_')
        lines.append(f'SKILL_{safe} = {sid}')

    lines += ['', '# ── 特性 ID ──']
    for name, tid in sorted(trait_map.items(), key=lambda x: x[1]):
        safe = name.replace('(', '_').replace(')', '_').replace('（', '_').replace('）', '_')
        lines.append(f'TRAIT_{safe} = {tid}')

    lines += ['', '', '# ── 名称→ID 映射 ──', '']
    lines.append('SKILL_NAME_TO_ID: dict[str, int] = {')
    for name, sid in sorted(skill_map.items(), key=lambda x: x[0]):
        lines.append(f'    {name!r}: {sid},')
    lines.append('}')

    lines += ['', 'SKILL_ID_TO_NAME: dict[int, str] = {']
    for name, sid in sorted(skill_map.items(), key=lambda x: x[1]):
        lines.append(f'    {sid}: {name!r},')
    lines.append('}')

    lines += ['', 'TRAIT_NAME_TO_ID: dict[str, int] = {']
    for name, tid in sorted(trait_map.items(), key=lambda x: x[0]):
        lines.append(f'    {name!r}: {tid},')
    lines.append('}')

    lines += ['', 'TRAIT_ID_TO_NAME: dict[int, str] = {']
    for name, tid in sorted(trait_map.items(), key=lambda x: x[1]):
        lines.append(f'    {tid}: {name!r},')
    lines.append('}')

    return '\n'.join(lines) + '\n'


def main():
    print('=== 技能/特性 ID 分配 ===')
    print()

    # ── 技能 ──
    print('分配技能 ID...')
    skill_map = assign_skill_ids()
    print(f'  技能总数: {len(skill_map)}')
    print(f'  范围: {min(skill_map.values())} ~ {max(skill_map.values())}')

    wiki_count = len([n for n in skill_map if not is_test_skill(n)])
    test_count = len([n for n in skill_map if is_test_skill(n)])
    print(f'  正式: {wiki_count}, 测试: {test_count}')

    # 写入技能 JSON
    print('  写入技能 JSON...')
    for name, sid in skill_map.items():
        path = SKILL_DIR / f'{name}.json'
        if path.exists():
            write_json_id(path, sid)
    print(f'  {len(skill_map)} 个技能 JSON 已更新')

    # ── 特性 ──
    print()
    print('分配特性 ID...')
    trait_map = assign_trait_ids()
    print(f'  特性总数: {len(trait_map)}')
    print(f'  范围: {min(trait_map.values())} ~ {max(trait_map.values())}')

    print('  写入特性 JSON...')
    for name, tid in trait_map.items():
        path = TRAIT_DIR / f'{name}.json'
        if path.exists():
            write_json_id(path, tid)
    print(f'  {len(trait_map)} 个特性 JSON 已更新')

    # ── 精灵 ability_id ──
    print()
    print('更新精灵 ability_id...')
    write_sprites_ability_id(trait_map)

    # ── 索引文件 ──
    print()
    skill_ids = {name: sid for name, sid in skill_map.items()}
    skill_by_id = {str(sid): {'name': name, 'file': f'{name}.json'}
                   for name, sid in skill_map.items()}

    with open(SKILL_DIR / '_ids.json', 'w', encoding='utf-8') as f:
        json.dump({'ids': skill_ids, 'by_id': skill_by_id}, f,
                  ensure_ascii=False, indent=2)
    print(f'  技能索引: data/skills/_ids.json')

    trait_ids = {name: tid for name, tid in trait_map.items()}
    trait_by_id = {str(tid): {'name': name, 'file': f'{name}.json'}
                   for name, tid in trait_map.items()}

    with open(TRAIT_DIR / '_ids.json', 'w', encoding='utf-8') as f:
        json.dump({'ids': trait_ids, 'by_id': trait_by_id}, f,
                  ensure_ascii=False, indent=2)
    print(f'  特性索引: data/traits/_ids.json')

    # ── 常量文件 ──
    const_path = BASE / 'scripts' / 'common' / 'skill_trait_ids.py'
    content = generate_constants(skill_map, trait_map)
    with open(const_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  常量文件: scripts/common/skill_trait_ids.py')

    print()
    print('=== 完成 ===')


if __name__ == '__main__':
    main()
