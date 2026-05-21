"""backend/sim/factory.py — 从 wiki 数据构建模拟对象"""

import json
import sys
from pathlib import Path

from backend.common.sprite_db import SpriteDB

from .battle import Battle
from .battleskill import BattleSkill
from .player import Item, Player, PlayStyle
from .skill import Skill
from .sprite import Sprite

BASE = Path(__file__).resolve().parent.parent.parent

MAX_SKILL_SLOTS = 10


class SimFactory:
    """从 wiki 数据创建战斗对象。运行时技能从 JSON 加载。"""

    def __init__(self):
        self.sprite_db = SpriteDB(BASE)
        self._skills_dir = BASE / 'data' / 'skills'
        self._skills_by_id: dict[int, Skill] = {}
        self._skills_by_name: dict[str, Skill] = {}

    def get_skill_by_id(self, skill_id: int) -> Skill | None:
        """按 ID 查找技能（惰性加载）。"""
        if skill_id in self._skills_by_id:
            return self._skills_by_id[skill_id]
        # 尝试从索引查找名称
        try:
            from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
            name = SKILL_ID_TO_NAME.get(skill_id)
            if name:
                path = self._skills_dir / f'{name}.json'
                if path.exists():
                    data = json.loads(path.read_text(encoding='utf-8'))
                    skill = Skill.load(data)
                    self._skills_by_id[skill_id] = skill
                    self._skills_by_name[name] = skill
                    return skill
        except ImportError:
            pass
        return None

    def get_skill_by_name(self, name: str) -> Skill | None:
        """按名称查找技能（惰性加载）。"""
        if name in self._skills_by_name:
            return self._skills_by_name[name]
        path = self._skills_dir / f'{name}.json'
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            skill = Skill.load(data)
            self._skills_by_name[name] = skill
            if skill.id:
                self._skills_by_id[skill.id] = skill
            return skill
        return None

    # ── 精灵 ──

    def build_sprite(
        self, name: str, skills: list[str],
        nature: str | None = None,
        iv: dict[str, int] | None = None,
        form: str = '',
        bloodline: str | None = None,
    ) -> Sprite:
        """从精灵名 + 技能列表构建 Sprite。"""
        species = self.sprite_db.get(name, form)
        if not species:
            raise ValueError(f'精灵未找到: {name!r}')

        from backend.common.formulas import StatsCalc
        calc = StatsCalc()
        result = calc.compute(
            species, nature=nature,
            iv=iv or {k: 0 for k in ['hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed']},
        )

        sprite = Sprite.from_result(result)
        sprite.skills = self._build_skill_list(skills)
        if bloodline and bloodline in sprite.bloodline_skills:
            sprite.bloodline = bloodline
        elif species.elements:
            sprite.bloodline = species.elements[0]
        return sprite

    def _build_skill_list(self, skill_names: list[str]) -> list[BattleSkill]:
        skills: list[BattleSkill] = []
        for name in skill_names:
            if len(skills) >= MAX_SKILL_SLOTS:
                print(f'[SimFactory] 技能槽位已达上限({MAX_SKILL_SLOTS}), 跳过: {name!r}', file=sys.stderr)
                break
            path = self._skills_dir / f'{name}.json'
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                # Build minimal Skill (metadata only, no effects) — RISC IR
                # effects are served by CompiledSkill, not Skill.effects
                skill = Skill(
                    id=data.get('id', 0),
                    name=data['name'],
                    element=data.get('element', ''),
                    skill_type=data.get('skill_type', ''),
                    power=data.get('power', 0),
                    energy_cost=data.get('energy_cost', 0),
                    counter=data.get('counter', '无'),
                    priority=data.get('priority', 0),
                    combo=data.get('combo', 1),
                    effects=[],  # effects come from CompiledSkill
                    exclusive_to=data.get('exclusive_to', ''),
                    transmission=data.get('transmission', 0),
                    description=data.get('description', ''),
                    usable_while_charging=data.get('usable_while_charging', False),
                )
                skills.append(BattleSkill(base=skill))
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
                bloodline=spec.get('bloodline'),
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
        battle = Battle(player_a=player_a, player_b=player_b, weather=weather)
        battle.species_db = self.sprite_db
        battle.skill_loader = self._build_skill_list
        return battle

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
