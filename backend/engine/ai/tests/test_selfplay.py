"""backend/engine/ai/tests/test_selfplay.py — AI 自我博弈/建议管线测试。

覆盖：
  1. 回滚一致性（确定性引擎 + 序列化往返）——MCTS 正确性的前提。
  2. NetworkPolicyAgent 产出合法动作。
  3. collect_rl_samples 双视角采样、形状与标签正确。
  4. advise_single / PIMC advise 冒烟。
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import numpy as np

from backend.engine.ai.service.advisor import advise, advise_single, make_determinizations
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.evaluator import TorchEvaluator
from backend.engine.ai.core.mcts import NUM_ACTIONS, NetworkPolicyAgent
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.train import (
    _load_sprite_skills,
    collect_rl_samples,
    collect_rl_samples_parallel,
    evaluate_parallel,
)
from backend.engine.serializer import battle_from_dict, battle_to_dict
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory


# ═══════════════════════════════════════════════════════════════════
# 测试夹具
# ═══════════════════════════════════════════════════════════════════

def _two_teams(sprite_skills: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """构造确定的双精灵队伍（取字典序前 4 个有技能的精灵）。"""
    names = sorted(sprite_skills.keys())
    assert len(names) >= 4, "需要至少 4 个可用精灵"
    a, b, c, d = names[:4]
    team_a = [
        {"name": a, "skills": sprite_skills[a][:4]},
        {"name": c, "skills": sprite_skills[c][:4]},
    ]
    team_b = [
        {"name": b, "skills": sprite_skills[b][:4]},
        {"name": d, "skills": sprite_skills[d][:4]},
    ]
    return team_a, team_b


def _hp_vector(battle):
    return tuple(
        (s.current_hp, s.energy, s.is_fainted)
        for p in (battle.player_a, battle.player_b)
        for s in p.team
    )


# ═══════════════════════════════════════════════════════════════════
# 1. 回滚一致性
# ═══════════════════════════════════════════════════════════════════

def _assert_encoded_state(state: dict[str, np.ndarray]) -> None:
    assert state["sprite_stats"].shape == (12, 7)
    assert state["sprite_elements"].shape == (12, 2)
    assert state["sprite_states"].shape == (12, 105)
    assert state["skill_stats"].shape == (10, 2)
    assert state["skill_elements"].shape == (10, 2)
    assert state["skill_states"].shape == (10, 9)
    assert state["global_stats"].shape == (15,)
    assert state["global_elements"].shape == (1,)
    assert state["ast_tokens"].shape == (384,)
    assert state["ast_values"].shape == (384,)


def _assert_state_batch(states: list[dict[str, np.ndarray]]) -> None:
    assert isinstance(states, list)
    assert len(states) > 0
    _assert_encoded_state(states[0])


def test_rollback_determinism():
    """从同一快照重建并以相同动作重放两次，结果必须完全一致。

    这是 MCTS 能正确工作的前提：battle_to_dict/from_dict 必须无损，
    且引擎对相同输入产生相同输出。
    """
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)

    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)
    snapshot = battle_to_dict(battle)

    def replay():
        sim = battle_from_dict(snapshot, factory.sprite_db, factory._build_skill_list)
        agent_a = RuleAgent("A", sim.player_a)
        agent_b = RuleAgent("B", sim.player_b)
        turn = 0
        while not sim.is_finished and turn < 8:
            sim.execute_turn(agent_a, agent_b)
            turn += 1
        return _hp_vector(sim), sim.winner, sim.turn

    r1 = replay()
    r2 = replay()
    assert r1 == r2, "确定性引擎 + 快照回滚的重放结果不一致"


# ═══════════════════════════════════════════════════════════════════
# 2. NetworkPolicyAgent
# ═══════════════════════════════════════════════════════════════════

def test_network_policy_agent_valid_action():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    agent = NetworkPolicyAgent(model)
    action = agent.choose_action(battle)  # 为 player_b 决策
    assert action is not None
    assert action.kind in ("skill", "switch", "item", "gather")


# ═══════════════════════════════════════════════════════════════════
# 3. collect_rl_samples 双视角
# ═══════════════════════════════════════════════════════════════════

def test_torch_evaluator_shapes():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    ev = TorchEvaluator(model, device="cpu")
    state = encode_battle_state(battle)
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
    mask[0] = 1.0
    value, probs = ev.evaluate(state, mask)
    assert -1.0 <= value <= 1.0
    assert probs.shape == (NUM_ACTIONS,)
    assert abs(probs.sum() - 1.0) < 1e-4


def test_torch_evaluator_batch_matches_single_eval():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    model.eval()
    ev = TorchEvaluator(model, device="cpu")
    state_a = encode_battle_state(battle)
    state_b = encode_battle_state(battle, perspective="B")
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
    mask[0] = 1.0

    single_a = ev.evaluate(state_a, mask)
    single_b = ev.evaluate(state_b, mask)
    values, probs = ev.evaluate_batch([state_a, state_b], [mask, mask])

    assert values.shape == (2,)
    assert probs.shape == (2, NUM_ACTIONS)
    assert np.allclose(values, [single_a[0], single_b[0]], atol=1e-6)
    assert np.allclose(probs[0], single_a[1], atol=1e-6)
    assert np.allclose(probs[1], single_b[1], atol=1e-6)


def test_collect_rl_dual_perspective():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    model = ModularBattleNet()

    X, P, M, v, _ = collect_rl_samples(
        model, factory, sprite_skills,
        num_battles=1, num_simulations=6, device="cpu",
        max_turns=20, verbose=False,
    )

    _assert_state_batch(X)
    assert P.ndim == 2 and P.shape[1] == NUM_ACTIONS
    assert M.ndim == 2 and M.shape[1] == NUM_ACTIONS
    assert len(X) == len(P) == len(M) == len(v)
    assert len(X) > 0, "应至少收集到若干样本（A+B 双方）"
    # 结果标签只能是 -1/0/+1
    assert set(np.unique(v).tolist()).issubset({-1.0, 0.0, 1.0})
    # mask 只能包含 0/1
    assert set(np.unique(M).tolist()).issubset({0.0, 1.0})
    # 访问分布每行和 <= 1（可能为 0 表示无合法动作的极端帧）
    row_sums = P.sum(axis=1)
    assert np.all(row_sums <= 1.0 + 1e-4)
    # mask 标记为合法的动作，其 P 值可能为 0（MCTS 未访问），但不应 > 0
    # mask 标记为非法的动作，其 P 值必须为 0
    assert np.all(P[M == 0] == 0.0)


def test_collect_rl_parallel_smoke():
    """多进程 + 主进程批量推理冒烟（CPU，小参数）。"""
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    model = ModularBattleNet()

    X, P, M, v, _ = collect_rl_samples_parallel(
        model, factory, sprite_skills,
        num_battles=2, num_workers=2, device="cpu",
        inference_batch_size=16, inference_timeout_ms=2.0,
        num_simulations=2, max_turns=8,
        verbose=False, progress_every=1,
    )

    _assert_state_batch(X)
    assert M.ndim == 2 and M.shape[1] == NUM_ACTIONS
    assert len(X) == len(P) == len(M) == len(v)
    assert len(X) > 0


def test_evaluate_parallel_smoke():
    """并行门控评估冒烟：双模型经主进程批量推理，返回合法胜率。"""
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    candidate = ModularBattleNet()
    best = ModularBattleNet()

    win_rate = evaluate_parallel(
        candidate, best, factory, sprite_skills,
        n_games=2, num_workers=2, device="cpu",
        inference_batch_size=16, inference_timeout_ms=2.0,
        num_simulations=1, max_turns=4, verbose=False,
    )

    assert 0.0 <= win_rate <= 1.0


# ═══════════════════════════════════════════════════════════════════
# 4. 建议（advise_single / PIMC）
# ═══════════════════════════════════════════════════════════════════

def test_advise_single_smoke():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    adv = advise_single(battle, model, factory, num_simulations=8)
    assert 0.0 <= adv.win_prob <= 1.0
    assert adv.best_action is not None
    assert isinstance(adv.summary(), str) and adv.summary()


def test_pimc_advise_smoke():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    dets = make_determinizations(battle, factory, bench_pool=None, k=3)
    assert len(dets) == 3
    adv = advise(dets, model, factory, num_simulations=6)
    assert adv.num_determinizations == 3
    assert 0.0 <= adv.win_prob <= 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {name}: {e}")
