"""backend/sim/resolver.py — 技能效果解析器

保留功能：应对判断、伤害计算（委托 vm/damage.py）、回合末结算。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.vm.effect import AbnormalEffect
from backend.sim.traits import get_trait

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

#: 技能 IR 里「本次使用才生效」的伤害修正，按技能名缓存（估伤用）。
#: 元素 = (条件要求 tuple[(cond, 期望值), ...], kind, payload)，
#: kind ∈ {"add_power", "power_mult", "damage_mult"}。
_SAME_TURN_OPS: dict[str, tuple] = {}

#: `power_mod`/`mult_mod` 的 target 里，属于「当前使用的这个技能」的拼写
_SELF_SKILL_TARGETS = frozenset({"skill_off_0", "skill_self", "self_skill"})


def _cond_name(cond) -> str:
    if isinstance(cond, dict):
        return str(cond.get("cond", ""))
    return str(getattr(cond, "cond", "") or "")


def _collect_same_turn(node, reqs: tuple, out: list) -> None:
    """递归收集技能自身 IR 中影响本次伤害的修正（含 when 条件要求）。"""
    from backend.vm.ir_skill import MultModOp, PowerModOp, WhenBlock

    for item in node or ():
        if isinstance(item, WhenBlock):
            for cond, branch in [(item.cond, item.then)] + [
                    (b.cond, b.then) for b in item.elif_]:
                if _cond_name(cond) == "counter_succeeded":
                    continue          # 估伤按「未应对」口径：应对分支不计
                _collect_same_turn(branch, reqs + ((cond, True),), out)
            if item.else_:
                _collect_same_turn(item.else_, reqs + ((item.cond, False),), out)
        elif isinstance(item, PowerModOp):
            if (item.attr == "power" and (item.target or "") in _SELF_SKILL_TARGETS
                    and getattr(item, "mode", "add") == "add"):
                out.append((reqs, "add_power", item.delta))
        elif isinstance(item, MultModOp) and (item.target or "") in _SELF_SKILL_TARGETS:
            payload = (item.value, getattr(item, "mode", "set") or "set")
            if item.attr == "power_mult":
                out.append((reqs, "power_mult", payload))
            elif item.attr == "damage_mult":
                out.append((reqs, "damage_mult", payload))


def _same_turn_ops(battle, skill_name: str) -> tuple:
    """技能自身 IR 里「本次使用才生效」的伤害修正（按技能名缓存）。"""
    cached = _SAME_TURN_OPS.get(skill_name)
    if cached is not None:
        return cached
    ops: tuple = ()
    try:
        effects = battle._get_skill_record(skill_name).effects
    except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
        effects = ()
    if effects:
        found: list = []
        _collect_same_turn(effects, (), found)
        ops = tuple(found)
    _SAME_TURN_OPS[skill_name] = ops
    return ops


def _same_turn_modifiers(battle, bs, attacker, defender, use, globals_,
                         team: str) -> tuple[int, float, float]:
    """技能自身的同回合修正 → (power_add, power_mult, damage_mult)。

    实战路径：技能 effect 里的 `power_mod`/`mult_mod` 变成 ModifierInjection，
    由 `engine/modifiers` 汇总：`power`(add) → power_add → 按 `(power+add)/power`
    折进 power_mult；`power_mult` 按 mode 加/乘；`damage_mult` 一律相乘。
    这里用**引擎自己的 Ctx**（`Battle._make_ctx`）+ 引擎自己的条件求值
    （`vm/cond.compile_cond`）静态复算同一批修正——魔能爆「=@self.energy * 20」
    这类公式因此也能算；求值失败就不计（保守）。
    """
    if battle is None:
        return 0, 1.0, 1.0
    name = getattr(bs, 'name', '')
    if not name:
        return 0, 1.0, 1.0
    ops = _same_turn_ops(battle, name)
    if not ops:
        return 0, 1.0, 1.0
    try:
        # skill_index 传 -1（未知）时 `skill_at` 条件自然不成立 → 保守不计
        ctx = battle._make_ctx(attacker, defender, use.battle_skill, None, globals_,
                               team=team, skill_index=use.skill_index)
    except Exception:
        return 0, 1.0, 1.0

    from backend.vm.cond import compile_cond
    from backend.vm.resolve import resolve

    power_add = 0
    power_mult = 1.0
    damage_mult = 1.0
    for reqs, kind, payload in ops:
        met = True
        for cond, expected in reqs:
            try:
                if bool(compile_cond(cond)(ctx)) is not expected:
                    met = False
                    break
            except Exception:
                met = False      # 条件求值失败 → 保守不计
                break
        if not met:
            continue
        if kind == "add_power":
            try:
                power_add += int(resolve(ctx, payload))
            except Exception:
                continue
        else:
            value, mode = payload
            try:
                v = float(resolve(ctx, value))
            except Exception:
                continue
            if kind == "power_mult":
                power_mult = power_mult + v if mode == "add" else power_mult * v
            else:
                damage_mult *= v
    return power_add, power_mult, damage_mult


class SkillResolver:
    """技能效果解析器（纯方法；持 battle 引用只为估伤取同回合修正）。"""

    def __init__(self, battle=None) -> None:
        #: 估伤（calc_damage）需要 battle 才能取「技能自身的同回合修正」
        #: （IR 记录 + Ctx）。没有 battle 时退化为只读技能级/精灵级修正。
        self._battle = battle

    @staticmethod
    def resolve_counter(atk_skill: Skill, def_skill: Skill) -> bool:
        """返回 def_skill 是否应对了 atk_skill。"""
        if def_skill.counter == '攻击' and atk_skill.is_attack:
            return True
        if def_skill.counter == '防御' and atk_skill.is_defense:
            return True
        return bool(def_skill.counter == '状态' and atk_skill.is_status)

    def calc_damage(
        self,
        attacker: Sprite, defender: Sprite,
        use: SkillUse, globals_: GlobalEffects,
        attacker_team: str = 'A',
    ) -> tuple[int, list[str]]:
        """伤害公式: 37/41 * atk/def * (威力*应对+固定) * 本系 * 克制 * 天气 * 减伤 * 修正 * 连击 * 倍率。

        收集输入后委托 vm/damage.calc_damage 执行核心运算。

        输入口径与**实战**一致（`engine/snapshot.build_ctx` + `engine/modifiers`）：
        技能级与精灵级 `power_mult`/`damage_mult` 相加、防御方精灵级减伤、以及技能
        自身写在 `effects[]` 里的同回合 `power_mod`（如魔能爆「每消耗 1 点能量威力+20」）。
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

        # ── 技能级 / 精灵级倍率（与实战 snapshot 的合并公式一致）──
        # 此前只读 use.modifiers（旧 kind 层，IR 语料下恒空）→ 这两类修正对估伤
        # 完全不可见（实测：精灵级 power_mult=1.5 时实战 39→59、估伤恒 33）。
        skill_mods = getattr(bs, '_modifiers', None) or {}
        power_mult = (1.0
                      + (float(attacker.power_mult_modifier) - 1.0)
                      + (float(skill_mods.get('power_mult', 1.0) or 1.0) - 1.0)) * use.power_mult
        damage_mult = (1.0
                       + (float(attacker.damage_mult_modifier) - 1.0)
                       + (float(skill_mods.get('damage_mult', 1.0) or 1.0) - 1.0)) * use.damage_mult
        # 减伤：实战 op_hit 传的是**防御方**精灵级减伤（防御技在这一手之前已落地）
        damage_reduction = max(use.damage_reduction,
                               float(defender.damage_reduction_modifier))

        additive_power = (
            globals_.mark_power_bonus(attacker_team, bs)
            + use.modifiers.get('power_bonus', 0)
        )

        type_mult = SkillResolver._get_type_mult(bs, attacker, defender)
        use.modifiers['type_mult'] = type_mult

        mark_mult = globals_.mark_damage_mult(attacker_team, use.is_first)
        mark_bonus = mark_mult - 1.0

        # 连击 = 技能释放次数（含技能自身修正与门控后的精灵级增益/倍率），
        # 与引擎同口径：此前这里读 use.multi_hit（旧版 special，全库无数据），
        # 等于所有连击技能的估伤都少算了 N 倍（虫刺 3 连击：实战 39 / 估伤 13）
        from .battleskill import effective_combo
        combo_count = effective_combo(bs, attacker)

        damage = _vm_damage(
            power=bs.power,
            atk_base=atk_base,
            def_base=def_base,
            atk_stage=atk_stage,
            def_stage=def_stage,
            stab_mult=SkillResolver._get_stab(bs, attacker),
            type_mult=type_mult,
            weather_mult=globals_.weather_damage_mult(bs.element or ''),
            damage_reduction=damage_reduction,
            power_mult=power_mult,
            counter_power_mult=use.counter_power_mult,
            additive_power=additive_power,
            damage_mult=damage_mult,
            combo_count=combo_count,
            mark_bonus=mark_bonus,
        )

        # ── 技能自身的**同回合**修正：与 `engine/modifiers.adjust_damage` 同序 ──
        # 实战是先按基础威力算完伤害，再由 adjust_damage 乘折算倍率并取整
        # （`power_add` 折成 `(power+add)/power`）。这一「先算后乘」的次序对
        # 低威力技能影响很大：魔能爆 1 威力 +60 → 实战略 61（不是 41），
        # 所以这里也必须后乘而不是并进公式。
        power_add, st_power_mult, st_damage_mult = _same_turn_modifiers(
            self._battle, bs, attacker, defender, use, globals_, attacker_team)
        if power_add > 0 and bs.power > 0:
            st_power_mult *= (bs.power + power_add) / bs.power
        if st_power_mult != 1.0 or st_damage_mult != 1.0:
            damage = max(1, round(damage * st_power_mult * st_damage_mult))
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

            # 快照迭代：decay_on_tick 会把衰减到 0 层的效果从 active_effects
            # 移除（update_stacks），原列表迭代会因此跳过紧随其后的异常效果
            # （灼烧→中毒时中毒整轮不 tick）
            for ae in list(active):
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

                # 寄生等「吸取」类：伤害回补给施加方（游戏内文本「从寄生来源吸收」）
                if ae.absorb_to_source and actual > 0 and ae.origin_team:
                    source = sprites.get(ae.origin_team)
                    if source is not None and not source.is_fainted and source is not s:
                        healed = source.heal(actual)
                        if healed:
                            events.append(f'{source.name} 吸收+{healed}HP')

                if ae.decay_on_tick:
                    # 煤渣草：在场时灼烧衰减变为增长
                    if name == "灼烧" and cinder_grass_active is None:
                        cinder_grass_active = any(
                            sp._modifiers.get("_cinder_grass", False)
                            for sp in all_sprites if not sp.is_fainted
                        )
                    if name == "灼烧" and cinder_grass_active:
                        growth = ae.stacks // 2
                        new_stacks = ae.stacks + growth
                        s.update_stacks(name, new_stacks)
                        events.append(f'{s.name} {name}增长至{new_stacks}层')
                    else:
                        old_stacks = ae.stacks
                        new_stacks = ae.apply_decay()
                        s.update_stacks(name, new_stacks)
                        events.append(f'{s.name} {name}衰减至{new_stacks}层')
                        # 焰色反应：在场时衰减的灼烧变为相同层数的中毒
                        if name == "灼烧" and new_stacks < old_stacks:
                            holder = any(
                                (h := get_trait(sp)) is not None and h.name == "焰色反应"
                                for sp in all_sprites if not sp.is_fainted
                            )
                            if holder:
                                lost = old_stacks - new_stacks
                                s.add_effect(AbnormalEffect(
                                    name="中毒", source="焰色反应",
                                    scope="persistent", stacks=lost,
                                    tick_damage_pct=0.03, tick_element="毒",
                                ))
                                events.append(f'{s.name} 衰减的灼烧化为{lost}层中毒')

            for bs in s.skills:
                if bs.cooldown > 0:
                    bs.cooldown -= 1

            # 禁足回合数递减（游戏内文本：「处于禁足状态时，精灵无法离场」——
            # 此前 locked_turns 只写不递减，导致一次禁足永久有效）
            if getattr(s, 'locked_turns', 0) > 0:
                s.locked_turns -= 1
                if s.locked_turns <= 0:
                    s.locked_turns = 0
                    events.append(f'{s.name} 禁足解除')

        events += globals_.weather_turn_effects(all_sprites)
        globals_.tick_weather()
        events += globals_.mark_turn_end_effects(sprites)

        return events
