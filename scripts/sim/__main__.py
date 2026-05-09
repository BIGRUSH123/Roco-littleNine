"""scripts/sim/__main__.py — 人机对战 CLI

用法:
  python -m scripts.sim                        # 人 vs AI
  python -m scripts.sim --ai-only              # AI vs AI 观战
  python -m scripts.sim --log battle_log.md    # 保存对局记录
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


def _load_sprite_skills() -> list[tuple[int, str, list[str]]]:
    """从 wiki 精灵图鉴提取精灵→技能映射，按编号排序。
    返回 [(编号, 精灵名, [技能]), ...]。
    """
    available_skills = {p.stem for p in SKILLS_DIR.glob('*.json')}

    entries: list[tuple[int, str, list[str]]] = []
    sprite_dir = WIKI_ROOT / '精灵图鉴'
    if not sprite_dir.is_dir():
        return entries

    for md in sprite_dir.rglob('*.md'):
        if md.name.startswith('_') or md.stem == 'index':
            continue
        text = md.read_text(encoding='utf-8', errors='ignore')

        num_m = re.search(r'^number:\s*(\d+)', text, re.MULTILINE)
        num = int(num_m.group(1)) if num_m else 9999
        name_m = re.search(r'^name:\s*"(.+?)"', text, re.MULTILINE)
        sprite_name = name_m.group(1) if name_m else md.stem

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
            entries.append((num, sprite_name, skills))

    entries.sort(key=lambda x: x[0])
    return entries


def _pick_sprites(entries: list[tuple[int, str, list[str]]], player_name: str,
                  count: int = 3) -> list[dict]:
    """交互式组队：先选精灵，再为该精灵选技能。精灵按编号排序。"""
    if not entries:
        print('无可用精灵')
        return []

    page_size = 20
    total_pages = (len(entries) + page_size - 1) // page_size

    print(f'\n{player_name} 组队 (选{count}只):')
    print(f'  共 {len(entries)} 只精灵可选 ({total_pages} 页)')

    team: list[dict] = []
    while len(team) < count:
        print(f'\n--- 第{len(team)+1}只精灵 ---')
        page = 0
        while True:
            start = page * page_size
            end = min(start + page_size, len(entries))
            print(f'  [第{page+1}/{total_pages}页]')
            for i in range(start, end):
                num, name, skills = entries[i]
                skills_preview = ', '.join(skills[:5])
                if len(skills) > 5:
                    skills_preview += f' ...(+{len(skills)-5})'
                print(f'  [{i:3d}] #{num:03d} {name:　<10s}  技能: {skills_preview}')

            try:
                raw = input('选择编号/翻页(n/p) > ').strip()
            except EOFError:
                print('\n  非交互模式')
                return []

            r = raw.lower()
            if r == 'n':
                if page < total_pages - 1:
                    page += 1
                else:
                    print(f'  已是最后一页')
                continue
            if r == 'p':
                if page > 0:
                    page -= 1
                else:
                    print(f'  已是第一页')
                continue

            try:
                idx = int(raw)
                if 0 <= idx < len(entries):
                    num, sprite_name, skill_pool = entries[idx]
                    break
                print(f'  超出范围，输入 0-{len(entries)-1}')
            except ValueError:
                print('  请输入数字 (或 n/p 翻页)')

        # ── 选技能 ──
        print(f'\n  #{num:03d} {sprite_name} 可学技能 ({len(skill_pool)}个):')
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
            print(f'  OK #{num:03d} {sprite_name}: {", ".join(selected_skills)}')
        else:
            print('  跳过该精灵')

    return team


def main() -> None:
    ai_only = '--ai-only' in sys.argv

    # --log <path>
    log_path = ''
    for i, arg in enumerate(sys.argv):
        if arg == '--log' and i + 1 < len(sys.argv):
            log_path = sys.argv[i + 1]
            break

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
        entries = _load_sprite_skills()
        if not entries:
            print('错误: 无可用精灵/技能数据')
            sys.exit(1)

        # 玩家组队（选精灵 + 选技能）
        team = _pick_sprites(entries, '你的队伍')

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

    if log_path and battle.log:
        battle.save_log(log_path)
        print(f'对局记录已保存: {log_path}')


if __name__ == '__main__':
    main()
