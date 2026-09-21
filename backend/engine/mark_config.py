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
    "降灵印记": MarkEffect(
        name="降灵印记", source="印记", category="negative", scope="persistent",
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
    "暗涌印记": MarkEffect(
        name="暗涌印记", source="印记", category="negative", scope="persistent",
        leave_random_debuffs=5,
    ),
    "萌芽印记": MarkEffect(
        name="萌芽印记", source="印记", category="positive", scope="persistent",
        buff_bonus_layers=1,
    ),
}

# Derived sets for quick category lookup
POSITIVE_MARK_NAMES = frozenset(
    name for name, m in MARK_TEMPLATES.items() if m.is_positive
)
NEGATIVE_MARK_NAMES = frozenset(
    name for name, m in MARK_TEMPLATES.items() if m.is_negative
)

# 数据面（nrc 技能/特性）用词条全名（"棘刺印记"），模板键用短名（"棘刺"）。
# 两套写法都要能用：apply_mark 先归一，再查模板；查不到才走零效果兜底。
MARK_ALIASES: dict[str, str] = {
    "棘刺印记": "棘刺",
    "风起印记": "风起",
    "减速印记": "减速",
    "萌芽": "萌芽印记",
    "暗涌": "暗涌印记",
    "润泽": "润泽印记",
}


def canonical_mark_name(name: str) -> str:
    """把数据面的印记别名归一到模板键（未知名字原样返回）。"""
    return MARK_ALIASES.get(name, name)


def mark_template(name: str) -> MarkEffect | None:
    """按名称（含别名）取印记模板。"""
    return MARK_TEMPLATES.get(canonical_mark_name(name))

