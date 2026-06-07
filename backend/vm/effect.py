"""EffectObject — unified effect identity and lifecycle.

EffectObject sits ABOVE the VM layer. It wraps JSON effect dicts with
identity (name, source) and lifecycle (scope, ttl). The VM still receives
raw dicts — EffectObject is purely metadata.

Two subtypes unify the currently-scattered effect systems:
  - ObserverEffect   → replaces ad-hoc Observer identity fields
  - ModifierEffect   → replaces sprite._direct_mod_tracked dict

Both funnel into sprite.active_effects for a single queryable source of truth.
Engine hooks (max_energy, starfall_consume_ratio) are handled as ModifierEffect
with sprite-level attrs read by the consuming property methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class EffectObject:
    """Base class for all effects — identity + lifecycle.

    Does NOT replace VM ops, Mutations, or Observers.
    It wraps them with metadata so effects can be tracked, queried, and cleaned up.
    """

    name: str                           # "冰封-入场封锁"
    source: str                         # "冰封" (trait or skill name)
    scope: str = "battlefield"          # turn | battlefield | persistent | permanent
    ttl: int = 0                        # remaining turns (0 = infinite)
    cooldown: int = 0                   # trigger cooldown; 0 = inactive

    def should_clear(self, reason: str) -> bool:
        """Unified lifecycle gate. No more per-system scope logic.

        Reasons:
            reload      — always clear (prevents duplicate registration)
            leave       — sprite switches out
            faint       — sprite is KO'd
            turn_end    — end of turn cleanup
        """
        if reason == "reload":
            return True
        if self.scope == "turn":
            return reason == "turn_end"
        if self.scope == "battlefield":
            return reason in ("leave", "faint")
        if self.scope == "persistent":
            return reason == "faint"
        return False  # permanent — never cleared by engine events


@dataclass(slots=True)
class ObserverEffect(EffectObject):
    """Passive trigger-based effect: condition → sub-effects.

    Carries the same config fields as the Observer dataclass,
    serving as its upstream identity wrapper.
    """

    cond: dict = field(default_factory=dict)
    then: list = field(default_factory=list)
    listen: frozenset = field(default_factory=frozenset)
    threshold: int = 1
    reset_on_fire: bool = True


@dataclass(slots=True)
class ModifierEffect(EffectObject):
    """Direct stat/skill modifier applied immediately on trait load.

    Replaces the ad-hoc _direct_mod_tracked dict on sprite.
    """

    target: str = "sprite_self"         # sprite_self | sprite_opp | skill_off_0
    attr: str = ""                      # power | atk | energy_cost | damage_mult
    value: float = 0.0
    mode: str = "add"                   # set | add | multiply
    skill_where: dict | None = None     # per-skill conditional filter


@dataclass(slots=True)
class AbnormalEffect(EffectObject):
    """Abnormal status effect: poison, burn, parasite, freeze, moe.

    Carries tick behavior so new abnormal types can be added via config
    without changing turn_end() or tick opcode handlers.
    """

    stacks: int = 0
    tick_damage_pct: float = 0.0       # HP% damage per tick (x stacks if tick_per_stack)
    tick_element: str = ""             # for element multiplier (克制)
    decay_on_tick: bool = False        # burn: (stacks + 1) // 2 each tick
    max_stacks: int = 0                # cap (freeze: 20); 0 = no cap
    tick_per_stack: bool = True        # True: dmg × stacks; False: flat dmg

    def tick_params(self) -> dict:
        """Engine reads this to compute tick damage."""
        return {
            "damage_pct": self.tick_damage_pct,
            "element": self.tick_element,
            "decay": self.decay_on_tick,
            "per_stack": self.tick_per_stack,
        }

    def apply_decay(self) -> int:
        """Burn decay: stacks // 2 (floor). Returns new stack count."""
        return self.stacks // 2


@dataclass(slots=True)
class MarkEffect(EffectObject):
    """Team-level mark effect. Replaces the _MARK_EFFECTS config dict.

    Each mark type is defined as a template in mark_config.py.
    When applied, the engine clones the template with the requested stacks.
    """

    stacks: int = 0
    category: str = "positive"            # "positive" | "negative"

    # Per-stack behavior fields (0 = no effect)
    power_bonus: int = 0                  # +N 威力 per stack
    damage_mult: float = 0.0              # +N% 伤害倍率 per stack
    speed_penalty: int = 0                # -N 速度 per stack
    energy_mod: int = 0                   # -N 能耗 per stack
    turn_end_energy: int = 0              # +N 能量 per stack at turn end
    turn_end_damage_pct: float = 0.0      # N% maxHP damage per stack at turn end
    switch_damage_pct: float = 0.0        # N% maxHP damage per stack on entry
    switch_energy_loss: int = 0           # -N 能量 per stack on entry
    starfall_damage: int = 0              # N 威力幻系魔法伤害 per stack

    condition: str = ""                   # "is_attack" | "is_first" | "not_first" | ""

    @property
    def is_positive(self) -> bool:
        return self.category == "positive"

    @property
    def is_negative(self) -> bool:
        return self.category == "negative"


@dataclass(slots=True)
class StatBuffEffect(EffectObject):
    """Stat buff/debuff — replaces StatusEffect(category="stat").

    Covers both visible stage changes (atk+30%) and visible modifier
    stats (combo+1, priority+2). Invisible modifiers (damage_mult, etc.)
    remain in sprite._modifiers only.
    """

    stat_key: str = ""                    # atk | def | sp_atk | sp_def | speed | power | combo | priority | energy_cost
    steps: int = 0                        # positive = buff, negative = debuff
    display_mult: float | None = None     # mult_mod ratio value for display only (not used in calculation)
    display_value: float | None = None    # absolute value for display only (combo, priority, etc.)
    is_inherent: bool = False             # True = from trait (not inherited by other traits)

    @property
    def is_positive(self) -> bool:
        if self.display_mult is not None:
            return self.display_mult > 0
        if self.display_value is not None:
            return self.display_value > 0
        return self.steps > 0

    @property
    def is_negative(self) -> bool:
        if self.display_mult is not None:
            return self.display_mult < 0
        if self.display_value is not None:
            return self.display_value < 0
        return self.steps < 0

    @property
    def display_name(self) -> str:
        """Human-readable label for UI: '物攻+30%', '先手+3', etc."""
        label = _STAT_LABELS.get(self.stat_key, self.stat_key)
        if self.display_mult is not None:
            return f'{label}{self.display_mult:+.0%}'
        unit = _STEP_UNITS.get(self.stat_key, 10)
        if self.stat_key in ('priority', 'energy_cost', 'combo'):
            return f'{label}{self.steps:+d}'
        if self.stat_key in ('speed', 'power'):
            if self.display_value is not None:
                sign = '+' if self.display_value > 0 else ''
                return f'{label}{sign}{self.display_value:.0f}'
            sign = '+' if self.steps > 0 else ''
            return f'{label}{sign}{self.steps * unit}'
        if self.stat_key == 'life_drain':
            sign = '+' if self.steps > 0 else ''
            return f'{label}{sign}{self.steps * unit}%'
        sign = '+' if self.steps > 0 else ''
        return f'{label}{sign}{self.steps * unit}%'


@dataclass(slots=True)
class StateEffect(EffectObject):
    """Special state effect — replaces StatusEffect(category="state").

    Covers transient states: charging, locked, redirect, interrupted, first_action, etc.
    """

    state_type: str = ""                  # "charging" | "locked" | "redirect" | "interrupted" | "first_action"
    params: dict = field(default_factory=dict)


# ── Stat display helpers (shared with sprite.py) ──

_STAT_LABELS: dict[str, str] = {
    'atk': '物攻', 'sp_atk': '魔攻', 'def': '物防', 'sp_def': '魔防',
    'speed': '速度', 'power': '威力', 'priority': '先手',
    'energy_cost': '能耗', 'combo': '连击', 'life_drain': '吸血',
    'power_mod': '威力', 'power_mult': '威力倍率', 'damage_mult': '伤害倍率',
    'damage_reduction': '减伤', 'energy_cost_mult': '能耗倍率',
    'heal_reverse': '回复反转', 'ignore_resistance': '无视抗性',
    'ignore_mods': '无视修正', 'survive': '不屈', 'combo_set': '连击固定',
    'swift': '迅捷', 'drive': '传动',
}

_STEP_UNITS: dict[str, int] = {
    'power': 10, 'speed': 10, 'life_drain': 10,
    'priority': 1, 'energy_cost': 1, 'combo': 1,
}
