"""交互式技能审查工具：逐页浏览 data/skills/*.json。

方向键 n/p 翻页，q 退出，j 跳转到指定编号。
"""

import json
import os
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'skills'

# 效果 kind 对应的简短标签
_KIND_LABEL = {
    'stat': '属', 'abnormal': '异', 'mark': '印',
    'weather': '天', 'conditional': '条',
}


def _effect_label(eff: dict) -> str:
    kind = eff.get('kind', '?')
    if kind == 'stat':
        s = eff.get('stat', '?')
        t = eff.get('target', '?')
        st = eff.get('steps', 0)
        return f'属:{t}.{s}({st:+d})'
    if kind == 'abnormal':
        return f'异:{eff.get("target","?")}.{eff.get("name","?")}x{eff.get("stacks",1)}'
    if kind == 'mark':
        return f'印:{eff.get("target","?")}.{eff.get("name","?")}x{eff.get("stacks",1)}'
    if kind == 'weather':
        return f'天:{eff.get("weather","?")} {eff.get("turns",0)}t'
    if kind == 'conditional':
        when = eff.get('when', {})
        then_n = len(eff.get('then', []))
        return f'条:{when.get("kind","?")}→{then_n}eff'
    # special / flat kind
    val = eff.get('value', 0) or eff.get('amount', 0)
    return f'{kind}:{val}' if val else kind


def review():
    files = sorted(
        [f for f in SKILLS_DIR.glob('*.json') if not f.name.startswith('_')],
        key=lambda f: f.stem,
    )
    total = len(files)
    idx = 0

    while 0 <= idx < total:
        os.system('cls' if os.name == 'nt' else 'clear')
        f = files[idx]
        data = json.loads(f.read_text('utf-8'))

        # 头部信息
        skill_type = data.get('skill_type', '?')
        counter = data.get('counter', '无')
        combo = data.get('combo', -1)
        transmission = data.get('transmission', 0)
        main_axis = data.get('main_axis', None)

        flags = []
        if transmission == -1 or main_axis:
            flags.append('主轴')
        if combo == -1:
            flags.append('无连击')
        if combo >= 2:
            flags.append(f'{combo}连击')
        if data.get('exclusive_to'):
            flags.append(f'专属:{data["exclusive_to"]}')
        flag_str = f' [{", ".join(flags)}]' if flags else ''

        print(f'━━━ [{idx+1}/{total}] ID:{data.get("id","?")} {data["name"]} ━━━')
        print(f'  属:{data.get("element","?")} | 类:{skill_type} | 威:{data.get("power",0)} '
              f'| 耗:{data.get("energy_cost",0)} | 应:{counter} | 优:{data.get("priority",0)}')
        print(f'  combo:{combo} | trans:{transmission}{flag_str}')
        desc = data.get('description', '')
        if desc:
            print(f'  📝 {desc[:80]}')

        # 效果列表
        effects = data.get('effects', [])
        if effects:
            print(f'  ⚡ 效果 ({len(effects)}):')
            for i, e in enumerate(effects):
                label = _effect_label(e)
                print(f'     [{i}] {label}')
        else:
            print(f'  ⚡ 无效果')

        print()
        print('  [n]下一页 [p]上一页 [j]跳转 [f]搜索 [q]退出')

        cmd = input('> ').strip().lower()

        if cmd == 'q':
            break
        elif cmd == 'n':
            idx = min(idx + 1, total - 1)
        elif cmd == 'p':
            idx = max(idx - 1, 0)
        elif cmd == 'j':
            try:
                target = int(input('跳转到第几个? '))
                idx = max(0, min(target - 1, total - 1))
            except ValueError:
                pass
        elif cmd == 'f':
            query = input('搜索技能名(支持部分匹配): ').strip()
            if query:
                for i, f2 in enumerate(files):
                    if query in f2.stem:
                        idx = i
                        break
                else:
                    print(f'  未找到匹配 "{query}" 的技能')
                    input('按回车继续...')
        elif cmd == '':
            idx = min(idx + 1, total - 1)  # 回车=下一页


if __name__ == '__main__':
    review()
