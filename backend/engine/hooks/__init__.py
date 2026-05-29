"""Engine-level trait hooks.

Hooks that can't be expressed in pure JSON observer format
(e.g. state comparison across time steps like 机械变式).
"""

from backend.sim.traits.trait_engine import register_hook


def _mech_transmission_hook(sprite, _prev, battle, team):
    """机械变式(id=20159)：传动后位置变化的技能永久能耗-1/次。

    _apply_transmission 在每个 pass 后已将移动次数写入
    BattleSkill._transmission_move_count。此 hook 读取并永久累加到
    _mech_energy_reduction（不清零，跨回合持久）。
    """
    from backend.sim.traits import get_trait

    trait = get_trait(sprite)
    if trait is None or trait.trait_id != 20159:
        return None

    events = []
    for bs in sprite.skills:
        count = getattr(bs, '_transmission_move_count', 0)
        if count > 0:
            bs._mech_energy_reduction -= count
            events.append(f'{sprite.name} 机械变式: {bs.name} 能耗-{count}')
        bs._transmission_move_count = 0  # reset for next turn
    return events


register_hook('after_transmission', _mech_transmission_hook, '机械变式')
