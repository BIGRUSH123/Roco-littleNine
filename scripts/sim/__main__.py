"""scripts/sim/__main__.py — 人机对战 CLI

用法:
  python -m scripts.sim                        # 人 vs AI
  python -m scripts.sim --ai-only              # AI vs AI 观战
"""

import sys
from .factory import SimFactory
from .agent import HumanAgent, RuleAgent
from .battle import Battle
from .player import Item, PlayStyle


def _get_player_team(name: str) -> list[dict]:
    """简易组队：预设几种精灵+技能。"""
    presets = {
        '迪莫':  ['闪击', '冥想', '聚能'],
        '火神':  ['闪击', '火柱', '冥想'],
        '水灵':  ['水枪', '冰雹', '冥想'],
        '空灵':  ['闪击', '冥想'],
    }
    # 让玩家选3只
    print(f'\n{name} 组队 (选3只):')
    available = list(presets.keys())
    for i, n in enumerate(available):
        print(f'  [{i}] {n} 技能: {", ".join(presets[n])}')

    team: list[dict] = []
    while len(team) < 3:
        try:
            raw = input(f'第{len(team)+1}只 > ')
        except EOFError:
            print('\n  非交互模式，使用默认队伍')
            return [
                {'name': '迪莫', 'skills': ['闪击', '冥想']},
                {'name': '火神', 'skills': ['闪击']},
                {'name': '水灵', 'skills': ['冰雹']},
            ]
        try:
            idx = int(raw.strip())
            if 0 <= idx < len(available):
                sprite_name = available[idx]
                team.append({'name': sprite_name, 'skills': presets[sprite_name]})
            else:
                print(f'  超出范围，输入 0-{len(available)-1}')
        except ValueError:
            print('  请输入数字')
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

        # 玩家组队
        team = _get_player_team('你的队伍')

        p1 = factory.build_player('你', team, item=Item.leader())
        p2 = factory.build_player('AI', [
            {'name': '水灵', 'skills': ['冰雹', '冥想']},
            {'name': '迪莫', 'skills': ['闪击']},
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
