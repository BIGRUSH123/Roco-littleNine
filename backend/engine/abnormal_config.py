"""Abnormal effect templates — single source of truth for abnormal behavior.

New abnormal types can be added here without changing turn_end() or tick handlers:
    ABNORMAL_TEMPLATES["流血"] = AbnormalEffect(
        name="流血", tick_damage_pct=0.04, tick_element="普", scope="battlefield",
    )
"""

from __future__ import annotations

from backend.vm.effect import AbnormalEffect

ABNORMAL_TEMPLATES: dict[str, AbnormalEffect] = {
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
}
