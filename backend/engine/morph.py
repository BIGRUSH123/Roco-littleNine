"""morph — 巧变池注册表与选池（引擎侧通用服务）。

巧变语义（来源：wiki.biligame.com/nrc/巧变 「游戏内介绍」）：
    使用后会变为**指定范围内的随机技能**，且**能耗-1**。
    **使用该随机技能后会变回原技能。**
即巧变是**一次性**变化：技能 A（带巧变）使用后槽位变为随机技能 B（B 能耗-1）；
使用 B 之后，槽位变回 A。

实现：`apply_after_use` 在 A 结算后把槽位 `replaced_by` 置为随机 B 并标记
`_morph_temp`；下一次使用该槽时（此时是 B）由 `revert_after_use` 还原为 A。
能耗-1 由 `Battle.skill_energy_cost()` 在 `_morph_temp` 时扣减。

池类别可扩展：`register_pool_builder(name, fn)`，fn(battle, sprite, bs, params) -> tuple[str, ...]。
"""

from __future__ import annotations

import json
import os
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.sprite import Sprite

POOL_BUILDERS: dict[str, object] = {}

# 技能静态索引缓存：name → (id, element, exclusive_to, skill_type, description)
_SKILL_INDEX: dict[str, tuple[int, str, str, str, str]] = {}
_SKILL_CACHE: dict[str, object] = {}
_SKILL_INDEX_READY = False


def _load_index() -> dict[str, tuple[int, str, str, str, str]]:
    global _SKILL_INDEX_READY
    if _SKILL_INDEX_READY:
        return _SKILL_INDEX
    d = os.path.join('data', 'skills')
    try:
        names = os.listdir(d)
    except OSError:
        _SKILL_INDEX_READY = True
        return _SKILL_INDEX
    for fn in names:
        if not fn.endswith('.json') or fn.startswith('_'):
            continue
        try:
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        name = data.get('name')
        if not name:
            continue
        _SKILL_INDEX[name] = (
            int(data.get('id', 0) or 0),
            data.get('element', '') or '',
            data.get('exclusive_to', '') or '',
            data.get('skill_type', '') or '',
            data.get('description', '') or '',
        )
    _SKILL_INDEX_READY = True
    return _SKILL_INDEX


def load_skill(name: str):
    """按名加载 Skill（带缓存）。找不到返回 None。"""
    if name in _SKILL_CACHE:
        return _SKILL_CACHE[name]
    from backend.sim.skill import Skill

    path = os.path.join('data', 'skills', f'{name}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    skill = Skill.load(data)
    _SKILL_CACHE[name] = skill
    return skill


def register_pool_builder(name: str, fn) -> None:
    POOL_BUILDERS[name] = fn


def _morph_pool_for_test(element: str, exclude: str) -> tuple[str, ...]:
    """测试/调试用：直接构造 same_element 池（无 battle 上下文）。"""
    return _build_same_element(None, None, _StubSkill(element, exclude), None)


class _StubSkill:
    """最小技能替身：仅供池构造读取 element/name。"""

    __slots__ = ("element", "name")

    def __init__(self, element: str, name: str) -> None:
        self.element = element
        self.name = name


def _build_same_element(battle, sprite, bs, params) -> tuple[str, ...]:
    """同系别技能池：同属性、排除自身与专属技能，按 id 升序。"""
    index = _load_index()
    element = getattr(bs, 'element', '')
    current = getattr(bs, 'name', '')
    if not element:
        return ()
    pool = [(sid, n) for n, (sid, el, excl, _st, _desc) in index.items()
            if el == element and n != current and not excl]
    pool.sort()
    return tuple(n for _, n in pool)


_ATTACK_TYPES = frozenset({"物攻", "魔攻", "动态攻击"})


def _build_filter(battle, sprite, bs, params) -> tuple[str, ...]:
    """条件池（category 为 spec dict 时使用）。

    spec 支持：name（精确技能名，如 齐鸣「巧变：虫鸣」）、element（"恶" / "!幻"）、
    skill_type（"attack"/"defense"/"status" 或精确值）、contains（描述子串，如 "冻结"、"吸血"）。
    """
    index = _load_index()
    spec = params or {}
    current = getattr(bs, 'name', '')
    exact_name = spec.get("name", "")
    element = spec.get("element", "")
    skill_type = spec.get("skill_type", "")
    contains = spec.get("contains", "")

    def ok(name: str) -> bool:
        if name == current:
            return False
        _sid, el, excl, st, desc = index[name]
        if excl:
            return False
        if exact_name and name != exact_name:
            return False
        if element:
            if element.startswith("!"):
                if el == element[1:]:
                    return False
            elif el != element:
                return False
        if skill_type:
            if skill_type == "attack":
                if st not in _ATTACK_TYPES:
                    return False
            elif skill_type == "defense":
                if st != "防御":
                    return False
            elif skill_type == "status":
                if st != "状态":
                    return False
            elif st != skill_type:
                return False
        return not (contains and contains not in desc)

    pool = sorted((index[n][0], n) for n in index if ok(n))
    return tuple(n for _, n in pool)


register_pool_builder("same_element", _build_same_element)
register_pool_builder("filter", _build_filter)


def _resolve_category(category) -> str:
    """把 category 解析成池构造器名：内置名，或 spec dict（→ filter 池）。"""
    if isinstance(category, str):
        return category
    if isinstance(category, dict):
        return "filter"
    return ""


def pick(battle: Battle, sprite: Sprite, bs, category, slot: int) -> str:
    """按类别选池：返回目标技能名（无候选返回 ""）。

    游戏内为随机：池按 id 升序后 `random.choice`。对局由 `random.seed(seed)`
    播种（差分录制/自对弈均如此），因此仍可复现。
    """
    pool_name = _resolve_category(category)
    builder = POOL_BUILDERS.get(pool_name)
    if builder is None:
        return ""
    params = category if isinstance(category, dict) else None
    try:
        pool = builder(battle, sprite, bs, params)
    except Exception:  # noqa: BLE001 — 自定义池构造失败不应打断结算
        return ""
    if not pool:
        return ""
    return random.choice(pool)


def register_skill_morphs(sprite: Sprite) -> int:
    """登记技能自带的「巧变：X」元数据（技能 JSON 的 qiaobian 字段）。

    在技能所持技能上挂一条 morph 声明（affects=self，按技能名匹配），
    与特性授予的巧变走同一条机制。返回登记条数。
    """
    from backend.vm.effect import GrantEffect

    if sprite is None:
        return 0
    count = 0
    for bs in (sprite.skills or ()):
        spec = getattr(bs.base, 'qiaobian', None)
        if not spec:
            continue
        payload = {"category": spec, "skill_where": {"name": bs.base.name}}
        if any(isinstance(e, GrantEffect) and e.mechanism == "morph"
               and e.payload == payload for e in sprite.active_effects):
            continue
        sprite.active_effects.append(GrantEffect(
            name=f"morph:巧变:{bs.base.name}", source="巧变", scope="persistent",
            mechanism="morph", affects="self", payload=payload,
        ))
        sprite._invalidate_effects_cache()
        count += 1
    return count


def apply_after_use(battle: Battle, team: str, sprite: Sprite, slot: int, bs) -> str:
    """技能结算完成后调用。

    若该技能被授予巧变 → 槽位变为池中**随机**技能并标记 `_morph_temp`（能耗-1）；
    若该槽位当前本来就是巧变产物 → 还原为原技能（「使用该随机技能后会变回原技能」）。

    返回事件文本（无变化返回 ""）。
    """
    from backend.engine import mechanisms

    if sprite is None or bs is None:
        return ""
    if getattr(bs, '_morph_temp', False):
        return revert_after_use(bs)
    category = mechanisms.morph_category(battle, sprite, bs)
    if not category:
        return ""
    target_name = pick(battle, sprite, bs, category, slot)
    if not target_name:
        return ""
    new_skill = load_skill(target_name)
    if new_skill is None:
        return ""
    old_name = bs.name
    bs.replaced_by = new_skill
    bs._morph_temp = True
    # 新技能若自带「巧变：X」元数据，同样登记（保持不变量）
    register_skill_morphs(sprite)
    return f'{old_name} 巧变 → {target_name}(能耗-1)'


def revert_after_use(bs) -> str:
    """巧变产物被使用后还原原技能。返回事件文本（无变化返回 ""）。"""
    if bs is None or not getattr(bs, '_morph_temp', False):
        return ""
    used_name = bs.name
    bs.replaced_by = None
    bs._morph_temp = False
    return f'{used_name} 巧变结束 → 还原{bs.name}'
