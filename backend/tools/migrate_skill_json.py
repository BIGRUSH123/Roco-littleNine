"""一次性迁移脚本：将 data/skills/*.json 对齐 SKILL_JSON_GUIDE.md 规范。

1. main_axis → transmission=-1（删除 main_axis 字段）
2. 防御技能 combo:1 → combo:-1
"""

import json
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'skills'


def migrate_file(path: Path) -> list[str]:
    """迁移单个 JSON 文件。返回变更描述列表。"""
    data = json.loads(path.read_text('utf-8'))
    changes = []

    # 1. main_axis → transmission
    main_axis = data.pop('main_axis', None)
    if main_axis is True:
        data['transmission'] = -1
        changes.append('main_axis=true → transmission=-1')

    # 2. 防御技能 combo:1 → combo:-1
    if data.get('skill_type') == '防御' and data.get('combo') == 1:
        data['combo'] = -1
        changes.append('combo:1 → combo:-1')

    if changes:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', 'utf-8')

    return changes


def main():
    total = 0
    changed = 0
    all_changes: list[str] = []

    for p in sorted(SKILLS_DIR.glob('*.json')):
        if p.name.startswith('_'):
            continue
        total += 1
        changes = migrate_file(p)
        if changes:
            changed += 1
            all_changes.append(f'{p.name}: {", ".join(changes)}')

    print(f'扫描: {total} 个技能文件')
    print(f'修改: {changed} 个文件')
    for c in all_changes:
        print(f'  {c}')


if __name__ == '__main__':
    main()
