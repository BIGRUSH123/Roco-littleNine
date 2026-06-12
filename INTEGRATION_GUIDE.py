"""集成 MCTS 并行化到训练代码的补丁

在 train.py 中添加对 ParallelMCTSAgent 的支持。
"""

# 在 train.py 顶部添加导入（在现有导入之后）
"""
from backend.engine.ai.parallel_agent import ParallelMCTSAgent
"""


# 修改 collect_rl_samples 函数，添加 mcts_parallel 和 mcts_workers 参数
def collect_rl_samples_with_parallel(
    model,
    factory,
    sprite_skills,
    num_battles: int,
    num_simulations: int = 200,
    device: str = "cpu",
    max_turns: int = 100,
    draw_margin: float = 0.02,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
    progress_every: int = 10,
    battle_log_writer = None,
    gamma: float = 1.0,
    tanh_k: float = 0.0,
    leaf_batch_size: int = 16,
    mirror: bool = False,
    mcts_parallel: bool = False,  # 新增：是否启用 MCTS 并行
    mcts_workers: int = 4,  # 新增：MCTS 并行 worker 数
    mcts_pool = None,  # 新增：复用的进程池
):
    """带 MCTS 并行化支持的 collect_rl_samples"""
    import copy
    import time
    import numpy as np
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.mcts import NetworkPolicyAgent
    from backend.engine.ai.core.outcome import battle_outcome_a
    from backend.engine.ai.train import MCTSAgent, _random_teams, _random_item
    from backend.engine.ai.parallel_agent import ParallelMCTSAgent

    all_states = []
    all_probs = []
    all_masks = []
    all_outcomes = []
    reason_counts = {}
    evaluator = TorchEvaluator(model, device)
    pe = max(1, progress_every)

    # 决定使用哪个 Agent 类
    AgentClass = ParallelMCTSAgent if mcts_parallel else MCTSAgent

    for i in range(num_battles):
        team_a, team_b = _random_teams(factory, sprite_skills)
        if mirror:
            team_b = copy.deepcopy(team_a)
        p1 = factory.build_player("A", team_a, item=_random_item())
        p2 = factory.build_player("B", team_b, item=_random_item())
        battle = factory.build_battle(p1, p2)

        opp_a = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
        opp_b = NetworkPolicyAgent(evaluator=evaluator, greedy=True)

        # 根据 mcts_parallel 选择 Agent 类型
        agent_kwargs = {
            "factory": factory,
            "num_simulations": num_simulations,
            "temperature": temperature,
            "root_noise": root_noise,
            "record": True,
            "evaluator": evaluator,
            "max_turns": max_turns,
            "draw_margin": draw_margin,
            "gamma": gamma,
            "tanh_k": tanh_k,
            "leaf_batch_size": leaf_batch_size,
        }

        if mcts_parallel:
            # 添加并行化参数
            agent_kwargs["num_workers"] = mcts_workers
            agent_kwargs["pool"] = mcts_pool

        agent_a = AgentClass("A", p1, opp_a, **agent_kwargs)
        agent_b = AgentClass("B", p2, opp_b, **agent_kwargs)

        battle_started = time.monotonic()
        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1
            if time.monotonic() - battle_started >= 450:
                break

        outcome_a, end_reason = battle_outcome_a(
            battle, max_turns, draw_margin=draw_margin,
            gamma=gamma, tanh_k=tanh_k,
        )
        reason_counts[end_reason] = reason_counts.get(end_reason, 0) + 1

        # 收集样本（双方视角）
        for agent, outcome in [(agent_a, outcome_a), (agent_b, -outcome_a)]:
            for state, probs, mask in agent.history:
                all_states.append(state)
                all_probs.append(probs)
                all_masks.append(mask)
                all_outcomes.append(outcome)

        if verbose and (i + 1) % pe == 0:
            print(f"  完成 {i + 1}/{num_battles} 局")

    P = np.stack(all_probs, axis=0)
    M = np.stack(all_masks, axis=0)
    v = np.array(all_outcomes, dtype=np.float32)

    return all_states, P, M, v, reason_counts


# ============================================================================
# 使用说明
# ============================================================================

"""
在 train.py 的 main() 函数中添加：

1. 在开始训练前创建进程池（如果启用了 mcts_parallel）：

    mcts_pool = None
    if args.mcts_parallel:
        import multiprocessing as mp
        mcts_pool = mp.Pool(args.mcts_workers)
        _log(f"创建 MCTS 并行进程池: {args.mcts_workers} workers")

2. 修改 collect_rl_samples 调用：

    X, P, M, v, reason_counts = collect_rl_samples(
        best_model, factory, sprite_skills,
        num_battles=args.battles,
        num_simulations=effective_sims,
        device=device,
        max_turns=args.max_turns,
        draw_margin=args.draw_margin,
        temperature=temp,
        root_noise=args.root_noise,
        progress_every=args.progress_every,
        battle_log_writer=battle_log,
        gamma=args.gamma,
        tanh_k=args.tanh_k,
        leaf_batch_size=args.leaf_batch_size,
        mirror=all_mirror,
        mcts_parallel=args.mcts_parallel,  # 新增
        mcts_workers=args.mcts_workers,    # 新增
        mcts_pool=mcts_pool,              # 新增
    )

3. 训练结束后关闭进程池：

    if mcts_pool is not None:
        mcts_pool.close()
        mcts_pool.join()
        _log("MCTS 并行进程池已关闭")

4. 运行命令：

    # 不启用并行（默认）
    python -m backend.engine.ai.train --battles 200 --sims 200

    # 启用 MCTS 并行（推荐）
    python -m backend.engine.ai.train --battles 200 --sims 800 --mcts-parallel --mcts-workers 4

    # 预期效果：训练速度提升约 2x
"""
