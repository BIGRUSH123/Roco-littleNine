"""backend/sim/resolver.py — 技能效果解析器

保留功能：应对判断、伤害计算（委托 vm/damage.py）、回合末结算。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.vm.effect import AbnormalEffect

if TYPE_CHECKING:
    from .battleskill import SkillUse
    from .globals import GlobalEffects
    from .skill import Skill
    from .sprite import Sprite


# 系别克制表（18 系）— 来源: wiki/对战机制/属性克制关系表.md
_TYPE_CHART: dict[str, dict[str, float]] = {
    '光': {'冰': 0.5, '幽': 2.0, '恶': 2.0, '翼': 0.5},
    '冰': {'冰': 0.5, '地': 2.0, '机械': 0.5, '火': 0.5, '翼': 2.0, '草': 2.0, '龙': 2.0},
    '地': {'冰': 2.0, '武': 0.5, '毒': 2.0, '火': 2.0, '电': 2.0, '草': 0.5},
    '幻': {'光': 0.5, '幻': 0.5, '机械': 0.5, '武': 2.0, '毒': 2.0},
    '幽': {'光': 2.0, '幻': 2.0, '幽': 2.0, '恶': 0.5, '普通': 0.5},
    '恶': {'光': 0.5, '幽': 2.0, '恶': 0.5, '武': 0.5, '毒': 2.0, '萌': 2.0},
    '普通': {'地': 0.5, '幽': 0.5, '机械': 0.5},
    '机械': {'冰': 2.0, '地': 2.0, '机械': 0.5, '水': 0.5, '火': 0.5, '电': 0.5, '萌': 2.0},
    '武': {'冰': 2.0, '地': 2.0, '幻': 0.5, '幽': 0.5, '恶': 2.0, '普通': 2.0, '机械': 2.0, '毒': 0.5, '翼': 0.5, '萌': 0.5, '虫': 0.5},
    '毒': {'地': 0.5, '幽': 0.5, '机械': 0.5, '毒': 0.5, '草': 2.0, '萌': 2.0},
    '水': {'冰': 0.5, '地': 2.0, '机械': 2.0, '火': 2.0, '草': 0.5, '龙': 0.5},
    '火': {'冰': 2.0, '地': 0.5, '机械': 2.0, '水': 0.5, '草': 2.0, '虫': 2.0, '龙': 0.5},
    '电': {'地': 0.5, '水': 2.0, '电': 0.5, '翼': 2.0, '草': 0.5, '龙': 0.5},
    '翼': {'地': 0.5, '机械': 0.5, '武': 2.0, '电': 0.5, '草': 2.0, '虫': 2.0, '龙': 0.5},
    '草': {'光': 2.0, '地': 2.0, '机械': 0.5, '毒': 0.5, '水': 2.0, '火': 0.5, '翼': 0.5, '萌': 0.5, '虫': 0.5, '龙': 0.5},
    '萌': {'恶': 2.0, '机械': 0.5, '武': 2.0, '毒': 0.5, '火': 0.5, '龙': 2.0},
    '虫': {'幻': 2.0, '幽': 0.5, '恶': 2.0, '机械': 0.5, '武': 0.5, '毒': 0.5, '火': 0.5, '翼': 0.5, '草': 2.0, '萌': 0.5},
    '龙': {'机械': 0.5, '龙': 2.0},
}

_STEP_PCT = 10  # 非速度六维：1步=10%


class SkillResolver:
    """技能效果解析器（无状态，纯方法）。"""

    @staticmethod
    def resolve_counter(atk_skill: Skill, def_skill: Skill) -> bool:
        """返回 def_skill 是否应对了 atk_skill。"""
        if def_skill.counter == '攻击' and atk_skill.is_attack:
            return True
        if def_skill.counter == '防御' and atk_skill.is_defense:
            return True
        return bool(def_skill.counter == '状态' and atk_skill.is_status)

    @staticmethod
    def calc_damage(
        attacker: Sprite, defender: Sprite,
        use: SkillUse, globals_: GlobalEffects,
        attacker_team: str = 'A',
    ) -> tuple[int, list[str]]:
        """伤害公式: 37/41 * atk/def * (威力*应对+固定) * 本系 * 克制 * 天气 * 减伤 * 修正 * 连击 * 倍率。

        收集输入后委托 vm/damage.calc_damage 执行核心运算。
        """
        from backend.vm.damage import calc_damage as _vm_damage

        events: list[str] = []
        bs = use.battle_skill

        keys = bs.get_atk_def_keys(attacker)
        if not keys:
            return 0, events

        atk_key, def_key = keys
        ignore_mods = use.modifiers.get('ignore_mods', False)

        atk_base = attacker.initial_stats.get(atk_key, 0)
        def_base = defender.initial_stats.get(def_key, 0)
        if atk_base <= 0 or def_base <= 0:
            return 0, events

        atk_steps = attacker._sum_steps(atk_key)
        def_steps = defender._sum_steps(def_key)
        if ignore_mods:
            atk_steps = max(0, atk_steps)
            def_steps = min(0, def_steps)
        atk_stage = atk_steps / _STEP_PCT
        def_stage = def_steps / _STEP_PCT

        additive_power = (
            attacker.power_mod * 10
            + globals_.mark_power_bonus(attacker_team, bs)
            + use.modifiers.get('power_bonus', 0)
        )

        type_mult = SkillResolver._get_type_mult(bs, attacker, defender)
        use.modifiers['type_mult'] = type_mult

        mark_mult = globals_.mark_damage_mult(attacker_team, use.is_first)
        mark_bonus = mark_mult - 1.0

        damage = _vm_damage(
            power=bs.power,
            atk_base=atk_base,
            def_base=def_base,
            atk_stage=atk_stage,
            def_stage=def_stage,
            stab_mult=SkillResolver._get_stab(bs, attacker),
            type_mult=type_mult,
            weather_mult=globals_.weather_damage_mult(bs.element or ''),
            damage_reduction=use.damage_reduction,
            power_mult=use.power_mult,
            counter_power_mult=use.counter_power_mult,
            additive_power=additive_power,
            damage_mult=use.damage_mult,
            combo_count=use.multi_hit,
            mark_bonus=mark_bonus,
        )
        return damage, events

    @staticmethod
    def _get_type_mult(skill: Skill, attacker: Sprite, defender: Sprite) -> float:
        elem = skill.element
        if not elem:
            return 1.0
        def_elems = defender.species.elements or tuple(
            e.strip() for e in (defender.species.attributes or '').split(',') if e.strip()
        )
        if not def_elems:
            return 1.0
        chart = _TYPE_CHART.get(elem, {})
        mult = 1.0
        for de in def_elems:
            mult *= chart.get(de, 1.0)
        return mult

    @staticmethod
    def _get_stab(skill: Skill, attacker: Sprite) -> float:
        elem = skill.element
        if not elem:
            return 1.0
        attrs = attacker.species.elements or tuple(
            e.strip() for e in (attacker.species.attributes or '').split(',') if e.strip()
        )
        if elem in attrs:
            return 1.25
        return 1.0

    _TICK_ELEMENT = {'灼烧': '火', '中毒': '毒', '寄生': '草'}

    @staticmethod
    def _tick_multiplier(sprite: Sprite, tick_name: str, element: str = '') -> float:
        """元素克制倍率用于异常 tick 伤害。"""
        elem = element or SkillResolver._TICK_ELEMENT.get(tick_name, '')
        if not elem:
            return 1.0
        attrs = sprite.species.elements or tuple(
            e.strip() for e in (getattr(sprite.species, 'attributes', '') or '').split(',') if e.strip()
        )
        mult = 1.0
        for attr in attrs:
            mult *= _TYPE_CHART.get(elem, {}).get(attr, 1.0)
        return mult

    @staticmethod
    def turn_end(
        sprites: dict[str, Sprite], globals_: GlobalEffects,
    ) -> list[str]:
        """回合末：异常tick + 冷却递减 + 印记 + 天气递减。"""

        events: list[str] = []
        all_sprites = list(sprites.values())
        cinder_grass_active: bool | None = None

        for s in all_sprites:
            if s.is_fainted:
                continue

            # Tick damage from AbnormalEffect in active_effects
            active = getattr(s, 'active_effects', None) or []

            for ae in active:
                if not isinstance(ae, AbnormalEffect):
                    continue
                if ae.stacks <= 0 or ae.tick_damage_pct <= 0:
                    continue

                name = ae.name
                stacks = ae.stacks
                pct = ae.tick_damage_pct
                raw = max(1, round(s.max_hp * pct * stacks)) if ae.tick_per_stack else max(1, round(s.max_hp * pct))
                mult = SkillResolver._tick_multiplier(s, name, ae.tick_element)
                dmg = max(1, round(raw * mult))
                actual = s.take_damage(dmg)
                s._last_abnormal_dmg[name] = actual
                events.append(f'{s.name} {name}-{actual}HP')

                if ae.decay_on_tick:
                    # 煤渣草：在场时灼烧衰减变为增长
                    if name == "灼烧" and cinder_grass_active is None:
                        cinder_grass_active = any(
                            sp._modifiers.get("_cinder_grass", False)
                            for sp in all_sprites if not sp.is_fainted
                        )
                    if name == "灼烧" and cinder_grass_active:
                        growth = ae.stacks // 2
                        ae.stacks += growth
                        s.update_stacks(name, ae.stacks)
                        events.append(f'{s.name} {name}增长至{ae.stacks}层')
                    else:
                        new_stacks = ae.apply_decay()
                        ae.stacks = new_stacks
                        s.update_stacks(name, new_stacks)
                        events.append(f'{s.name} {name}衰减至{new_stacks}层')

            for bs in s.skills:
                if bs.cooldown > 0:
                    bs.cooldown -= 1

        events += globals_.weather_turn_effects(all_sprites)
        globals_.tick_weather()
        events += globals_.mark_turn_end_effects(sprites)

        return events
