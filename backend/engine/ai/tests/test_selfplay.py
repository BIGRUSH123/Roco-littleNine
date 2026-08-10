"""backend/engine/ai/tests/test_selfplay.py — AI 自我博弈/建议管线测试。

覆盖：
  1. 回滚一致性（确定性引擎 + 序列化往返）——MCTS 正确性的前提。
  2. NetworkPolicyAgent 产出合法动作。
  3. collect_rl_samples 双视角采样、形状与标签正确。
  4. advise_single / PIMC advise 冒烟。
"""

from __future__ import annotations

import copy
import io
import inspect
import queue
import sys

sys.path.insert(0, ".")

import numpy as np
import torch

from backend.engine.ai import train as train_module
from backend.engine.ai.service import advisor as advisor_module
from backend.engine.ai.service import agent as service_agent_module
from backend.engine.ai.core import encoder as encoder_module
from backend.engine.ai.service.advisor import advise, advise_single, describe_action, make_determinizations
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.evaluator import BatchedInferenceServer, QueuePolicyEvaluator, TorchEvaluator
from backend.engine.ai.core.mcts import NUM_ACTIONS, NetworkPolicyAgent
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.replay_buffer import DictReplayBuffer
from backend.engine.ai.run_logger import RunLogger
from backend.engine.ai.train import (
    DEFAULT_MCTS_LEAF_BATCH_SIZE,
    MCTSAgent,
    _gate_decision,
    _load_sprite_skills,
    collect_rl_samples,
    collect_rl_samples_parallel,
    evaluate_parallel,
    train_rl,
)
from backend.engine.serializer import battle_from_dict, battle_to_dict
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory
from backend.vm.effect import ObserverEffect


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


def test_console_print_replaces_characters_unsupported_by_gbk():
    """Windows GBK 控制台不能因状态符号导致训练进程退出。"""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict")

    train_module._console_print("✗ 未达门控阈值", file=stream, flush=True)

    assert raw.getvalue().decode("gbk").strip() == "? 未达门控阈值"


def test_run_logger_finalize_handles_promoted_summary_on_gbk_console(
    tmp_path, monkeypatch,
):
    """包含晋升符号的汇总不能让 Windows GBK 控制台终止训练。"""
    logger = RunLogger(tmp_path, run_name="gbk_summary")
    logger.record_iteration({"iteration": 1, "promoted": True})
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    try:
        logger.finalize(best_iteration=1)
    finally:
        if not logger._metrics_fp.closed:
            logger._metrics_fp.close()

    stream.flush()
    assert logger.summary_path.exists()
    assert "训练汇总" in raw.getvalue().decode("gbk")


def test_random_teams_use_one_shared_team_size(monkeypatch):
    """双方人数必须由同一次抽样决定，避免阵容人数主导训练标签。"""
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    sampled_sizes = iter((1, 3))
    monkeypatch.setattr(
        train_module.random,
        "randint",
        lambda _low, _high: next(sampled_sizes),
    )

    team_a, team_b = train_module._random_teams(factory, sprite_skills)

    assert len(team_a) == len(team_b) == 1


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


def test_model_action_space_matches_mcts():
    assert ModularBattleNet.NUM_ACTIONS == NUM_ACTIONS


def test_model_ast_position_cache_is_checkpoint_compatible():
    model = ModularBattleNet(ast_max_len=128)

    assert model.ast_max_len == 128
    assert model._ast_pos_ids.shape == (1, 128)
    assert "_ast_pos_ids" not in model.state_dict()


def test_model_ast_forward_ignores_trailing_pad_tokens():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    model.eval()
    ev = TorchEvaluator(model, device="cpu")
    state = encode_battle_state(battle)
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
    mask[0] = 1.0

    non_pad = np.flatnonzero(state["ast_tokens"] != 0)
    assert len(non_pad) > 0
    effective_len = int(non_pad[-1]) + 1
    trimmed_state = {
        key: value.copy()
        for key, value in state.items()
    }
    trimmed_state["ast_tokens"] = state["ast_tokens"][:effective_len].copy()
    trimmed_state["ast_values"] = state["ast_values"][:effective_len].copy()

    with torch.inference_mode():
        full_value, full_probs = ev.evaluate(state, mask)
        trimmed_value, trimmed_probs = ev.evaluate(trimmed_state, mask)

    assert np.allclose(full_value, trimmed_value, atol=1e-6)
    assert np.allclose(full_probs, trimmed_probs, atol=1e-6)


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


def test_queue_policy_evaluator_batch_roundtrip():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    model = ModularBattleNet()
    model.eval()
    request_q: queue.Queue = queue.Queue()
    reply_q: queue.Queue = queue.Queue()
    server = BatchedInferenceServer(
        model, "cpu", request_q, {3: reply_q},
        batch_size=8, timeout_ms=1,
    )
    server.start()
    try:
        ev = QueuePolicyEvaluator(3, request_q, reply_q, reply_timeout_s=5)
        state_a = encode_battle_state(battle)
        state_b = encode_battle_state(battle, perspective="B")
        mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
        mask[0] = 1.0

        values, probs = ev.evaluate_batch([state_a, state_b], [mask, mask])
    finally:
        server.stop(drain=False)

    assert values.shape == (2,)
    assert probs.shape == (2, NUM_ACTIONS)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-4)


def test_training_mcts_leaf_batch_defaults_to_batched_eval():
    targets = (
        MCTSAgent.__init__,
        collect_rl_samples,
        collect_rl_samples_parallel,
        evaluate_parallel,
    )

    for target in targets:
        params = inspect.signature(target).parameters
        assert params["leaf_batch_size"].default == DEFAULT_MCTS_LEAF_BATCH_SIZE


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


def test_collect_rl_uses_deterministic_opponent_inside_search(monkeypatch):
    """同一树节点不能因随机对手动作映射到多个不同的下一状态。"""
    seen_opp_greedy: list[bool] = []

    def fake_mcts_search(battle, *_args, **kwargs):
        seen_opp_greedy.append(kwargs["opp_greedy"])
        _, mask = train_module.get_valid_actions(battle.player_a, battle)
        return mask / max(float(mask.sum()), 1.0)

    monkeypatch.setattr(train_module, "mcts_search", fake_mcts_search)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    model = ModularBattleNet(trunk_dim=64, num_blocks=1, with_attention=False)

    collect_rl_samples(
        model, factory, sprite_skills,
        num_battles=1, num_simulations=1, device="cpu",
        max_turns=1, verbose=False,
    )

    assert seen_opp_greedy
    assert all(seen_opp_greedy)


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

def test_gate_decision_early_fail_when_remaining_cannot_reach_gate():
    assert _gate_decision(wins=3.0, completed=8, total=10, gate=0.6) is False


def test_gate_decision_early_pass_when_losses_cannot_drop_below_gate():
    assert _gate_decision(wins=6.0, completed=6, total=10, gate=0.6) is True


def test_gate_decision_continues_when_result_can_change():
    assert _gate_decision(wins=4.0, completed=6, total=10, gate=0.6) is None


def test_paired_eval_tasks_reuse_matchup_when_models_swap_sides(monkeypatch):
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    marker = iter((1, 2, 3))

    def fake_random_teams(_factory, _sprite_skills):
        value = next(marker)
        return ([{"name": f"A{value}"}], [{"name": f"B{value}"}])

    monkeypatch.setattr(train_module, "_random_teams", fake_random_teams)

    tasks = train_module._paired_eval_tasks(factory, sprite_skills, n_games=5)

    assert [game_index for game_index, _ in tasks] == [0, 1, 2, 3, 4]
    assert tasks[0][1] == tasks[1][1]
    assert tasks[2][1] == tasks[3][1]
    assert tasks[1][1] != tasks[2][1]


def test_paired_gate_waits_for_complete_pairs_even_out_of_order():
    tracker = train_module._PairedGateTracker(total_games=4, gate=0.75)

    assert tracker.add(game_index=0, score=1.0) is None
    assert tracker.add(game_index=2, score=1.0) is None
    assert tracker.completed_games == 0

    assert tracker.add(game_index=3, score=1.0) is None
    assert tracker.completed_games == 2

    assert tracker.add(game_index=1, score=0.0) is True
    assert tracker.completed_games == 4
    assert tracker.score == 0.75


def test_train_rl_smoke_uses_replay_buffer_batches():
    replay = DictReplayBuffer(capacity=4)
    state = {
        key: np.zeros(buffer.shape[1:], dtype=buffer.dtype)
        for key, buffer in replay.buffers.items()
    }
    policy = np.zeros(NUM_ACTIONS, dtype=np.float32)
    policy[0] = 1.0
    mask = np.ones(NUM_ACTIONS, dtype=np.float32)
    for outcome in (1.0, -1.0, 0.5, -0.5):
        replay.push(state, policy, mask, outcome)

    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0, with_attention=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)

    history = train_rl(
        model, replay, epochs=1, batch_size=2, device="cpu",
        optimizer=optimizer, val_split=0.25,
    )

    assert len(history) == 1
    assert 0.0 <= history[0]["val_acc"] <= 1.0


def test_train_rl_policy_loss_weight_controls_policy_head_updates():
    replay = DictReplayBuffer(capacity=4)
    state = {
        key: np.zeros(buffer.shape[1:], dtype=buffer.dtype)
        for key, buffer in replay.buffers.items()
    }
    policy = np.zeros(NUM_ACTIONS, dtype=np.float32)
    policy[0] = 1.0
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
    mask[:2] = 1.0
    for outcome in (1.0, -1.0, 0.5, -0.5):
        replay.push(state, policy, mask, outcome)

    torch.manual_seed(7)
    value_only = ModularBattleNet(
        trunk_dim=64, num_blocks=1, dropout=0.0, with_attention=False,
    )
    with_policy = copy.deepcopy(value_only)
    value_only_before = value_only.skill_head[-1].bias.detach().clone()
    with_policy_before = with_policy.skill_head[-1].bias.detach().clone()

    train_rl(
        value_only, replay, epochs=1, batch_size=4, device="cpu",
        optimizer=torch.optim.SGD(value_only.parameters(), lr=1e-2),
        val_split=0.25, policy_loss_weight=0.0,
    )
    train_rl(
        with_policy, replay, epochs=1, batch_size=4, device="cpu",
        optimizer=torch.optim.SGD(with_policy.parameters(), lr=1e-2),
        val_split=0.25, policy_loss_weight=1.0,
    )

    assert torch.equal(value_only.skill_head[-1].bias, value_only_before)
    assert not torch.equal(with_policy.skill_head[-1].bias, with_policy_before)


def test_restore_rejected_candidate_clears_rejected_adam_momentum():
    torch.manual_seed(11)
    model = torch.nn.Linear(2, 1)
    best_model = copy.deepcopy(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    optimizer_before = copy.deepcopy(optimizer.state_dict())
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.5)

    loss = model(torch.ones(1, 2)).sum()
    loss.backward()
    optimizer.step()
    scheduler.step()
    scheduled_lr = scheduler.get_last_lr()[0]
    assert optimizer.state

    train_module._restore_rejected_candidate(
        model,
        best_model,
        optimizer,
        optimizer_before,
        scheduled_lr=scheduled_lr,
    )

    for actual, expected in zip(model.parameters(), best_model.parameters()):
        assert torch.equal(actual, expected)
    assert not optimizer.state
    assert optimizer.param_groups[0]["lr"] == scheduled_lr


def test_replay_buffer_push_batch_wraps_vectorized_writes():
    replay = DictReplayBuffer(capacity=3)

    def state_with_marker(value: float):
        state = {
            key: np.zeros(buffer.shape[1:], dtype=buffer.dtype)
            for key, buffer in replay.buffers.items()
        }
        state["global_stats"][0] = value
        return state

    states = [state_with_marker(float(i)) for i in range(5)]
    policies = np.zeros((5, NUM_ACTIONS), dtype=np.float32)
    masks = np.ones((5, NUM_ACTIONS), dtype=np.float32)
    outcomes = np.arange(5, dtype=np.float32)

    assert replay.push_batch(states[:2], policies[:2], masks[:2], outcomes[:2]) == 2
    assert replay.ptr == 2
    assert replay.size == 2

    assert replay.push_batch(states[2:], policies[2:], masks[2:], outcomes[2:]) == 3

    assert replay.ptr == 2
    assert replay.size == 3
    np.testing.assert_allclose(replay.buffers["global_stats"][:, 0], [3.0, 4.0, 2.0])
    np.testing.assert_allclose(replay.outcome_buffer, [3.0, 4.0, 2.0])


def test_replay_buffer_push_batch_keeps_last_entries_when_batch_exceeds_capacity():
    replay = DictReplayBuffer(capacity=3)
    template = {
        key: np.zeros(buffer.shape[1:], dtype=buffer.dtype)
        for key, buffer in replay.buffers.items()
    }
    states = []
    for i in range(5):
        state = {key: value.copy() for key, value in template.items()}
        state["global_stats"][0] = float(i)
        states.append(state)

    policies = np.zeros((5, NUM_ACTIONS), dtype=np.float32)
    masks = np.ones((5, NUM_ACTIONS), dtype=np.float32)
    outcomes = np.arange(5, dtype=np.float32)

    assert replay.push_batch(states, policies, masks, outcomes) == 5

    assert replay.ptr == 2
    assert replay.size == 3
    np.testing.assert_allclose(replay.buffers["global_stats"][:, 0], [3.0, 4.0, 2.0])
    np.testing.assert_allclose(replay.outcome_buffer, [3.0, 4.0, 2.0])


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


def test_describe_action_uses_fixed_bench_slot_for_switches():
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    names = sorted(sprite_skills.keys())
    team_a = [
        {"name": names[0], "skills": sprite_skills[names[0]][:4]},
        {"name": names[1], "skills": sprite_skills[names[1]][:4]},
        {"name": names[2], "skills": sprite_skills[names[2]][:4]},
    ]
    team_b = [{"name": names[3], "skills": sprite_skills[names[3]][:4]}]
    battle = factory.build_battle(
        factory.build_player("A", team_a),
        factory.build_player("B", team_b),
    )
    battle.player_a.team[1].current_hp = 0

    label = describe_action(battle.player_a, 11)

    assert battle.player_a.team[2].name in label


def test_pimc_advise_reuses_default_network_opponent(monkeypatch):
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    battle = factory.build_battle(
        factory.build_player("A", team_a),
        factory.build_player("B", team_b),
    )
    dets = make_determinizations(battle, factory, bench_pool=None, k=3)
    model = ModularBattleNet()
    created: list[object] = []

    class StubNetworkPolicyAgent:
        def __init__(self, *args, **kwargs):
            created.append(self)

    def fake_mcts(*args, **kwargs):
        return np.eye(1, NUM_ACTIONS, dtype=np.float32)[0]

    def fake_win_prob(*args, **kwargs):
        return 0.5

    monkeypatch.setattr(advisor_module, "NetworkPolicyAgent", StubNetworkPolicyAgent)
    monkeypatch.setattr(advisor_module, "mcts_search", fake_mcts)
    monkeypatch.setattr(advisor_module, "_estimate_win_prob", fake_win_prob)

    advise(dets, model, factory, opponent_agent=None, num_simulations=1)

    assert len(created) == 1


def test_neural_mcts_agent_does_not_create_factory_per_action(monkeypatch):
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    team_a, team_b = _two_teams(sprite_skills)
    battle = factory.build_battle(
        factory.build_player("A", team_a),
        factory.build_player("B", team_b),
    )
    original_a = battle.player_a
    original_b = battle.player_b
    model = ModularBattleNet()
    created_factories: list[object] = []

    class StubFactory:
        def __init__(self):
            created_factories.append(self)

    def fake_mcts(bt, model_arg, factory_arg, opponent, **kwargs):
        assert bt.player_a is original_b
        assert bt.player_b is original_a
        assert factory_arg is None
        assert kwargs["opp_greedy"] is True
        return np.eye(1, NUM_ACTIONS, 15, dtype=np.float32)[0]

    monkeypatch.setattr(service_agent_module, "_load_model", lambda: model)
    monkeypatch.setattr("backend.sim.factory.SimFactory", StubFactory)
    monkeypatch.setattr(service_agent_module, "mcts_search", fake_mcts)

    agent = service_agent_module.NeuralMCTSAgent()
    agent.NUM_SIMULATIONS = 1
    action = agent.choose_action(battle)

    assert action.kind == "gather"
    assert created_factories == []
    assert battle.player_a is original_a
    assert battle.player_b is original_b


def test_enum_token_lookup_is_cached():
    encoder_module._ENUM_TOKEN_CACHE.clear()
    key = ("power", ("ATTR_",))

    first = encoder_module._try_enum_token("power", ("ATTR_",))
    second = encoder_module._try_enum_token("power", ("ATTR_",))

    assert first == "ATTR_POWER"
    assert second == first
    assert encoder_module._ENUM_TOKEN_CACHE[key] == "ATTR_POWER"


def test_skill_effect_ast_flattening_is_cached(monkeypatch):
    encoder_module._skill_effects_cache.clear()
    encoder_module._skill_flat_effects_cache.clear()
    encoder_module._skill_flat_effect_ids_cache.clear()
    encoder_module._skill_effects_cache["__cache_test__"] = [
        {"op": "heal", "target": "sprite_self", "amount": 1},
    ]
    calls = {"count": 0}
    original = encoder_module.tokenize_effect_dfs

    def counted(effect):
        calls["count"] += 1
        return original(effect)

    monkeypatch.setattr(encoder_module, "tokenize_effect_dfs", counted)

    first = encoder_module._get_flat_skill_effect_tokens("__cache_test__")
    second = encoder_module._get_flat_skill_effect_tokens("__cache_test__")
    first_ids = encoder_module._get_flat_skill_effect_token_ids("__cache_test__")
    second_ids = encoder_module._get_flat_skill_effect_token_ids("__cache_test__")

    assert first == second
    assert first_ids == second_ids
    assert first_ids[0] == tuple(encoder_module.VOCAB_TO_ID.get(token, encoder_module.VOCAB_TO_ID["<UNK>"]) for token in first[0])
    assert first_ids[1] == first[1]
    assert calls["count"] == 1
    assert "__cache_test__" in encoder_module._skill_flat_effects_cache
    assert "__cache_test__" in encoder_module._skill_flat_effect_ids_cache


def test_observer_effect_ast_token_ids_are_cached(monkeypatch):
    encoder_module._observer_effect_token_ids_cache.clear()
    eff = ObserverEffect(
        name="__observer_cache__",
        source="test",
        cond={"cond": "always"},
        then=[{"op": "heal", "target": "sprite_self", "amount": 1}],
        listen=frozenset({"post_skill"}),
    )
    calls = {"count": 0}
    original = encoder_module.tokenize_effect_dfs

    def counted(effect):
        calls["count"] += 1
        return original(effect)

    monkeypatch.setattr(encoder_module, "tokenize_effect_dfs", counted)

    first = encoder_module._get_observer_effect_token_ids(eff)
    first_count = calls["count"]
    second = encoder_module._get_observer_effect_token_ids(eff)
    second_count = calls["count"]
    eff.then.append({"op": "heal", "target": "sprite_self", "amount": 2})
    third = encoder_module._get_observer_effect_token_ids(eff)

    assert first == second
    assert third != first
    assert first_count > 0
    assert second_count == first_count
    assert calls["count"] > second_count
    assert id(eff) in encoder_module._observer_effect_token_ids_cache


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {name}: {e}")
