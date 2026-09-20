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
        name="寄生",
        source="寄生",
        scope="persistent",
        tick_damage_pct=0.06,
        tick_element="草",
        tick_per_stack=False,
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
}
