"""JournalReplayer — apply VM Mutations to mutable battle state.

Pure counterpart to the VM: takes a Journal and replays each Mutation
against the mutable Sprite/GlobalEffects objects, producing side effects
and observer-triggering events.
"""

from __future__ import annotations

import random
from copy import copy
from typing import TYPE_CHECKING

from backend.vm.journal import (
    AbnormalChange,
    Borrow,
    BurstGrant,
    Charge,
    CounterRegister,
    CounterWrite,
    Damage,
    Dispel,
    Double,
    EffectDelta,
    EnergyChange,
    Escape,
    Exchange,
    GainSkillsMutation,
    Heal,
    InheritEffectsMutation,
    Interrupt,
    Journal,
    LivesDelta,
    Lock,
    MarkChange,
    MechanismGrant,
    ModifierInjection,
    Mutation,
    Redirect,
    Replay,
    ReplayChoice,
    ReplaceSkill,
    Reset,
    Return,
    ScheduleEntry,
    SkillRotate,
    StarfallTrigger,
    StatChange,
    StatConvert,
    StatRandom,
    Steal,
    TeamCounterDelta,
    Tick,
    TraitInteractionMutation,
    TransformMutation,
    WeatherSet,
)

if TYPE_CHECKING:
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite

    from .observer import ObserverRegistry


# Stats whose values are ratios (display as percentage)
_RATIO_STATS: frozenset[str] = frozenset({
    "power_mult", "damage_mult", "damage_reduction",
    "energy_cost_mult",
    "heal_reverse", "life_drain",
    "ignore_resistance", "ignore_mods", "survive",
})

# Ratio stats where the modifier stores the TOTAL (1.0 + bonus), not the bonus.
# When creating display effects, we convert: display_mult = delta - 1.0.
_TOTAL_BASED_RATIO_STATS: frozenset[str] = frozenset({
    "power_mult", "damage_mult",
})

# Modifier stats that should also create a visible StatBuffEffect.
# These are integer-count stats that players expect to see as buff/debuff icons.
# (power is excluded: values are direct power amounts with per-skill scope,
# not global sprite buffs.)
_VISIBLE_MOD_STATS: frozenset[str] = frozenset({
    "combo", "priority", "life_drain", "power_mod",
})

_STEP_PCT = 10        # 非速度六维：1步=10%
_SPEED_STEP = 10       # 速度：1步=10点

# Stage stats that can also appear as value-based ModifierInjections.
# Value → steps: non-speed: steps = int(value * 10); speed: steps = int(value / 10).
_STAGE_STATS: frozenset[str] = frozenset({
    "atk", "def", "sp_atk", "sp_def", "speed",
    "speed_flat",  # 速度点数（1 step = 1 点，零头通道）
})

# Chinese labels for stat keys (modifiers + stage stats)
_STAT_LABELS: dict[str, str] = {
    # Stage stats (1步=10%, speed=10点, speed_flat=1点)
    "atk": "物攻", "sp_atk": "魔攻", "def": "物防", "sp_def": "魔防",
    "speed": "速度", "speed_flat": "速度",
    # Modifier stats
    "energy_cost": "能耗",
    "power": "威力",
    "combo": "连击",
    "combo_set": "连击固定",
    "priority": "先手",
    "power_mult": "威力倍率",
    "damage_mult": "伤害倍率",
    "damage_reduction": "减伤",
    "energy_cost_mult": "能耗倍率",
    "heal_reverse": "回复反转",
    "life_drain": "吸血",
    "ignore_resistance": "无视抗性",
    "ignore_mods": "无视修正",
    "survive": "不屈",
    "drive": "传动",
    "power_mod": "威力",
    "swift": "迅捷",
}

# Step unit for display conversion: steps → display value
_STEP_UNIT: dict[str, int] = {
    "power": 10, "speed": 10, "speed_flat": 1, "life_drain": 10,
    "priority": 1, "energy_cost": 1, "combo": 1,
}


# Stats that distribute to all BattleSkills when skill_filter="all" is used
# on a sprite-scoped target (e.g. power_mod {target: "sprite_self", skill_filter: "all"})
_SKILL_DISTRIBUTE_STATS = frozenset({"energy_cost", "power", "combo", "priority"})

#: 不进 `_PER_TURN_KEYS` 清理、也不需要「跨回合重放」的携带型技能属性。
#: `attach_abnormal`（附加中毒，3013）写在 BattleSkill._modifiers 里天然跨回合保留，
#: 若再登记进 _trait_direct_effects，每回合 _apply_direct_mods 会再加一次 → 叠加错误。
_NO_DIRECT_MOD_PERSIST = frozenset({"attach_abnormal"})


_ATTACK_TYPES: frozenset[str] = frozenset({"物攻", "魔攻", "动态攻击"})


def _matches_skill_type(skill_filter: str | None, skill_type: str) -> bool:
    """Check if a skill's type matches the skill_filter（只看类型的基础筛选）。

    结构型筛选（`adjacent` / `others` / `bare_*`）需要上下文，见 `_matches_skill_filter`。
    """
    if not skill_filter or skill_filter == "all":
        return True
    if skill_filter == "attack":
        return skill_type in _ATTACK_TYPES
    if skill_filter == "defense":
        return skill_type == "防御"
    if skill_filter == "status":
        return skill_type == "状态"
    return True  # unknown filters pass through


def _skill_has_extra_effects(bs, battle=None) -> bool:
    """技能是否带额外效果（`bare_*` 判定）。实现见 `engine/modifiers.py`。"""
    from backend.engine.modifiers import skill_has_extra_effects
    return skill_has_extra_effects(bs, battle)


def _matches_skill_filter(
    skill_filter,
    bs,
    *,
    sprite=None,
    ref_bs=None,
    battle=None,
) -> bool:
    """skill_filter 匹配（含 adjacent / others / bare_*）。实现见 `engine/modifiers.py`。

    - `others`   = 除**参考技能**（本回合正在使用的技能）以外的携带技能（激怒）
    - `adjacent` = **参考技能**槽位两侧的技能（不环绕）（减压阀/联动装置/能量守恒/轴承支撑）
    - `bare_*`   = 无额外效果的纯类型技能（不移）
    结构筛选缺上下文时返回 False，不再退化为「匹配全部」。
    """
    from backend.engine.modifiers import matches_skill_filter
    return matches_skill_filter(skill_filter, bs, sprite=sprite,
                                ref_bs=ref_bs, battle=battle)


def _matches_direction(stat_key: str, steps: int, what: str) -> bool:
    """该 StatBuffEffect 是否属于 `what`（"positive"/"negative"）方向。

    口径与 `JournalReplayer._match_stat_effect` 一致：`energy_cost` 的正负含义
    与其余维度相反（能耗 -N 才是增益）。
    """
    if stat_key == 'energy_cost':
        if what == "negative":
            return steps > 0
        return steps < 0
    if what == "positive":
        return steps > 0
    return steps < 0


def _skill_attr_base(bs, stat: str) -> int | None:
    """技能**自带基础值**（power / energy_cost），供 `mode:"set_base"` 反算增量。

    「基础值」= 生效技能的自带值（巧变/借用产物按替换后的技能算，与
    `BattleSkill.power` / `.energy_cost` 的 base 项口径一致）。
    非「基础值 + 增量」结构的 attr 返回 None（调用方退化为 `mode:"set"`）。
    """
    if stat not in ("power", "energy_cost"):
        return None
    skill = getattr(bs, "replaced_by", None) or getattr(bs, "base", None)
    if skill is None:
        return None
    return int(getattr(skill, stat, 0) or 0)


def _write_skill_mod(bs, bs_mods, m, delta) -> None:
    """把 MOD 写进技能槽 `_modifiers`（add/set/multiply/set_base 四种语义）。"""
    cur = bs_mods.get(m.stat, 0.0)
    if m.mode == "add":
        bs_mods[m.stat] = cur + delta
    elif m.mode == "set_base":
        base = _skill_attr_base(bs, m.stat)
        bs_mods[m.stat] = delta if base is None else delta - base
    elif m.mode == "set":
        bs_mods[m.stat] = delta
    elif m.mode == "multiply":
        bs_mods[m.stat] = cur * delta if cur else delta


def _apply_to_all_skills(sprite, m, replayer=None) -> str:
    """Distribute a modifier to all BattleSkills on the sprite."""
    label = _STAT_LABELS.get(m.stat, m.stat)
    delta = m.value
    if m.stat == "energy_cost" and m.mode != "set_base":
        delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
    for bs in (sprite.skills or []):
        bs_mods = getattr(bs, '_modifiers', None)
        if bs_mods is None:
            continue
        _write_skill_mod(bs, bs_mods, m, delta)
        # Permanent scope: persist to sprite._modifiers so load_permanent_mods()
        # can restore after _SKILL_PER_TURN_KEYS cleanup each turn.
        if m.scope == "permanent" and bs.name:
            key = f"skill.{bs.name}.{m.stat}"
            if m.mode == "add":
                sprite._modifiers[key] = bs_mods[m.stat]
            else:
                sprite._modifiers[key] = bs_mods[m.stat]
    # Create display-only effect for trait tooltip (energy_cost, combo, priority, etc.)
    source = m.source or ""
    if source and replayer is not None:
        replayer._sync_mult_display_effect(
            sprite, m.stat, 0.0, m.scope, source,
            display_value=float(delta))
    if m.stat == "energy_cost":
        prefix = "基础" if m.mode == "set_base" else ""
        return f"{sprite.name} 全技能{prefix}能耗{delta:+.0f}"
    return f"{sprite.name} 全技能{label}{delta:+.0f}"


def _apply_to_matching_skills(sprite, m, mark_energy_mod: int = 0, replayer=None,
                              ref_bs=None) -> str:
    """Apply a modifier to BattleSkills matching skill_where / skill_filter / element.

    Also registers the effect in sprite._trait_direct_effects so
    reapply_all_direct_mods() can restore it after _PER_TURN_KEYS cleanup.

    mark_energy_mod: team-level mark energy reduction (not in bs.energy_cost property).
    ref_bs: 目标精灵本回合正在使用的技能槽（`adjacent` / `others` 的参考技能）。
    """
    from backend.engine.modifiers import eval_skill_where

    label = _STAT_LABELS.get(m.stat, m.stat)
    delta = m.value
    if m.stat == "energy_cost" and m.mode != "set_base":
        delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
    applied = False
    battle = getattr(replayer, "_battle", None) if replayer is not None else None
    for bs in (sprite.skills or []):
        bs_mods = getattr(bs, '_modifiers', None)
        if bs_mods is None:
            continue
        skill_info = {
            "name": getattr(bs, 'name', ''),
            "energy_cost": max(0, getattr(bs, 'energy_cost', 0) - mark_energy_mod),
            "element": getattr(getattr(bs, 'base', None), 'element', ''),
            "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
        }
        if not eval_skill_where(m.skill_where, skill_info):
            continue
        st = skill_info.get("skill_type", "")
        if m.skill_filter and not _matches_skill_filter(
            m.skill_filter, bs, sprite=sprite, ref_bs=ref_bs, battle=battle
        ):
            continue
        # Element filter: "光" matches exactly; "!幻" excludes the element
        if m.element:
            expected = m.element[1:] if m.element.startswith("!") else m.element
            actual = skill_info.get("element", "")
            if m.element.startswith("!"):
                if actual == expected:
                    continue
            elif actual != expected:
                continue
        cur = bs_mods.get(m.stat, 0.0)
        _write_skill_mod(bs, bs_mods, m, delta)
        applied = True

    # Register for turn-to-turn persistence (survives _PER_TURN_KEYS cleanup)
    # attach_abnormal 不在 _PER_TURN_KEYS 里、本来就不被清，且重放会跨回合叠加 → 不登记
    if (applied and m.scope != "turn" and m.mode in ("add", "set", "set_base")
            and m.stat not in _NO_DIRECT_MOD_PERSIST):
        effect_dict = {
            "op": "power_mod",
            "attr": m.stat,
            "delta": m.value,
            "mode": m.mode,
            "skill_where": m.skill_where,
            "skill_filter": m.skill_filter,
            "element": m.element,
            "source": m.source,
        }
        if not m.skill_filter:
            del effect_dict["skill_filter"]
        if not m.element:
            del effect_dict["element"]
        if not m.source:
            del effect_dict["source"]
        if m.ttl > 0:
            effect_dict["ttl"] = m.ttl
        direct_effects = getattr(sprite, '_trait_direct_effects', None)
        if direct_effects is None:
            sprite._trait_direct_effects = []
        if effect_dict not in sprite._trait_direct_effects:
            sprite._trait_direct_effects.append(effect_dict)
        if replayer is not None and replayer._battle is not None:
            replayer._battle._vm_engine.trait_loader._direct_mod_sprite_ids.add(id(sprite))

    if applied:
        source = m.source or ""
        # Create display-only StatBuffEffect for trait tooltip
        if source:
            from backend.vm.effect import _STAT_LABELS as _EFF_LABELS
            from backend.vm.effect import StatBuffEffect
            eff_name = _EFF_LABELS.get(m.stat, m.stat)
            existing = next(
                (e for e in getattr(sprite, 'active_effects', [])
                 if isinstance(e, StatBuffEffect) and e.stat_key == m.stat
                 and e.source == source and e.steps == 0),
                None,
            )
            if m.stat == "energy_cost":
                # Absolute value: show cost reduction (negative delta = reduction)
                display_val = float(delta)
                if existing is not None:
                    existing.display_value = display_val
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0,
                            display_value=display_val,
                            ttl=m.ttl,
                        ))
                return ""  # energy bar shows cost visually
            elif m.stat == "power_mod":
                if existing is not None:
                    existing.display_value = delta * 10
                    existing.scope = "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source, scope="battlefield",
                            stat_key=m.stat, steps=0, display_value=delta * 10,
                            ttl=m.ttl,
                        ))
                element = (m.skill_where or {}).get("element", "")
                prefix = f"{element}" if element else ""
                return f"{sprite.name} {prefix}{label}{delta * 10:+.0f}"
            elif m.stat in _RATIO_STATS:
                display_bonus = delta - 1.0 if m.stat in _TOTAL_BASED_RATIO_STATS else delta
                if existing is not None:
                    existing.display_mult = display_bonus
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0, display_mult=display_bonus,
                            ttl=m.ttl,
                        ))
                return f"{sprite.name} {label}={delta:.0%}"
            else:
                # Non-ratio, non-stage stats: display as absolute delta
                if existing is not None:
                    existing.display_value = delta
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0, display_value=delta,
                            ttl=m.ttl,
                        ))
                return f"{sprite.name} {label}{delta:+.0f}"
        # No source → just log, no display effect
        if m.stat == "energy_cost":
            return ""
        if m.stat == "_burst_extended":
            return ""
        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {label}={delta:.0%}"
        return f"{sprite.name} {label}{delta:+.0f}"
    return ""


class JournalReplayer:
    """Replays a VM Journal against mutable battle state.

    Usage:
        replayer = JournalReplayer(self_sprite, opp_sprite, globals_, registry)
        events = replayer.replay(journal)
    """

    def __init__(
        self,
        self_sprite: Sprite,
        opp_sprite: Sprite,
        globals_: GlobalEffects,
        registry: ObserverRegistry | None = None,
        team: str = "A",
        species_lookup = None,
        self_skill = None,
        battle = None,
        leaving_sprite: Sprite | None = None,
    ):
        self.self = self_sprite
        self.opp = opp_sprite
        self.globals = globals_
        self.registry = registry
        self.team = team  # "A" or "B"
        self._species_lookup = species_lookup  # callable(number) -> SpeciesStats | None
        self._self_skill = self_skill
        self._battle = battle  # optional ref for trait-level ops
        self._leaving = leaving_sprite  # for post_enemy_leave: the sprite that left
        self._trait_sourcing: bool = False  # True during trait observer then-effect replay
        self._cleared_position_stats: set[str] = set()  # per-replay batch cleanup tracking
        self._energy_deltas: dict[int, int] = {}  # id(m) -> actual delta (capped by max_energy/floor 0)
        # 附加中毒（3013）：本次 replay 批内已追加过中毒的受损精灵 id（每次行动只追加一次）
        self._attached_abnormal_done: set[int] = set()
        # MCTS 仿真模式下跳过所有 UI 显示逻辑（字符串格式化、_sync_mult_display_effect）
        self.is_headless: bool = getattr(battle, '_mcts_sim', False) if battle else False

    def _invalidate_battle_ctx_cache(self) -> None:
        if self._battle is not None and hasattr(self._battle, "_invalidate_ctx_team_cache"):
            self._battle._invalidate_ctx_team_cache()

    # ── Main entry ──

    def replay(self, journal: Journal) -> list[str]:
        """Replay all mutations. Returns event strings for logging."""
        self._cleared_position_stats.clear()
        self._energy_deltas.clear()
        self._attached_abnormal_done.clear()
        if self.is_headless:
            dispatch = self._DISPATCH
            for mutation in journal:
                handler = dispatch.get(type(mutation))
                if handler is not None:
                    handler(self, mutation)
            return []
        events: list[str] = []
        for mutation in journal:
            ev = self._apply(mutation)
            if ev:
                events.append(ev)
        return events

    # ── Dispatch ──

    # O(1) type dispatch dict — replaces 31-branch cls.__name__ string chain
    _DISPATCH: dict[type, callable] = {}

    def _apply(self, m: Mutation) -> str:
        handler = self._DISPATCH.get(type(m))
        if handler is not None:
            if self.is_headless:
                handler(self, m)
                return ""
            return handler(self, m)
        return f"Unknown mutation: {type(m).__name__}"

    # ── Handlers ──

    def _apply_stat_change(self, m: StatChange) -> str:
        sprite = self._target_sprite(m.target)
        # Immunity gate: block stat debuffs (steps < 0) on stage stats
        if m.steps < 0 and m.stat in _STAGE_STATS:
            if self._check_immune(sprite, "immune_stat_down", m.stat):
                return "" if self.is_headless else f"{sprite.name} 免疫{_STAT_LABELS.get(m.stat, m.stat)}降低"
        steps = m.steps
        # 萌芽印记：携带方获得增益时，额外获得 buff_bonus_layers×层数 层
        if (steps > 0 and m.stat in _STAGE_STATS and self._battle is not None):
            team = self.team
            if m.target in ("sprite_opp", "opp", "team_opp"):
                team = "B" if self.team == "A" else "A"
            sprout = self._battle.globals.get_mark_by_name(team, "萌芽印记")
            if sprout is not None and sprout.buff_bonus_layers:
                steps = steps + sprout.buff_bonus_layers * sprout.stacks
        # 同步战斗逻辑所需数据到 active_effects（影响编码器输入）
        self._sync_stat_buff_effect(sprite, m.stat, steps, m.scope,
                                    m.source or "skill",
                                    is_inherent=self._trait_sourcing)
        # ── 以下为纯 UI 显示逻辑，MCTS 仿真模式跳过 ──
        if self.is_headless:
            return ""
        label = _STAT_LABELS.get(m.stat, m.stat)
        unit = _STEP_UNIT.get(m.stat, 10)
        if m.stat in ('priority', 'energy_cost', 'combo'):
            display = f'{label}{steps * unit:+d}' if steps != 0 else f'{label}{steps:+d}'
        elif m.stat in ('speed', 'speed_flat', 'power'):
            display = f'{label}{steps * unit:+d}'
        else:
            display = f'{label}{steps * unit:+d}%'
        # Stage stats from traits: create display-only effect for trait tooltip
        if m.stat in _STAGE_STATS:
            source = m.source or ""
            if m.stat == "speed":
                self._sync_mult_display_effect(sprite, m.stat, 0.0, m.scope, source,
                                                display_value=float(m.steps * _SPEED_STEP),
                                                additive=True)
            elif m.stat == "speed_flat":
                self._sync_mult_display_effect(sprite, m.stat, 0.0, m.scope, source,
                                                display_value=float(m.steps),
                                                additive=True)
            else:
                mult_value = m.steps * (_STEP_PCT / 100)
                self._sync_mult_display_effect(sprite, m.stat, mult_value, m.scope, source,
                                                additive=True)
        # Non-stage stats (power, energy_cost, priority, combo): display as absolute values
        elif m.source and m.stat in ('power', 'energy_cost', 'priority', 'combo') and m.steps != 0:
            self._sync_mult_display_effect(sprite, m.stat, 0.0, m.scope, m.source,
                                           display_value=float(m.steps * unit))
        return f"{sprite.name} {display}"

    def _apply_skill_cooldown(self, sprite, m: ModifierInjection) -> str:
        """`flag:"cooldown"`：把选中技能放上/减少冷却（写 BattleSkill.cooldown）。

        口径见 data/IR_GUIDE.md：
          value=true → 设为 ttl（缺省 1）；value=false → 清 0；
          value=正数 → 设为该值（冷却 N 回合）；
          value=负数 → 在当前值上加（下限 0），用于「防御技能冷却 -1」。
        目标技能：target="skill_opp_current" 取对手本回合用过的技能；
        否则按 skill_filter / skill_where 在目标精灵的技能里筛。
        """
        from backend.engine.replayer import _matches_skill_type

        targets: list = []
        if m.target == "skill_opp_current" and self._battle is not None:
            opp_team = "B" if self.team == "A" else "A"
            used = (self._battle._turn_skills.get(opp_team) or {}).get("name", "")
            if used:
                targets = [bs for bs in sprite.skills if getattr(bs, "name", "") == used]
        if not targets and (m.skill_filter or m.skill_where):
            for bs in sprite.skills:
                info = {
                    "name": getattr(bs, "name", ""),
                    "energy_cost": getattr(bs, "energy_cost", 0),
                    "element": getattr(getattr(bs, "base", None), "element", ""),
                    "skill_type": getattr(bs, "skill_type", ""),
                }
                if m.skill_where is not None:
                    from backend.engine.modifiers import eval_skill_where
                    if not eval_skill_where(m.skill_where, info):
                        continue
                if m.skill_filter and not _matches_skill_type(m.skill_filter, info["skill_type"]):
                    continue
                targets.append(bs)
        if not targets:
            return ""

        value = m.value
        parts: list[str] = []
        for bs in targets:
            before = bs.cooldown
            if value is True:
                bs.cooldown = int(m.ttl or 1)
            elif value is False:
                bs.cooldown = 0
            else:
                delta = int(value or 0)
                bs.cooldown = max(0, before + delta) if delta < 0 else delta
            if bs.cooldown != before:
                parts.append(f"{bs.name} 冷却 {before}→{bs.cooldown}")
        return "；".join(parts)

    def _apply_skill_drive(self, sprite, m: ModifierInjection) -> str:
        """`flag:"drive"` + `target:"skill_off_0"`：本回合额外传动 N（轮班暗分支）。

        口径（data/IR_GUIDE.md §3A flag_set drive）：让**当前使用的技能**以传动等级
        `value` 参与一次**额外的**传动 pass（= 本回合多移动 `value` 个槽位），
        随后把所有技能等级还原。回合开始的传动 pass 在本回合行动选择之前已经跑完，
        因此「本回合额外传动1」只能靠这次即时 pass 体现；
        还原等级保证后续回合的传动量不变。

        实现：把全体技能等级临时夹到 `≤ value`（其余技能仍按自己的等级参与，
        一次 pass 只移动 1 格），当前技能设为 `value` → `_apply_transmission`
        的 pass 数正好是 `value`。
        """
        bs = self._self_skill
        if bs is None or getattr(bs, "base", None) is None:
            return ""
        try:
            gain = int(m.value or 0)
        except (TypeError, ValueError):
            gain = 1
        if gain <= 0:
            gain = 1
        skills = list(sprite.skills or [])
        saved = {id(b): getattr(b, "_transmission", 0) for b in skills}
        for b in skills:
            b._transmission = min(getattr(b, "_transmission", 0), gain)
        bs._transmission = gain
        extra: list[str] = []
        if self._battle is not None and hasattr(self._battle, "_apply_transmission"):
            try:
                extra = self._battle._apply_transmission(sprite, team=self.team)
            except Exception:
                extra = []
        for b in skills:
            if id(b) in saved:
                b._transmission = saved[id(b)]
        label = f"{bs.name} 传动+{gain}"
        return f"{label}（{'；'.join(extra)}）" if extra else label

    def _apply_modifier(self, m: ModifierInjection) -> str:
        """Store modifier on target sprite for later snapshot consumption.

        ModifierInjections carry values like damage_reduction, power_mult,
        combo, etc. They are stored on the sprite's _modifiers dict and
        read by build_ctx when constructing Ctx for subsequent skills.

        If on_next=True, the modifier is deferred to _pending_modifiers
        and will be consumed on the next matching skill use.
        """
        # Devotion writes to team-level player.devotion, not sprite._modifiers
        if m.stat == "devotion":
            if self._battle is None:
                return ""
            t = ("B" if self.team == "A" else "A") if m.target == "opp" else self.team
            player = self._battle.get_player(t)
            if player is None:
                return ""
            devotion_name = m.name or ""
            if not devotion_name or devotion_name == "random":
                import random

                from backend.engine.devotion_config import DEVOTION_TYPES
                if not DEVOTION_TYPES:
                    return ""
                if m.mode == "add":
                    total = int(m.value)
                    for _ in range(total):
                        pick = random.choice(list(DEVOTION_TYPES.keys()))
                        player.devotion[pick] = player.devotion.get(pick, 0) + 1
                    return f"{t}队 获得{total}次随机奉献"
                devotion_name = random.choice(list(DEVOTION_TYPES.keys()))
            cur = player.devotion.get(devotion_name, 0)
            if m.mode == "set":
                player.devotion[devotion_name] = int(m.value)
            elif m.mode == "add":
                player.devotion[devotion_name] = cur + int(m.value)
            else:
                player.devotion[devotion_name] = int(m.value)
            delta = player.devotion[devotion_name] - cur
            return f"{t}队 奉献{devotion_name} {delta:+d}层"

        sprite = self._target_sprite(m.target)

        # flag:"cooldown" 写的是技能冷却，不是精灵 _modifiers（见 data/IR_GUIDE.md）
        if m.stat == "cooldown":
            return self._apply_skill_cooldown(sprite, m)

        # flag:"drive" + target:"skill_off_0"（轮班暗分支「本回合额外传动1」）：
        # 临时提升**当前技能**的传动等级并立即跑一次传动 pass，随后还原。
        if (m.stat == "drive" and m.target
                and m.target.startswith("skill_") and not m.target.startswith("skill_at_")):
            return self._apply_skill_drive(sprite, m)

        if m.on_next:
            # `attr:"energy_gain_delta"` + on_next 的相位是**回合**而不是「下一次技能」：
            # 入梦「敌方下回合回复的能量-5」——压入目标的待生效队列，由
            # `Battle._phase_turn_start` 在**目标下一回合开始时**武装（持续该回合整回合，
            # 回合末归零）。见 data/IR_GUIDE.md §3A `power_mod`。
            if m.stat == "energy_gain_delta":
                delta = int(m.value)
                sprite.queue_energy_gain_delta(delta, m.source or "")
                label = _STAT_LABELS.get(m.stat, m.stat)
                return f"{sprite.name} 下回合{label}{delta:+d}"
            sprite._pending_modifiers.append(m)
            # Track scope for cleanup when consumed (e.g. 野性感官 priority+1 → turn scope)
            skill_scoped_on = m.target.startswith("skill_") if m.target else False
            if not skill_scoped_on and m.scope in ("turn", "battlefield", "persistent"):
                sprite._mod_scopes[m.stat] = m.scope
            if m.stat == 'energy_cost':
                return f"{sprite.name} 获得待机效果: 能耗{m.value:+}"
            label = _STAT_LABELS.get(m.stat, m.stat)
            return f"{sprite.name} 获得待机效果: {label}{m.value:+}"

        skill_scoped = m.target.startswith("skill_") if m.target else False

        # ── speed_flat：速度点数通道（1 点 = 1 点）──
        # 写入阶段效果（StatBuffEffect），战斗逻辑（effective_stat）与快照
        # （speed_self/speed_opp）都读它；与 attr:"speed" 的百分比通道并存。
        if m.stat == "speed_flat" and not skill_scoped:
            steps = int(m.value)
            self._sync_stat_buff_effect(sprite, "speed_flat", steps, m.scope,
                                        m.source or "skill", mode=m.mode)
            if self.is_headless:
                return ""
            return f"{sprite.name} 速度{steps:+d}"

        # ── skill_filter "all" on sprite target: distribute to every BattleSkill ──
        if not skill_scoped and m.skill_filter == "all" and m.stat in _SKILL_DISTRIBUTE_STATS:
            return _apply_to_all_skills(sprite, m, replayer=self)

        # ── skill_where / skill_filter (attack/defense/status/adjacent/others/
        #    bare_*) / element on sprite target：走**技能级**落点 ──
        # `element` 也是技能级筛选：只写 element（不带 skill_filter/skill_where）
        # 此前落到精灵级 `_modifiers` 而没有读取点 → 静默空操作（消波块 / 冻土）。
        if not skill_scoped and (m.skill_where is not None or
                                (m.skill_filter and m.skill_filter != "all") or
                                m.element):
            mark_mod = 0
            if self._battle is not None and self.team:
                mark_mod = self._battle.globals.mark_energy_mod(self.team)
            return _apply_to_matching_skills(sprite, m, mark_energy_mod=mark_mod,
                                             replayer=self,
                                             ref_bs=self._ref_skill_for(sprite))

        if skill_scoped:
            if m.target.startswith("skill_at_"):
                # Route to specific skill position (1-indexed: skill_at_1 → skills[0])
                # Before first occurrence of each stat per replay batch, clear old
                # position-based modifiers from all skills (prevents stacking across turns).
                if m.stat not in self._cleared_position_stats:
                    self._cleared_position_stats.add(m.stat)
                    for bs in sprite.skills:
                        bs._modifiers.pop(m.stat, None)
                        if m.stat == "drive":
                            bs._transmission = bs.base.transmission
                        elif m.stat == "sealed":
                            bs.sealed = False
                try:
                    pos = int(m.target.rsplit("_", 1)[-1]) - 1
                    if 0 <= pos < len(sprite.skills):
                        target_mods = sprite.skills[pos]._modifiers
                        # drive flag → _transmission on the target skill
                        # 「传动X」可叠加（1033）：在技能自带传动等级上累加，
                        # 而不是覆盖（覆盖会把 钢铁洪流 的传动2 降成 翼轴 给的 1）
                        if m.stat == "drive":
                            bs_t = sprite.skills[pos]
                            bs_t._transmission = (bs_t.base.transmission or 0) + int(m.value or 0)
                        elif m.stat == "sealed":
                            sprite.skills[pos].sealed = bool(m.value)
                    else:
                        target_mods = sprite._modifiers
                except (ValueError, IndexError):
                    target_mods = sprite._modifiers
            elif self._self_skill is not None:
                target_mods = self._self_skill._modifiers
                if m.scope == "permanent" and self._self_skill.skill:
                    skill_name = getattr(self._self_skill.skill, 'name', '')
                    if skill_name:
                        key = f"skill.{skill_name}.{m.stat}"
                        cur = sprite._modifiers.get(key, 0.0)
                        if m.mode == "set":
                            sprite._modifiers[key] = m.value
                        elif m.mode == "add":
                            sprite._modifiers[key] = cur + m.value
                        elif m.mode == "multiply":
                            sprite._modifiers[key] = (cur or 1.0) * m.value
            else:
                target_mods = sprite._modifiers
        else:
            target_mods = sprite._modifiers

        if target_mods is None:
            final = m.value
            label = _STAT_LABELS.get(m.stat, m.stat)
            if m.stat in _RATIO_STATS:
                return f"{sprite.name} {label}={final:.0%}"
            if m.stat == "energy_cost":
                return ""
            return f"{sprite.name} {label}{final:+.0f}"

        cur = target_mods.get(m.stat)
        if m.mode == "set":
            target_mods[m.stat] = m.value
        elif m.mode == "set_base":
            # 「基础值设为 N」：按 (N − base) 反算增量；非基础值通道退化为 set
            base = (_skill_attr_base(self._self_skill, m.stat)
                    if skill_scoped and self._self_skill is not None else None)
            target_mods[m.stat] = m.value if base is None else m.value - base
        elif m.mode == "add":
            delta = m.value
            if m.stat == "energy_cost":
                delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
            if cur is None:
                # damage_reduction and life_drain base is 0.0 (0%=none),
                # unlike multiplier ratio stats whose base is 1.0 (1.0×=no change).
                cur = 0.0 if m.stat in ("damage_reduction", "life_drain") else 1.0 if m.stat in _RATIO_STATS else 0.0
            target_mods[m.stat] = cur + delta
        elif m.mode == "multiply":
            target_mods[m.stat] = (cur or 1.0) * m.value if cur is not None else m.value
        else:
            target_mods[m.stat] = m.value
        final = target_mods[m.stat]

        # 失效属性缓存（当修改任何缓存的修正值时）
        if m.stat in ("atk", "def", "sp_atk", "sp_def",
                      "damage_reduction", "power_mult", "damage_mult",
                      "energy_cost_mult", "combo_mult", "life_drain"):
            sprite._invalidate_stat_cache()

        # Sync sprite-level attrs (max_energy, starfall_consume_ratio) to active_effects.
        # Observer-triggered power_mod writes to _modifiers but property methods read
        # from active_effects → ModifierEffect (see Sprite.max_energy).
        from backend.engine.trait_loader import TraitLoader
        from backend.vm.effect import ModifierEffect
        if not skill_scoped and m.stat in TraitLoader._SPRITE_LEVEL_ATTRS:
            existing = None
            for e in sprite.active_effects:
                if isinstance(e, ModifierEffect) and e.attr == m.stat:
                    existing = e
                    break
            if existing is not None:
                existing.value = m.value
                # Update name for immunity attrs (may change from blanket → specific)
                if m.stat.startswith("immune_") and m.name:
                    existing.name = m.name
            else:
                # Immunity attrs: use raw name from JSON (empty = blanket immunity)
                effect_name = (m.name or "") if m.stat.startswith("immune_") else f"{m.source or 'trait'}-{m.stat}"
                sprite.active_effects.append(ModifierEffect(
                    name=effect_name,
                    source=m.source or "trait",
                    attr=m.stat,
                    value=m.value,
                    mode=m.mode,
                    target=m.target,
                    scope=m.scope,
                ))

        # Track invisible modifiers for scope cleanup
        if not skill_scoped and m.scope in ("turn", "battlefield", "persistent"):
            sprite._mod_scopes[m.stat] = m.scope

        # ── 以下为纯 UI 显示逻辑（创建 StatBuffEffect 用于界面展示），
        #     MCTS 仿真模式跳过全部。战斗逻辑数据已通过 _modifiers /
        #     ModifierEffect 完成同步。 ──
        if self.is_headless:
            return ""

        label = _STAT_LABELS.get(m.stat, m.stat)

        if not skill_scoped and m.value != 0:
            create_visible = False
            steps = 0
            if m.stat in _VISIBLE_MOD_STATS:
                steps = int(m.value * _STEP_UNIT.get(m.stat, 10)) if m.stat in _RATIO_STATS else int(m.value)
                create_visible = True
            # _STAGE_STATS (atk/def/sp_atk/sp_def/speed) are already applied
            # through _modifiers → build_ctx → atk_self/def_self/etc.
            # Creating a visible effect here would cause double-counting:
            # once via _modifiers and once via _extract_stat_stages.
            if create_visible and steps != 0:
                self._sync_stat_buff_effect(sprite, m.stat, steps, m.scope,
                                            m.source or "skill", mode=m.mode)
        # Create display-only StatBuffEffect for trait tooltip
        # (steps=0 so _extract_stat_stages ignores it, no double-counting).
        # Only created when m.source is explicitly set (trait-injected),
        # never fall back to species.ability — that would misattribute
        # skill effects as trait effects.
        source = m.source or ""
        if source:
            if m.stat in _STAGE_STATS:
                # Percentage stats: display_mult = ratio value (e.g., 0.5 = +50%)
                self._sync_mult_display_effect(sprite, m.stat, m.value, m.scope, source,
                                                additive=(m.mode == "add"))
            elif m.stat in _VISIBLE_MOD_STATS:
                if m.stat in _RATIO_STATS:
                    self._sync_mult_display_effect(sprite, m.stat, m.value, m.scope, source)
                elif m.stat == "power_mod":
                    # power_mod is step-based (1步=10威力), use display_value for flat power display
                    self._sync_mult_display_effect(
                        sprite, m.stat, 0.0, m.scope, source,
                        display_value=float(m.value) * 10)
                else:
                    self._sync_mult_display_effect(sprite, m.stat, 0, m.scope, source,
                                                   display_value=float(m.value))

        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {label}={final:.0%}"
        if m.stat == "power_mod":
            return f"{sprite.name} {label}{final * 10:+.0f}"
        if m.stat == "sealed":
            if m.target.startswith("skill_at_"):
                try:
                    pos = int(m.target.rsplit("_", 1)[-1])
                except ValueError:
                    pos = "?"
                return f"{sprite.name} {pos}号位封印"
            return f"{sprite.name} 技能封印"
        if m.stat in _STAGE_STATS:
            return f"{sprite.name} {label}{final:+.0%}"
        if m.stat == "energy_cost":
            return ""
        if m.stat == "_burst_extended":
            return ""
        if m.stat == "combo_set":
            return f"{sprite.name} 连击固定为{final:.0f}"
        if m.stat in ("swift", "drive"):
            skill_name = ""
            label = _STAT_LABELS.get(m.stat, m.stat)
            if m.target == "skill_off_0" and self._self_skill is not None:
                skill_name = getattr(self._self_skill.base, 'name', '') if hasattr(self._self_skill, 'base') else ''
            elif m.target.startswith("skill_at_"):
                try:
                    pos = int(m.target.rsplit("_", 1)[-1]) - 1
                    if 0 <= pos < len(sprite.skills):
                        skill_name = sprite.skills[pos].base.name
                except (ValueError, IndexError):
                    pass
            if skill_name:
                return f"{skill_name} 获得{label}"
        return f"{sprite.name} {label}{final:+.0f}"

    def _apply_replay_choice(self, m) -> str:
        """replay_branch: 重放本次「选择」技能的目标分支（有求必应/一意孤行）。"""
        battle = self._battle
        info = getattr(battle, "_last_choice_execution", None) if battle else None
        if not info:
            return ""
        choices = info.get("choices") or ()
        if not choices:
            return ""
        cur = info.get("branch", 0)
        idx = cur if m.which == "same" else (cur + 1) % len(choices)
        chosen = choices[idx]
        if chosen.get("cond") is not None:
            return ""  # 条件分支不自动重放
        effects = list(chosen.get("effects") or ())
        if not effects:
            return ""
        try:
            record = battle._get_skill_record(info.get("skill_name", ""))
        except Exception:
            record = None
        ctx = battle._make_ctx(self.self, self.opp, record, None, self.globals,
                               team=self.team, turn=battle.turn)
        sub_journal = vm_execute(ctx, effects)
        ev = self.replay(sub_journal)
        return " ".join(ev) if ev else ""

    def _apply_damage(self, m: Damage) -> str:
        sprite = self._target_sprite(m.target)
        # on_fatal_damage hook: 致命伤落地前拦截（如 不死鸟 锁血），返回 True 表示已处理
        if m.amount >= sprite.current_hp and self._battle is not None:
            from backend.sim.traits.trait_engine import fire_hook_first
            handled = fire_hook_first('on_fatal_damage', sprite, m.amount, self._battle, self.team)
            if handled:
                return f"{sprite.name} 保留1HP!"
        actual = sprite.take_damage(m.amount)

        # Life drain: attacker heals by a percentage of damage dealt.
        healed = 0
        if m.target != "sprite_self":
            drain_pct = self.self._modifiers.get("life_drain", 0.0)
            if self._self_skill is not None:
                drain_pct = max(drain_pct, self._self_skill._modifiers.get("life_drain", 0.0))
            if drain_pct > 0:
                healed = self.self.heal(round(actual * drain_pct))

        extra: list[str] = []
        # 加分项：信息遮蔽状态（木桶/月陨星）「被敌方攻击时解除」——只对**受击方**生效，
        # 自我伤害（m.target == "sprite_self"）不解（见 abnormal_config.INTEL_STATES）
        if m.target != "sprite_self":
            from backend.engine.abnormal_config import release_intel_states
            released = release_intel_states(sprite, on="damage")
            if released and not self.is_headless:
                extra.append(f"{sprite.name} {('/'.join(released))}解除")

        if actual > 0 and m.target != "sprite_self":
            # 附加中毒（3013）：携带的攻击技能命中后追加 N 层中毒。
            # 口径与 life_drain 一致：精灵级与技能级取 max；每次行动只追加一次。
            attach = self.self._modifiers.get("attach_abnormal", 0.0)
            if self._self_skill is not None:
                attach = max(attach, self._self_skill._modifiers.get("attach_abnormal", 0.0))
            attach = int(attach)
            if attach > 0 and id(sprite) not in self._attached_abnormal_done:
                self._attached_abnormal_done.add(id(sprite))
                ev = self._apply_abnormal_change(AbnormalChange(
                    target=m.target, name="中毒", delta=attach, scope="battlefield",
                ))
                if ev and not self.is_headless:
                    extra.append(ev)

        if self.is_headless:
            return ""
        result = f"{sprite.name} -{actual}HP"
        if sprite.is_fainted:
            result += " (fainted)"
        if healed:
            result += f" [吸血+{healed}HP]"
        if extra:
            result = " ".join([result, *extra])
        return result

    def _apply_heal(self, m: Heal) -> str:
        sprite = self._target_sprite(m.target)
        actual = sprite.heal(m.amount)
        return f"{sprite.name} +{actual}HP"

    def _apply_energy_change(self, m: EnergyChange) -> str:
        overflow = bool(getattr(m, "overflow", False))
        # ── Team-level targets: apply to every (non-fainted) sprite of the team ──
        # `team_*_all`（小型打劫「敌方队伍中所有精灵失去1能量」）：**全队 6 只**，
        # 含替补与力竭者，下限 0（`lose_energy` 已是 min(当前能量, amount)）。
        if m.target in ("team_own_all", "team_opp_all") and self._battle is not None:
            if m.target == "team_own_all":
                player = self._battle.get_player(self.team)
            else:
                player = self._battle.get_opponent(self.team)
            parts: list[str] = []
            total = 0
            for sp in list(player.team):
                actual = (sp.gain_energy(m.delta, overflow=True) if overflow
                          else sp.gain_energy(m.delta)) if m.delta > 0 \
                    else sp.lose_energy(-m.delta)
                total += actual
                parts.append(f"{sp.name} {'+' if m.delta > 0 else '-'}{actual}E")
            self._energy_deltas[id(m)] = total if m.delta > 0 else -total
            return " ".join(parts) if parts else ""
        if m.target in ("team_own", "team_own_benched", "team_both") and self._battle is not None:
            player = self._battle.get_player(self.team)
            if m.target == "team_own_benched":
                targets = [s for i, s in enumerate(player.team) if i != player.active_index]
            else:
                targets = list(player.team)
            if m.target == "team_both":
                targets += list(self._battle.get_opponent(self.team).team)
            parts: list[str] = []
            total = 0
            for sp in targets:
                if sp.is_fainted:
                    continue
                actual = (sp.gain_energy(m.delta, overflow=True) if overflow
                          else sp.gain_energy(m.delta)) if m.delta > 0 \
                    else sp.lose_energy(-m.delta)
                total += actual
                parts.append(f"{sp.name} {'+' if m.delta > 0 else '-'}{actual}E")
            self._energy_deltas[id(m)] = total if m.delta > 0 else -total
            return " ".join(parts) if parts else ""
        sprite = self._target_sprite(m.target)
        if m.delta > 0:
            actual = sprite.gain_energy(m.delta, overflow=overflow)
            self._energy_deltas[id(m)] = actual
            return f"{sprite.name} +{actual}E"
        else:
            actual = sprite.lose_energy(-m.delta)
            self._energy_deltas[id(m)] = -actual
            return f"{sprite.name} -{actual}E"

    def _apply_mechanism_grant(self, m: MechanismGrant) -> str:
        """挂载机制声明（aura / element_convert / morph / grant_choice）。

        声明是挂在该精灵身上的 GrantEffect，随 scope 生命周期清除；
        消费端（mechanisms.element_for / morph_category / choices_for、
        mechanisms.refresh）在需求值，因此这里只需维护声明本身。
        """
        from backend.vm.effect import GrantEffect

        sprite = self._target_sprite(m.target)
        if sprite is None:
            return ""
        for e in sprite.active_effects:
            if isinstance(e, GrantEffect) and e.mechanism == m.mechanism \
                    and e.source == m.source and e.payload == m.payload:
                e.scope = m.scope or e.scope
                e.ttl = 0
                return ""
        sprite.active_effects.append(GrantEffect(
            name=f"{m.mechanism}:{m.source}", source=m.source, scope=m.scope,
            mechanism=m.mechanism, affects=m.affects, payload=dict(m.payload),
        ))
        sprite._invalidate_effects_cache()
        return ""

    def _apply_counter_write(self, m: CounterWrite) -> str:
        """精灵级计数器写入（add / set）。"""
        sprite = self._target_sprite(m.target)
        if sprite is None:
            return ""
        if m.mode == "set":
            sprite.counters[m.key] = m.delta
        else:
            sprite.counters[m.key] = sprite.counters.get(m.key, 0) + m.delta
        if self.is_headless:
            return ""
        return f"{sprite.name} 计数 {m.key}={sprite.counters.get(m.key, 0)}"

    def _apply_mark_change(self, m: MarkChange) -> str:
        """Apply, dispel, steal, or convert marks."""
        if m.action == "apply":
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            category = self.globals.classify_mark(m.name)
            coexist = bool(self.self._modifiers.get("mark_coexist", False))
            self.globals.apply_mark(team, m.name, category, m.delta, coexist=coexist)
            return f"{team}队 {m.name} {m.delta:+d}层"

        if m.action == "enhance_all":
            # 许愿池「双方已有的印记层数+1」：只加层，不新建印记（空队伍是空操作）
            from backend.vm.effect import MarkEffect

            if m.target_team == "both":
                teams = ["A", "B"]
            else:
                teams = [self.team if m.target_team == "own"
                         else ("B" if self.team == "A" else "A")]
            gain = int(m.delta or 1)
            parts: list[str] = []
            for t in teams:
                marks = [mk for mk in self.globals.mark_effects.get(t, [])
                         if isinstance(mk, MarkEffect) and getattr(mk, 'stacks', 0) > 0]
                if not marks:
                    continue
                for mk in marks:
                    mk.stacks += gain
                names = "、".join(f"{mk.name}×{mk.stacks}" for mk in marks)
                parts.append(f"{t}队 印记增层 +{gain}（{names}）")
            return "；".join(parts)

        if m.action == "dispel":
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            pos, neg = self.globals.get_marks(team)
            all_marks = pos + neg
            count = m.delta or 1
            for mark in all_marks:
                if mark.name == m.name and mark.stacks > 0:
                    removed = min(mark.stacks, count)
                    mark.stacks -= removed
                    if mark.stacks <= 0:
                        self.globals.mark_effects.get(team, []).remove(mark)
                    return f"{self.self.name} 驱散{team}方{m.name}×{removed}"
            return ""

        if m.action == "convert_all":
            # 合并目标队伍全部印记为一枚同层数印记（与星星同行）
            from backend.vm.effect import MarkEffect

            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            marks = self.globals.mark_effects.get(team, [])
            total = sum(getattr(mk, 'stacks', 0) for mk in marks
                        if isinstance(mk, MarkEffect))
            target_total = int(m.delta) if m.delta else total
            if total <= 0 and target_total <= 0:
                return ""
            self.globals.mark_effects[team] = []
            if target_total > 0:
                category = self.globals.classify_mark(m.name)
                coexist = bool(self.self._modifiers.get("mark_coexist", False))
                self.globals.apply_mark(team, m.name, category, target_total, coexist=coexist)
            return f"{team}队 印记合并 → {m.name}×{target_total}"

        if m.action == "steal":
            opp_team = "B" if self.team == "A" else "A"
            team = self.team if m.target_team == "own" else opp_team
            from_team = opp_team if team == self.team else self.team
            pos, neg = self.globals.get_marks(from_team)
            all_marks = pos + neg
            count = m.delta or 1
            if m.name:
                for mark in all_marks:
                    if mark.name == m.name and mark.stacks > 0:
                        removed = min(mark.stacks, count)
                        mark.stacks -= removed
                        if mark.stacks <= 0:
                            self.globals.mark_effects.get(from_team, []).remove(mark)
                        category = self.globals.classify_mark(m.name)
                        coexist = bool(self.self._modifiers.get("mark_coexist", False))
                        self.globals.apply_mark(team, m.name, category, removed, coexist=coexist)
                        return f"{self.self.name} 偷取{m.name}×{removed}"
            else:
                import random
                available = [mk for mk in all_marks if mk.stacks > 0]
                if available:
                    mark = random.choice(available)
                    removed = min(mark.stacks, count)
                    mark.stacks -= removed
                    if mark.stacks <= 0:
                        self.globals.mark_effects.get(from_team, []).remove(mark)
                    category = self.globals.classify_mark(mark.name)
                    coexist = bool(self.self._modifiers.get("mark_coexist", False))
                    self.globals.apply_mark(team, mark.name, category, removed, coexist=coexist)
                    return f"{self.self.name} 偷取{mark.name}×{removed}"
            return ""

        if m.action == "convert":
            from backend.vm.effect import AbnormalEffect
            source_name = m.source_abnormal
            if not source_name:
                return ""
            effects_list = [e for e in self.self.active_effects
                            if isinstance(e, AbnormalEffect)
                            and e.name == source_name]
            total_stacks = sum(getattr(e, 'stacks', 0) for e in effects_list)
            if total_stacks <= 0:
                return ""
            marks = max(1, int(total_stacks * m.ratio))
            consumed = int(marks / m.ratio) if m.ratio > 0 else total_stacks
            for e in effects_list:
                remove_stacks = min(getattr(e, 'stacks', 0), consumed)
                e.stacks -= remove_stacks
                consumed -= remove_stacks
                if consumed <= 0:
                    break
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            category = self.globals.classify_mark(m.name)
            coexist = bool(self.self._modifiers.get("mark_coexist", False))
            self.globals.apply_mark(team, m.name, category, marks, coexist=coexist)
            return f"{self.self.name} {source_name}→{m.name}×{marks}"

        return ""

    @staticmethod
    def _check_immune(sprite, immune_type: str, target_name: str = "") -> bool:
        """Check if sprite has immunity to a specific abnormal or stat debuff.

        immune_type: "immune_abnormal" or "immune_stat_down"
        target_name: specific name ("灼烧", "atk") — empty name on effect = blanket immunity
        """
        from backend.vm.effect import ModifierEffect
        for e in getattr(sprite, 'active_effects', []):
            if isinstance(e, ModifierEffect) and e.attr == immune_type:
                if not e.name or e.name == target_name:
                    return True
        return False

    def _apply_abnormal_change(self, m: AbnormalChange) -> str:
        from backend.engine.abnormal_config import is_element_immune
        sprite = self._target_sprite(m.target)
        # 萌化: trigger form devolution via apply_moe (needs species lookup)
        if m.name == '萌化' and self._species_lookup is not None and m.delta > 0:
            return self._apply_moe_via_replayer(sprite, m)
        # Immunity gate: only block application (delta > 0), never block removal
        if m.delta > 0 and (
            self._check_immune(sprite, "immune_abnormal", m.name)
            or is_element_immune(sprite, m.name)
        ):
            return f"{sprite.name} 免疫{m.name}"
        self._sync_abnormal_effect(sprite, m.name, m.delta, m.scope, self.team)
        if m.name == '萌化':
            self._invalidate_battle_ctx_cache()
        events = [f"{sprite.name} {m.name} +{m.delta}层"]
        # 层数阈值即时效果（引电：2 层 → 25% 生命电系伤害并失去 2 层）
        if m.delta > 0:
            ev = self._apply_abnormal_threshold(sprite, m.name)
            if ev:
                events.append(ev)
        return " ".join(events)

    def _apply_abnormal_threshold(self, sprite, name: str) -> str:
        """异常的层数阈值效果（模板字段 threshold_* 驱动，通用规则）。"""
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES

        template = ABNORMAL_TEMPLATES.get(name)
        if template is None or not getattr(template, 'threshold_stacks', 0):
            return ""
        stacks = sprite.get_stacks(name)
        if stacks < template.threshold_stacks:
            return ""
        # 免疫系别（引电：电系精灵免疫）
        immune = getattr(template, 'threshold_immune_element', '')
        if immune and immune in (getattr(sprite.species, 'elements', ()) or ()):
            return ""
        events: list[str] = []
        if template.threshold_damage_pct:
            base = max(1, round(sprite.max_hp * template.threshold_damage_pct))
            mult = self._tick_element_mult(sprite, template.threshold_element)
            dmg = sprite.take_damage(max(1, round(base * mult)))
            events.append(f"{sprite.name} {name}触发-{dmg}HP")
        if template.threshold_consume:
            sprite.update_stacks(name, max(0, stacks - template.threshold_consume))
            events.append(f"失去{template.threshold_consume}层{sprite.name}的{name}")
        return " ".join(events)

    @staticmethod
    def _sync_abnormal_effect(sprite, name: str, delta: int, scope: str,
                              origin_team: str = "") -> None:
        """Create or update AbnormalEffect on sprite.active_effects (dual-write).

        Incrementally updates sprite._cached_abnormals to avoid O(N) rebuild.
        `origin_team` = 施加方队伍（寄生「从来源吸收」回补用）。
        """
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
        from backend.vm.effect import AbnormalEffect

        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active if isinstance(e, AbnormalEffect) and e.name == name), None
        )
        if existing is not None:
            existing.stacks += delta
            # 增量更新缓存（MCTS 热路径）
            if not getattr(sprite, '_effects_dirty', True):
                sprite._cached_abnormals[name] = sprite._cached_abnormals.get(name, 0) + delta
            if existing.stacks <= 0:
                active.remove(existing)
                sprite._invalidate_effects_cache()
            return

        if delta <= 0:
            return

        template = ABNORMAL_TEMPLATES.get(name)
        if template is not None:
            new_effect = AbnormalEffect(
                name=template.name,
                source=template.source,
                scope=scope or template.scope,
                ttl=template.ttl,
                stacks=delta,
                tick_damage_pct=template.tick_damage_pct,
                tick_element=template.tick_element,
                decay_on_tick=template.decay_on_tick,
                max_stacks=template.max_stacks,
                # 模板字段必须逐个带上：漏传会让模板声明静默失效
                # （tick_per_stack 曾因此恒为默认值，寄生/引电口径全跑偏）
                tick_per_stack=template.tick_per_stack,
                threshold_stacks=template.threshold_stacks,
                threshold_damage_pct=template.threshold_damage_pct,
                threshold_element=template.threshold_element,
                threshold_consume=template.threshold_consume,
                threshold_immune_element=template.threshold_immune_element,
                absorb_to_source=template.absorb_to_source,
                origin_team=origin_team,
            )
        else:
            new_effect = AbnormalEffect(
                name=name, source="skill", scope=scope, stacks=delta,
                origin_team=origin_team,
            )
        active.append(new_effect)
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            sprite._cached_abnormals[name] = sprite._cached_abnormals.get(name, 0) + delta

    def _apply_moe_via_replayer(self, sprite: Sprite, m: AbnormalChange) -> str:
        """Apply 萌化 form devolution through the replayer path.

        Creates a minimal battle adapter so apply_moe() can look up species.
        """
        class _MoeBattle:
            def lookup_species_by_number(_self, number, appearance=''):
                return self._species_lookup(number, appearance)
        events = sprite.apply_moe(m.delta, _MoeBattle())
        self._invalidate_battle_ctx_cache()
        return ' | '.join(events) if events else f"{sprite.name} {m.name} +{m.delta}层"

    def _apply_stat_random(self, m: StatRandom) -> str:
        """随机 N 层属性增益/减益（stat_random op）。

        逐层在 `m.stats`（缺省五维）里随机挑一维，走 `_apply_stat_change` 的
        同一条落地路径（`StatBuffEffect` + 属性缓存失效）。
        随机源是全局 `random`（对局由 `random.seed(seed)` 播种，与 morph.pick 同约定）。
        """
        sprite = self._target_sprite(m.target)
        if m.layers <= 0:
            return ""
        stats = tuple(m.stats) or ("atk", "def", "sp_atk", "sp_def", "speed")
        sign = 1 if m.direction == "positive" else -1
        tally: dict[str, int] = {}
        for _ in range(int(m.layers)):
            stat = random.choice(stats)
            tally[stat] = tally.get(stat, 0) + sign
        for stat, steps in tally.items():
            self._apply_stat_change(StatChange(
                target=m.target, stat=stat, steps=steps,
                scope=m.scope, source=m.source or "skill",
            ))
        label = "属性增益" if sign > 0 else "属性减益"
        detail = " ".join(f"{_STAT_LABELS.get(s, s)}{v:+d}" for s, v in tally.items())
        if self.is_headless:
            return ""
        return f"{sprite.name} 随机{m.layers}层{label}({detail})"

    def _apply_stat_convert(self, m: StatConvert) -> str:
        """属性增益 ⇄ 属性减益转换（stat_convert op，掉包）。

        就地翻转命中 `StatBuffEffect` 的 `steps` 符号（层数不变），
        并同步这些维度在 `_modifiers` 里的镜像（power/combo/priority 等）。
        `from_` 决定命中的方向语义（与 `_match_stat_effect` 同口径：
        `energy_cost` 的正负含义与其余维度相反），`to` 缺省为反面。
        """
        from backend.vm.effect import StatBuffEffect

        sprite = self._target_sprite(m.target)
        to_sign = 1 if m.to == "positive" else -1
        if m.to not in ("positive", "negative"):
            to_sign = -1 if m.from_ == "positive" else 1

        touched: set[str] = set()
        for e in list(getattr(sprite, 'active_effects', ())):
            if not isinstance(e, StatBuffEffect):
                continue
            if m.name and e.stat_key != m.name:
                continue
            if not e.steps:
                continue
            if not _matches_direction(e.stat_key, e.steps, m.from_):
                continue
            # 翻转符号即在两个方向间切换（energy_cost 的反向语义已被 _matches_direction 吸收）
            e.steps = -e.steps
            touched.add(e.stat_key)
        if not touched:
            return ""

        if to_sign > 0:
            self._bump_modifier_nonpositive(sprite, touched)
        else:
            self._bump_modifier_positive(sprite, touched)
        sprite._invalidate_effects_cache()
        sprite._invalidate_stat_cache()
        if self.is_headless:
            return ""
        label = "增益→减益" if to_sign < 0 else "减益→增益"
        return f"{sprite.name} 属性{label} ({', '.join(sorted(touched))})"

    @staticmethod
    def _bump_modifier_positive(sprite, keys) -> None:
        """把刚刚翻成减益的维度在 _modifiers 里同步为负值。"""
        for key in keys:
            val = sprite._modifiers.get(key)
            if isinstance(val, (int, float)) and val > 0:
                sprite._modifiers[key] = -val

    @staticmethod
    def _bump_modifier_nonpositive(sprite, keys) -> None:
        """把刚刚翻成增益的维度在 _modifiers 里同步为正值。"""
        for key in keys:
            val = sprite._modifiers.get(key)
            if isinstance(val, (int, float)) and val < 0:
                sprite._modifiers[key] = -val

    def _apply_replace_skill(self, m: ReplaceSkill) -> str:
        """把对手本回合正在使用的技能替换为指定技能（replace_skill op）。

        定位方式与 `flag_set flag:"cooldown" target:"skill_opp_current"` 一致：
        对手队伍 `battle._turn_skills[opp_team]["name"]`。回合末由
        `Battle._phase_turn_end()` 依 `battle._replaced_restore` 还原。
        """
        if self._battle is None:
            return ""
        from backend.engine import morph

        opp_team = "B" if self.team == "A" else "A"
        used = (self._battle._turn_skills.get(opp_team) or {}).get("name", "")
        sprite = self.opp
        if sprite is None or not used:
            return ""
        target_bs = None
        idx = -1
        for i, bs in enumerate(sprite.skills or ()):
            if getattr(bs, "name", "") == used:
                target_bs, idx = bs, i
                break
        if target_bs is None:
            return ""

        # 与巧变同一条加载路径（按名加载 + 缓存），保证替换技能的字段构造一致
        replaced = morph.load_skill(m.skill)
        if replaced is None:
            return ""
        old_name = target_bs.name
        # 不写 _morph_temp：巧变状态由 morph 独占，本 op 只借 replaced_by 字段
        target_bs.replaced_by = replaced
        self._battle._replaced_restore[(opp_team, idx)] = True
        if self.is_headless:
            return ""
        return f"{sprite.name} 的{old_name} 变为{m.skill}"

    def _apply_weather_set(self, m: WeatherSet) -> str:
        if not m.extend:
            self.globals.set_weather(m.weather, m.turns)
            return f"天气 → {m.weather} ({m.turns}t)"
        # extend：同天气累加回合数；无天气则起天气；其它天气不生效（IR_GUIDE §3B weather）
        result = self.globals.extend_weather(m.weather, m.turns)
        if not result:
            return ""
        return f"天气 {result} ({self.globals.weather_turns}t)"

    def _apply_dispel(self, m: Dispel) -> str:
        # 印记是队伍级资源，target 可能是 team_both（倾泻「驱散双方所有印记」），
        # 不能先按精灵解析目标，所以这一支放在最前面。
        if m.what == "mark":
            if m.target == "team_both":
                teams = ['A', 'B']
            elif m.target in ("team_own", "own_team", "sprite_self", "own"):
                teams = [self.team]
            else:
                teams = ["B" if self.team == "A" else "A"]
            return "；".join(p for p in (self._dispel_marks(t, m) for t in teams) if p)

        sprite = self._target_sprite(m.target)
        if m.what == "positive":
            n = self._dispel_by_source(sprite, m.source, positive_only=True) if m.source else sprite.dispel_positive(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 增益"
        elif m.what == "negative":
            n = self._dispel_by_source(sprite, m.source, positive_only=False) if m.source else sprite.dispel_negative(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 减益"
        elif m.what == "abnormal":
            if m.name == '萌化' and self._species_lookup is not None and sprite._moe_position > 0:
                class _MoeBattle:
                    def lookup_species_by_number(_self, number, appearance=''):
                        return self._species_lookup(number, appearance)
                old_name = sprite.name
                removed = sprite.remove_moe(sprite._moe_position, _MoeBattle())
                self._invalidate_battle_ctx_cache()
                return f"{old_name} 萌化解除 → 变为{sprite.name}(-{removed}层)"
            if m.source:
                n = self._remove_by_source(sprite, m.source, "abnormal")
                self._remove_abnormal_effect(sprite, source=m.source)
                return f"{sprite.name} 驱散异常(source={m.source}) x{n}"
            sprite.remove_effect(m.name, "abnormal")
            self._remove_abnormal_effect(sprite, name=m.name)
            if m.name == '萌化':
                self._invalidate_battle_ctx_cache()
            return f"{sprite.name} 驱散异常 {m.name}"
        elif m.what == "mark":
            return self._dispel_marks(self.team if m.target in ("team_own", "own_team", "own") else ("B" if self.team == "A" else "A"), m)
        return ""

    def _dispel_marks(self, team: str, m: Dispel) -> str:
        """驱散队伍印记。

        数据面写 `target:"team_both"`（无 `limit`/`name`）= 驱散**全部**印记的全部层数；
        给了 `m.name` 则只驱散该印记；给了 `m.limit` 则最多驱散该层数（随机分配），
        与 IR_GUIDE 的 dispel 口径一致。
        """
        marks = [mk for mk in self.globals.mark_effects.get(team, [])
                 if getattr(mk, 'stacks', 0) > 0
                 and (not m.name or getattr(mk, 'name', '') == m.name)]
        if not marks:
            return f"{team}队 无{m.name or '印记'}可驱散" if m.name else ""
        if m.limit:
            import random
            remaining = int(m.limit)
            removed_total = 0
            while remaining > 0 and marks:
                mk = random.choice(marks)
                removed = min(mk.stacks, remaining)
                mk.stacks -= removed
                remaining -= removed
                removed_total += removed
                if mk.stacks <= 0:
                    self.globals.mark_effects[team].remove(mk)
                    marks.remove(mk)
            return f"{team}队 驱散印记×{removed_total}"
        total = sum(mk.stacks for mk in marks)
        names = "、".join(dict.fromkeys(getattr(mk, 'name', '') for mk in marks))
        for mk in marks:
            self.globals.mark_effects[team].remove(mk)
        return f"{team}队 驱散全部印记（{names}×{total}）"

    @staticmethod
    def _dispel_by_source(sprite, source: str, positive_only: bool = True) -> int:
        """Remove effects from sprite matching the given source. Returns count removed."""
        from backend.vm.effect import StatBuffEffect
        removed = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if isinstance(e, StatBuffEffect) and getattr(e, 'source', '') == source:
                if positive_only and e.steps <= 0:
                    continue
                if not positive_only and e.steps >= 0:
                    continue
                sprite.active_effects.remove(e)
                removed += 1
        if removed:
            sprite._invalidate_effects_cache()
        return removed

    @staticmethod
    def _remove_by_source(sprite, source: str, category: str = '') -> int:
        """Remove effects matching source and optional category. Returns count removed."""
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
        type_map = {'stat': StatBuffEffect, 'abnormal': AbnormalEffect, 'state': StateEffect}
        target_type = type_map.get(category)
        removed = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if getattr(e, 'source', '') != source:
                continue
            if target_type is not None and not isinstance(e, target_type):
                continue
            sprite.active_effects.remove(e)
            removed += 1
        if removed:
            sprite._invalidate_effects_cache()
        return removed

    @staticmethod
    def _remove_abnormal_effect(sprite, name: str = '', source: str = '') -> None:
        """Remove AbnormalEffect from sprite.active_effects by name or source."""
        from backend.vm.effect import AbnormalEffect
        active = getattr(sprite, 'active_effects', None)
        if not active:
            return
        to_remove = []
        for e in active:
            if not isinstance(e, AbnormalEffect):
                continue
            if name and e.name == name or source and e.source == source:
                to_remove.append(e)
        for e in to_remove:
            active.remove(e)
        if to_remove:
            sprite._invalidate_effects_cache()

    @staticmethod
    def _sync_stat_buff_effect(sprite, stat_key: str, steps: int, scope: str,
                               source: str, mode: str = "add",
                               is_inherent: bool = False) -> None:
        """Create or update StatBuffEffect on sprite.active_effects (dual-write).

        When mode="set", existing steps are replaced instead of accumulated.
        is_inherent=True marks effects from traits that should not be inherited.
        Incrementally updates sprite._cached_stages / _cached_positive to avoid
        O(N) _extract_sprite_effects rebuild on every Ctx snapshot.
        """
        from backend.vm.effect import StatBuffEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active
             if isinstance(e, StatBuffEffect) and e.stat_key == stat_key and e.scope == scope),
            None,
        )
        if existing is not None:
            old_steps = existing.steps
            if mode == "set":
                existing.steps = steps
            else:
                existing.steps += steps
            # 增量更新缓存（MCTS 热路径）
            if not getattr(sprite, '_effects_dirty', True):
                if mode == "set":
                    sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) - old_steps + steps
                else:
                    sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) + steps
                # positive 计数调整
                new_steps = existing.steps
                if old_steps <= 0 and new_steps > 0:
                    sprite._cached_positive += 1
                elif old_steps > 0 and new_steps <= 0:
                    sprite._cached_positive -= 1
            # Propagate is_inherent to existing effect if not already set
            if is_inherent and not getattr(existing, 'is_inherent', False):
                existing.is_inherent = True
            return

        active.append(StatBuffEffect(
            name=f'{stat_key}', source=source, scope=scope,
            stat_key=stat_key, steps=steps, is_inherent=is_inherent,
        ))
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) + steps
            if steps > 0:
                sprite._cached_positive += 1

    @staticmethod
    def _sync_mult_display_effect(sprite, stat_key: str, mult_value: float,
                                   scope: str, source: str,
                                   display_value: float | None = None,
                                   additive: bool = False) -> None:
        """Create or update display-only StatBuffEffect for mult_mod values.

        Sets steps=0 so _extract_stat_stages ignores it (no double-counting).
        The display_mult field carries the ratio for UI display only.
        The display_value field carries absolute values (combo, priority, etc.).

        When additive=True, mult_value and display_value are added to existing
        values instead of replacing them (used for cumulative stat_stage triggers).
        """
        if not source:
            return
        from backend.vm.effect import StatBuffEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active
             if isinstance(e, StatBuffEffect) and e.stat_key == stat_key
             and e.source == source and e.steps == 0),
            None,
        )
        if existing is not None:
            if additive:
                # None 保护：合并谓词 steps==0 可能命中 _sync_stat_buff_effect
                # 刚创建的真实 StatBuff（display_mult=None），此前此处 TypeError
                # 会炸断整个观察者 then-block（后续效果静默丢失）
                existing.display_mult = (existing.display_mult or 0.0) + mult_value
                if display_value is not None:
                    existing.display_value = (existing.display_value or 0) + display_value
            else:
                existing.display_mult = mult_value
                if display_value is not None:
                    existing.display_value = display_value
            existing.scope = scope
            return

        from backend.vm.effect import _STAT_LABELS
        active.append(StatBuffEffect(
            name=_STAT_LABELS.get(stat_key, stat_key),
            source=source, scope=scope,
            stat_key=stat_key, steps=0, display_mult=mult_value,
            display_value=display_value,
        ))

    @staticmethod
    def _sync_state_effect(sprite, state_type: str, params: dict | None = None) -> None:
        """Create or update StateEffect on sprite.active_effects (dual-write)."""
        from backend.vm.effect import StateEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        # Replace existing state of same type
        active[:] = [e for e in active
                     if not (isinstance(e, StateEffect) and e.state_type == state_type)]

        active.append(StateEffect(
            name=state_type, source="skill", scope="turn",
            state_type=state_type, params=params or {},
        ))
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            if state_type == "charging":
                sprite._cached_charging = True
            elif state_type == "charged":
                sprite._cached_charged = True

    def _apply_steal(self, m: Steal) -> str:
        # Steal effects/energy/marks from target to self
        if m.what == "positive":
            from copy import copy

            from backend.vm.effect import StatBuffEffect
            target = self._target_sprite(m.from_target)
            positives = [e for e in target.active_effects
                         if isinstance(e, StatBuffEffect) and e.steps > 0]
            if m.action == "copy":
                for e in positives:
                    self.self.add_effect(copy(e))
                return f"{self.self.name} 复制 {len(positives)} 增益 from {target.name}"
            for e in positives:
                target.active_effects.remove(e)
                self.self.add_effect(e)
            if positives:
                target._invalidate_effects_cache()
            return f"{self.self.name} 偷取 {len(positives)} 增益 from {target.name}"
        elif m.what == "energy":
            amount = m.amount or 0
            if m.from_target == "team_opp" and self._battle is not None:
                opp_player = self._battle.get_opponent(self.team)
                total_stolen = 0
                names: list[str] = []
                for s in opp_player.team:
                    if s.energy <= 0:
                        continue
                    s_stolen = min(s.energy, amount)
                    s.lose_energy(s_stolen)
                    total_stolen += s_stolen
                    names.append(f"{s.name}({s_stolen})")
                self.self.gain_energy(total_stolen)
                return f"{self.self.name} 偷取 {total_stolen}E from {', '.join(names)}"
            target = self._target_sprite(m.from_target)
            stolen = min(target.energy, amount)
            target.lose_energy(stolen)
            self.self.gain_energy(stolen)
            return f"{self.self.name} 偷取 {stolen}E from {target.name}"
        elif m.what == "mark":
            from_team_key = "A" if m.from_target == "team_own" else "B"
            to_team_key = self.team
            name = m.name
            if name:
                mark = self.globals.get_mark_by_name(from_team_key, name)
                if mark and mark.stacks > 0:
                    stacks = mark.stacks
                    category = mark.category
                    self.globals.mark_effects.get(from_team_key, []).remove(mark)
                    coexist = bool(self.self._modifiers.get("mark_coexist", False))
                    self.globals.apply_mark(to_team_key, name, category, stacks, coexist=coexist)
                    return f"{self.self.name} 偷取 {name} x{stacks}"
            return ""
        return ""

    def _apply_tick(self, m: Tick) -> str:
        # Trigger abnormal tick damage — matches turn_end() formula
        sprite = self._target_sprite(m.target)
        stacks = sprite.get_stacks(m.abnormal_name)
        if stacks <= 0:
            return ""

        from backend.vm.effect import AbnormalEffect
        active = getattr(sprite, 'active_effects', None)
        dmg_pct = 0.03
        tick_element = ""
        tick_per_stack = True
        if active:
            ae = next(
                (e for e in active if isinstance(e, AbnormalEffect) and e.name == m.abnormal_name),
                None,
            )
            if ae is not None:
                dmg_pct = ae.tick_damage_pct or dmg_pct
                tick_element = ae.tick_element
                tick_per_stack = ae.tick_per_stack

        raw = max(1, round(sprite.max_hp * dmg_pct * stacks)) if tick_per_stack else max(1, round(sprite.max_hp * dmg_pct))
        mult = self._tick_element_mult(sprite, tick_element)
        dmg = max(1, round(raw * mult))
        sprite.take_damage(dmg)
        return f"{sprite.name} {m.abnormal_name} tick -{dmg}HP"

    @staticmethod
    def _tick_element_mult(sprite, element: str) -> float:
        """元素克制乘数，与 SkillResolver._tick_multiplier 一致。"""
        if not element:
            return 1.0
        from backend.sim.resolver import _TYPE_CHART
        attrs = getattr(sprite.species, 'attributes', '')
        mult = 1.0
        for attr in (attrs.split(',') if attrs else []):
            # strip：attributes 形如 "冰, 地"（带空格），未 strip 时查表 miss
            mult *= _TYPE_CHART.get(element, {}).get(attr.strip(), 1.0)
        return mult

    def _apply_skill_rotate(self, m: SkillRotate) -> str:
        """跨精灵技能轮转（过山车）——委托可复用 pass `Battle.rotate_team_skills()`。

        `target` 定位队伍：`team_own` / `sprite_self`（默认）为己方，`team_opp` /
        `sprite_opp` 为对方。`_target_sprite` 对队伍名返回的是**在场精灵**，因此这里
        单独解析队伍（轮转作用于该队**全部**精灵携带的技能）。
        """
        if self._battle is None:
            return ""
        target = m.target or "team_own"
        if target in ("team_opp", "opp_team", "sprite_opp", "enemy"):
            team = "B" if self.team == "A" else "A"
        else:
            team = self.team
        events = self._battle.rotate_team_skills(team, int(getattr(m, "offset", 1) or 1))
        return "；".join(events)

    def _apply_starfall_trigger(self, m: StarfallTrigger) -> str:
        """手动触发星陨印记（starfall_trigger op；引力偏转「以魔法伤害触发敌方的星陨效果」）。

        结算完全复用自然路径 `GlobalEffects.trigger_starfall()`（消耗层数 + X²+24X−24 幻伤），
        只是把「谁持有印记 / 用哪种伤害类型」交给 op：
          - `target` 是**印记持有方**：`sprite_opp`（默认）= 对手队伍持有的星陨印记，
            `sprite_self` = 自己队伍持有的；
          - 攻防键由 `damage_type` 决定（物攻→atk/def，魔攻→sp_atk/sp_def，
            动态攻击按**触发方**（攻击者）的物/魔攻高低判定，与自然结算一致）。
        攻守方向：印记持有方是防守方（吃伤害），触发方（`self.self`）是攻击方。
        """
        if self._battle is None or self.self is None or self.opp is None:
            return ""
        held_by_opp = (m.target or "sprite_opp") != "sprite_self"
        mark_team = ("B" if self.team == "A" else "A") if held_by_opp else self.team
        attacker = self.self
        defender = self.opp if held_by_opp else self.self
        damage_type = m.damage_type or "魔攻"
        from backend.sim.skill import Skill
        trigger_skill = Skill(name="(星陨触发)", skill_type=damage_type)
        dmg = self._battle.globals.trigger_starfall(
            mark_team, attacker, defender, trigger_skill)
        if dmg <= 0:
            return ""
        return f"星陨印记引爆({damage_type}): {defender.name} -{dmg}HP"

    def _apply_double(self, m: Double) -> str:
        sprite = self._target_sprite(m.target)
        if m.what == "positive":
            n = sprite.double_positive()
            return f"{sprite.name} 增益 ×2 ({n})"
        elif m.what == "negative":
            n = sprite.double_negative()
            return f"{sprite.name} 减益 ×2 ({n})"
        elif m.what == "abnormal":
            stacks = sprite.get_stacks(m.name or "")
            if stacks > 0:
                sprite.update_stacks(m.name or "", stacks * 2)
                return f"{sprite.name} {m.name} ×2"
        elif m.what == "mark":
            # 印记是**队伍级**：target 定位队伍（二律背反「使敌方星陨印记层数翻倍」）
            return self._double_marks(m)
        return ""

    def _double_marks(self, m: Double) -> str:
        """`what:"mark"`：把队伍印记层数 ×2（IR_GUIDE §3B double）。"""
        opp_team = "B" if self.team == "A" else "A"
        if m.target in ("team_own", "own_team"):
            team = self.team
        else:
            # team_opp / opp_team / 其它 → 对手队（印记是队伍级，与精灵级 target 区分）
            team = opp_team
        doubled: list[str] = []
        for me in self.globals.mark_effects.get(team, []):
            if m.name and getattr(me, "name", "") != m.name:
                continue
            stacks = getattr(me, "stacks", 0)
            if stacks <= 0:
                continue
            me.stacks = stacks * 2
            doubled.append(f"{me.name} {stacks}→{me.stacks}")
        if not doubled:
            return ""
        return f"{team}队 印记翻倍({', '.join(doubled)})"

    def _apply_effect_delta(self, m: EffectDelta) -> str:
        from backend.vm.effect import AbnormalEffect, StatBuffEffect

        sprite = self._target_sprite(m.target)
        n = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if isinstance(e, AbnormalEffect):
                if m.what == "negative":
                    new_stacks = e.stacks + m.delta
                    if e.max_stacks and new_stacks > e.max_stacks:
                        new_stacks = e.max_stacks
                    e.stacks = new_stacks
                    n += 1
            elif isinstance(e, StatBuffEffect):
                direction = self._match_stat_effect(e, m.what)
                if direction:
                    e.steps += direction * m.delta
                    n += 1
        # Also handle sprite._modifiers for increment-style positive stats
        _INC_STATS = frozenset({"combo", "power", "priority"})
        _DEC_STATS = frozenset({"energy_cost"})
        if m.what == "positive":
            for key in _INC_STATS:
                val = sprite._modifiers.get(key, 0)
                if val > 0:
                    sprite._modifiers[key] = val + m.delta
                    n += 1
            for key in _DEC_STATS:
                val = sprite._modifiers.get(key, 0)
                if val < 0:
                    sprite._modifiers[key] = val - m.delta
                    n += 1
        tag = "增益" if m.what == "positive" else "减益"
        if n:
            sprite._invalidate_effects_cache()
        return f"{sprite.name} {tag} +{m.delta}层 ({n})"

    @staticmethod
    def _match_stat_effect(e, what: str) -> int:
        """返回累加方向: +1 表示 steps+=delta, -1 表示 steps-=delta, 0 不匹配."""

        if e.stat_key == 'energy_cost':
            if what == "negative" and e.steps > 0:
                return 1
            if what == "positive" and e.steps < 0:
                return -1
        else:
            if what == "positive" and e.steps > 0:
                return 1
            if what == "negative" and e.steps < 0:
                return -1
        return 0

    def _apply_charge(self, m: Charge) -> str:
        sprite = self._target_sprite(m.target)
        self._sync_state_effect(sprite, "charging")
        return f"{sprite.name} 开始蓄力"

    def _apply_escape(self, m: Escape) -> str:
        sprite = self._target_sprite(m.target)
        name = sprite.name if sprite else m.target
        if self._battle:
            self._battle.pending_escape = {
                "team": self.team,
                "inherit": m.inherit,
                "urgent": m.urgent,
                "user_name": name,
            }
        return f"{name} 脱离 (inherit={m.inherit}, urgent={m.urgent})"

    def _apply_return(self, m: Return) -> str:
        sprite = self._target_sprite(m.target)
        sprite.pending_return = True
        return f"{sprite.name} 准备返场"

    def _apply_lock(self, m: Lock) -> str:
        sprite = self._target_sprite(m.target)
        # 游戏内文本（禁足）：「无法离场，且无法获得新的禁足状态」
        if getattr(sprite, 'locked_turns', 0) > 0:
            return f"{sprite.name} 已有禁足（{sprite.locked_turns}t），不再刷新"
        sprite.locked_turns = m.turns
        self._sync_state_effect(sprite, "locked", {"turns": m.turns})
        return f"{sprite.name} 锁定 {m.turns}t"

    def _apply_interrupt(self, m: Interrupt) -> str:
        sprite = self._target_sprite(m.target)
        sprite.interrupted = True
        self._sync_state_effect(sprite, "interrupted")
        return f"{sprite.name} 被打断"

    def _exchange_partner(self, target: str):
        """`exchange` 的对家选择（与 `replayer.self` 配对的那一只）。

        口径见 data/IR_GUIDE.md §3C exchange：
          "sprite_opp"（默认）/ 缺省 → `replayer.opp`
          "leaving" → `replayer._leaving`（刚离场者；仅 post_enemy_leave 提供）
          "entering" → 本次换入者：post_enemy_leave 下即 `replayer.opp`；
                       post_leave（自己离场）下引擎不提供 → None（空操作）
        返回 None 表示该语境下定位不到对家。
        """
        key = (target or "sprite_opp").strip()
        if key in ("sprite_opp", "opp", "opp_team", "team_opp", "enemy"):
            return self.opp
        if key in ("leaving", "leaver", "enemy_leaving", "sprite_leaving"):
            return self._leaving
        if key in ("entering", "enemy_new", "new_sprite"):
            # post_enemy_leave 的 ctx 里 opp 就是换入者；离场者存在与否正是该语境的标志
            return self.opp if self._leaving is not None else None
        return self.opp

    def _apply_exchange(self, m: Exchange) -> str:
        # adjacent_skills 只动自己槽位，与对家无关（不受 target 定位失败影响）
        if m.what == "adjacent_skills":
            pre_pos = {id(bs): i for i, bs in enumerate(self.self.skills or [])}
            self._swap_adjacent_skills(self.self)
            position_events = []
            if self._battle is not None:
                for i, bs in enumerate(self.self.skills or []):
                    if pre_pos.get(id(bs), -1) != i:
                        position_events += self._battle._fire_skill_position_changed(
                            self.team, self.self, bs
                        )
            if position_events:
                return " | ".join(["交换相邻技能位置", *position_events])
            return "交换相邻技能位置"

        other = self._exchange_partner(m.target)
        if other is None or other is self.self:
            return ""
        if m.what == "hp_ratio":
            self.self.current_hp, other.current_hp = \
                round(other.current_hp / other.max_hp * self.self.max_hp) if other.max_hp else 0, \
                round(self.self.current_hp / self.self.max_hp * other.max_hp) if self.self.max_hp else 0
            if other is not self.opp:
                return f"交换HP比例（{self.self.name} ↔ {other.name}）"
            return "交换HP比例"
        elif m.what == "effects":
            self.self.active_effects, other.active_effects = other.active_effects, self.self.active_effects
            self.self._invalidate_effects_cache()
            other._invalidate_effects_cache()
            return "交换增益减益"
        elif m.what == "skills":
            self.self.skills, other.skills = other.skills, self.self.skills
            return "交换技能"
        return ""

    def _apply_reset(self, m: Reset) -> str:
        """`reset`：把指定 stat 还原到**基础值**（消除永久增量）。

        口径（data/IR_GUIDE.md §3C reset；气沉丹田「每次应对后本技能能耗-3，使用后能耗重置」）：
        技能槽的最终值 = 技能自带基础值 + `_modifiers[stat]` 增量，因此「还原到基础值」
        = 清掉该增量；同时清掉`sprite._modifiers["skill.<技能名>.<stat>"]` 的永久登记
        （`scope:"permanent"` 的 skill_off_0 修正在那里留了一份，否则下一回合
        `_load_permanent_skill_mods_for_sprite()` 会把增量装回来）。

        target：
        - `skill_off_0`（默认）→ 本次使用的技能槽（self._self_skill）；
        - `skill_at_N`（1-indexed）→ 目标精灵的第 N 个技能槽；
        - 其余（`sprite_self` / `sprite_opp` …）→ 精灵级 `_modifiers[stat]` 增量。
        """
        stat = m.stat
        if not stat:
            return ""
        target = m.target or "skill_off_0"

        if target.startswith("skill_"):
            bs = None
            if target == "skill_off_0":
                bs = self._self_skill
            elif target.startswith("skill_at_"):
                try:
                    pos = int(target.rsplit("_", 1)[-1]) - 1
                except (ValueError, IndexError):
                    pos = -1
                skills = list(getattr(self.self, "skills", None) or [])
                if 0 <= pos < len(skills):
                    bs = skills[pos]
            if bs is None:
                return ""
            sprite = self.self
            had = bs._modifiers.pop(stat, None)
            skill_name = getattr(getattr(bs, "base", None), "name", "") or getattr(bs, "name", "")
            perm_removed = None
            if skill_name:
                key = f"skill.{skill_name}.{stat}"
                perm_removed = sprite._modifiers.pop(key, None)
            if had is None and perm_removed is None:
                return f"{skill_name} {stat} 已为基础值"
            label = _STAT_LABELS.get(stat, stat)
            return f"{skill_name} {label}重置（{had if had is not None else perm_removed:+}→0）"

        sprite = self._target_sprite(target)
        if sprite is None:
            return ""
        had = sprite._modifiers.pop(stat, None)
        if had is None:
            return ""
        label = _STAT_LABELS.get(stat, stat)
        return f"{sprite.name} {label}重置（{had:+}→0）"

    def _apply_redirect(self, m: Redirect) -> str:
        # Set redirect flag on self — engine reads this in _handle_redirect
        self.self._redirect_target = m.target
        return f"伤害重定向 → {m.target}"

    def _apply_replay(self, m: Replay) -> str:
        # Engine needs to find historical skills and replay them
        return f"重放技能 from={m.from_}"

    def _apply_borrow(self, m: Borrow) -> str:
        return f"借用技能 from={m.from_skill}"

    def _apply_burst_grant(self, m: BurstGrant) -> str:
        """Write burst effects to matching BattleSkills on the target sprite.

        `from_="explicit"`（默认）：注入 `then` 列出的效果。
        `from_="triggered"`（踏雷）：从本队**已触发过的迸发**池
        （`BattleVMEngine._burst_effects[team]`，按触发时间升序）取最近 `count` 条效果列表。
        """
        from backend.engine.modifiers import eval_skill_where

        sprite = self._target_sprite(m.target)

        grant_effects: list[dict] = []
        if m.from_ == "triggered":
            vm = getattr(self._battle, '_vm_engine', None) if self._battle is not None else None
            pool = list(getattr(vm, '_burst_effects', {}).get(self.team, []) or []) \
                if vm is not None else []
            # 池 ——[(技能名, 效果列表), …]；取最近 count 条（尾部 = 最新触发）
            count = m.count
            if isinstance(count, str):
                take = len(pool)  # "all" 或其它字符串 → 全部
            else:
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    n = 1
                take = len(pool) if n <= 0 else min(n, len(pool))
            for _skill_name, effects in pool[len(pool) - take:]:
                grant_effects.extend(list(effects))
            if not grant_effects:
                return ""
        else:
            grant_effects = list(m.effects)

        applied = 0
        for bs in (sprite.skills or []):
            if m.skill_where:
                skill_info = {
                    "name": getattr(bs, 'name', ''),
                    "energy_cost": getattr(bs, 'energy_cost', 0),
                    "element": getattr(getattr(bs, 'base', None), 'element', ''),
                    "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
                }
                if not eval_skill_where(m.skill_where, skill_info):
                    continue
            if m.skill_filter and m.skill_filter != "all":
                st = getattr(getattr(bs, 'base', None), 'skill_type', '')
                if not _matches_skill_type(m.skill_filter, st):
                    continue
            bs._burst_effects.extend(list(grant_effects))
            bs._modifiers["burst"] = float(len(bs._burst_effects) > 0)
            applied += 1
        if applied:
            return f"{sprite.name} {m.source} 迸发赋予 {applied} 技能"
        return ""

    def _apply_team_counter_delta(self, m: TeamCounterDelta) -> str:
        """Write to a team-level counter. Requires battle reference."""
        if self._battle is None:
            return ""
        t = ("B" if self.team == "A" else "A") if m.target == "opp" else self.team
        self._battle.inc_team_counter(t, m.key, m.delta)
        return ""

    def _apply_lives_delta(self, m: LivesDelta) -> str:
        """Modify player lives. Requires battle reference."""
        if self._battle is None:
            return ""
        t = ("B" if self.team == "A" else "A") if m.target_team == "opp" else self.team
        player = self._battle.get_player(t)
        if player is None:
            return ""
        if m.delta < 0 and player.lives <= 0:
            return ""
        player.lives += m.delta
        label = f"奉献{m.delta}" if m.delta > 0 else f"魔力{m.delta}"
        return f"{self.self.name} {label}→{player.lives}" if self.self else ""

    def _apply_schedule_entry(self, m: ScheduleEntry) -> str:
        """Register delayed effects. Requires battle reference."""
        if self._battle is None:
            return ""
        self._battle.scheduled_effects.append({
            'turn': self._battle.turn + m.turns,
            'phase': m.at,
            'effects': m.then,
            'source': self.self,
            'ctx_snapshot': {'team': self.team, 'target': 'self'},
        })
        return f"{self.self.name}: 延时效果({m.turns}回合后)" if self.self else ""

    def _resolve_source(self, source_key: str):
        """Resolve a source key to a sprite reference.

        "self" → replayer.self (attacker / trait bearer / leaving sprite)
        "sprite_opp" → replayer._leaving in post_enemy_leave context,
                       otherwise replayer.opp
        anything else → replayer.opp
        """
        if source_key == "self":
            return self.self
        if source_key == "sprite_opp" and self._leaving is not None:
            return self._leaving
        return self.opp

    def _apply_inherit_effects_mutation(self, m: InheritEffectsMutation) -> str:
        """Transfer effects between sprites. Requires battle reference.

        When `m.effects` is non-empty: those declared effects are used verbatim
        (離場後換入者以 X 狀態登場：木桶戏法 / 观测者效应) instead of copying from
        the source sprite — so the leaver does not need to hold them itself.
        When inherit_stat_effects=True: copy all StatBuffEffect objects
        (六维/连击/威力/吸血等) regardless of scope.
        Otherwise: filter by scope (legacy behavior).
        """
        if self._battle is None:
            return ""
        source_sprite = self._resolve_source(m.source_key)
        from copy import copy

        from backend.vm.effect import StatBuffEffect

        declared: list = []
        if m.effects:
            from backend.vm.effect_factory import from_dict as _effect_from_dict
            for eff in m.effects:
                src = eff.get("source", "") if isinstance(eff, dict) else getattr(eff, "source", "") or ""
                obj = _effect_from_dict(eff, source=src)
                if obj is not None:
                    declared.append(obj)

        if declared:
            inherited = declared
            source_name = source_sprite.name if source_sprite is not None else self.team
        else:
            if source_sprite is None:
                return ""
            source_name = source_sprite.name
            if m.inherit_stat_effects:
                inherited = [copy(e) for e in getattr(source_sprite, 'active_effects', [])
                             if isinstance(e, StatBuffEffect)
                             and not getattr(e, 'is_inherent', False)]
            else:
                inherited = [copy(e) for e in getattr(source_sprite, 'active_effects', [])
                             if getattr(e, 'scope', '') == m.scope]
        if not inherited:
            return ""
        if m.via_pending:
            self._battle.pending_effects.setdefault(self.team, [])
            self._battle.pending_effects[self.team].extend(inherited)
            return f"{source_name}→next({self.team}) 继承{len(inherited)}个效果"
        else:
            target_sprite = self.opp if m.target_key == "enemy_new" else self.self
            if target_sprite is None:
                return ""
            for e in inherited:
                target_sprite.add_effect(e)
            return f"{source_name}→{target_sprite.name} 继承{len(inherited)}个效果"

    def _apply_transform_mutation(self, m: TransformMutation) -> str:
        """Transform a sprite's species. Requires battle reference for species lookup."""
        if self._battle is None:
            return ""
        from backend.common.models import SpeciesStats
        sprite = self.self
        new_species = self._battle.lookup_species(m.species)
        if new_species is None:
            s = sprite.species
            new_species = SpeciesStats(
                name=m.species, form='',
                hp=s.hp, atk=s.atk, sp_atk=s.sp_atk,
                def_=s.def_, sp_def=s.sp_def, speed=s.speed,
                attributes=s.attributes, ability=s.ability,
            )
        skill_names = list(m.skills) if m.skills else []
        new_skills = self._battle.build_skills(skill_names) if skill_names else []
        if m.reset_hp:
            sprite.current_hp = sprite.max_hp
        if m.reset_energy:
            sprite.energy = getattr(sprite, 'max_energy', 10)
        result = sprite.transform(new_species, new_skills) if hasattr(sprite, 'transform') else f"{sprite.name} → {m.species}"
        self._invalidate_battle_ctx_cache()
        if isinstance(result, list):
            return ' | '.join(str(x) for x in result)
        return result

    def _apply_trait_interaction_mutation(self, m: TraitInteractionMutation) -> str:
        """Suppress, remove, or copy a trait on a sprite."""
        target = self.self if m.target in ("sprite_self", "self") else self.opp
        if target is None:
            return ""

        def replace_ability(sprite, ability: str, ability_id: int = 0) -> None:
            species = copy(sprite.species)
            species.ability = ability
            species.ability_id = ability_id
            sprite.species = species
            sprite._trait_handler = None

        if m.action == 'suppress':
            target._trait_suppressed = True
            target._trait_handler = None
            return f'{target.name} 特性被压制'
        if m.action == 'remove':
            target._trait_suppressed = True
            target._trait_handler = None
            if m.new_ability:
                replace_ability(target, m.new_ability)
                target._trait_suppressed = False
                return f'{target.name} 特性变为 {m.new_ability}'
            return f'{target.name} 特性被移除'
        if m.action == 'copy':
            source = self.opp if m.copy_from == "sprite_opp" else self.self
            if source is None or target is source:
                return ""
            source_ability = source.species.ability
            source_ability_id = getattr(source.species, 'ability_id', 0)
            if not source_ability and not source_ability_id:
                return ""
            replace_ability(target, source_ability, source_ability_id)
            target._trait_suppressed = False
            return f'{target.name} 复制特性 → {source_ability or source_ability_id}'
        return ""

    def _apply_gain_skills(self, m: GainSkillsMutation) -> str:
        """Grant temporary skills to a sprite from a skill pool.

        Picks count random skills not already carried by the sprite,
        builds BattleSkill instances, and appends them to the sprite's
        skill bar as temporary skills (cleared after battle).
        """
        import random
        sprite = self._target_sprite(m.target)
        if sprite is None:
            return ""

        # Build candidate pool
        if self._battle is not None and hasattr(self._battle, 'list_all_skill_names'):
            all_names = self._battle.list_all_skill_names()
        else:
            return f"{sprite.name} gain_skills: no skill pool available"

        if not all_names:
            return f"{sprite.name} gain_skills: empty skill pool"

        carried = {getattr(bs.base, 'name', '') for bs in (sprite.skills or []) if bs.base}

        candidates = all_names
        if m.exclude_carried:
            candidates = [n for n in all_names if n not in carried]

        if m.source == "learnset":
            species_elements = set(getattr(sprite.species, 'elements', []))
            if species_elements and self._battle is not None and hasattr(self._battle, 'skill_element_map'):
                elem_map = self._battle.skill_element_map()
                candidates = [n for n in candidates
                              if set(elem_map.get(n, [])) & species_elements]

        if not candidates:
            return f"{sprite.name} gain_skills: no candidates"

        count = min(m.count, len(candidates))
        chosen = random.sample(candidates, count)

        if self._battle is not None and self._battle.skill_loader is not None:
            new_skills = self._battle.skill_loader(chosen)
        else:
            return f"{sprite.name} gain_skills: no skill loader"

        for bs in new_skills:
            bs.is_temporary = True
            sprite.skills.append(bs)

        names = ', '.join(getattr(bs, 'name', str(bs)) for bs in new_skills)
        return f"{sprite.name} 获得临时技能: {names}"

    def _apply_counter_register(self, m: CounterRegister) -> str:
        # Counter registration is handled by _register_counters_from_journal
        # in battle.py → register_counter(). The replayer's job here is just
        # to produce the verbose log entry (named counters only).
        if self.registry and m.name and m.name.strip():
            return f"注册计次器: {m.name}"
        return ""

    # ── Helpers ──

    def _target_sprite(self, target: str) -> Sprite:
        if target.startswith("skill_at_"):
            return self.self
        if target in ("sprite_self", "self", "team_own", "skill_off_0"):
            return self.self
        if target == "ally_new" and self._battle is not None:
            return self._battle.get_player(self.team).active
        if target == "enemy_new" and self._battle is not None:
            return self._battle.get_opponent(self.team).active
        if target == "sprite_bench" and self._battle is not None:
            player = self._battle.get_player(self.team)
            bench = [s for i, s in enumerate(player.team) if i != player.active_index and not s.is_fainted]
            if bench:
                import random
                return random.choice(bench)
            return self.self
        return self.opp

    def _ref_skill_for(self, sprite):
        """目标精灵「本回合正在使用的技能」槽（`adjacent` / `others` 的参考技能）。

        - 自己：`self._self_skill`（本次行动正在结算的技能）；
        - 对家：本回合对手使用的那只技能（与 `flag:"cooldown" target:"skill_opp_current"`
          同源：`battle._turn_skills[opp_team]["name"]`）——激怒「敌方除本回合使用的技能」
          的目标是**对手**，参考技能必须取对手那一手，不能取自己的；
        - 两者都不是（场下/离场语境）：None → 结构筛选不匹配。
        """
        if sprite is None:
            return None
        if sprite is self.self:
            return self._self_skill
        if sprite is self.opp:
            if self._battle is None:
                return None
            opp_team = "B" if self.team == "A" else "A"
            used = (getattr(self._battle, "_turn_skills", None) or {}).get(opp_team) or {}
            used_name = used.get("name", "") if isinstance(used, dict) else ""
            if not used_name:
                return None
            for bs in (getattr(sprite, "skills", None) or []):
                if getattr(bs, "name", "") == used_name:
                    return bs
        return None

    @staticmethod
    def _swap_adjacent_skills(sprite: Sprite) -> None:
        """Swap the current skill with its adjacent neighbors (left and right).

        For skills at positions 0 and 3 (edge cases), only swap the available side.
        """
        skills = sprite.skills
        if not skills or len(skills) < 2:
            return
        n = len(skills)
        # Find current skill position (the one being used)
        # In a typical 4-skill layout, swap positions 1<->2 for symmetry
        # Simplified: swap all adjacent pairs (0<->1, 2<->3)
        for i in range(0, n - 1, 2):
            skills[i], skills[i + 1] = skills[i + 1], skills[i]


# ── O(1) dispatch dict for JournalReplayer._apply ──
# Built once at import time; replaces the 31-branch cls.__name__ if-elif chain.
JournalReplayer._DISPATCH = {
    AbnormalChange: JournalReplayer._apply_abnormal_change,
    Borrow: JournalReplayer._apply_borrow,
    BurstGrant: JournalReplayer._apply_burst_grant,
    Charge: JournalReplayer._apply_charge,
    CounterRegister: JournalReplayer._apply_counter_register,
    CounterWrite: JournalReplayer._apply_counter_write,
    Damage: JournalReplayer._apply_damage,
    Dispel: JournalReplayer._apply_dispel,
    Double: JournalReplayer._apply_double,
    EffectDelta: JournalReplayer._apply_effect_delta,
    EnergyChange: JournalReplayer._apply_energy_change,
    Escape: JournalReplayer._apply_escape,
    Exchange: JournalReplayer._apply_exchange,
    GainSkillsMutation: JournalReplayer._apply_gain_skills,
    Heal: JournalReplayer._apply_heal,
    InheritEffectsMutation: JournalReplayer._apply_inherit_effects_mutation,
    Interrupt: JournalReplayer._apply_interrupt,
    LivesDelta: JournalReplayer._apply_lives_delta,
    Lock: JournalReplayer._apply_lock,
    MarkChange: JournalReplayer._apply_mark_change,
    MechanismGrant: JournalReplayer._apply_mechanism_grant,
    ModifierInjection: JournalReplayer._apply_modifier,
    Redirect: JournalReplayer._apply_redirect,
    Replay: JournalReplayer._apply_replay,
    ReplayChoice: JournalReplayer._apply_replay_choice,
    ReplaceSkill: JournalReplayer._apply_replace_skill,
    Reset: JournalReplayer._apply_reset,
    Return: JournalReplayer._apply_return,
    ScheduleEntry: JournalReplayer._apply_schedule_entry,
    SkillRotate: JournalReplayer._apply_skill_rotate,
    StarfallTrigger: JournalReplayer._apply_starfall_trigger,
    StatChange: JournalReplayer._apply_stat_change,
    StatConvert: JournalReplayer._apply_stat_convert,
    StatRandom: JournalReplayer._apply_stat_random,
    Steal: JournalReplayer._apply_steal,
    TeamCounterDelta: JournalReplayer._apply_team_counter_delta,
    Tick: JournalReplayer._apply_tick,
    TraitInteractionMutation: JournalReplayer._apply_trait_interaction_mutation,
    TransformMutation: JournalReplayer._apply_transform_mutation,
    WeatherSet: JournalReplayer._apply_weather_set,
}
