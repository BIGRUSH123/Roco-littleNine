"""mechanisms — 授予型机制的引擎侧通用服务（无特性名硬编码）。

数据面用 IR op 声明机制（`aura` / `element_convert` / `morph` / `grant_choice`），
落点为授予者精灵身上的 `GrantEffect`。本模块提供三类通用能力：

1. **计数器源注册表**（`COUNT_SOURCES`）：`aura` 的数值来源。新增计数源 = 注册一个
   函数，任何特性数据即可复用（`register_count_source`）。
2. **需求值**：`element_for(battle, sprite, bs)` / `morph_category(...)` /
   `choices_for(...)`——在技能使用点按需从声明求值，因此**无额外持久状态**，
   换人/离场由 scope 清理声明即可。
3. **重算**：`refresh(battle)` 幂等重算所有 aura（把已应用步数记录在
   `sprite.counters["aura:<source>:<stat>"]`，应用差值）。

生命周期：声明是 `GrantEffect(scope=...)`，`Sprite.clear_effects()` 会连同
`aura:` 追踪计数一起清掉，因此换人来回不会漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.sprite import Sprite

# ═══════════════════════════════════════════════════════════════
# 计数器源注册表（aura）
# ═══════════════════════════════════════════════════════════════

COUNT_SOURCES: dict[str, object] = {}


def register_count_source(name: str, fn) -> None:
    """注册计数源：fn(battle, sprite, params) -> int。"""
    COUNT_SOURCES[name] = fn


def _active(battle: Battle, team: str) -> Sprite | None:
    player = battle.get_player(team)
    return player.active if player is not None else None


def _team_of(battle: Battle, sprite: Sprite) -> str:
    for team in ('A', 'B'):
        if sprite in battle.get_player(team).team:
            return team
    return 'A'


def _count_mark_kinds_both(battle: Battle, sprite: Sprite, params) -> int:
    names: set[str] = set()
    for team in ('A', 'B'):
        for me in battle.globals.mark_effects.get(team, ()):
            if getattr(me, 'stacks', 0) > 0:
                names.add(getattr(me, 'name', ''))
    names.discard('')
    return len(names)


def _count_positive_kinds_both(battle: Battle, sprite: Sprite, params) -> int:
    from backend.vm.effect import AbnormalEffect, StatBuffEffect

    kinds: set[tuple[str, str]] = set()
    for team in ('A', 'B'):
        active = _active(battle, team)
        if active is None:
            continue
        for e in getattr(active, 'active_effects', ()):
            # 光环自身施加的步数不计入增益种类（否则会自指反馈）
            if str(getattr(e, 'source', '')).startswith("aura:"):
                continue
            if isinstance(e, StatBuffEffect):
                if getattr(e, 'steps', 0) > 0:
                    kinds.add(("stat", str(getattr(e, 'stat_key', ''))))
            elif isinstance(e, AbnormalEffect):
                if getattr(e, 'category', '') == 'positive':
                    kinds.add(("abn", str(getattr(e, 'name', ''))))
    return len(kinds)


def _count_lethal_forecast(battle: Battle, sprite: Sprite, params) -> int:
    """敌方当前可用技能是否足以击败自己（1/0，估算公式同 calc_damage）。"""
    team = _team_of(battle, sprite)
    enemy = _active(battle, 'B' if team == 'A' else 'A')
    if enemy is None or sprite.is_fainted:
        return 0
    return 1 if forecast_lethal(enemy, sprite) else 0


def forecast_lethal(enemy: Sprite, me: Sprite) -> bool:
    """估算：敌方任一可用攻击技能能否一击击败自己。"""
    from backend.sim.resolver import _TYPE_CHART
    from backend.vm.damage import calc_damage

    hp = me.current_hp
    if hp <= 0:
        return False
    my_elements = [a.strip() for a in
                   (getattr(me.species, 'attributes', '') or '').split(',') if a.strip()]
    reduction = me.damage_reduction_modifier
    for bs in (enemy.skills or ()):
        if bs.sealed or bs.nullified or bs.cooldown > 0:
            continue
        sk = bs.replaced_by or bs.base
        if not getattr(sk, 'is_attack', False) or bs.power <= 0:
            continue
        keys = sk.get_atk_def_keys(enemy)
        if not keys:
            continue
        atk_key, def_key = keys
        atk = enemy.effective_stat(atk_key)
        dfn = me.effective_stat(def_key)
        if atk <= 0 or dfn <= 0:
            continue
        chart = _TYPE_CHART.get(bs.element, {})
        type_mult = 1.0
        for el in my_elements:
            type_mult *= chart.get(el, 1.0)
        # 连击 = N 次独立命中：单段伤害 × 段数（与实战同序；此前把段数并进公式，
        # 等于只在末尾取整一次）
        from backend.sim.battleskill import effective_combo
        amount = calc_damage(
            bs.power, atk, dfn,
            type_mult=type_mult,
            damage_reduction=reduction,
            combo_count=1,
        ) * effective_combo(bs, enemy)
        if amount >= hp:
            return True
    return False


register_count_source("mark_kinds_both", _count_mark_kinds_both)
register_count_source("positive_kinds_both", _count_positive_kinds_both)
register_count_source("lethal_forecast", _count_lethal_forecast)


# ═══════════════════════════════════════════════════════════════
# 声明查询
# ═══════════════════════════════════════════════════════════════

def _grant_effects(sprite: Sprite, mechanism: str, target: Sprite):
    """产出 (授予者, GrantEffect)：授予者是 sprite 自身，或（affects="both" 时）
    站在场上另一方、并把机制授予全场的精灵。"""
    if sprite is None:
        return
    from backend.vm.effect import GrantEffect

    for e in getattr(sprite, 'active_effects', ()):
        if isinstance(e, GrantEffect) and e.mechanism == mechanism:
            yield e


def _grantors(battle: Battle, sprite: Sprite, mechanism: str):
    """返回可能把 mechanism 授予 sprite 的 (声明) 列表。

    三条来源：自身声明（affects=self/team/both）+ **同队（含场下）** 的 affects=team
    声明（齐鸣：「己方精灵携带的虫系技能获得巧变：虫鸣」）+ 对方场上精灵的 affects=both
    声明（魔术帽）。三种机制（element_convert / morph / grant_choice）共用本函数。
    """
    mech = mechanism
    for e in _grant_effects(sprite, mech, sprite):
        yield e
    team = _team_of(battle, sprite)
    player = battle.get_player(team)
    if player is not None:
        for mate in player.team:
            if mate is sprite or not getattr(mate, '_has_grant', False):
                continue
            for e in _grant_effects(mate, mech, sprite):
                if getattr(e, 'affects', 'self') == 'team':
                    yield e
    opp = _active(battle, 'B' if team == 'A' else 'A')
    if opp is not None:
        for e in _grant_effects(opp, mech, sprite):
            if getattr(e, 'affects', 'self') == 'both':
                yield e


def _matches(bs, payload: dict) -> bool:
    """技能是否命中声明（复用 replayer 的筛选语义）。"""
    from backend.engine.modifiers import eval_skill_where

    info = {
        "name": getattr(bs, 'name', ''),
        "energy_cost": getattr(bs, 'energy_cost', 0),
        "element": getattr(bs, 'element', ''),
        "skill_type": getattr(bs, 'skill_type', ''),
    }
    if not eval_skill_where(payload.get("skill_where"), info):
        return False
    skill_filter = payload.get("skill_filter")
    if skill_filter and skill_filter != "all":
        st = info["skill_type"]
        if skill_filter == "attack":
            if st not in ("物攻", "魔攻", "动态攻击"):
                return False
        elif skill_filter == "defense":
            if st != "防御":
                return False
        elif skill_filter == "status":
            if st != "状态":
                return False
        elif st != skill_filter:
            return False
    element = payload.get("element")
    if element:
        expected, negate = (element[1:], True) if element.startswith("!") else (element, False)
        if negate and info["element"] == expected:
            return False
        if not negate and info["element"] != expected:
            return False
    return True


def element_for(battle: Battle, sprite: Sprite, bs) -> str:
    """技能在当前声明下的生效属性（无声明时返回原始属性）。"""
    for e in _grantors(battle, sprite, "element_convert"):
        payload = e.payload
        if not _matches(bs, payload):
            continue
        if payload.get("from") and getattr(bs, 'element', '') != payload["from"]:
            continue
        to_element = payload.get("to", "")
        if to_element:
            return to_element
    return ""


def morph_category(battle: Battle, sprite: Sprite, bs) -> str:
    """技能当前的巧变类别（无授予时返回 ""）。"""
    for e in _grantors(battle, sprite, "morph"):
        if _matches(bs, e.payload):
            return e.payload.get("category", "same_element")
    return ""


def choices_for(battle: Battle, sprite: Sprite, action: str = "", bs=None) -> list:
    """聚能（action="gather"）或某技能（bs）获得的附加分支。"""
    out: list = []
    for e in _grantors(battle, sprite, "grant_choice"):
        payload = e.payload
        if payload.get("action", "") != action:
            continue
        if action == "" and bs is not None and not _matches(bs, payload):
            continue
        out.extend(payload.get("choices", ()) or ())
    return out


# ═══════════════════════════════════════════════════════════════
# aura 重算（幂等）
# ═══════════════════════════════════════════════════════════════

def _replay(battle: Battle, team: str, sprite: Sprite, mutations) -> list[str]:
    from backend.engine.replayer import JournalReplayer
    opp = _active(battle, 'B' if team == 'A' else 'A')
    r = JournalReplayer(sprite, opp, battle.globals,
                        battle._vm_engine.registry, team=team, battle=battle)
    return r.replay(mutations)


def refresh(battle: Battle) -> None:
    """重算全部 aura（幂等；回合开始、每次行动后、每次换人后调用）。"""
    from backend.vm.effect import GrantEffect
    from backend.vm.journal import StatChange

    teams = ('A', 'B')
    sprites: list[tuple[str, Sprite]] = []
    for team in teams:
        player = battle.get_player(team)
        if player is None:
            continue
        for sprite in player.team:
            sprites.append((team, sprite))
    if not sprites:
        return
    # 快路径：没有任何 aura 声明/追踪时直接返回
    if not any(getattr(s, '_has_grant', False) or s.counters for _, s in sprites):
        return

    for team, sprite in sprites:
        declarations = [e for e in getattr(sprite, 'active_effects', ())
                        if isinstance(e, GrantEffect) and e.mechanism == "aura"]
        live_keys: set[str] = set()
        for e in declarations:
            payload = e.payload
            stat = payload.get("stat", "")
            src_fn = COUNT_SOURCES.get(payload.get("count", ""))
            if not stat or src_fn is None:
                continue
            key = f"aura:{e.source}:{stat}"
            live_keys.add(key)
            want = int(payload.get("per_unit", 1)) * int(
                src_fn(battle, sprite, payload.get("count_params")))
            applied = sprite.counters.get(key, 0)
            if want == applied:
                continue
            _replay(battle, team, sprite, [StatChange(
                target="sprite_self", stat=stat, steps=want - applied,
                scope=e.scope or "battlefield", source=e.source or "aura",
            )])
            sprite.counters[key] = want
        # 声明已消失（离场/驱散）但追踪仍在 → 归零（效果已随 scope 清除）
        stale = [k for k in sprite.counters if k.startswith("aura:") and k not in live_keys]
        for key in stale:
            sprite.counters[key] = 0
