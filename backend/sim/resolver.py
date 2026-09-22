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

#: 技能 IR 里「本次使用才生效」的伤害/连击修正，按技能名缓存（估伤用）。
#: 元素 = (条件要求 tuple[(cond, 期望值), ...], kind, payload)，
#: kind ∈ {"add_power", "power_mult", "damage_mult", "combo_add", "combo_set", "combo_mult"}。
_SAME_TURN_OPS: dict[str, tuple] = {}

#: `power_mod`/`mult_mod` 的 target 里，属于「当前使用的这个技能」的拼写
_SELF_SKILL_TARGETS = frozenset({"skill_off_0", "skill_self", "self_skill"})

#: 技能 IR 里出现过的 op 类型名，按技能名缓存（`_skill_op_kinds`）
_SKILL_OP_KINDS: dict[str, frozenset] = {}


def _cond_name(cond) -> str:
    if isinstance(cond, dict):
        return str(cond.get("cond", ""))
    return str(getattr(cond, "cond", "") or "")


def _collect_same_turn(node, reqs: tuple, out: list) -> None:
    """递归收集技能自身 IR 中影响本次伤害/段数的修正（含 when 条件要求）。"""
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
            if (item.target or "") not in _SELF_SKILL_TARGETS:
                continue
            mode = getattr(item, "mode", "add") or "add"
            # 与 op_power_mod 同取：value 优先，否则 delta
            payload = item.value if item.value is not None else item.delta
            if item.attr == "power" and mode == "add":
                out.append((reqs, "add_power", payload))
            elif item.attr in ("combo", "combo_set"):
                # 与 op_power_mod + `_collect_modifiers_from_entries` 同口径：
                # mode:"set" → combo_set（绝对段数），其余 → combo_add
                out.append((reqs, "combo_set" if mode == "set" else "combo_add", payload))
            elif item.attr == "combo_mult":
                out.append((reqs, "combo_mult", (payload, mode)))
        elif isinstance(item, MultModOp) and (item.target or "") in _SELF_SKILL_TARGETS:
            payload = (item.value, getattr(item, "mode", "set") or "set")
            if item.attr == "power_mult":
                out.append((reqs, "power_mult", payload))
            elif item.attr == "damage_mult":
                out.append((reqs, "damage_mult", payload))
            elif item.attr == "combo_mult":
                out.append((reqs, "combo_mult", payload))


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


def _exec_ctx(battle, use, attacker, defender, globals_, team: str, *, pay_cost: bool = False):
    """估伤用的「本次使用」Ctx（引擎自己的 `Battle._make_ctx`）；取不到返回 None。

    `skill_index` 用于 `skill_at` 这类位置条件：传 -1（未知）时条件自然不成立 → 保守不计。

    `pay_cost=True` 镜像引擎的**支付次序**（先付能耗、再执行 effects）：技能自身
    写在 `effects[]` 里的 `=@self.energy * N` 读的是支付后的能量——实测甜蜜陷阱
    （50 威力、4 费、`+能量×10`）估伤 264 / 实战 194，差的就是这 4 点能量。
    蓄力门控在引擎里位于支付**之前**，故那里用默认的 `pay_cost=False`。
    """
    bs = use.battle_skill
    saved_energy = None
    if pay_cost:
        try:
            cost = battle.skill_energy_cost(team, attacker, bs, use.skill_index)
        except Exception:
            cost = 0
        if cost > 0:
            saved_energy = attacker.energy
            attacker.energy = max(0, attacker.energy - cost)
    try:
        return battle._make_ctx(attacker, defender, use.battle_skill, None, globals_,
                                team=team, skill_index=use.skill_index)
    except Exception:
        return None
    finally:
        if saved_energy is not None:
            attacker.energy = saved_energy


#: 技能「选择」分支（cond, name, ops），按技能名缓存（`choices` 里的分支效果）
_SKILL_CHOICES: dict[str, tuple] = {}


def _skill_choices(battle, skill_name: str) -> tuple:
    """技能自带 `choices` 各分支里「本次使用才生效」的修正。

    此前估伤**完全不读 `choices`**：分支内的同回合修正整批看不见（试飞分支 0
    「威力永久+10」在实战当手就折进 `(power+add)/power` = ×1.5，估伤 28 / 实战 42）。
    AI 的动作空间没有 branch 维（永远走分支 0 + 引擎兜底回退），因此镜像引擎那段
    选择逻辑即可对齐。
    """
    cached = _SKILL_CHOICES.get(skill_name)
    if cached is not None:
        return cached
    out: tuple = ()
    try:
        record = battle._get_skill_record(skill_name)
        choices = tuple(getattr(record, "choices", ()) or ())
    except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
        choices = ()
    if choices:
        items = []
        for c in choices:
            found: list = []
            _collect_same_turn(tuple(c.get("effects") or ()), (), found)
            items.append((c.get("cond"), c.get("name", ""), tuple(found)))
        out = tuple(items)
    _SKILL_CHOICES[skill_name] = out
    return out


def _skill_op_kinds(battle, skill_name: str) -> frozenset:
    """技能 IR 里出现过的 op 类型名（含嵌套 when 分支），按技能名缓存。

    用于「这个技能有没有 redirect/charge 类 op」这类**廉价前置筛**：
    筛掉之后热路径不建 Ctx、不跑 VM。
    """
    cached = _SKILL_OP_KINDS.get(skill_name)
    if cached is not None:
        return cached
    from backend.vm.ir_skill import WhenBlock

    kinds: set[str] = set()

    def walk(node) -> None:
        for item in node or ():
            kinds.add(type(item).__name__)
            if isinstance(item, WhenBlock):
                walk(item.then)
                walk(item.else_)
                for b in item.elif_:
                    walk(b.then)

    try:
        effects = battle._get_skill_record(skill_name).effects
    except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
        effects = ()
    if effects:
        walk(effects)
    found = frozenset(kinds)
    _SKILL_OP_KINDS[skill_name] = found
    return found


def _would_redirect_to_self(battle, bs, attacker, defender, use, globals_, team: str) -> bool:
    """本次使用的伤害是否被 `redirect` 打到**自己**（灾厄「未应对时对自己造成物伤」）。

    估伤口径是「对敌方的伤害」：打自己的技能应为 0（此前估 41、实战 0）。
    用引擎自己的 VM 复算条件（`counter_succeeded` 等分支与实战同判据）。
    """
    if battle is None:
        return False
    name = getattr(bs, 'name', '')
    if not name or "RedirectOp" not in _skill_op_kinds(battle, name):
        return False
    ctx = _exec_ctx(battle, use, attacker, defender, globals_, team)
    if ctx is None:
        return False
    try:
        from backend.vm.executor import execute as vm_execute
        from backend.vm.journal import Redirect
        journal = vm_execute(ctx, battle._get_skill_record(name).effects)
    except Exception:
        return False
    return any(isinstance(m, Redirect) and m.target in ("sprite_self", "self") for m in journal)


def _would_charge(battle, bs, attacker) -> bool:
    """本次使用是否只走「开始蓄力」（= 本回合不结算伤害）。

    镜像引擎自己的蓄力门控（`sim/battle.py::_charge_gate`，用的是同一个
    `_has_charge_op`/`Battle._skill_has_charge`，不另立一套判据）：

      - 技能不含 `charge` op → 不蓄力；
      - 已在蓄力中 → 门控要么把状态升成 `charged`（这就是那只蓄力技能 → 正常结算），
        要么放行别的技能（`usable_while_charging` / `charge_any_skill`）→ 都不算「本次蓄力」；
      - `pre_charged`（架势类）→ 跳过首次蓄力，立即结算；
      - 其余（未蓄力 + 有 `charge` op）→ 本回合开始蓄力，0 伤。

    实测：升龙咆哮 未蓄力时估伤 175 / 实战 0（那回合只产出 `Charge`）。
    """
    if battle is None:
        return False
    name = getattr(bs, 'name', '')
    if not name:
        return False
    try:
        record = battle._get_skill_record(name)
        has_charge = battle._skill_has_charge(record)
    except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
        return False
    if not has_charge:
        return False
    if getattr(attacker, '_charging', False):
        return False
    if int(getattr(attacker, '_modifiers', {}).get('pre_charged', 0) or 0) > 0:
        return False
    return True


def _same_turn_mods(battle, bs, attacker, defender, use, globals_,
                    team: str) -> dict:
    """技能自身的同回合修正 → {power_add, power_mult, damage_mult, combo_*}。

    实战路径：技能 effect 里的 `power_mod`/`mult_mod` 变成 ModifierInjection，
    由 `engine/modifiers` 汇总：`power`(add) → power_add → 按 `(power+add)/power`
    折进 power_mult；`power_mult` 按 mode 加/乘；`damage_mult` 一律相乘；
    `combo`/`combo_set`/`combo_mult` **不改伤害值，改段数**。
    这里用**引擎自己的 Ctx**（`Battle._make_ctx`）+ 引擎自己的条件求值
    （`vm/cond.compile_cond`）静态复算同一批修正——魔能爆「=@self.energy * 20」
    这类公式因此也能算；求值失败就不计（保守）。
    """
    out = {"power_add": 0, "power_mult": 1.0, "damage_mult": 1.0,
           "combo_add": 0, "combo_set": 0, "combo_mult": 0.0}
    if battle is None:
        return out
    name = getattr(bs, 'name', '')
    if not name:
        return out
    ops = _same_turn_ops(battle, name)
    choices = _skill_choices(battle, name)
    if not ops and not choices:
        return out
    # 同回合修正的 Ctx 按**支付后**状态建（引擎先付能耗再执行 effects）
    ctx = _exec_ctx(battle, use, attacker, defender, globals_, team, pay_cost=True)
    if ctx is None:
        return out

    # `choices` 分支：镜像引擎的分支选择（branch 0；cond 不成立 → 第一个无条件分支）
    if choices:
        branch = getattr(use, 'branch', None)
        idx = 0 if branch is None else max(0, min(int(branch), len(choices) - 1))
        cond, _bname, bops = choices[idx]
        from backend.vm.cond import compile_cond as _cc
        if cond is not None:
            try:
                if not bool(_cc(cond)(ctx)):
                    fb = next((c for c in choices if c[0] is None), choices[0])
                    bops = fb[2]
            except Exception:
                bops = choices[0][2]      # 条件求值失败 → 保守退回 0 号分支
        ops = tuple(ops) + tuple(bops)
    if not ops:
        return out

    from backend.vm.cond import compile_cond
    from backend.vm.resolve import resolve

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
                # **保留浮点**：引擎 `adjust_damage` 的 power_add 就是浮点
                # （钢钻「两侧威力和/3」= 21.645），截断会差 1 点
                out["power_add"] += float(resolve(ctx, payload))
            except Exception:
                continue
        elif kind in ("combo_add", "combo_set"):
            try:
                v = int(resolve(ctx, payload))
            except Exception:
                continue
            if kind == "combo_set":
                out["combo_set"] += v        # 与 adjust_damage 同构：set 与 add 相加
            else:
                out["combo_add"] += v
        else:
            value, mode = payload
            try:
                v = float(resolve(ctx, value))
            except Exception:
                continue
            if kind == "power_mult":
                out["power_mult"] = out["power_mult"] + v if mode == "add" else out["power_mult"] * v
            elif kind == "combo_mult":
                # 加成分数（1 = +100%）：与 `engine/modifiers` 的 combo_mult 通道同口径
                if mode == "add":
                    out["combo_mult"] += v
                elif mode == "multiply":
                    out["combo_mult"] = (1.0 + out["combo_mult"]) * v - 1.0
                else:
                    out["combo_mult"] = v
            else:
                out["damage_mult"] *= v
    return out


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

        # 蓄力类技能「未蓄力」时：本次使用只产出 `Charge`（开始蓄力），本回合 0 伤。
        # 引擎按 effects 的 when/else 决定，这里用同一套 VM 复算（实测：升龙咆哮
        # 未蓄力时估伤 175 / 实战 0，会让斩杀规则在「以为自己能一击带走」时选中它）。
        if _would_charge(self._battle, bs, attacker):
            return 0, ["蓄力（本回合不结算伤害）"]

        # `redirect`：本次伤害打到**自己**（灾厄「未应对时对自己造成物伤」）→
        # 对敌方的估伤为 0（口径是「对敌方伤害」，打自己的部分不在这里计）
        if _would_redirect_to_self(self._battle, bs, attacker, defender, use, globals_, attacker_team):
            return 0, ["重定向到自己（本技能未造成对敌伤害）"]

        # 付不起的技能：本次使用根本打不出来 → 估伤 0。
        # 判据用引擎自己的 `can_pay_skill_energy_cost`（掩码/门控同源），
        # 否则「威力很大但永远放不出」的技能会在威胁评估里被当成真实威胁
        # （实测：抛石 30 费 > 可达上限 20，估伤 95 / 实战 0）。
        if self._battle is not None:
            try:
                _idx = use.skill_index if (use.skill_index or -1) >= 0 else None
                _can_pay, _cost, _hp = self._battle.can_pay_skill_energy_cost(
                    attacker_team, attacker, bs, _idx)
            except Exception:
                _can_pay = True
            if not _can_pay:
                return 0, ["能量不足（本次无法使用）"]

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

        # 连击 = **N 次独立命中**（用户 2026-09-22 确认）：公式先按**单段**算
        # （`combo_count=1`，与 `vm/ops/hit.op_hit` 同口径），段数最后乘。
        # 与引擎同序：ctx 侧段数（技能级 + 门控后的精灵级 add/set，不含 combo_mult）
        # → 同回合 `combo`/`combo_set` 改写 → 再乘 `combo_mult`。
        from .battleskill import combo_base_count
        hits = combo_base_count(bs, attacker)

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
            combo_count=1,
            mark_bonus=mark_bonus,
        )

        # ── 技能自身的**同回合**修正：与 `engine/modifiers.adjust_damage` 同序 ──
        # 实战是先按基础威力算完伤害，再由 adjust_damage 乘折算倍率并取整
        # （`power_add` 折成 `(power+add)/power`）。这一「先算后乘」的次序对
        # 低威力技能影响很大：魔能爆 1 威力 +60 → 实战略 61（不是 41），
        # 所以这里也必须后乘而不是并进公式。
        st = _same_turn_mods(
            self._battle, bs, attacker, defender, use, globals_, attacker_team)
        st_power_mult = st["power_mult"]
        if st["power_add"] > 0 and bs.power > 0:
            st_power_mult *= (bs.power + st["power_add"]) / bs.power
        if st_power_mult != 1.0 or st["damage_mult"] != 1.0:
            # 单段：先取整、再按 adjust_damage 的 max(1, …) 语义兜底
            damage = round(damage * st_power_mult * st["damage_mult"])
            damage = max(1, damage) if damage > 0 else 0

        # ── 段数（同回合连击修正）──
        if st["combo_set"] > 0:
            hits = max(1, st["combo_set"] + st["combo_add"])
        elif st["combo_add"]:
            hits = max(1, hits + st["combo_add"])
        combo_mult = st["combo_mult"]
        if self._battle is not None and bs.combo_keyword:
            combo_mult += float(getattr(attacker, "_modifiers", {}).get("combo_mult", 0.0) or 0.0)
        if combo_mult > 0:
            hits = max(1, round(hits * (1.0 + combo_mult)))

        return damage * hits, events

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
