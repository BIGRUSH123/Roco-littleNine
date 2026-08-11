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
