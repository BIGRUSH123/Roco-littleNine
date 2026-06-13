import numpy as np


class UniformEvaluator:
    def evaluate(self, state, mask):
        probs = mask.astype(np.float32, copy=True)
        total = probs.sum()
        if total > 0:
            probs /= total
        return 0.0, probs

    def evaluate_batch(self, states, masks):
        mask_arr = np.stack(masks, axis=0) if isinstance(masks, list) else masks
        probs = mask_arr.astype(np.float32, copy=True)
        totals = probs.sum(axis=1, keepdims=True)
        np.divide(probs, totals, out=probs, where=totals > 0)
        return np.zeros(len(states), dtype=np.float32), probs


def _build_battle():
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "水灵"}])
    p2 = factory.build_player("B", [{"name": "草衣虫"}])
    return factory, factory.build_battle(p1, p2)


def test_worker_mcts_restores_battle_without_create_battle():
    from backend.engine.ai.core.mcts import NetworkPolicyAgent
    from backend.engine.ai.core.mcts_parallel import _worker_mcts

    factory, battle = _build_battle()
    evaluator = UniformEvaluator()
    opponent = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
    initial_state = {
        "player_a": battle.player_a,
        "player_b": battle.player_b,
        "weather": battle.globals.weather,
        "mutable_state": battle.save_mutable_state(),
    }

    visits = _worker_mcts(
        initial_state,
        factory,
        None,
        {
            "type": "NetworkPolicyAgent",
            "temperature": opponent._temperature,
            "greedy": opponent._greedy,
        },
        1,
        "cpu",
        {
            "evaluator": evaluator,
            "root_noise": 0.0,
            "leaf_batch_size": 1,
        },
        0,
    )

    assert visits.shape == (17,)
    assert visits.sum() > 0


def test_parallel_mcts_search_root_with_process_pool():
    from backend.engine.ai.core.mcts import NetworkPolicyAgent
    from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root

    factory, battle = _build_battle()
    evaluator = UniformEvaluator()
    opponent = NetworkPolicyAgent(evaluator=evaluator, greedy=True)

    probs = parallel_mcts_search_root(
        battle=battle,
        model=None,
        factory=factory,
        opponent_agent=opponent,
        num_simulations=2,
        num_workers=1,
        evaluator=evaluator,
        root_noise=0.0,
        leaf_batch_size=1,
    )

    assert probs.shape == (17,)
    assert np.isclose(probs.sum(), 1.0)
