"""backend/sim/skill_ir.py — 技能 IR 只读查询层（规则层用）。

**为什么需要这一层**：技能 JSON 的 `effects[]` 是 IR（op 形式），而 `Skill.effects`
（旧 kind 形式）对 IR 技能**是空的**（`Skill.load` 静默跳过没有 `kind` 的条目）。
任何"按 `skill.effects` 找增益/异常"的规则因此都会变成死代码 —— 实测：
`RuleAgentV2` 的"强化推队"规则（`e.kind == 'stat'`）在 IR 语料上恒不触发。

几条社区 PVP 攻略里的战术在这里都能落到**类型字段**上，不用按技能名写死：

| 攻略说法 | IR 依据（实测技能） |
|---|---|
| 清除敌方强化 / 退化 | `DispelOp` / `StealOp`；`StatStageOp(target=对手, steps<0)`（破绽 −7 双防） |
| 消耗对手能量 / 加对手能耗 | `EnergizeOp(target=对手, delta<0)`（恶作剧 −6）；`PowerModOp(attr='energy_cost', target=对手, delta>0)`（精神扰乱 +3） |
| 叠层强化（越打越强） | `StatStageOp(target=自己, steps>0)`（泥浆铠甲 攻/防各 +6）；`MultModOp(speed_flat/power_mult…)`（快速移动、热身） |
| 蓄能/节能 | `EnergizeOp(delta>0)`；`PowerModOp(attr='energy_cost', target=自己, delta<0)` |
| 打断节奏 / 封锁 | `InterruptOp`（摇篮曲）、`LockOp` |
| 异常叠层（中毒/灼烧/冻结…） | `AbnormalOp(name, stacks)`（剧毒 3→应对 8 层） |
| 印记体系（星陨印记等） | `MarkOp(name)` |

**分支语义**：`when/else` 是二选一（如剧毒 常态 3 层 / 应对成功 8 层），所以同族效果
取**最大值**而不是相加。"应对成功"分支单独标 `on_counter`，规则层据此判断"这条技能
的应对收益值多少"。

量纲：所有增益/减益统一折算成**步数**（1 步 = 10%，与 `Sprite.effective_stat` 同口径）。
`speed_flat` 按 10 点 = 1 步折算，`power_mult`/`damage_reduction` 按倍率折算。只用于
和阈值比较，不做精确伤害预测（伤害预测走 `resolver.calc_damage`）。

本模块**只读**：不落伤害、不改引擎状态。画像按技能名缓存（引擎同样假设 IR 与技能名
一一对应，见 `Battle._get_skill_record` 的全局缓存）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from backend.vm.ir_skill import (
    AbnormalOp,
    ChargeOp,
    DispelOp,
    DoubleOp,
    EnergizeOp,
    HealOp,
    InterruptOp,
    LockOp,
    MarkOp,
    MultModOp,
    PowerModOp,
    StatStageOp,
    StealOp,
    WhenBlock,
)

_SELF_TARGETS = frozenset({
    "sprite_self", "self", "team_own", "team_self", "sprite_own_random",
    "sprite_own_bench", "sprite_own", "self_team",
})
_OPP_TARGETS = frozenset({
    "sprite_opp", "opp", "team_opp", "sprite_opp_random", "sprite_opp_bench",
    "opp_team", "target", "sprite_target",
})

# mult_mod 的 attr → 折算成步数的系数（步数口径：1 步 = 10%）
_MULT_STEP_ATTRS = frozenset({
    "speed_flat", "atk", "sp_atk", "def", "sp_def", "speed",
})
# 这些 attr 是"输出/生存"类增益（power_mult / damage_reduction / combo_mult / ...）
_MULT_OUTPUT_ATTRS = frozenset({
    "power_mult", "damage_mult", "damage_reduction", "combo_mult", "life_drain", "priority",
})
# power_mod 的 attr → 是否算"强化"
_POWER_BUFF_ATTRS = frozenset({"power", "combo", "priority"})


def _num(value) -> float | None:
    """IRValue → float。`Literal(value=x)` 取 x；query 形式（RefExpr 等）返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    inner = getattr(value, "value", None)          # Literal
    if isinstance(inner, (int, float)):
        return float(inner)
    if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
        return float(value["value"])
    return None


def _side(target: str | None) -> str:
    """'self' | 'opp' | 'other'（未识别按 other，规则层保守忽略）。"""
    t = (target or "").strip()
    if t in _SELF_TARGETS:
        return "self"
    if t in _OPP_TARGETS:
        return "opp"
    return "other"


def iter_ops(effects, *, branch: str = "main") -> Iterator[tuple[object, str]]:
    """展开技能 IR：产出 (op, branch)。branch ∈ {main, on_counter, on_other}。

    `WhenBlock` 的 then 归 on_counter（本项目里 when 条件几乎都是 `counter_succeeded`，
    实测 113 处），elif 也按条件名判断；else 归 on_other（常态分支）。
    """
    for node in effects or ():
        if isinstance(node, WhenBlock):
            cond = getattr(getattr(node, "cond", None), "cond", "") or ""
            then_branch = "on_counter" if cond == "counter_succeeded" else "on_other"
            yield from iter_ops(node.then, branch=then_branch)
            yield from iter_ops(node.else_, branch="on_other")
            for extra in node.elif_:
                sub_cond = getattr(getattr(extra, "cond", None), "cond", "") or ""
                sub_branch = "on_counter" if sub_cond == "counter_succeeded" else "on_other"
                yield from iter_ops(extra.then, branch=sub_branch)
                # `WhenBranch` 只有 (cond, then)——没有 else_（见 vm/ir_skill.py）。
                # 此前这里读 `extra.else_`：全库 3 处 `else_if`（鸣沙陷阱/砂糖弹球/闪击）
                # 一进画像就 AttributeError（哑雷，2026-09-22 修）。
        elif isinstance(node, (tuple, list)):
            yield from iter_ops(node, branch=branch)
        else:
            yield node, branch


@dataclass(frozen=True)
class SkillProfile:
    """一条技能的 IR 画像（步数口径，粗略量级）。"""

    name: str = ""
    counters: str = ""                  # 应对标签（无|攻击|防御|状态）
    is_status: bool = False             # 状态技（引擎口径 skill_type == '状态'）

    self_buff_value: float = 0.0        # 给自己/己队的增益（折算步数）
    self_buff_stats: tuple[str, ...] = ()
    self_buff_on_counter: float = 0.0   # 其中"应对成功才吃到"的那部分
    opp_debuff_value: float = 0.0       # 给对手的减益（折算步数）
    opp_debuff_stats: tuple[str, ...] = ()

    dispels_opp: bool = False           # 驱散对手增益
    steals: bool = False                # 偷取
    opp_energy_drain: int = 0           # 让对手净损失的能量
    opp_energy_cost_pressure: int = 0   # 给对手技能加的能耗
    self_energy_gain: int = 0           # 给自己回的能量
    self_cost_reduction: int = 0        # 给自己技能的能耗减免
    heals: bool = False
    life_drain: bool = False
    interrupts: bool = False
    locks: bool = False
    charges: bool = False
    doubles_buffs: bool = False         # `DoubleOp`（应对成功时增益翻倍，如泥浆铠甲）
    abnorms: tuple[tuple[str, int], ...] = ()
    marks: tuple[str, ...] = ()

    @property
    def is_disruptive(self) -> bool:
        """干扰类：抽能量 / 加能耗 / 打断 / 封锁 / 驱散。"""
        return bool(self.opp_energy_drain or self.opp_energy_cost_pressure
                    or self.interrupts or self.locks or self.dispels_opp)


def _profile_from_effects(name: str, effects, skill) -> SkillProfile:
    # 同族取"按 key 最大值"再汇总：when/else 是二选一分支，直接相加会虚高
    buffs: dict[str, float] = {}
    buffs_counter: dict[str, float] = {}
    debuffs: dict[str, float] = {}
    drains: list[int] = []
    pressures: list[int] = []
    gains: list[int] = []
    cuts: list[int] = []
    abnorms: dict[str, int] = {}
    marks: list[str] = []
    flags = {"dispel": False, "steal": False, "heal": False, "drain": False,
             "interrupt": False, "lock": False, "charge": False, "double": False}

    for op, branch in iter_ops(effects):
        side_target = _side(getattr(op, "target", None))
        if isinstance(op, StatStageOp):
            steps = float(getattr(op, "steps", 0) or 0)
            if steps == 0:
                num = _num(getattr(op, "value", None))
                steps = num if num is not None else 0.0
            if steps == 0:
                continue
            key = str(getattr(op, "stat", "") or "stat")
            if side_target == "self" and steps > 0:
                (buffs_counter if branch == "on_counter" else buffs)[key] = max(
                    (buffs_counter if branch == "on_counter" else buffs).get(key, 0.0), steps)
            elif side_target == "opp" and steps < 0:
                debuffs[key] = max(debuffs.get(key, 0.0), -steps)
        elif isinstance(op, MultModOp):
            attr = str(getattr(op, "attr", "") or "")
            num = _num(getattr(op, "value", None))
            if num is None:
                continue
            mode = getattr(op, "mode", "set") or "set"
            if attr in _MULT_STEP_ATTRS:
                steps = num / 10.0                     # 速度/六维：10 点 = 1 步
            elif attr in _MULT_OUTPUT_ATTRS:
                steps = (num * 10.0) if mode == "add" else (num - 1.0) * 10.0
            else:
                steps = 0.0
            if steps <= 0:
                continue
            if side_target == "self":
                (buffs_counter if branch == "on_counter" else buffs)[attr] = max(
                    (buffs_counter if branch == "on_counter" else buffs).get(attr, 0.0), steps)
            elif side_target == "opp":
                debuffs[attr] = max(debuffs.get(attr, 0.0), steps)
            if attr == "life_drain":
                flags["drain"] = True
        elif isinstance(op, PowerModOp):
            attr = str(getattr(op, "attr", "") or "")
            delta = _num(getattr(op, "delta", None))
            if delta is None:
                continue
            if attr == "energy_cost":
                if side_target == "opp" and delta > 0:
                    pressures.append(int(delta))
                elif side_target == "self" and delta < 0:
                    cuts.append(int(-delta))
            elif attr in _POWER_BUFF_ATTRS and side_target == "self" and delta > 0:
                buffs[attr] = max(buffs.get(attr, 0.0), delta * 2.0)   # 威力步：1 步 ≈ 10 威力
        elif isinstance(op, EnergizeOp):
            delta = _num(getattr(op, "delta", None))
            if delta is None:
                continue
            if side_target == "opp" and delta < 0:
                drains.append(int(-delta))
            elif side_target == "self" and delta > 0:
                gains.append(int(delta))
        elif isinstance(op, DispelOp):
            flags["dispel"] = True
        elif isinstance(op, StealOp):
            flags["steal"] = True
        elif isinstance(op, HealOp):
            flags["heal"] = True
        elif isinstance(op, InterruptOp):
            flags["interrupt"] = True
        elif isinstance(op, LockOp):
            flags["lock"] = True
        elif isinstance(op, ChargeOp):
            flags["charge"] = True
        elif isinstance(op, DoubleOp):
            flags["double"] = True
        elif isinstance(op, AbnormalOp):
            cur = abnorms.get(str(op.name), 0)
            abnorms[str(op.name)] = max(cur, int(getattr(op, "stacks", 0) or 0))
        elif isinstance(op, MarkOp):
            marks.append(str(op.name))

    return SkillProfile(
        name=name,
        counters=str(getattr(skill, "counter", "") or ""),
        is_status=(getattr(skill, "skill_type", "") == "状态"),
        self_buff_value=sum(buffs.values()),
        self_buff_stats=tuple(buffs),
        self_buff_on_counter=sum(buffs_counter.values()),
        opp_debuff_value=sum(debuffs.values()),
        opp_debuff_stats=tuple(debuffs),
        dispels_opp=flags["dispel"] or flags["steal"],
        steals=flags["steal"],
        opp_energy_drain=max(drains, default=0),
        opp_energy_cost_pressure=max(pressures, default=0),
        self_energy_gain=max(gains, default=0),
        self_cost_reduction=max(cuts, default=0),
        heals=flags["heal"],
        life_drain=flags["drain"],
        interrupts=flags["interrupt"],
        locks=flags["lock"],
        charges=flags["charge"],
        doubles_buffs=flags["double"],
        abnorms=tuple(sorted(abnorms.items())),
        marks=tuple(dict.fromkeys(marks)),
    )


_PROFILE_CACHE: dict[str, SkillProfile] = {}


def skill_profile(battle, skill) -> SkillProfile:
    """取技能画像（按名字缓存）。IR 走引擎自己的编译记录。"""
    name = getattr(skill, "name", "") or ""
    cached = _PROFILE_CACHE.get(name)
    if cached is not None:
        return cached
    effects = ()
    getter = getattr(battle, "_get_skill_record", None)
    if getter is not None and name:
        try:
            effects = getter(name).effects
        except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
            effects = ()
    profile = _profile_from_effects(name, effects, skill)
    _PROFILE_CACHE[name] = profile
    return profile


def clear_profile_cache() -> None:
    """测试/热重载用：清空画像缓存。"""
    _PROFILE_CACHE.clear()


def total_buff_steps(sprite) -> float:
    """一只精灵身上**现存的**正向增益（步数口径），用于"对手已经叠起来了"判定。"""
    total = 0.0
    for effect in getattr(sprite, "active_effects", None) or []:
        steps = getattr(effect, "steps", None)
        if steps is None or not hasattr(effect, "stat_key"):
            continue
        try:
            value = float(steps or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            total += value
    for key, value in (getattr(sprite, "_modifiers", {}) or {}).items():
        if key in ("atk", "def", "sp_atk", "sp_def") and isinstance(value, (int, float)):
            if abs(float(value)) <= 3:          # 比率修正（0.6 = +60%）
                total += 10.0 * float(value)
    return total
