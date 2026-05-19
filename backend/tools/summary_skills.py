"""技能汇总表：按属性分组输出所有技能的关键字段，方便快速扫描异常。

输出到终端，可重定向到文件: python summary_skills.py > skills_table.txt
"""

import json
from pathlib import Path
from collections import defaultdict

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'skills'


def main():
    by_element = defaultdict(list)

    for f in sorted(SKILLS_DIR.glob('*.json')):
        if f.name.startswith('_'):
            continue
        data = json.loads(f.read_text('utf-8'))
        by_element[data.get('element', '?')].append(data)

    for element in sorted(by_element):
        skills = sorted(by_element[element], key=lambda s: s.get('id', 0))
        print(f'\n═══ {element} ({len(skills)}个) ═══')
        print(f'{"ID":>6} {"名称":<12} {"类型":<6} {"威力":>4} {"能耗":>4} {"应对":<4} {"combo":>5} {"传动":>4} {"效果数":>5} {"异常标记":<20}')
        print('-' * 85)

        for s in skills:
            effects = s.get('effects', [])
            n_eff = len(effects)

            # 异常标记
            warnings = []
            if s.get('main_axis'):
                warnings.append('旧main_axis')
            if s.get('skill_type') == '防御' and s.get('combo', -1) != -1:
                warnings.append(f'防御未设combo=-1')
            if s.get('skill_type') in ('物攻', '魔攻', '动态攻击') and s.get('combo', -1) == -1:
                warnings.append('攻击技combo=-1?')
            if not effects:
                warnings.append('无效果')
            warn_str = ', '.join(warnings) if warnings else '✓'

            print(
                f'{s.get("id",0):>6} '
                f'{s["name"]:<12} '
                f'{s.get("skill_type","?"):<6} '
                f'{s.get("power",0):>4} '
                f'{s.get("energy_cost",0):>4} '
                f'{s.get("counter","无"):<4} '
                f'{s.get("combo",-1):>5} '
                f'{s.get("transmission",0):>4} '
                f'{n_eff:>5} '
                f'{warn_str:<20}'
            )


if __name__ == '__main__':
    main()
