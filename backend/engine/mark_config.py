"""Mark effect templates — single source of truth for mark behavior.

Replaces the _MARK_EFFECTS config dict in globals.py.
New mark types can be added here without changing GlobalEffects query methods:
    MARK_TEMPLATES["新印记"] = MarkEffect(name="新印记", category="positive", power_bonus=15)
"""

from __future__ import annotations

from backend.vm.effect import MarkEffect

MARK_TEMPLATES: dict[str, MarkEffect] = {
    # ── Positive marks ──
    "攻击印记": MarkEffect(
        name="攻击印记", source="印记", category="positive", scope="persistent",
        power_bonus=10,
    ),
    "蓄电印记": MarkEffect(
        name="蓄电印记", source="印记", category="positive", scope="persistent",
        power_bonus=10, condition="is_attack",
    ),
    "润泽印记": MarkEffect(
        name="润泽印记", source="印记", category="positive", scope="persistent",
        energy_mod=1,
    ),
    "湿润印记": MarkEffect(
        name="湿润印记", source="印记", category="positive", scope="persistent",
        energy_mod=1,
    ),
    "风起": MarkEffect(
        name="风起", source="印记", category="positive", scope="persistent",
        damage_mult=0.20, condition="is_first",
    ),
    "光合印记": MarkEffect(
        name="光合印记", source="印记", category="positive", scope="persistent",
        turn_end_energy=1,
    ),
    "龙噬印记": MarkEffect(
        name="龙噬印记", source="印记", category="positive", scope="persistent",
    ),
    "蓄势印记": MarkEffect(
        name="蓄势印记", source="印记", category="positive", scope="persistent",
    ),

    # ── Negative marks ──
    "减速": MarkEffect(
        name="减速", source="印记", category="negative", scope="persistent",
        speed_penalty=10,
    ),
    "迟缓": MarkEffect(
        name="迟缓", source="印记", category="negative", scope="persistent",
        damage_mult=0.30, condition="not_first",
    ),
    "棘刺": MarkEffect(
        name="棘刺", source="印记", category="negative", scope="persistent",
        switch_damage_pct=0.06,
    ),
    "降临印记": MarkEffect(
        name="降临印记", source="印记", category="negative", scope="persistent",
        switch_energy_loss=1,
    ),
    "中毒印记": MarkEffect(
        name="中毒印记", source="印记", category="negative", scope="persistent",
        turn_end_damage_pct=0.03,
    ),
    "星陨印记": MarkEffect(
        name="星陨印记", source="印记", category="negative", scope="persistent",
        starfall_damage=30,
    ),
}

# Derived sets for quick category lookup
POSITIVE_MARK_NAMES = frozenset(
    name for name, m in MARK_TEMPLATES.items() if m.is_positive
)
NEGATIVE_MARK_NAMES = frozenset(
    name for name, m in MARK_TEMPLATES.items() if m.is_negative
)
