"""
试点：为光系精灵和技能页面添加 YAML frontmatter
用于 Obsidian Bases 动态视图
"""
import re
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def has_frontmatter(text):
    return text.startswith('---\n')


def strip_existing_frontmatter(text):
    """剥离所有 frontmatter 层（递归式），直到正文"""
    while text.startswith('---\n'):
        end = text.find('\n---\n', 3)
        if end != -1:
            text = text[end + 5:]
        else:
            break
    return text


def parse_pokemon(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    # 如果已有 frontmatter，剥离后重新生成
    body = strip_existing_frontmatter(text)

    # 编号
    m = re.search(r'\*\*编号\*\*[：:]\s*`(\d+)`', body)
    number = int(m.group(1)) if m else None

    # 名称 — 从 # 行取
    m = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
    name = m.group(1).strip().strip('*') if m else None

    # 属性 — 可能单属性或双属性（"光" 或 "光、 地"）
    m = re.search(r'\*\*属性\*\*[：:]\s*`(.+?)`', body)
    attrs_raw = m.group(1) if m else ''
    attributes = [a.strip() for a in re.split(r'[、,，]', attrs_raw) if a.strip()]

    # 种族值合计
    m = re.search(r'##\s*种族值[：:]\s*(\d+)', body)
    total_stats = int(m.group(1)) if m else None

    # 六维种族值
    stats = {}
    table_pattern = r'\*\*生命\*\*\s*\|\s*\*\*(\d+)\*\*.*?\*\*物攻\*\*\s*\|\s*\*\*(\d+)\*\*.*?\*\*魔攻\*\*\s*\|\s*\*\*(\d+)\*\*.*?\*\*物防\*\*\s*\|\s*\*\*(\d+)\*\*.*?\*\*魔防\*\*\s*\|\s*\*\*(\d+)\*\*.*?\*\*速度\*\*\s*\|\s*\*\*(\d+)\*\*'
    m = re.search(table_pattern, body, re.DOTALL)
    if m:
        stats = {
            'hp': int(m.group(1)),
            'atk': int(m.group(2)),
            'sp_atk': int(m.group(3)),
            'def': int(m.group(4)),
            'sp_def': int(m.group(5)),
            'speed': int(m.group(6)),
        }

    # 特性名称 — [[../对战机制/特性/XXX|显示名]]
    m = re.search(r'\*\*\[\[\.\./\.\./对战机制/特性/[^|]+\|([^\]]+)\]\]\*\*', body)
    ability = m.group(1) if m else None

    # 构建 frontmatter
    lines = ['---']
    if number is not None:
        lines.append(f'number: {number}')
    if name:
        lines.append(f'name: "{name}"')
    if attributes:
        lines.append(f'attributes: [{", ".join(attributes)}]')
    if total_stats is not None:
        lines.append(f'total_stats: {total_stats}')
    for k in ['hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed']:
        if k in stats:
            lines.append(f'{k}: {stats[k]}')
    if ability:
        lines.append(f'ability: "{ability}"')
    lines.append('---')
    lines.append('')

    frontmatter = '\n'.join(lines)
    return frontmatter + body


def parse_skill(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    body = strip_existing_frontmatter(text)

    # 名称
    m = re.search(r'^#\s+\*\*(.+?)\*\*', body)
    name = m.group(1).strip() if m else None

    # 属性
    m = re.search(r'\*\*属性[：:]\s*(.+?)\*\*', body)
    attribute = m.group(1).strip() if m else None

    # 类型
    m = re.search(r'\*\*类型[：:]\s*(.+?)\*\*', body)
    skill_type = m.group(1).strip() if m else None

    # 威力
    m = re.search(r'\*\*威力[：:]\s*(\d+)\s*\*\*', body)
    power = int(m.group(1)) if m else None

    # 耗能
    m = re.search(r'\*\*耗能[：:]\s*(\d+)\s*\*\*', body)
    energy_cost = int(m.group(1)) if m else None

    # 应对
    m = re.search(r'\*\*应对[：:]\s*(.+?)\s*\*\*', body)
    counter = m.group(1).strip() if m else None

    # 描述 — **描述：**`内容`
    m = re.search(r'\*\*描述[：:]\*\*`(.+?)`', body)
    description = m.group(1).strip() if m else None

    lines = ['---']
    if name:
        lines.append(f'name: "{name}"')
    if attribute:
        lines.append(f'attribute: {attribute}')
    if skill_type:
        lines.append(f'type: {skill_type}')
    if power is not None:
        lines.append(f'power: {power}')
    if energy_cost is not None:
        lines.append(f'energy_cost: {energy_cost}')
    if counter:
        lines.append(f'counter: {counter}')
    if description:
        # 避免 YAML 特殊字符问题
        escaped = description.replace('"', '\\"')
        lines.append(f'description: "{escaped}"')
    lines.append('---')
    lines.append('')

    frontmatter = '\n'.join(lines)
    return frontmatter + body


def main():
    # 用法: python scripts/tools/add_frontmatter_pilot.py [属性名]
    # 默认值: "光"
    attr = sys.argv[1] if len(sys.argv) > 1 else "光"

    pokemon_dir = os.path.join(BASE, 'wiki', '精灵图鉴', attr)
    skill_dir = os.path.join(BASE, 'wiki', '技能图鉴', attr)

    if not os.path.isdir(pokemon_dir):
        print(f'⚠ 目录不存在: 精灵图鉴/{attr}')
    else:
        for fname in sorted(os.listdir(pokemon_dir)):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(pokemon_dir, fname)
            content = parse_pokemon(fpath)
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'  ✓ 精灵 {fname}')

    if not os.path.isdir(skill_dir):
        print(f'⚠ 目录不存在: 技能图鉴/{attr}')
    else:
        for fname in sorted(os.listdir(skill_dir)):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(skill_dir, fname)
            content = parse_skill(fpath)
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'  ✓ 技能 {fname}')

    print(f'\n系别 [{attr}] 处理完成！')


if __name__ == '__main__':
    main()
