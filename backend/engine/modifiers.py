"""Modifier collector — compute effective modifier values from Journal.

After VM execution, the engine scans the journal for ModifierInjections,
computes effective values for each modifier category, and adjusts Damage
mutations accordingly. This bridges the gap between same-skill modifier
effects and the damage formula.

The VM is pure: op_hit only sees the pre-execution Ctx snapshot. Effects
that produce power_mult/damage_mult/etc. are ModifierInjections in the
journal — the engine must collect them and apply them to Damage.

Cross-skill modifiers (damage_reduction, stat_stages) are already in Ctx
via the snapshot → replayer → _modifiers loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.vm.journal import Damage, Journal, ModifierInjection

if TYPE_CHECKING:
    from backend.vm.ctx import Ctx


_DAMAGE_MOD_STATS = frozenset({
    "power",
    "power_mult",
    "damage_mult",
    "damage_reduction",
    "combo",
})

_ATTACK_SKILL_TYPES: frozenset[str] = frozenset({"物攻", "魔攻", "动态攻击"})

#: `data/skills/*.json` 的「有无额外效果」缓存（bare_* 判定用）。
_BARE_SKILL_CACHE: dict[str, bool] = {}
_SKILLS_DIR: list = []   # 单元素缓存：[Path | None]


def _skills_dir():
    if not _SKILLS_DIR:
        from pathlib import Path
        cand = Path("data") / "skills"
        if not cand.is_dir():
            cand = Path(__file__).resolve().parents[2] / "data" / "skills"
        _SKILLS_DIR.append(cand if cand.is_dir() else None)
    return _SKILLS_DIR[0]


def _skill_raw_has_extra_effects(name: str) -> bool | None:
    """从 `data/skills/<name>.json` 判断「有无额外效果」；拿不到 JSON 时返回 None。"""
    if name in _BARE_SKILL_CACHE:
        return _BARE_SKILL_CACHE[name]
    d = _skills_dir()
    if d is None:
        return None
    import json
    path = d / f"{name}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    extra = bool(raw.get("effects") or raw.get("choices") or raw.get("passive"))
    _BARE_SKILL_CACHE[name] = extra
    return extra


def skill_has_extra_effects(bs, battle=None) -> bool:
    """技能是否带额外效果（供 `bare_attack` / `bare_defense` / `bare_status` 判定）。

    「无额外效果」= 技能 JSON 的 `effects` / `choices` / `passive` 全为空
    （不移「携带的无额外效果的攻击技能，威力+30%」）；隐式伤害由 InjectHitPass
    注入，不算额外效果。

    取不到 JSON（纯程序构造的技能、找不到 data 目录）时保守返回 True（有额外效果），
    避免把带效果的技能误判成裸技能。`battle` 参数保留兼容旧签名，不参与判定。
    """
    skill = getattr(bs, "replaced_by", None) or getattr(bs, "base", None)
    if skill is None:
        return True
    if getattr(skill, "effects", None) or getattr(skill, "choices", None):
        return True   # 旧 kind 形式效果 / 选择分支
    name = getattr(skill, "name", "")
    extra = _skill_raw_has_extra_effects(name) if name else None
    if extra is None:
        return True   # 无数据来源 → 保守
    return extra


def matches_skill_filter(skill_filter, bs, *, sprite=None, ref_bs=None, battle=None) -> bool:
    """skill_filter 匹配：基础类型筛选 + 结构筛选（data/IR_GUIDE.md §3A）。

    - `attack` / `defense` / `status` / `all`：只看 skill_type；
    - `others`   = 除**参考技能**（`ref_bs`，本回合正在使用的技能）以外的携带技能
                   ——激怒「敌方除本回合使用的技能，其他技能能耗+3」；
    - `adjacent` = **参考技能**槽位两侧的技能（不环绕）
                   ——减压阀/联动装置/能量守恒/轴承支撑「两侧技能…」；
    - `bare_attack` / `bare_defense` / `bare_status` = 无额外效果的纯类型技能（不移）。

    结构筛选缺上下文（bs / ref_bs / sprite 缺失）时返回 False——此前未知 filter
    一律「匹配全部」，会把这些筛选静默放大成对全部技能生效。
    未知 filter 名保持既有行为（放行），以免历史数据里的小众写法变成空操作。
    """
    if not skill_filter or skill_filter == "all":
        return True
    skill_type = getattr(bs, "skill_type", "") if bs is not None else ""
    if skill_filter == "attack":
        return skill_type in _ATTACK_SKILL_TYPES
    if skill_filter == "defense":
        return skill_type == "防御"
    if skill_filter == "status":
        return skill_type == "状态"

    if skill_filter == "others":
        return bs is not None and ref_bs is not None and bs is not ref_bs

    if skill_filter == "adjacent":
        if bs is None or ref_bs is None or sprite is None:
            return False
        skills = list(getattr(sprite, "skills", None) or [])
        try:
            ref_idx = next(i for i, b in enumerate(skills) if b is ref_bs)
        except StopIteration:
            return False
        idx = next((i for i, b in enumerate(skills) if b is bs), None)
        return idx is not None and abs(idx - ref_idx) == 1

    if skill_filter.startswith("bare_"):
        want = skill_filter[5:]
        if want == "attack":
            if skill_type not in _ATTACK_SKILL_TYPES:
                return False
        elif want == "defense":
            if skill_type != "防御":
                return False
        elif want == "status":
            if skill_type != "状态":
                return False
        else:
            return False   # 未知 bare_ 类别：不匹配（而不是匹配全部）
        return not skill_has_extra_effects(bs, battle)

    return True  # unknown filters pass through（保持既有行为）


def _collect_modifiers_from_entries(entries: list[ModifierInjection], ctx: Ctx) -> dict:
    """Collect ModifierInjections from journal and compute effective values.

    Returns a dict of modifier category → effective value. Uses ctx for
    combo/damage-reduction base values and the journal for same-skill
    adjustments.

    Two-pass processing ensures deterministic mode priority regardless of
    journal entry order: all 'set' baselines are collected first, then
    'add' and 'multiply' are applied on top.
    """
    if not entries:
        # combo_mult 是 ctx 播种的跨技能持久修正（如「行动时连击数+100%」），
        # 不依赖本技能的 ModifierInjection——无注入的普攻也必须生效，
        # 否则这类特性对纯伤害技能静默失效。其余种子值均中性，可安全跳过。
        if ctx.combo_mult_self <= 0:
            return {}  # 快速路径：无修饰符且无跨技能连击倍率
        return {"combo_base": max(1, ctx.combo_self), "combo_mult": ctx.combo_mult_self}

    mods: dict[str, float] = {
        # op_hit already applied ctx.power_mult_self to Damage. This value is
        # only the same-skill delta emitted during the current VM execution.
        "power_mult": 1.0,
        "damage_mult": 1.0,
        "damage_reduction": ctx.damage_reduction_opp,
        "combo_add": 0,
        "combo_set": 0,
        "combo_base": max(1, ctx.combo_self),
        "combo_mult": ctx.combo_mult_self,
        "power_add": 0,
    }

    # ── Pass 1: collect set baselines (last set in journal wins) ──
    power_mult_base = 1.0
    dr_base = ctx.damage_reduction_opp

    for m in entries:
        if m.mode != "set":
            continue

        if m.stat == "power_mult":
            power_mult_base = m.value
        elif m.stat == "damage_reduction":
            dr_base = m.value
        elif m.stat in ("combo", "combo_set"):
            # op_power_mod 会把 mode:"set" 的连击数改写成 combo_set（绝对语义），
            # 两个名字都要认，否则「改为 N 连击」会被静默丢弃
            mods["combo_set"] = int(m.value)
        elif m.stat == "power" and m.value != 0:
            mods["power_base"] = m.value

    mods["power_mult"] = power_mult_base
    mods["damage_reduction"] = dr_base

    # ── Pass 2: apply adds and multiplies on top of baselines ──
    for m in entries:
        stat = m.stat
        value = m.value
        mode = m.mode

        if stat == "power_mult":
            if mode == "add":
                mods["power_mult"] += value
            elif mode != "set":
                mods["power_mult"] *= value
        elif stat == "damage_mult":
            mods["damage_mult"] *= value
        elif stat == "damage_reduction":
            if mode == "add":
                mods["damage_reduction"] = min(1.0, mods["damage_reduction"] + value)
            elif mode == "multiply":
                mods["damage_reduction"] = 1.0 - (1.0 - mods["damage_reduction"]) * (1.0 - value)
        elif stat in ("combo", "combo_set"):
            if mode == "add":
                mods["combo_add"] += int(m.value)
        elif stat == "power":
            if mode == "add":
                mods["power_add"] += value
            elif mode == "multiply":
                mods["power_mult"] *= value

    # Convert power_add to equivalent power_mult multiplier
    if mods.get("power_add", 0) > 0 and ctx.power_self > 0:
        effective_power = ctx.power_self + mods["power_add"]
        mods["power_mult"] *= effective_power / ctx.power_self

    # Compute same-skill damage_reduction delta (ctx already has cross-skill value)
    base_dr = ctx.damage_reduction_opp
    total_dr = mods.get("damage_reduction", base_dr)
    if total_dr > base_dr:
        mods["damage_reduction_delta"] = total_dr - base_dr

    return mods


def collect_modifiers(journal: Journal, ctx: Ctx) -> dict:
    """Collect same-skill damage modifiers from a Journal."""
    entries = [m for m in journal if isinstance(m, ModifierInjection)]
    return _collect_modifiers_from_entries(entries, ctx)


def adjust_damage(dmg: Damage, mods: dict) -> Damage:
    """Apply collected modifiers to a Damage mutation.

    power_mult, damage_mult, and combo_add are applied multiplicatively.
    damage_reduction is skipped here because op_hit already applied the
    Ctx snapshot value — only same-skill ModifierInjections of
    damage_reduction need to be accounted for.
    """
    power_mult = mods.get("power_mult", 1.0)
    damage_mult = mods.get("damage_mult", 1.0)
    combo_add = mods.get("combo_add", 0)
    combo_set = mods.get("combo_set", 0)
    combo_base = mods.get("combo_base", 1)
    combo_mult = mods.get("combo_mult", 0.0)
    mods.get("damage_reduction", 0.0)

    # Only adjust for same-skill modifier deltas
    amount = dmg.amount
    amount = round(amount * power_mult * damage_mult)

    # combo: set overrides base, add adds to it
    effective_combo = max(1, combo_set + combo_add) if combo_set > 0 else max(1, combo_base + combo_add)

    # combo_mult 在最后乘入（跨技能倍率，排序在 set/add 之后）
    if combo_mult > 0:
        effective_combo = max(1, round(effective_combo * (1 + combo_mult)))

    if effective_combo != combo_base and combo_base > 0:
        amount = round(amount * effective_combo / combo_base)

    # Only apply extra damage_reduction beyond what op_hit already applied
    # (op_hit uses ctx snapshot damage_reduction; same-skill mods add extra)
    extra_dr = mods.get("damage_reduction_delta", 0.0)
    if extra_dr > 0:
        amount = round(amount * (1.0 - extra_dr))

    # If damage was already fully negated by op_hit (via calc_damage's
    # damage_reduction >= 1.0 early return), or same-skill damage_reduction
    # delta reduced it to 0 (e.g. 逐魂鸟 pre_defend), keep it at 0.
    if amount <= 0:
        return Damage(
            target=dmg.target,
            amount=0,
            element=dmg.element,
            type=dmg.type,
        )

    return Damage(
        target=dmg.target,
        amount=max(1, amount),
        element=dmg.element,
        type=dmg.type,
    )


def eval_skill_where(skill_where: dict | None, skill: dict) -> bool:
    """Evaluate a skill_where condition against a single skill's properties.

    Two formats supported:
      Query format:  {"q": "energy_cost", "op": "gt", "value": 3}
      Shorthand:     {"name": "虫鸣", "element": "虫"}
                     All key=value pairs must match (AND logic).

    Returns True if the skill matches the condition (or no condition).
    """
    if not skill_where:
        return True

    # Shorthand: {"name": "虫鸣"} → match all field=value pairs (equality)
    # 值可以是列表 → 成员包含（{"name": ["音波弹", "音爆", ...]}）
    if "q" not in skill_where and "op" not in skill_where:
        for field, expected in skill_where.items():
            actual = skill.get(field)
            if isinstance(expected, (list, tuple, set, frozenset)):
                if actual is None or actual not in expected:
                    return False
            elif actual is None or actual != expected:
                return False
        return True

    # Query format: {"q": "energy_cost", "op": "gt", "value": 3}
    q = skill_where.get("q", "")
    op = skill_where.get("op", "eq")
    expected = skill_where.get("value")
    actual = skill.get(q)
    if actual is None:
        return False
    if op == "gt":
        return actual > expected
    elif op == "gte":
        return actual >= expected
    elif op == "lt":
        return actual < expected
    elif op == "lte":
        return actual <= expected
    elif op == "eq":
        return actual == expected
    elif op == "neq":
        return actual != expected
    return False


def select_skills_by_element(skills: list[dict], per_element: int) -> list[dict]:
    """Select skills when element='each', taking at most per_element per element group.

    Skills are grouped by element, then the first per_element from each group
    are selected (in original order within each group).
    """
    if per_element is None or per_element <= 0:
        return list(skills)
    groups: dict[str, list[dict]] = {}
    for s in skills:
        el = s.get("element", "普通")
        groups.setdefault(el, []).append(s)
    selected = []
    for el, group in groups.items():
        selected.extend(group[:per_element])
    return selected


def apply_modifiers_to_journal(journal: Journal, ctx: Ctx) -> Journal:
    """Scan journal, collect modifiers, and adjust all Damage mutations.

    Returns a new Journal with adjusted Damage amounts. Non-Damage
    mutations pass through unchanged.
    """
    has_damage = False
    has_damage_mod = False
    entries: list[ModifierInjection] = []

    for m in journal:
        if isinstance(m, Damage):
            has_damage = True
        elif isinstance(m, ModifierInjection):
            entries.append(m)
            if m.stat in _DAMAGE_MOD_STATS:
                has_damage_mod = True

    if not has_damage:
        return journal
    if not has_damage_mod and ctx.combo_mult_self <= 0:
        return journal

    mods = _collect_modifiers_from_entries(entries, ctx)
    # 快速路径：无有效修饰符
    if not mods:
        return journal
    if (
        mods.get("power_mult", 1.0) == 1.0
        and mods.get("damage_mult", 1.0) == 1.0
        and mods.get("combo_add", 0) == 0
        and mods.get("combo_set", 0) == 0
        and mods.get("combo_mult", 0.0) <= 0
        and mods.get("damage_reduction_delta", 0.0) <= 0
    ):
        return journal

    result: Journal = []
    for m in journal:
        if isinstance(m, Damage):
            result.append(adjust_damage(m, mods))
        else:
            result.append(m)
    return result
