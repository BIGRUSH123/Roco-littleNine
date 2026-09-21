"""Abnormal effect templates — single source of truth for abnormal behavior.

New abnormal types can be added here without changing turn_end() or tick handlers:
    ABNORMAL_TEMPLATES["流血"] = AbnormalEffect(
        name="流血", tick_damage_pct=0.04, tick_element="普", scope="battlefield",
    )
"""

from __future__ import annotations

from backend.vm.effect import AbnormalEffect

ABNORMAL_TEMPLATES: dict[str, AbnormalEffect] = {
    # 引电（nrc 游戏内文本）：获得 2 层时立即受到 25% 生命的电系伤害并失去 2 层；电系精灵免疫
    "引电": AbnormalEffect(
        name="引电",
        source="引电",
        scope="persistent",
        threshold_stacks=2,
        threshold_damage_pct=0.25,
        threshold_element="电",
        threshold_consume=2,
        threshold_immune_element="电",
    ),
    "中毒": AbnormalEffect(
        name="中毒",
        source="中毒",
        scope="persistent",
        tick_damage_pct=0.03,
        tick_element="毒",
    ),
    "灼烧": AbnormalEffect(
        name="灼烧",
        source="灼烧",
        scope="persistent",
        tick_damage_pct=0.02,
        tick_element="火",
        decay_on_tick=True,
    ),
    "寄生": AbnormalEffect(
        # 游戏内文本：回合结束时，从寄生来源吸收 2% 生命（草系免疫）→ 每层 2%，
        # 伤害回补给施加方（absorb_to_source）
        name="寄生",
        source="寄生",
        scope="persistent",
        tick_damage_pct=0.02,
        tick_element="草",
        tick_per_stack=True,
        absorb_to_source=True,
    ),
    "冻结": AbnormalEffect(
        name="冻结",
        source="冻结",
        scope="persistent",
        max_stacks=20,
    ),
    "萌化": AbnormalEffect(
        name="萌化",
        source="萌化",
        scope="persistent",
    ),
    "眩晕": AbnormalEffect(
        # 本回合无法行动（本地 wiki 词条"晕眩"）；由行动 Gate 消费，不造成伤害
        name="眩晕",
        source="眩晕",
        scope="persistent",
    ),
    # ── 情报遮蔽状态（游戏内文本 3024 / 3025）──
    # 「该状态下会隐藏精灵的信息，自己行动或被敌方攻击时解除。」
    # 本引擎对局是完全信息（双方 agent 读同一份 state），「隐藏精灵的信息」没有可观测的
    # 战斗后果，因此只落地**获得/解除**两个时点（隐藏信息本身不实现，见 data/IR_GUIDE.md）：
    #   - 自己行动（行动完成）→ battle._execute_skill_vm() 执行尾调用 release_intel_states()
    #   - 被敌方攻击（受伤落地）→ replayer._apply_damage() 对受击方调用（自我伤害不解）
    "木桶状态": AbnormalEffect(
        name="木桶状态",
        source="木桶状态",
        scope="battlefield",
        release_on_action=True,
        release_on_damage=True,
    ),
    "月陨星状态": AbnormalEffect(
        name="月陨星状态",
        source="月陨星状态",
        scope="battlefield",
        release_on_action=True,
        release_on_damage=True,
    ),
}

#: 情报遮蔽状态名 → (自己行动时解除, 被敌方攻击时解除)。
#: 引擎侧按**名字**判定（不从 effect 对象读标记，序列化往返后标志可能丢），
#: 新增同类状态只需在模板里加名 + 在这里登记时机。
INTEL_STATES: dict[str, tuple[bool, bool]] = {
    name: (bool(getattr(tpl, "release_on_action", False)),
           bool(getattr(tpl, "release_on_damage", False)))
    for name, tpl in ABNORMAL_TEMPLATES.items()
    if getattr(tpl, "release_on_action", False) or getattr(tpl, "release_on_damage", False)
}


def release_intel_states(sprite, *, on: str = "") -> list[str]:
    """解除精灵身上的情报遮蔽状态，返回被解除的状态名列表。

    on: ""（两者都查）/ "action"（自己行动）/ "damage"（被敌方攻击）。
        解除时机由 `INTEL_STATES` 登记表决定，而不是 effect 对象上的标记。
    """
    if sprite is None or not INTEL_STATES:
        return []
    active = getattr(sprite, "active_effects", None)
    if not active:
        return []
    removed: list[str] = []
    for e in list(active):
        name = getattr(e, "name", "")
        flags = INTEL_STATES.get(name)
        if flags is None:
            continue
        if on == "action" and not flags[0]:
            continue
        if on == "damage" and not flags[1]:
            continue
        sprite.remove_effect(name, "abnormal")
        removed.append(name)
    return removed

#: 同系免疫表：这些异常/状态对(含)该系别的精灵无效。
#: 依据游戏内文本——中毒「毒系精灵免疫此效果」、灼烧「火系」、冻结「冰系」、
#: 寄生「草系」、引电「电系」（暴风雪/雷鸣的天气施加同样受此约束）。
IMMUNE_ELEMENT: dict[str, str] = {
    "中毒": "毒",
    "灼烧": "火",
    "冻结": "冰",
    "寄生": "草",
    "引电": "电",
}


def is_element_immune(sprite, name: str) -> bool:
    """该精灵是否因自身系别免疫此异常/状态。"""
    element = IMMUNE_ELEMENT.get(name)
    if not element or sprite is None:
        return False
    elements = getattr(getattr(sprite, "species", None), "elements", ()) or ()
    return element in elements
