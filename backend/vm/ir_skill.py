"""技能 IR 节点 — 21 op + WhenBlock + SkillCondition."""
from __future__ import annotations

from dataclasses import dataclass, field

from .ir_values import IRValue

# ── Condition ──

@dataclass(frozen=True, slots=True)
class CondExpr:
    cond: str
    params: dict[str, object] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.cond, str(sorted(self.params.items()))))

@dataclass(frozen=True, slots=True)
class AndCond:
    conditions: tuple[SkillCondition, ...]

@dataclass(frozen=True, slots=True)
class OrCond:
    conditions: tuple[SkillCondition, ...]

@dataclass(frozen=True, slots=True)
class NotCond:
    condition: SkillCondition

SkillCondition = CondExpr | AndCond | OrCond | NotCond


# ── When block ──

@dataclass(frozen=True, slots=True)
class WhenBranch:
    cond: SkillCondition
    then: tuple[SkillIROp, ...]

@dataclass(frozen=True, slots=True)
class WhenBlock:
    cond: SkillCondition
    then: tuple[SkillIROp, ...]
    else_: tuple[SkillIROp, ...] = ()
    elif_: tuple[WhenBranch, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0


# ── Op nodes (28: 7 RISC + 14 specialist + 7 legacy) ──

# RISC register-modifying ops (split from ModOp)
@dataclass(frozen=True, slots=True)
class StatStageOp:
    """RISC: stat_stage — modify sprite stat stages (atk/def/sp_atk/sp_def/speed)."""
    target: str = "sprite_self"
    stat: str = ""
    steps: int = 0
    value: IRValue | None = None   # query-based steps (RefExpr)
    per_hit: bool = False
    scope: str = "battlefield"
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class StatRandomOp:
    """RISC: stat_random — 随机 N 层属性增益/减益（逐层随机分配到五维）。

    随机分配无法用 stat_stage 组合表达（编译期不知道分配结果），故单独一条指令。
    分配在 replayer 落地（`_apply_stat_random`），随机源是全局 random（可播种）。
    """
    target: str = "sprite_self"
    layers: int = 0
    value: IRValue | None = None      # Query/RefExpr 形式的 layers（动态层数）
    direction: str = "positive"      # "positive" | "negative"
    stats: tuple[str, ...] = ()      # 空 = 五维（物攻/物防/魔攻/魔防/速度）
    scope: str = "battlefield"
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class StatConvertOp:
    """RISC: stat_convert — 属性增益 ⇄ 属性减益转换（层数不变、只翻符号）。"""
    target: str = "sprite_opp"
    from_: str = "positive"          # "positive" | "negative"
    to: str = ""                     # 空 = from 的反面
    name: str | None = None          # 只转换指定维度
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class PowerModOp:
    """RISC: power_mod — modify skill attributes (power/energy_cost/combo/priority)."""
    target: str = "sprite_self"
    attr: str = ""
    delta: IRValue | None = None
    value: IRValue | None = None
    mode: str = "add"
    per_hit: bool = False
    scope: str = "battlefield"
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    skill_filter: str | None = None
    element: str | None = None
    ttl: int = 0
    source: str | None = None
    name: str | None = None
    on_next: bool = False   # 下一次行动才生效（如「下一次行动先手+1」）
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class MultModOp:
    """RISC: mult_mod — modify multipliers (power_mult/damage_mult/damage_reduction/life_drain)."""
    target: str = "sprite_self"
    attr: str = ""
    value: IRValue | None = None
    mode: str = "set"
    per_hit: bool = False
    scope: str = "battlefield"
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    skill_filter: str | None = None
    element: str | None = None
    source: str | None = None
    on_next: bool = False
    if_type: str | None = None
    name: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class FlagSetOp:
    """RISC: flag_set — set/clear boolean flags (immune/freeze_immune/survive/...)."""
    target: str = "sprite_self"
    flag: str = ""
    value: IRValue | None = None   # true/false
    name: str | None = None        # e.g. abnormal name for immune
    scope: str = "battlefield"
    source: str | None = None
    skill_filter: str | None = None   # flag:"cooldown" 时选定技能（attack/defense/status/all）
    skill_where: dict | None = None   # flag:"cooldown" 时的技能筛选条件
    ttl: int = 0                      # flag:"cooldown" + value:true 时的冷却回合数
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class HealOp:
    """RISC: heal — HP recovery or damage."""
    target: str = "sprite_self"
    ratio: float | None = None     # 0-1 ratio of max HP
    value: IRValue | None = None   # absolute HP or query
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class EnergizeOp:
    """RISC: energize — energy recovery or drain."""
    target: str = "sprite_self"
    delta: IRValue | None = None   # positive=recover, negative=drain
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ReviveOp:
    """RISC: revive — revive a fainted sprite."""
    target: str = "sprite_self"
    hp_ratio: IRValue | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

# Legacy mega-opcode — still accepted but new code should use the 7 RISC types above
@dataclass(frozen=True, slots=True)
class ModOp:
    target: str
    stat: str
    value: IRValue
    mode: str = "set"
    scope: str = "battlefield"
    steps: int = 0
    on_next: bool = False
    per_hit: bool = False
    skill_filter: str | None = None
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    if_type: str | None = None
    element: str | None = None
    per_element: int | None = None
    name: str | None = None
    delay: int = 0
    ttl: int = 0
    cooldown: int = 0
    source: str | None = None
    mutate: bool = False
    then: list | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class DevotionOp:
    """devotion — 队伍奉献注册（原 op:"mod" stat="devotion" 专用化）。"""
    target: str
    value: IRValue
    mode: str = "add"
    scope: str = "battlefield"
    name: str | None = None
    then: list | None = None
    ttl: int = 0
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class HitOp:
    power: IRValue
    type: str
    element: str | None = None
    combo: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class MarkOp:
    target: str
    name: str
    stacks: int = 1
    value: IRValue | None = None
    per_hit: bool = False
    then: tuple[SkillIROp, ...] = ()
    action: str = "apply"           # "apply" | "dispel" | "steal" | "convert"
    ratio: float = 1.0              # convert: abnormal→mark conversion ratio
    target_team: str = ""           # for dispel/steal/conversion: which team to target
    source: str | None = None       # trait/skill name for tracking/dispel
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class AbnormalOp:
    target: str
    name: str
    stacks: int = 1
    value: IRValue | None = None
    scope: str = "battlefield"
    per_hit: bool = False
    heal_pct: float = 0.0
    energy_gain: int = 0
    then: tuple[SkillIROp, ...] = ()
    source: str | None = None       # trait/skill name for tracking/dispel
    duration: int = 0               # turns until auto-expire (0 = persistent)
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class WeatherOp:
    weather: str
    turns: int = 8
    extend: bool = False   # true = 延长同天气（见 IR_GUIDE §3B weather）
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ReplaceSkillOp:
    """RISC: replace_skill — 把对手本回合的技能替换为指定技能（回合末还原）。"""
    target: str = "skill_opp_current"
    skill: str = ""
    scope: str = "turn"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class DispelOp:
    target: str
    what: str
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None
    source: str | None = None       # only dispel effects from this source
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class StealOp:
    target: str
    what: str
    name: str | None = None
    amount: int = 0
    action: str = "steal"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class TickOp:
    target: str
    name: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class DoubleOp:
    target: str
    what: str
    name: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class EffectDeltaOp:
    target: str
    what: str = "negative"
    delta: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ChargeOp:
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class EscapeOp:
    target: str
    inherit: bool = False
    urgent: bool = False
    then: tuple[SkillIROp, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ReturnOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class LockOp:
    target: str
    turns: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class InterruptOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ExchangeOp:
    what: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ResetOp:
    target: str
    stat: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class RedirectOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class ReplayOp:
    from_: str
    skill_filter: dict | None = field(default=None, hash=False, compare=False)
    what: str = ""
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class BorrowOp:
    from_: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class CountOp:
    name: str = ""
    when: SkillCondition | None = None
    then: tuple[SkillIROp, ...] = ()
    scope: str = "persistent"
    threshold: int = 1               # fire then every N triggers (1 = every time)
    reset_on_fire: bool = True       # reset counter after then executes
    feeds: str = ""
    needs: str = ""
    priority: int = 0


# ── Trait-level ops (engine-replayed, added Phase C1) ──

@dataclass(frozen=True, slots=True)
class TeamCounterWrite:
    """Write to a team-level counter (e.g. skill element usage count)."""
    target: str = "own"     # "own" | "opp"
    key: str = ""           # counter key name
    delta: int = 1          # +1 or -1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class LivesChange:
    """Modify player lives (魔力)."""
    target_team: str = "own"  # "own" | "opp"
    delta: int = 1            # +1 (奉献) or -1 (魔力消耗)
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class Schedule:
    """Register delayed effects for a future turn (RISC: defer)."""
    turns: int = 0
    at: str = "turn_start"                       # "turn_start" | "turn_end"
    then: tuple[SkillIROp, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ReplayChoiceOp:
    """replay_branch — 重放当前「选择」技能的另一支/相同一支（有求必应/一意孤行）。"""
    which: str = "other"                         # "other" | "same"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class InheritEffects:
    """Transfer effects from one sprite to another on switch."""
    source: str = "self"          # "self" | "target"
    inherit_target: str = "enemy_new"  # sprite ref for the receiver
    scope: str = "battlefield"
    via_pending: bool = False     # route through battle.pending_effects
    effects: tuple[SkillIROp, ...] = ()  # fixed effects to pass to incoming sprite
    inherit_stat_effects: bool = False  # copy dynamic stat effects from leaving sprite
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class Transform:
    """Transform a sprite's species and optionally skills."""
    species: str
    skills: tuple[str, ...] | None = None  # skill names; None = keep current
    reset_hp: bool = False
    reset_energy: bool = False
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True, slots=True)
class GainSkills:
    """Grant temporary skills to a sprite from a skill pool (learnset or global)."""
    count: int = 1
    exclude_carried: bool = True
    source: str = "learnset"   # "learnset" | "global"
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class TraitInteraction:
    """Suppress, remove, or copy a trait."""
    action: str         # "suppress" | "remove" | "copy"
    target: str         # sprite ref
    copy_from: str | None = None
    new_ability: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class BurstGrantOp:
    """Grant burst effects to matching skills (trait direct effect)."""
    target: str = "sprite_self"
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    skill_filter: str | None = None
    then: tuple[SkillIROp, ...] = ()
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class AuraOp:
    """RISC: aura — 按命名计数源持续重算的属性修饰。

    stat 每满一个 count 单位 +per_unit 步（1 步 = 10%；speed 为 10 点）。
    count 指向引擎计数源注册表（backend.engine.auras.COUNT_SOURCES）中的名字，
    例如 "mark_kinds_both" / "positive_kinds_both" / "lethal_forecast"。
    可复用于任何「每有 X 便获得 Y」的持续型特性。
    """
    target: str = "sprite_self"
    stat: str = ""
    per_unit: int = 1
    count: str = ""
    count_params: dict | None = field(default=None, hash=False, compare=False)
    scope: str = "battlefield"
    affects: str = "self"      # "self" | "both"（both = 场上双方）
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ElementConvertOp:
    """RISC: element_convert — 在场时把命中的技能属性转换为另一属性。"""
    target: str = "sprite_self"
    from_element: str = ""
    to_element: str = ""
    skill_filter: str | None = None
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    scope: str = "battlefield"
    affects: str = "self"      # "self" | "both"（both = 场上双方）
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class MorphOp:
    """RISC: morph — 巧变授予：命中的技能每次使用后变为 category 类别的技能。

    category 指向引擎巧变池注册表（backend.engine.morph.POOL_BUILDERS）。
    """
    target: str = "sprite_self"
    category: str | dict = "same_element"
    skill_filter: str | None = None
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    scope: str = "battlefield"
    affects: str = "self"      # "self" | "both"（场上双方） | "team"（己方队伍，含场下）
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class GrantChoiceOp:
    """RISC: grant_choice — 为命中技能（或聚能行动）附加一个可选分支。

    action="" 作用于技能（按 skill_filter/skill_where 匹配）；
    action="gather" 作用于聚能行动（记录在精灵上）。
    """
    target: str = "sprite_self"
    action: str = ""
    name: str = ""
    choices: tuple = ()
    skill_filter: str | None = None
    skill_where: dict | None = field(default=None, hash=False, compare=False)
    element: str | None = None
    scope: str = "battlefield"
    affects: str = "self"      # "self" | "both"（both = 场上双方）
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class CounterOp:
    """RISC: counter — 读改精灵级计数器（累积/重置，跨回合持久）。"""
    target: str = "sprite_self"
    key: str = ""
    delta: IRValue | None = None
    value: IRValue | None = None
    mode: str = "add"          # "add" | "set"
    scope: str = "persistent"
    source: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0


SkillIROp = (
    # RISC register-modifying ops
    StatStageOp | StatRandomOp | StatConvertOp | PowerModOp | MultModOp | FlagSetOp |
    HealOp | EnergizeOp | ReviveOp | CounterOp | AuraOp |
    ElementConvertOp | MorphOp | GrantChoiceOp |
    # Team resources
    DevotionOp |
    # Specialist ops
    HitOp | MarkOp | AbnormalOp | WeatherOp | ReplaceSkillOp |
    DispelOp | StealOp | TickOp | DoubleOp | EffectDeltaOp | ChargeOp |
    EscapeOp | ReturnOp | LockOp | InterruptOp |
    ExchangeOp | ResetOp | RedirectOp | ReplayOp |
    BorrowOp | CountOp | WhenBlock |
    TeamCounterWrite | LivesChange | Schedule |
    InheritEffects | Transform | TraitInteraction |
    GainSkills | BurstGrantOp | ReplayChoiceOp
)

# ── Compiled skill (frozen output of SkillCompiler) ──

@dataclass(frozen=True, slots=True)
class CompiledSkill:
    """Frozen, validated skill produced by the SkillCompiler pipeline."""
    id: int
    name: str
    element: str
    skill_type: str
    power: int
    energy_cost: int
    priority: int = 0
    combo: int = 1
    counter: str = ""
    effects: tuple[SkillIROp, ...] = ()
    description: str = ""
    tag: str = ""
    use_devotion: bool = False
    usable_while_charging: bool = False
    position_locked: bool = False
    # 「选择」分支（明/暗 等）：每项 {"name", "cond"(dict|None), "effects"(tuple)}
    choices: tuple = ()
