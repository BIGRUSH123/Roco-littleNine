"""backend/sim/traits/_hooks.py — 引擎级 hook 回调注册（复活的 Layer 3b）。

被 backend/sim/traits/__init__.py import 以完成注册。
当前 hook 点：on_fatal_damage（backend/engine/replayer.py:_apply_damage 落地前触发）。
"""

from __future__ import annotations

from .trait_engine import fire_hook_first, register_hook  # noqa: F401


def _immortal_bird_fatal(sprite, damage, battle, team) -> bool:
    """不死鸟: 每场战斗 1 次，受到致命伤害时保留 1 血，且敌方获得 15 层灼烧。

    返回 True 表示伤害已被拦截（引擎跳过 take_damage）。
    """
    handler = _get_trait(sprite)
    if handler is None or handler.name != "不死鸟":
        return False
    # 每场战斗 1 次（sprite 计数器门控，可序列化）
    if sprite.get_counter("不死鸟_used") > 0:
        return False
    sprite.inc_counter("不死鸟_used")

    # 锁血：留 1 HP
    sprite.current_hp = 1

    # 敌方获得 15 层灼烧
    opp_team = "B" if team == "A" else "A"
    attacker = battle.get_opponent(team).active if battle else None
    if attacker is not None:
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
        template = ABNORMAL_TEMPLATES.get("灼烧")
        from backend.vm.effect import AbnormalEffect
        eff = AbnormalEffect(
            name="灼烧", source="不死鸟",
            scope=template.scope if template else "persistent",
            stacks=15,
            tick_damage_pct=template.tick_damage_pct if template else 0.02,
            tick_element=template.tick_element if template else "火",
            decay_on_tick=template.decay_on_tick if template else False,
        )
        attacker.add_effect(eff)
    return True


def _get_trait(sprite):
    from . import get_trait
    try:
        return get_trait(sprite)
    except Exception:
        return None


register_hook("on_fatal_damage", _immortal_bird_fatal, "不死鸟")
