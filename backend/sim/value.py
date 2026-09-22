# -*- coding: utf-8 -*-
"""backend/sim/value.py — E1：效果感知的局面估值（搜索用的叶子函数）。

**为什么需要它**：官方局面分 `outcome.team_battle_score` 只看存活数 / 全队血量比 / 心力 /
在场能量，看不见效果层，于是"铺垫一回合再收成""举盾吃下这一击""留印记逼换人"这类计划
在一回合 rollout 里几乎不产生价值差（实测：一回合视野下 regret 中位数 0.017）。
本模块把技能与特性的效果层折成同一量纲的分数，供 rollout 搜索当叶子。

**设计原则**
1. **类型字段优先，不靠技能名**：效果对象自带类型字段，直接按字段估值 ——
   `StatBuffEffect.stat_key/steps`、`MarkEffect.power_bonus/damage_mult/speed_penalty/
   energy_mod/turn_end_energy/switch_damage_pct/...`、`ModifierEffect.attr/value/mode`。
2. **伤害用引擎自己的换算**：回合末固定伤害（异常 tick + 印记末伤）直接调
   `tactics.predict_turn_end_damage`，不自己推百分比单位。
3. **与官方局面分同量纲**：基础项沿用官方权重（存活 1.0 / 血量比 0.5 / 心力 0.25 /
   能量 0.05），效果层是**附加项**，因此 E0 的 regret 数字换叶子前后可直接对比。
4. **权重可调**：集中在 `ValueParams`；v1 是手设值，只保证符号与单调性（见
   `backend/tests/test_value.py`），绝对量级靠 E0 的 regret 与同局配对 A/B 校准。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.sim.tactics import predict_turn_end_damage
from backend.vm.effect import AbnormalEffect, MarkEffect, ModifierEffect, StatBuffEffect


@dataclass(frozen=True)
class ValueParams:
    """估值权重（v1 手设）。基础项与 `outcome.team_battle_score` 同量纲。"""

    alive: float = 1.00
    hp_ratio: float = 0.50
    lives: float = 0.25
    energy: float = 0.05
    # 效果层附加项
    active_hp: float = 0.20        # 在场这只的额外血量权重（官方分没有）
    stat_value: float = 0.25       # 六维修正按**倍率**给：atk/def/sp_atk/sp_def 各 ±100% 折算
    speed_value: float = 0.05      # 速度每 ±1 步（= ±10 点）
    combo_step: float = 0.012      # 每级连击
    power_step: float = 0.004      # 每步威力（1 步 = 10 威力）
    mark_positive: float = 0.030   # 正面印记每层
    mark_negative: float = 0.030   # 负面印记每层
    tick_damage: float = 1.00      # 回合末固定伤害（血量比口径，打 5 折 —— 可以换人躲）
    switch_hazard: float = 0.50    # 自侧印记的进场伤害/掉能量（换人代价，打 5 折）
    bench_weight: float = 0.30     # 板凳身上的效果打折
    short_ttl_discount: float = 0.5  # 只剩 1 回合的效果打折
    # 「临场加权」：打满回合上限时按**血量差**判胜（`core/outcome.py` 的 draw_margin 口径，
    # 30K 数据集里打满的 4749 局有 62% 是这样判出来的），而叶子对血量的权重与回合数无关。
    # 该项 = clock_hp_weight × (turn / turn_cap) × (我血比 − 它血比)：越接近上限，血量差越当钱。
    clock_hp_weight: float = 0.0   # 0 = 关闭（出厂行为）
    turn_cap: int = 60             # 与 `core/outcome.py: DEFAULT_SELFPLAY_MAX_TURNS` 同口径


DEFAULT_PARAMS = ValueParams()

# 引擎口径：六维「步数」1 步 = 10%，且有效值下限为 0
# （`Sprite.effective_stat`：`max(0, base * (1 + steps / _STEP_PCT))`，`_STEP_PCT = 10`）。
# 因此 steps 是**无界**的（实测减益可累积到 -199980 步 = 把攻击压到 0），
# 估值必须按倍率算并夹到引擎自己的区间，不能按步数线性折算。
_STAT_STEP_CAP = 10.0     # ±10 步 = ±100%
_OTHER_STEP_CAP = 5.0     # 速度/连击/先手/能耗：±5 步


def _clamped(value: float, cap: float) -> float:
    return max(-cap, min(cap, float(value or 0.0)))


def _stat_multiplier_delta(steps: float) -> float:
    """步数 → 倍率增量（与 `effective_stat` 同口径：1 步 = 10%，下限 0）。"""
    capped = _clamped(steps, _STAT_STEP_CAP)
    return max(0.0, 1.0 + capped / _STAT_STEP_CAP) - 1.0


def _ttl_factor(effect, params: ValueParams) -> float:
    ttl = int(getattr(effect, "ttl", 0) or 0)
    return params.short_ttl_discount if ttl == 1 else 1.0


def _modifier_value(mod, params: ValueParams) -> float:
    """`ModifierEffect`：属性类按倍率折算，伤害倍率类按比例折算。"""
    attr = getattr(mod, "attr", "") or ""
    value = float(getattr(mod, "value", 0.0) or 0.0)
    if attr in ("atk", "def", "sp_atk", "sp_def"):
        # 这里的 value 是比率修正（如 0.2 = +20%），夹在 ±100%
        return params.stat_value * _clamped(value, 1.0)
    if attr == "speed":
        return params.speed_value * _clamped(value * 10.0, 5.0)
    if attr in ("power", "power_mod"):
        return params.power_step * _clamped(value / 10.0, 30.0)
    if attr in ("damage_mult", "damage_taken_mult"):
        return 0.1 * _clamped(value, 1.0)
    if attr == "energy_cost":
        return -0.02 * _clamped(value, 5.0)
    return 0.0


def _mark_value(mark, params: ValueParams) -> float:
    """印记：符号由 `category` 定（对持有侧是利还是害），量级由类型字段给。

    `turn_end_damage_pct` 不在这里算 —— 它由 `predict_turn_end_damage` 统一折算成血量比，
    避免与异常 tick 重复计入。
    """
    stacks = float(getattr(mark, "stacks", 0) or 0)
    if stacks <= 0:
        return 0.0
    harmful = (getattr(mark, "category", "negative") or "negative") == "negative"
    per = params.mark_negative if harmful else params.mark_positive
    v = (-per if harmful else per) * stacks
    # 类型字段（威力/伤害倍率/速度/能耗/末回能）：按"对持有侧的净影响"
    v -= stacks * float(getattr(mark, "power_bonus", 0) or 0) / 200.0
    v -= stacks * float(getattr(mark, "damage_mult", 0) or 0) * 0.1
    v -= stacks * float(getattr(mark, "speed_penalty", 0) or 0) * 0.002
    v += stacks * float(getattr(mark, "energy_mod", 0) or 0) * 0.02
    v += stacks * float(getattr(mark, "turn_end_energy", 0) or 0) * 0.02
    return v


def sprite_effect_value(battle, sprite, params: ValueParams = DEFAULT_PARAMS) -> float:
    """这只精灵身上的效果层价值（对**持有侧**的净影响；正=有利）。"""
    v = 0.0
    for effect in getattr(sprite, "active_effects", None) or []:
        f = _ttl_factor(effect, params)
        if isinstance(effect, StatBuffEffect):
            key = effect.stat_key
            steps = float(effect.steps or 0)
            if key in ("atk", "def", "sp_atk", "sp_def"):
                v += params.stat_value * _stat_multiplier_delta(steps) * f
            elif key == "speed":
                v += params.speed_value * _clamped(steps, _OTHER_STEP_CAP) * f
            elif key == "combo":
                v += params.combo_step * _clamped(steps, _OTHER_STEP_CAP) * f
            elif key == "power":
                v += params.power_step * _clamped(steps, 30.0) * f
            elif key in ("priority", "energy_cost"):
                v += params.combo_step * _clamped(steps, _OTHER_STEP_CAP) * f
        elif isinstance(effect, MarkEffect):
            v += _mark_value(effect, params) * f
        elif isinstance(effect, ModifierEffect):
            v += _modifier_value(effect, params) * f
        elif isinstance(effect, AbnormalEffect):
            pass  # 伤害部分统一由 predict_turn_end_damage 折成血量比
    v += params.combo_step * _clamped(
        float(getattr(sprite, "_modifiers", {}).get("combo", 0.0) or 0.0), _OTHER_STEP_CAP)
    return v


def team_value(battle, player, team: str, params: ValueParams = DEFAULT_PARAMS) -> float:
    """一方的局面分（对**该方**而言，正=好）。"""
    active = player.active
    alive = len(player.alive_sprites)
    hp_cur = sum(s.current_hp for s in player.team)
    hp_max = sum(max(1, s.max_hp) for s in player.team)
    hp_ratio = hp_cur / hp_max if hp_max > 0 else 0.0
    active_ratio = (active.current_hp / max(1, active.max_hp)) if active is not None else 0.0
    energy = (active.energy / 10.0) if active is not None else 0.0

    v = (alive * params.alive + hp_ratio * params.hp_ratio
         + max(0, player.lives) * params.lives + energy * params.energy
         + active_ratio * params.active_hp)

    for sprite in player.team:
        weight = 1.0 if sprite is active else params.bench_weight
        v += weight * sprite_effect_value(battle, sprite, params)

    marks = []
    try:
        marks = list(battle.globals.mark_effects.get(team, []) or [])
    except AttributeError:
        pass

    # 回合末固定伤害（只打场上那只，换人能躲 → 打五折）
    if active is not None and not active.is_fainted:
        tick = predict_turn_end_damage(battle, active, team)
        if tick > 0:
            v -= params.tick_damage * 0.5 * (tick / max(1, active.max_hp))

    # 换人代价：自侧印记的进场伤害与掉能量（换人决策才有意义）
    hazard = 0.0
    for mark in marks:
        stacks = float(getattr(mark, "stacks", 0) or 0)
        if stacks <= 0:
            continue
        hazard += stacks * (float(getattr(mark, "switch_damage_pct", 0) or 0)
                            + float(getattr(mark, "switch_energy_loss", 0) or 0) * 0.05)
    if hazard:
        v -= params.switch_hazard * hazard
    return v


def state_value(battle, side: str, params: ValueParams = DEFAULT_PARAMS) -> float:
    """叶子价值：`side` 视角的局面分差（我 − 对方）。"""
    me = battle.player_a if side == "A" else battle.player_b
    opp = battle.player_b if side == "A" else battle.player_a
    other = "B" if side == "A" else "A"
    v = team_value(battle, me, side, params) - team_value(battle, opp, other, params)
    if params.clock_hp_weight:
        cap = max(1, int(params.turn_cap))
        prog = min(1.0, max(0.0, getattr(battle, "turn", 0) / cap))
        me_hp = _team_hp_frac(me)
        opp_hp = _team_hp_frac(opp)
        v += params.clock_hp_weight * prog * (me_hp - opp_hp)
    return v


def _team_hp_frac(player) -> float:
    """全队血量比（存活精灵血量和 / 其最大血量和）——与判胜口径同量纲。"""
    cur = tot = 0.0
    for sp in getattr(player, "team", ()) or ():
        tot += max(1, int(getattr(sp, "max_hp", 0) or 0))
        cur += max(0, int(getattr(sp, "current_hp", 0) or 0))
    return cur / tot if tot else 0.0
