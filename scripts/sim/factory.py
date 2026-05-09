"""scripts/sim/factory.py — 从 wiki 数据构建模拟对象"""

import json
import sys
from pathlib import Path
from typing import Optional

from scripts.common.sprite_db import SpriteDB

from .sprite import Sprite
from .skill import Skill
from .battleskill import BattleSkill
from .player import Item, Player, PlayStyle
from .battle import Battle

BASE = Path(__file__).resolve().parent.parent.parent


class SimFactory:
    """从 wiki 数据创建战斗对象。运行时技能从 JSON 加载。"""

    def __init__(self):
        wiki_root = BASE / 'wiki'
        self.sprite_db = SpriteDB(wiki_root)
        self._skills_dir = BASE / 'data' / 'skills'

    # ── 精灵 ──

    def build_sprite(
        self, name: str, skills: list[str],
        nature: str | None = None,
        iv: dict[str, int] | None = None,
        form: str = '',
    ) -> Sprite:
        """从精灵名 + 技能列表构建 Sprite。"""
        species = self.sprite_db.get(name, form)
        if not species:
            raise ValueError(f'精灵未找到: {name!r}')

        from scripts.common.formulas import StatsCalc
        calc = StatsCalc()
        result = calc.compute(
            species, nature=nature,
            iv=iv or {k: 0 for k in ['hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed']},
        )

        sprite = Sprite.from_result(result)
        sprite.skills = self._build_skill_list(skills)
        return sprite

    def _build_skill_list(self, skill_names: list[str]) -> list[BattleSkill]:
        skills: list[BattleSkill] = []
        for name in skill_names:
            path = self._skills_dir / f'{name}.json'
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                skills.append(BattleSkill(base=Skill.load(data)))
            else:
                print(f'[SimFactory] 技能JSON未找到: {name!r}', file=sys.stderr)
        return skills

    # ── 玩家 ──

    def build_player(
        self, name: str, team_specs: list[dict],
        style: PlayStyle | None = None,
        lives: int = 4,
        item: 'Item | None' = None,
    ) -> Player:
        """从队伍规格列表构建 Player。"""
        sprites: list[Sprite] = []
        for spec in team_specs:
            sprite = self.build_sprite(
                name=spec['name'],
                skills=spec.get('skills', []),
                nature=spec.get('nature'),
                iv=spec.get('iv'),
                form=spec.get('form', ''),
            )
            sprites.append(sprite)

        return Player(
            name=name, team=sprites,
            style=style or PlayStyle(), lives=lives,
            item=item,
        )

    # ── 对局 ──

    def build_battle(
        self, player_a: Player, player_b: Player,
        weather: str = '',
    ) -> Battle:
        return Battle(player_a=player_a, player_b=player_b, weather=weather)

    @classmethod
    def default_style(cls, archetype: str = 'balanced') -> PlayStyle:
        """预设操作风格。"""
        styles = {
            'aggressive': PlayStyle(
                aggression=0.9, switch_hp_threshold=0.15,
                risk_tolerance=0.8, prefer_first_strike=0.7,
            ),
            'defensive': PlayStyle(
                aggression=0.2, switch_hp_threshold=0.5,
                risk_tolerance=0.3, prefer_first_strike=0.3,
            ),
            'balanced': PlayStyle(
                aggression=0.5, switch_hp_threshold=0.3,
                risk_tolerance=0.5, prefer_first_strike=0.5,
            ),
            'cautious': PlayStyle(
                aggression=0.3, switch_hp_threshold=0.6,
                risk_tolerance=0.15, prefer_first_strike=0.2,
            ),
        }
        return styles.get(archetype, styles['balanced'])
