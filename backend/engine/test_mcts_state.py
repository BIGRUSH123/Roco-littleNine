import random

import numpy as np

from backend.common.models import SpeciesStats
from backend.engine.ai.core.mcts import mcts_search
from backend.sim.agent import RuleAgent
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.player import Player
from backend.sim.sprite import Sprite
from backend.vm.effect import StatBuffEffect


class _UniformEvaluator:
    def evaluate(self, state, mask):
        prior = mask.astype(np.float32)
        return 0.0, prior / max(float(prior.sum()), 1.0)


def _equal_speed_battle() -> Battle:
    species = SpeciesStats(
        name="镜像测试精灵",
        hp=100,
        atk=100,
        sp_atk=100,
        def_=100,
        sp_def=100,
        speed=100,
    )
    stats = {
        "atk": 100,
        "sp_atk": 100,
        "def": 100,
        "sp_def": 100,
        "speed": 100,
    }

    def make_sprite() -> Sprite:
        return Sprite(
            species=species,
            current_hp=100,
            max_hp=100,
            energy=0,
            initial_stats=dict(stats),
        )

    return Battle(
        Player("A", [make_sprite()]),
        Player("B", [make_sprite()]),
        verbose=False,
    )


def test_mcts_search_does_not_advance_real_battle_rng():
    """搜索内的随机先手判定不能改变搜索后的真实对局随机序列。"""
    battle = _equal_speed_battle()
    random.seed(1)
    expected_state = random.getstate()

    mcts_search(
        battle,
        model=None,
        factory=SimFactory(),
        opponent_agent=RuleAgent("B", battle.player_b),
        num_simulations=2,
        root_noise=0.0,
        evaluator=_UniformEvaluator(),
    )

    assert random.getstate() == expected_state


def test_restore_mutable_state_invalidates_stat_cache():
    """回滚 modifier 后不能继续使用模拟分支计算出的四维属性缓存。"""
    battle = _equal_speed_battle()
    sprite = battle.player_a.active
    assert sprite.atk_with_modifiers == 100
    saved = battle.save_mutable_state()

    sprite._modifiers["atk"] = 1.0
    sprite._invalidate_stat_cache()
    assert sprite.atk_with_modifiers == 200

    battle.restore_mutable_state(saved)

    assert sprite.atk_with_modifiers == 100


def test_restore_mutable_state_restores_all_mutable_effect_fields():
    """模拟分支修改效果元数据后，回滚必须恢复完整效果状态。"""
    battle = _equal_speed_battle()
    effect = StatBuffEffect(
        name="攻击强化",
        source="测试",
        stat_key="atk",
        steps=1,
        display_mult=0.1,
        display_value=10.0,
        is_inherent=False,
        cooldown=3,
    )
    battle.player_a.active.add_effect(effect)
    saved = battle.save_mutable_state()

    effect.cooldown = 1
    effect.display_mult = 0.9
    effect.display_value = 90.0
    effect.is_inherent = True

    battle.restore_mutable_state(saved)

    assert effect.cooldown == 3
    assert effect.display_mult == 0.1
    assert effect.display_value == 10.0
    assert effect.is_inherent is False


def _containers(battle) -> tuple:
    """各「就地改」容器的可比快照（回滚幂等性的判据）。"""
    vm = battle._vm_engine
    return (
        tuple(sorted((t, tuple(sorted((m.name, m.stacks) for m in lst)))
                     for t, lst in battle.globals.mark_effects.items())),
        tuple(sorted((t, tuple(sorted(c.items())))
                     for t, c in battle.team_counters.items())),
        tuple(sorted(vm._counter_values.items())),
        tuple(sorted((k, tuple(v)) for k, v in vm._skill_history.items())),
        tuple(sorted((k, tuple(sorted(v.items()))) for k, v in vm._skill_tags.items())),
        tuple(sorted((t, tuple(v)) for t, v in vm._burst_effects.items())),
        tuple(sorted((t, tuple(sorted(v))) for t, v in vm._burst_names.items())),
        tuple((type(e).__name__, e.ttl) for e in battle.pending_effects["A"]),
        tuple((type(e).__name__, e.ttl) for e in battle.scheduled_effects),
        tuple(sorted(battle.player_a.devotion.items())),
        tuple(sorted(battle._borrowed_restore.keys(), key=str)),
    )


def test_repeated_restore_is_idempotent_for_mutable_containers():
    """同一个快照反复 restore 都要回到原局面 —— 快照本身不能被仿真写坏。

    2026-09-21 定位到的真实事故：`restore_mutable_state` 当时把这些容器**按对象**
    装回 live（`mark_effects = saved["marks"]` 等），而引擎对它们是就地改
    （印记 `stacks += n`、计数器 `+= 1`、VM burst/history 追加）。于是仿真改的正是
    快照里的对象，同一个快照每 restore 一次就多累加一次：实测星陨印记
    140 → 43140 → … → 2.5e9，最终在 `build_ctx_cy` 里触发 C 溢出，多进程 BC 数据
    生成整跑崩掉（40/40 局泄漏，MCTS 自博弈同样中招）。
    """
    battle = _equal_speed_battle()
    globals_ = battle.globals
    vm = battle._vm_engine
    globals_.apply_mark("A", "星陨印记", "negative", 3, coexist=True)
    battle.team_counters["A"] = {"element:火": 2}
    vm._counter_values["energy_spent"] = 5
    vm._skill_history["7"] = ["x"]
    vm._skill_tags["7"] = {"tag": "x"}
    vm._burst_effects["A"] = [("天光", ())]
    vm._burst_names["A"] = {"天光"}
    battle.pending_effects["A"] = [StatBuffEffect(name="挂起", source="测试",
                                                  stat_key="atk", steps=1, ttl=2)]
    battle.scheduled_effects = [StatBuffEffect(name="延时", source="测试",
                                               stat_key="atk", steps=1, ttl=3)]
    battle.player_a.devotion["火"] = 1
    battle._borrowed_restore[("A", 0)] = "skill"

    saved = battle.save_mutable_state()
    before = _containers(battle)

    def simulate() -> None:
        """仿真就地改各容器（引擎在回合内部就是这么改的）。"""
        globals_.consume_starfall_stacks("A", 2, battle.player_a.active)
        battle.team_counters["A"]["element:火"] += 1
        vm._counter_values["energy_spent"] += 7
        vm._skill_history["7"].append("y")
        vm._skill_tags["7"]["tag"] = "y"
        vm._burst_effects["A"].append(("骗局", ()))
        vm._burst_names["A"].add("骗局")
        battle.pending_effects["A"].append(
            StatBuffEffect(name="挂起2", source="测试", stat_key="atk", steps=1, ttl=1))
        battle.scheduled_effects.append(
            StatBuffEffect(name="延时2", source="测试", stat_key="atk", steps=1, ttl=1))
        battle.player_a.devotion["火"] += 1
        battle._borrowed_restore[("B", 0)] = "skill"

    simulate()
    battle.restore_mutable_state(saved)
    assert _containers(battle) == before, "第一次回滚就没回到原局面"

    simulate()
    battle.restore_mutable_state(saved)
    assert _containers(battle) == before, "第二次回滚失败：上一次仿真把快照写坏了"
