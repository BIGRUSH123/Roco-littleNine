"""scripts/sim/__main__.py — 人机对战 CLI

用法:
  python -m scripts.sim                        # 人 vs AI
  python -m scripts.sim --ai-only              # AI vs AI 观战
"""

import re
import sys
from pathlib import Path

from .factory import SimFactory
from .agent import HumanAgent, RuleAgent
from .battle import Battle
from .player import Item, PlayStyle

BASE = Path(__file__).resolve().parent.parent.parent
WIKI_ROOT = BASE / 'wiki'
SKILLS_DIR = BASE / 'data' / 'skills'


def _load_sprite_skills() -> dict[str, list[str]]:
    """从 wiki 精灵图鉴提取每只精灵的技能列表，只保留有 JSON 的技能。"""
    available_skills = {p.stem for p in SKILLS_DIR.glob('*.json')}

    sprite_skills: dict[str, list[str]] = {}
    sprite_dir = WIKI_ROOT / '精灵图鉴'
    if not sprite_dir.is_dir():
        return sprite_skills

    for md in sprite_dir.rglob('*.md'):
        if md.name.startswith('_') or md.stem == 'index':
            continue
        text = md.read_text(encoding='utf-8', errors='ignore')

        fm_match = re.search(r'^name:\s*"(.+?)"', text, re.MULTILINE)
        sprite_name = fm_match.group(1) if fm_match else md.stem

        skills: list[str] = []
        in_section = False
        for line in text.split('\n'):
            if re.match(r'## 技能', line):
                in_section = True
                continue
            if in_section:
                if line.startswith('## '):
                    break
                m = re.search(r'\[(?:[*]*)([^]]+?)(?:[*]*)\]\(', line)
                if m:
                    name = m.group(1).strip('*')
                    if name in available_skills:
                        skills.append(name)

        if skills:
            sprite_skills[sprite_name] = skills

    return sprite_skills


def _pick_sprites(sprite_skills: dict[str, list[str]], player_name: str,
                  count: int = 3) -> list[dict]:
    """交互式组队：先选精灵，再为该精灵选技能。"""
    available = sorted(sprite_skills.keys())
    if not available:
        print('无可用精灵')
        return []

    print(f'\n{player_name} 组队 (选{count}只):')
    print(f'  共 {len(available)} 只精灵可选')

    team: list[dict] = []
    while len(team) < count:
        # ── 选精灵 ──
        print(f'\n--- 第{len(team)+1}只精灵 ---')
        page_size = 20
        total_pages = (len(available) + page_size - 1) // page_size
        page = 0
        while True:
            start = page * page_size
            end = min(start + page_size, len(available))
            for i in range(start, end):
                skills_preview = ', '.join(sprite_skills[available[i]][:5])
                if len(sprite_skills[available[i]]) > 5:
                    skills_preview += f' ...(+{len(sprite_skills[available[i]])-5})'
                print(f'  [{i:3d}] {available[i]:　<10s}  技能: {skills_preview}')
            if total_pages > 1:
                print(f'  --- 第{page+1}/{total_pages}页 (n=下一页, p=上一页) ---')

            try:
                raw = input('选择精灵编号 > ').strip()
            except EOFError:
                print('\n  非交互模式')
                return []

            if raw.lower() == 'n' and page < total_pages - 1:
                page += 1; continue
            if raw.lower() == 'p' and page > 0:
                page -= 1; continue

            try:
                idx = int(raw)
                if 0 <= idx < len(available):
                    sprite_name = available[idx]
                    break
                print(f'  超出范围，输入 0-{len(available)-1}')
            except ValueError:
                print('  请输入数字 (或 n/p 翻页)')

        # ── 选技能 ──
        skill_pool = sprite_skills[sprite_name]
        print(f'\n  {sprite_name} 可学技能 ({len(skill_pool)}个):')
        for i, s in enumerate(skill_pool):
            print(f'    [{i:2d}] {s}')

        selected_skills: list[str] = []
        while len(selected_skills) < 4:
            try:
                raw = input(f'  选择技能 {len(selected_skills)+1}/4 (回车结束) > ').strip()
            except EOFError:
                break
            if raw == '':
                if selected_skills:
                    break
                print('  至少选1个技能')
                continue
            try:
                idx = int(raw)
                if 0 <= idx < len(skill_pool):
                    skill = skill_pool[idx]
                    if skill in selected_skills:
                        print(f'  已选过 {skill}')
                    else:
                        selected_skills.append(skill)
                        print(f'  + {skill} ({len(selected_skills)}/4)')
                else:
                    print(f'  超出范围，输入 0-{len(skill_pool)-1}')
            except ValueError:
                print('  请输入数字')

        if selected_skills:
            team.append({'name': sprite_name, 'skills': selected_skills})
            print(f'  OK {sprite_name}: {", ".join(selected_skills)}')
        else:
            print('  跳过该精灵')

    return team


def main() -> None:
    ai_only = '--ai-only' in sys.argv

    factory = SimFactory()

    if ai_only:
        # AI vs AI 观战
        p1 = factory.build_player('红AI', [
            {'name': '迪莫', 'skills': ['闪击', '冥想']},
            {'name': '火神', 'skills': ['闪击']},
        ], style=PlayStyle(aggression=0.8))
        p2 = factory.build_player('蓝AI', [
            {'name': '水灵', 'skills': ['冰雹', '冥想']},
            {'name': '迪莫', 'skills': ['闪击']},
        ], style=PlayStyle(aggression=0.3))
        a1 = RuleAgent('A', p1)
        a2 = RuleAgent('B', p2)
    else:
        print('═' * 40)
        print('  格斗小九 PVP 模拟器 — 人机对战')
        print('═' * 40)

        # 加载精灵技能映射
        sprite_skills = _load_sprite_skills()
        if not sprite_skills:
            print('错误: 无可用精灵/技能数据')
            sys.exit(1)

        # 玩家组队（选精灵 + 选技能）
        team = _pick_sprites(sprite_skills, '你的队伍')

        p1 = factory.build_player('你', team, item=Item.leader())
        p2 = factory.build_player('AI', [
            {'name': '迪莫', 'skills': ['闪击', '冥想']},
            {'name': '火神', 'skills': ['闪击']},
        ], style=PlayStyle(aggression=0.4), item=Item.wish())
        a1 = HumanAgent('A', p1, name='你')
        a2 = RuleAgent('B', p2)

    battle = Battle(p1, p2)

    try:
        winner = battle.run(a1, a2)
        print(f'\n对局结束: {winner or "平局"} 胜')
    except (KeyboardInterrupt, EOFError):
        print('\n对局中断')


if __name__ == '__main__':
    main()
