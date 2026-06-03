"""backend/engine/ai/train.py — AlphaZero 风格自我博弈强化学习训练

用法:
    python -m backend.engine.ai.train                         # 默认参数 RL 训练
    python -m backend.engine.ai.train --iterations 10         # 10轮迭代
    python -m backend.engine.ai.train --battles 200 --sims 400  # 每轮200局, 800次模拟
    python -m backend.engine.ai.train --resume model.pt       # 继续训练
    python -m backend.engine.ai.train --mode supervised       # 监督学习模式 (RuleAgent)
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.ai.battle_log import BattleLogWriter, extract_battle_summary
from backend.engine.ai.encode import encode_battle_state
from backend.engine.ai.evaluator import BatchedInferenceServer, BatchedModelInferenceServer, TorchEvaluator
from backend.engine.ai.outcome import (
    DEFAULT_DRAW_MARGIN,
    DEFAULT_EVAL_MAX_TURNS,
    DEFAULT_SELFPLAY_MAX_TURNS,
    battle_outcome_a,
    eval_score_for_candidate,
    format_reason_counts,
)
from backend.engine.ai.selfplay_worker import run_evaluate_worker, run_selfplay_worker
from backend.engine.ai.model import BattleNet
from backend.engine.ai.mcts import (
    NUM_ACTIONS,
    NetworkPolicyAgent,
    action_index_to_action,
    mcts_search,
)
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory

# ═══════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════

def _load_sprite_skills() -> dict[str, list[str]]:
    sprites_dir = PROJECT_ROOT / "data" / "sprites"
    skills_dir = PROJECT_ROOT / "data" / "skills"
    on_disk: set[str] = {p.stem for p in skills_dir.glob("*.json") if not p.stem.startswith("_")}

    result: dict[str, list[str]] = {}
    for path in sprites_dir.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name", path.stem.split("_", 1)[-1])
            skill_ids = data.get("skills", [])
            skill_names: list[str] = []
            for sid in skill_ids:
                sname = SKILL_ID_TO_NAME.get(sid)
                if sname and sname in on_disk:
                    skill_names.append(sname)
            if skill_names:
                result[name] = skill_names
        except (json.JSONDecodeError, KeyError):
            continue
    return result


def _random_teams(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    max_team_size: int = 3,
    max_skills: int = 4,
) -> tuple:
    names = list(sprite_skills.keys())
    random.shuffle(names)
    used: set[str] = set()  # 两队共享排重，避免 AB 重复选同一批精灵

    def build_team(label: str) -> list[dict]:
        size = random.randint(1, min(max_team_size, len(names) // 2))
        specs: list[dict] = []
        for name in names:
            if name in used:
                continue
            if len(specs) >= size:
                break
            available = sprite_skills[name]
            n_skills = min(max_skills, len(available))
            chosen = random.sample(available, max(1, n_skills))
            specs.append({"name": name, "skills": chosen})
            used.add(name)
        return specs

    return build_team("A"), build_team("B")


# ═══════════════════════════════════════════════════════════════════
# MCTSAgent — 将 MCTS 包装为 Agent 接口
# ═══════════════════════════════════════════════════════════════════

class MCTSAgent:
    """使用 MCTS 选择动作的 Agent，兼容 battle.execute_turn 接口。

    当 team=="B" 时，在调用 mcts_search 前临时交换 player_a/player_b，
    因为 mcts_search 始终从 player_a 视角工作。
    """

    def __init__(
        self,
        team: str,
        player,
        factory: SimFactory,
        opponent_agent,
        num_simulations: int = 200,
        temperature: float = 1.0,
        root_noise: float = 0.25,
        record: bool = False,
        *,
        model: BattleNet | None = None,
        device: str = "cpu",
        evaluator=None,
    ):
        self.team = team
        self.player = player
        self._factory = factory
        self._opponent = opponent_agent
        self._num_simulations = num_simulations
        self._temperature = temperature
        self._root_noise = root_noise
        self._record = record
        if evaluator is None:
            if model is None:
                raise ValueError("MCTSAgent 需要 model 或 evaluator")
            evaluator = TorchEvaluator(model, device)
        self._evaluator = evaluator
        # 自我博弈训练样本：(本方视角状态, MCTS 访问分布)
        self.history: list[tuple[np.ndarray, np.ndarray]] = []

    def choose_lead(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_action(self, battle):
        swapped = False
        if self.team == "B":
            battle.player_a, battle.player_b = battle.player_b, battle.player_a
            swapped = True

        try:
            # 本方视角下编码一次，同时用于 MCTS 根节点评估 + 训练样本记录
            state = encode_battle_state(battle) if self._record else None
            probs = mcts_search(
                battle, None, self._factory, self._opponent,
                num_simulations=self._num_simulations,
                root_noise=self._root_noise,
                evaluator=self._evaluator,
                root_state=state,  # 复用已编码状态，省掉 mcts_search 内部二次编码
            )
            if self._record and state is not None:
                self.history.append((state, probs.copy()))
        finally:
            if swapped:
                battle.player_a, battle.player_b = battle.player_b, battle.player_a

        action_idx = _sample_action(probs, self._temperature)
        player = battle.player_a if self.team == "A" else battle.player_b
        action = action_index_to_action(player, action_idx)
        if action is not None:
            return action
        from backend.sim.action import Action
        return Action(kind="gather")

    def choose_replacement(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        return alive[0] if alive else -1  # -1 通知引擎扣魔力

    def on_game_end(self, winner: str) -> None:
        pass


def _sample_action(probs: np.ndarray, temperature: float) -> int:
    """按温度参数从概率分布中采样动作索引。"""
    if temperature <= 0 or temperature < 1e-8:
        return int(np.argmax(probs))
    if temperature != 1.0:
        probs = probs ** (1.0 / temperature)
        s = probs.sum()
        if s > 0:
            probs = probs / s
        else:
            return int(np.argmax(probs))
    s = probs.sum()
    if s <= 0:
        return 0
    probs = probs / s
    return int(np.random.choice(len(probs), p=probs))


# ═══════════════════════════════════════════════════════════════════
# RL 数据收集 — MCTS 自我博弈
# ═══════════════════════════════════════════════════════════════════

def collect_rl_samples(
    model: BattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    num_simulations: int = 200,
    device: str = "cpu",
    max_turns: int = DEFAULT_SELFPLAY_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
    progress_every: int = 10,
    battle_log_writer = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """MCTS 自我博弈收集 (state, target_probs, outcome) 三元组。

    与 AlphaZero 的差异（已修正）：
      1. 搜索内对手用当前网络（NetworkPolicyAgent），而非固定 RuleAgent
         → 真正的自我博弈。
      2. 同时记录 A、B **双方视角**的样本（B 的状态在交换后的本方视角下
         编码，结果取反）→ 数据翻倍且消除先手偏置。
      3. 每个决策只搜索一次（由 MCTSAgent 内部记录），不再重复搜索。

    Returns:
        X: (N, 446) float32 状态向量
        P: (N, 10) float32 MCTS 访问分布（策略目标）
        v: (N,) float32 对局结果（以各样本本方视角，+1=本方赢, -1=输, 0=平）
    """
    all_states: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []
    all_outcomes: list[float] = []
    reason_counts: dict[str, int] = {}
    evaluator = TorchEvaluator(model, device)

    for i in range(num_battles):
        team_a, team_b = _random_teams(factory, sprite_skills)
        p1 = factory.build_player("A", team_a)
        p2 = factory.build_player("B", team_b)
        battle = factory.build_battle(p1, p2)

        # 搜索内的对手 = 当前网络策略头（槽位驱动，对 A/B 两侧搜索通用）
        opp_a = NetworkPolicyAgent(evaluator=evaluator, temperature=temperature)
        opp_b = NetworkPolicyAgent(evaluator=evaluator, temperature=temperature)
        agent_a = MCTSAgent(
            "A", p1, factory, opp_a, num_simulations,
            temperature, root_noise=root_noise, record=True,
            evaluator=evaluator,
        )
        agent_b = MCTSAgent(
            "B", p2, factory, opp_b, num_simulations,
            temperature, root_noise=root_noise, record=True,
            evaluator=evaluator,
        )

        battle_started = time.monotonic()
        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1
            if time.monotonic() - battle_started >= 300:  # 5min 单局上限
                break

        outcome_a, end_reason = battle_outcome_a(
            battle, max_turns, draw_margin=draw_margin,
        )
        if time.monotonic() - battle_started >= 300:
            end_reason = "timeout"
        reason_counts[end_reason] = reason_counts.get(end_reason, 0) + 1

        # 写入对局技能日志
        if battle_log_writer is not None:
            battle_summary = extract_battle_summary(battle, end_reason)
            battle_log_writer.write(battle_summary)

        for state, probs in agent_a.history:
            all_states.append(state)
            all_probs.append(probs)
            all_outcomes.append(outcome_a)
        for state, probs in agent_b.history:
            all_states.append(state)
            all_probs.append(probs)
            all_outcomes.append(-outcome_a)

        pe = max(1, progress_every)
        if verbose and (i + 1) % pe == 0:
            print(f"  RL 自我博弈 {i + 1}/{num_battles} 局, 样本 {len(all_states)}", flush=True)

    if not all_states:
        return (
            np.zeros((0, 446), dtype=np.float32),
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            {},
        )

    X = np.stack(all_states).astype(np.float32)
    P = np.stack(all_probs).astype(np.float32)
    v = np.array(all_outcomes, dtype=np.float32)
    return X, P, v, reason_counts


def _play_one_rl_battle(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    evaluator,
    num_simulations: int,
    max_turns: int,
    temperature: float,
    root_noise: float,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    game_timeout_s: float = 300.0,
) -> tuple[list[np.ndarray], list[np.ndarray], list[float], str, dict]:
    """单局自我博弈，返回 (states, probs, outcomes, end_reason, battle_summary)。

    game_timeout_s: 单局 wall-time 上限，超时强制退出并标记 end_reason="timeout"。
                    防止 MCTS 仿真在复杂对局状态中逐渐变慢导致单局卡死。
    """
    team_a, team_b = _random_teams(factory, sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    opp_a = NetworkPolicyAgent(evaluator=evaluator, temperature=temperature)
    opp_b = NetworkPolicyAgent(evaluator=evaluator, temperature=temperature)
    agent_a = MCTSAgent(
        "A", p1, factory, opp_a, num_simulations,
        temperature, root_noise=root_noise, record=True,
        evaluator=evaluator,
    )
    agent_b = MCTSAgent(
        "B", p2, factory, opp_b, num_simulations,
        temperature, root_noise=root_noise, record=True,
        evaluator=evaluator,
    )

    battle_started = time.monotonic()
    turn = 0
    while not battle.is_finished and turn < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turn += 1
        if time.monotonic() - battle_started >= game_timeout_s:
            break

    outcome_a, end_reason = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin,
    )
    # 超时覆盖 end_reason（优先级高于 max_turns / decisive）
    if time.monotonic() - battle_started >= game_timeout_s:
        end_reason = "timeout"

    # 提取对局回合技能摘要
    battle_summary = extract_battle_summary(battle, end_reason)

    states: list[np.ndarray] = []
    probs: list[np.ndarray] = []
    outcomes: list[float] = []
    for state, pi in agent_a.history:
        states.append(state)
        probs.append(pi)
        outcomes.append(outcome_a)
    for state, pi in agent_b.history:
        states.append(state)
        probs.append(pi)
        outcomes.append(-outcome_a)
    return states, probs, outcomes, end_reason, battle_summary


def collect_rl_samples_parallel(
    model: BattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    num_workers: int,
    device: str,
    inference_batch_size: int = 128,
    inference_timeout_ms: float = 5.0,
    num_simulations: int = 200,
    max_turns: int = DEFAULT_SELFPLAY_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
    progress_every: int = 1,
    stall_timeout_s: float = 600.0,
    battle_log_writer = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """多进程局级 self-play + 主进程 CUDA 批量推理。

    work-stealing：worker 干完一局再领下一局，慢局只拖自己。
    stall_timeout_s：若全部 worker 在该秒数内都无任何对局完成（疑似单局 VM 死循环），
    终止剩余 worker、丢弃未完成局，用已完成的局继续，避免整轮冻结。
    """
    n_workers = max(1, min(num_workers, num_battles))

    if verbose:
        print(
            f"  并行自我博弈: {n_workers} workers 动态领取 {num_battles} 局, "
            f"batch={inference_batch_size}, timeout={inference_timeout_ms}ms",
            flush=True,
        )

    ctx = mp.get_context("spawn")
    request_queue = ctx.Queue()
    result_queue = ctx.Queue()
    # 任务队列：num_battles 个单局任务 + 每个 worker 一个 None 停止哨兵。
    task_queue = ctx.Queue()
    for i in range(num_battles):
        task_queue.put(i)
    for _ in range(n_workers):
        task_queue.put(None)

    model.eval()
    reply_queues: dict[int, object] = {}
    processes: list[mp.Process] = []
    # 单局时间预算取 stall_timeout_s 的一半，确保慢局能在全局卡死保护之前自行退出
    game_timeout_s = stall_timeout_s / 2.0
    for wid in range(n_workers):
        reply_q = ctx.Queue()
        reply_queues[wid] = reply_q
        seed = random.randint(0, 2**31 - 1)
        proc = ctx.Process(
            target=run_selfplay_worker,
            args=(
                wid, seed,
                num_simulations, max_turns, draw_margin, temperature, root_noise,
                progress_every, game_timeout_s,
                task_queue, request_queue, reply_q, result_queue,
            ),
        )
        proc.start()
        processes.append(proc)

    server = BatchedInferenceServer(
        model, device, request_queue, reply_queues,
        batch_size=inference_batch_size,
        timeout_ms=inference_timeout_ms,
    )
    server.start()

    xs: list[np.ndarray] = []
    ps: list[np.ndarray] = []
    vs: list[np.ndarray] = []
    all_reason_counts: dict[str, int] = {}
    finished: set[int] = set()
    done_workers = 0
    battles_done = 0
    last_wait_report = time.monotonic()
    last_progress = time.monotonic()
    stalled = False
    try:
        while done_workers < n_workers:
            try:
                raw_result = result_queue.get(timeout=10.0)
                tag, wid = raw_result[0], raw_result[1]
            except queue.Empty:
                for pid_idx, proc in enumerate(processes):
                    if (
                        pid_idx not in finished
                        and proc.exitcode is not None
                        and proc.exitcode != 0
                    ):
                        raise RuntimeError(
                            f"self-play worker {pid_idx} 提前退出 "
                            f"(pid={proc.pid}, exitcode={proc.exitcode})"
                        )
                now = time.monotonic()
                if now - last_progress >= stall_timeout_s:
                    alive = [
                        wid for wid in range(n_workers)
                        if wid not in finished
                    ]
                    print(
                        f"  ⚠ 卡死保护：{stall_timeout_s:.0f}s 内无任何对局完成，"
                        f"判定 worker {alive} 单局死循环，终止剩余 worker，"
                        f"用已完成的 {battles_done}/{num_battles} 局继续",
                        flush=True,
                    )
                    stalled = True
                    break
                if verbose and now - last_wait_report >= 60.0:
                    print(
                        f"  进度: {battles_done}/{num_battles} 局完成, "
                        f"{done_workers}/{n_workers} workers 退出",
                        flush=True,
                    )
                    last_wait_report = now
                continue

            if tag == "error":
                raise RuntimeError(f"self-play worker {wid} 异常:\n{raw_result[2]}")
            last_progress = time.monotonic()
            if tag == "done":
                finished.add(wid)
                done_workers += 1
                continue
            # tag == "battle"
            X, P, v, end_reason, battle_summary = (
                raw_result[2], raw_result[3], raw_result[4],
                raw_result[5], raw_result[6],
            )
            if len(X) > 0:
                xs.append(X)
                ps.append(P)
                vs.append(v)
            all_reason_counts[end_reason] = all_reason_counts.get(end_reason, 0) + 1
            battles_done += 1
            # 写入对局技能日志
            if battle_log_writer is not None and battle_summary is not None:
                battle_log_writer.write(battle_summary)
            if verbose and (battles_done % max(1, progress_every) == 0):
                print(
                    f"  进度: {battles_done}/{num_battles} 局完成",
                    flush=True,
                )

        if stalled:
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=10.0)
        for proc in processes:
            proc.join(timeout=3600)
            if proc.is_alive():
                proc.terminate()
                proc.join()
                raise RuntimeError(f"self-play worker {proc.pid} 超时未结束")
    except Exception:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10.0)
        raise
    finally:
        server.stop()

    if not xs:
        return (
            np.zeros((0, 446), dtype=np.float32),
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            all_reason_counts,
        )
    return np.concatenate(xs), np.concatenate(ps), np.concatenate(vs), all_reason_counts


def collect_battle_samples(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    max_turns: int = 200,
    verbose: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    all_states: list[np.ndarray] = []
    all_labels: list[float] = []

    for i in range(num_battles):
        team_a, team_b = _random_teams(factory, sprite_skills)
        p1 = factory.build_player("A", team_a)
        p2 = factory.build_player("B", team_b)
        battle = factory.build_battle(p1, p2)

        agent_a = RuleAgent("A", p1)
        agent_b = RuleAgent("B", p2)

        turn = 0
        while not battle.is_finished and turn < max_turns:
            state = encode_battle_state(battle)
            all_states.append(state)
            battle.execute_turn(agent_a, agent_b)
            turn += 1

        if battle.winner == "A":
            label = 1.0
        elif battle.winner == "B":
            label = -1.0
        else:
            label = 0.0

        all_labels.extend([label] * turn)

        if verbose and (i + 1) % 50 == 0:
            print(f"  已收集 {i + 1}/{num_battles} 局, 样本 {len(all_states)}")

    X = np.stack(all_states).astype(np.float32)
    y = np.array(all_labels, dtype=np.float32)
    return X, y


# ═══════════════════════════════════════════════════════════════════
# RL 训练
# ═══════════════════════════════════════════════════════════════════

def train_rl(
    model: BattleNet,
    X: np.ndarray,
    P: np.ndarray,
    v: np.ndarray,
    epochs: int,
    batch_size: int,
    lr: float,
    device: str,
    val_split: float = 0.1,
    weight_decay: float = 1e-4,
) -> list[dict]:
    """训练双头网络：value loss (MSE) + policy loss (cross-entropy)。

    weight_decay: L2 正则（AlphaZero 用 ~1e-4 抑制过拟合）。
    """
    n = len(X)
    if n == 0:
        print("  [train_rl] 无样本，跳过训练")
        return []
    indices = np.random.permutation(n)
    # 至少保留 1 个验证样本（且训练集非空）
    split = int(n * (1 - val_split))
    split = min(max(split, 1), n - 1) if n > 1 else 1
    train_idx = indices[:split]
    val_idx = indices[split:] if n > 1 else indices[:1]

    X_train = torch.from_numpy(X[train_idx]).to(device)
    P_train = torch.from_numpy(P[train_idx]).to(device)
    v_train = torch.from_numpy(v[train_idx]).unsqueeze(1).to(device)
    X_val = torch.from_numpy(X[val_idx]).to(device)
    P_val = torch.from_numpy(P[val_idx]).to(device)
    v_val = torch.from_numpy(v[val_idx]).unsqueeze(1).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr * 0.01)
    history: list[dict] = []

    for epoch in range(epochs):
        model.train()
        total_value_loss = 0.0
        total_policy_loss = 0.0
        perm = torch.randperm(len(X_train), device=device)

        for start in range(0, len(X_train), batch_size):
            batch_idx = perm[start : start + batch_size]
            xb = X_train[batch_idx]
            pb = P_train[batch_idx]
            vb = v_train[batch_idx]

            value, logits = model(xb)
            value_loss = F.mse_loss(value, vb)
            # 用目标策略 pb 作为隐式 mask：pb>0 为合法动作，pb==0 为非法动作。
            # 将非法动作 logit 设为 -1e9（与 forward_with_mask 一致），
            # 使 softmax 分母仅对合法动作归一化，消除非法动作对梯度的无效占用。
            legal_mask = (pb > 0).float()
            masked_logits = logits.masked_fill(legal_mask == 0, -1e9)
            policy_loss = -torch.sum(pb * F.log_softmax(masked_logits, dim=-1), dim=-1).mean()
            loss = value_loss + policy_loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            total_value_loss += value_loss.item() * len(batch_idx)
            total_policy_loss += policy_loss.item() * len(batch_idx)

        train_v_loss = total_value_loss / len(X_train)
        train_p_loss = total_policy_loss / len(X_train)

        model.eval()
        with torch.no_grad():
            val_v, val_logits = model(X_val)
            val_v_loss = F.mse_loss(val_v, v_val).item()
            val_legal_mask = (P_val > 0).float()
            val_masked_logits = val_logits.masked_fill(val_legal_mask == 0, -1e9)
            val_p_loss = -torch.sum(P_val * F.log_softmax(val_masked_logits, dim=-1), dim=-1).mean().item()
            val_acc = ((val_v.squeeze().sign() == v_val.squeeze().sign()).float().mean().item())

        scheduler.step()

        history.append({
            "epoch": epoch + 1,
            "train_v_loss": train_v_loss,
            "train_p_loss": train_p_loss,
            "val_v_loss": val_v_loss,
            "val_p_loss": val_p_loss,
            "val_acc": val_acc,
        })

        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"  Epoch {epoch + 1:3d}/{epochs}  "
                  f"v_loss={train_v_loss:.4f}/{val_v_loss:.4f}  "
                  f"p_loss={train_p_loss:.4f}/{val_p_loss:.4f}  "
                  f"val_acc={val_acc:.3f}  "
                  f"lr={scheduler.get_last_lr()[0]:.2e}")

    return history


# ═══════════════════════════════════════════════════════════════════
# 监督学习训练（兼容旧模式）
# ═══════════════════════════════════════════════════════════════════

def train_supervised(
    model,
    X: np.ndarray,
    y: np.ndarray,
    epochs: int,
    batch_size: int,
    lr: float,
    device: str,
    val_split: float = 0.1,
) -> list[dict]:
    from backend.engine.ai.model import BattleValueNet
    n = len(X)
    indices = np.random.permutation(n)
    split = int(n * (1 - val_split))
    train_idx = indices[:split]
    val_idx = indices[split:]

    X_train = torch.from_numpy(X[train_idx]).to(device)
    y_train = torch.from_numpy(y[train_idx]).unsqueeze(1).to(device)
    X_val = torch.from_numpy(X[val_idx]).to(device)
    y_val = torch.from_numpy(y[val_idx]).unsqueeze(1).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = torch.nn.MSELoss()
    history: list[dict] = []

    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        perm = torch.randperm(len(X_train), device=device)
        for start in range(0, len(X_train), batch_size):
            batch_idx = perm[start : start + batch_size]
            xb = X_train[batch_idx]
            yb = y_train[batch_idx]

            pred = model(xb)
            loss = criterion(pred, yb)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(batch_idx)

        train_loss = total_loss / len(X_train)

        model.eval()
        with torch.no_grad():
            val_pred = model(X_val)
            val_loss = criterion(val_pred, y_val).item()

        history.append({"epoch": epoch + 1, "train_loss": train_loss, "val_loss": val_loss})

        if (epoch + 1) % 5 == 0 or epoch == 0:
            train_acc = ((model(X_train).squeeze().sign() == y_train.squeeze().sign()).float().mean().item())
            val_acc = ((val_pred.squeeze().sign() == y_val.squeeze().sign()).float().mean().item())
            print(f"  Epoch {epoch + 1:3d}/{epochs}  "
                  f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                  f"train_acc={train_acc:.3f}  val_acc={val_acc:.3f}")

    return history


# ═══════════════════════════════════════════════════════════════════
# 评估门控（AlphaZero：新网络须明显强于旧最优才晋升）
# ═══════════════════════════════════════════════════════════════════

def _clone_model(model, device: str):
    """深拷贝模型（支持 BattleNet / ModularBattleNet）。"""
    if hasattr(model, 'trunk_dim'):
        # ModularBattleNet
        from backend.engine.ai.model import ModularBattleNet
        clone = ModularBattleNet(
            trunk_dim=model.trunk_dim,
            num_blocks=model.num_blocks,
            dropout=model.dropout_val,
            with_attention=model.with_attention,
        )
    else:
        clone = BattleNet(hidden=model.hidden_dims, dropout=model.dropout)
    clone.load_state_dict(model.state_dict())
    clone.to(device)
    clone.eval()
    return clone


def evaluate(
    candidate: BattleNet,
    best: BattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    n_games: int,
    num_simulations: int,
    device: str,
    max_turns: int = DEFAULT_EVAL_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    verbose: bool = True,
) -> float:
    """candidate vs best 对打，返回 candidate 胜率（平局计 0.5）。

    每局双方各用自己的网络做 MCTS（贪心、无探索噪声）；偶数局 candidate
    执先手 A、奇数局执后手 B，以消除先后手偏置。
    """
    if n_games <= 0:
        return 0.0

    wins = 0.0
    for g in range(n_games):
        team_a, team_b = _random_teams(factory, sprite_skills)
        p1 = factory.build_player("A", team_a)
        p2 = factory.build_player("B", team_b)
        battle = factory.build_battle(p1, p2)

        cand_is_a = (g % 2 == 0)
        model_a = candidate if cand_is_a else best
        model_b = best if cand_is_a else candidate

        eval_a = TorchEvaluator(model_a, device)
        eval_b = TorchEvaluator(model_b, device)
        opp_a = NetworkPolicyAgent(evaluator=eval_a, greedy=True)
        opp_b = NetworkPolicyAgent(evaluator=eval_b, greedy=True)
        agent_a = MCTSAgent(
            "A", p1, factory, opp_a, num_simulations,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=eval_a,
        )
        agent_b = MCTSAgent(
            "B", p2, factory, opp_b, num_simulations,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=eval_b,
        )

        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1

        outcome_a, _ = battle_outcome_a(
            battle, max_turns, draw_margin=draw_margin,
        )
        wins += eval_score_for_candidate(outcome_a, cand_is_a)

        if verbose and (g + 1) % 10 == 0:
            print(f"    评估 {g + 1}/{n_games} 局, 当前胜率 {wins / (g + 1):.2%}")

    return wins / n_games


def _play_one_eval_game(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    candidate_evaluator,
    best_evaluator,
    game_index: int,
    num_simulations: int,
    max_turns: int,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
) -> float:
    """单局 candidate vs best，返回 candidate 得分：胜=1，平=0.5，负=0。"""
    team_a, team_b = _random_teams(factory, sprite_skills)
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)

    cand_is_a = (game_index % 2 == 0)
    eval_a = candidate_evaluator if cand_is_a else best_evaluator
    eval_b = best_evaluator if cand_is_a else candidate_evaluator

    opp_a = NetworkPolicyAgent(evaluator=eval_a, greedy=True)
    opp_b = NetworkPolicyAgent(evaluator=eval_b, greedy=True)
    agent_a = MCTSAgent(
        "A", p1, factory, opp_a, num_simulations,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_a,
    )
    agent_b = MCTSAgent(
        "B", p2, factory, opp_b, num_simulations,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_b,
    )

    turn = 0
    while not battle.is_finished and turn < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turn += 1

    outcome_a, _ = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin,
    )
    return eval_score_for_candidate(outcome_a, cand_is_a)


def evaluate_parallel(
    candidate: BattleNet,
    best: BattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    n_games: int,
    num_workers: int,
    device: str,
    inference_batch_size: int = 128,
    inference_timeout_ms: float = 5.0,
    num_simulations: int = 100,
    max_turns: int = DEFAULT_EVAL_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    verbose: bool = True,
    progress_every: int = 1,
    stall_timeout_s: float = 600.0,
) -> float:
    """多进程局级门控评估 + 主进程双模型批量推理。

    work-stealing + 卡死保护（见 collect_rl_samples_parallel）。若发生卡死，
    胜率按已完成的对局数计算，避免单局死循环冻结整轮门控。
    """
    if n_games <= 0:
        return 0.0

    n_workers = max(1, min(num_workers, n_games))

    if verbose:
        print(
            f"  并行门控评估: {n_workers} workers 动态领取 {n_games} 局, "
            f"batch={inference_batch_size}, timeout={inference_timeout_ms}ms",
            flush=True,
        )

    ctx = mp.get_context("spawn")
    request_queue = ctx.Queue()
    result_queue = ctx.Queue()
    # 任务队列：n_games 个 game_index（决定先后手交替）+ 每 worker 一个停止哨兵。
    task_queue = ctx.Queue()
    for game_index in range(n_games):
        task_queue.put(game_index)
    for _ in range(n_workers):
        task_queue.put(None)

    candidate.eval()
    best.eval()
    reply_queues: dict[int, object] = {}
    processes: list[mp.Process] = []
    for wid in range(n_workers):
        reply_q = ctx.Queue()
        reply_queues[wid] = reply_q
        seed = random.randint(0, 2**31 - 1)
        proc = ctx.Process(
            target=run_evaluate_worker,
            args=(
                wid, seed,
                num_simulations, max_turns, draw_margin, progress_every,
                task_queue, request_queue, reply_q, result_queue,
            ),
        )
        proc.start()
        processes.append(proc)

    server = BatchedModelInferenceServer(
        {"candidate": candidate, "best": best},
        device, request_queue, reply_queues,
        batch_size=inference_batch_size,
        timeout_ms=inference_timeout_ms,
    )
    server.start()

    wins = 0.0
    completed_games = 0
    finished: set[int] = set()
    done_workers = 0
    last_wait_report = time.monotonic()
    last_progress = time.monotonic()
    stalled = False
    try:
        while done_workers < n_workers:
            try:
                tag, wid, score, error = result_queue.get(timeout=10.0)
            except queue.Empty:
                for pid_idx, proc in enumerate(processes):
                    if (
                        pid_idx not in finished
                        and proc.exitcode is not None
                        and proc.exitcode != 0
                    ):
                        raise RuntimeError(
                            f"eval worker {pid_idx} 提前退出 "
                            f"(pid={proc.pid}, exitcode={proc.exitcode})"
                        )
                now = time.monotonic()
                if now - last_progress >= stall_timeout_s:
                    alive = [
                        wid for wid in range(n_workers)
                        if wid not in finished
                    ]
                    print(
                        f"  ⚠ 卡死保护：{stall_timeout_s:.0f}s 内无任何对局完成，"
                        f"判定 eval worker {alive} 单局死循环，终止剩余 worker，"
                        f"用已完成的 {completed_games}/{n_games} 局计算胜率",
                        flush=True,
                    )
                    stalled = True
                    break
                if verbose and now - last_wait_report >= 60.0:
                    print(
                        f"  评估进度: {completed_games}/{n_games} 局完成, "
                        f"{done_workers}/{n_workers} workers 退出",
                        flush=True,
                    )
                    last_wait_report = now
                continue

            if tag == "error":
                raise RuntimeError(f"eval worker {wid} 异常:\n{error}")
            last_progress = time.monotonic()
            if tag == "done":
                finished.add(wid)
                done_workers += 1
                continue
            # tag == "game"
            wins += float(score)
            completed_games += 1
            if verbose and (completed_games % max(1, progress_every) == 0):
                print(
                    f"  评估进度: {completed_games}/{n_games} 局, "
                    f"当前胜率 {wins / max(1, completed_games):.2%}",
                    flush=True,
                )

        if stalled:
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=10.0)
        for proc in processes:
            proc.join(timeout=3600)
            if proc.is_alive():
                proc.terminate()
                proc.join()
                raise RuntimeError(f"eval worker {proc.pid} 超时未结束")
    except Exception:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10.0)
        raise
    finally:
        server.stop()

    denom = completed_games if stalled else n_games
    return wins / denom if denom > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AlphaZero 风格 RL 训练")
    parser.add_argument("--mode", type=str, default="rl",
                        choices=["rl", "supervised"],
                        help="训练模式: rl (自我博弈强化) / supervised (RuleAgent监督)")
    parser.add_argument("--battles", type=int, default=200,
                        help="每轮迭代自我博弈局数 (default: 200)")
    parser.add_argument("--sims", type=int, default=200,
                        help="MCTS 每步模拟次数 (default: 200)")
    parser.add_argument("--iterations", type=int, default=5,
                        help="RL 迭代轮数 (default: 5)")
    parser.add_argument("--epochs", type=int, default=20,
                        help="每轮训练 epoch 数 (default: 20)")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=str, default="256,128")
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--output", type=str, default="checkpoints/model_rl.pt")
    parser.add_argument("--resume", type=str, default="")
    parser.add_argument("--base-model", type=str,
                        default="checkpoints/model_rl_best.pt",
                        help="基座模型路径（无 --resume 时从此权重加载而非随机初始化）")
    parser.add_argument("--device", type=str, default="")
    parser.add_argument("--data-cache", type=str, default="",
                        help="监督模式: 预生成数据缓存路径")
    parser.add_argument("--temperature", type=float, default=1.0,
                        help="自我博弈动作采样温度 (default: 1.0)")
    parser.add_argument("--weight-decay", type=float, default=1e-4,
                        help="L2 正则系数 (default: 1e-4)")
    parser.add_argument("--buffer", type=int, default=5,
                        help="经验回放缓冲：保留最近 N 轮自我博弈数据混合训练 (default: 5)")
    parser.add_argument("--eval-games", type=int, default=20,
                        help="每轮门控评估对局数，0=关闭门控 (default: 20)")
    parser.add_argument("--eval-sims", type=int, default=100,
                        help="门控评估时每步 MCTS 模拟次数 (default: 100)")
    parser.add_argument("--eval-workers", type=int, default=1,
                        help="门控评估并行 worker 数 (default: 1=串行)")
    parser.add_argument("--gate", type=float, default=0.55,
                        help="晋升阈值：候选胜率≥该值才替换最优模型 (default: 0.55)")
    parser.add_argument("--root-noise", type=float, default=0.25,
                        help="自我博弈根节点 Dirichlet 噪声强度 (default: 0.25)")
    parser.add_argument("--workers", type=int, default=1,
                        help="自我博弈并行 worker 数 (default: 1=串行)")
    parser.add_argument("--batched-inference", action="store_true",
                        help="多 worker 时由主进程 CUDA 批量推理（需 workers>1）")
    parser.add_argument("--inference-batch-size", type=int, default=128,
                        help="批量推理最大 batch (default: 128)")
    parser.add_argument("--inference-timeout-ms", type=float, default=5.0,
                        help="攒 batch 等待毫秒 (default: 5)")
    parser.add_argument("--progress-every", type=int, default=10,
                        help="自我博弈每 N 局打印进度 (default: 10)")
    parser.add_argument("--worker-stall-timeout", type=float, default=600.0,
                        help="并行采样/评估时，若全部 worker 在 N 秒内都无任何对局完成，"
                             "判定为卡死并终止剩余 worker，用已完成的对局继续 (default: 600)")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_SELFPLAY_MAX_TURNS,
                        help=f"自我博弈单局回合上限 (default: {DEFAULT_SELFPLAY_MAX_TURNS})")
    parser.add_argument("--eval-max-turns", type=int, default=DEFAULT_EVAL_MAX_TURNS,
                        help=f"门控评估单局回合上限 (default: {DEFAULT_EVAL_MAX_TURNS})")
    parser.add_argument("--draw-margin", type=float, default=DEFAULT_DRAW_MARGIN,
                        help=f"打满回合时局面分差低于此值记平局 (default: {DEFAULT_DRAW_MARGIN})")
    parser.add_argument("--log-dir", type=str, default="backend/engine/ai/log",
                        help="训练日志目录，自动写全量日志+结构化指标+汇总 (default: backend/engine/ai/log)")
    parser.add_argument("--no-log", action="store_true",
                        help="关闭自动日志记录")
    parser.add_argument("--run-name", type=str, default="",
                        help="实验批次名称，模型和日志自动存入对应子目录 (如 --run-name resnet_v1)")
    parser.add_argument("--model-type", type=str, default="battle",
                        choices=["battle", "modular"],
                        help="网络架构: battle (BattleNet) / modular (ModularBattleNet)")
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    logger = None
    battle_log = None
    run_name = args.run_name.strip() if args.run_name else ""
    if run_name:
        print(f"实验批次: {run_name}")

    # 日志/模型子目录
    log_dir = args.log_dir
    checkpoints_dir = "checkpoints"
    if run_name:
        log_dir = f"{args.log_dir}/{run_name}"
        checkpoints_dir = f"checkpoints/{run_name}"

    if not args.no_log:
        from backend.engine.ai.run_logger import RunLogger
        log_params = {
            "mode": args.mode, "device": device,
            "iterations": args.iterations, "battles": args.battles,
            "sims": args.sims, "epochs": args.epochs,
            "batch_size": args.batch_size, "lr": args.lr,
            "hidden": args.hidden, "buffer": args.buffer,
            "eval_games": args.eval_games, "eval_sims": args.eval_sims,
            "gate": args.gate, "max_turns": args.max_turns,
            "eval_max_turns": args.eval_max_turns, "draw_margin": args.draw_margin,
            "workers": args.workers, "weight_decay": args.weight_decay,
            "dropout": args.dropout,
            "run_name": run_name,
        }
        logger = RunLogger(log_dir, params=log_params)
        logger.install_stdout_tee()

        # 创建对局技能日志写入器
        battle_log = BattleLogWriter(log_dir, run_id=logger.run_id)

    print(f"设备: {device}  模式: {args.mode}")

    print("加载精灵技能数据...")
    sprite_skills = _load_sprite_skills()
    print(f"  可用精灵: {len(sprite_skills)}")

    factory = SimFactory()
    hidden = tuple(int(h) for h in args.hidden.split(","))

    # ── 监督学习模式 ──
    if args.mode == "supervised":
        from backend.engine.ai.model import BattleValueNet

        if args.data_cache and Path(args.data_cache).exists():
            print(f"从缓存加载: {args.data_cache}")
            data = np.load(args.data_cache)
            X, y = data["X"], data["y"]
        else:
            print(f"RuleAgent 自我博弈收集数据 ({args.battles} 局)...")
            t0 = time.time()
            X, y = collect_battle_samples(factory, sprite_skills, args.battles)
            print(f"  完成: {len(X)} 样本, {time.time() - t0:.1f}s")
            if args.data_cache:
                np.savez_compressed(args.data_cache, X=X, y=y)

        pos, neg, zero = (y > 0).sum(), (y < 0).sum(), (y == 0).sum()
        print(f"  标签分布: +1={pos}  -1={neg}  0={zero}")

        if args.resume:
            model = BattleValueNet.load(args.resume, device=device)
        else:
            model = BattleValueNet(hidden=hidden, dropout=args.dropout)
        model.to(device)
        print(f"模型参数量: {model.num_params:,}")

        t0 = time.time()
        history = train_supervised(model, X, y, args.epochs, args.batch_size, args.lr, device)
        print(f"训练完成: {time.time() - t0:.1f}s")

        Xt = torch.from_numpy(X).to(device)
        yt = torch.from_numpy(y).unsqueeze(1).to(device)
        with torch.no_grad():
            pred = model(Xt)
            acc = (pred.squeeze().sign() == yt.squeeze().sign()).float().mean().item()
        print(f"最终准确率: {acc:.3f}")

        model.save(f"{checkpoints_dir}/model_rl.pt")
        print(f"模型已保存: {checkpoints_dir}/model_rl.pt")
        if logger is not None:
            logger.finalize()
        return

    # ── RL 模式 ──
    from collections import deque

    use_modular = args.model_type == "modular"
    if use_modular:
        from backend.engine.ai.model import ModularBattleNet

    if args.resume:
        print(f"加载模型: {args.resume}")
        if use_modular:
            model = ModularBattleNet.load(args.resume, device=device)
        else:
            model = BattleNet.load(args.resume, device=device)
    elif args.base_model and Path(args.base_model).exists():
        print(f"加载基座模型: {args.base_model}")
        if use_modular:
            model = ModularBattleNet.load(args.base_model, device=device)
        else:
            model = BattleNet.load(args.base_model, device=device)
    else:
        if args.base_model:
            print(f"基座模型未找到: {args.base_model}，使用随机初始化")
        if use_modular:
            model = ModularBattleNet(
                trunk_dim=hidden[0] if hidden else 256,
                num_blocks=4,
                dropout=args.dropout,
                with_attention=True,
            )
        else:
            model = BattleNet(hidden=hidden, dropout=args.dropout)
    model.to(device)
    print(f"模型类型: {'ModularBattleNet (模块化+残差+注意力)' if use_modular else 'BattleNet (MLP)'}")
    print(f"模型参数量: {model.num_params:,}")

    # 最优模型副本（门控基准）
    best_model = _clone_model(model, device)
    Path(checkpoints_dir).mkdir(parents=True, exist_ok=True)
    best_ckpt = f"{checkpoints_dir}/model_rl_best.pt"

    # 经验回放缓冲：保留最近 N 轮 (X, P, v)
    buffer: deque[tuple[np.ndarray, np.ndarray, np.ndarray]] = deque(maxlen=max(1, args.buffer))
    best_iteration: int | None = None

    for iteration in range(1, args.iterations + 1):
        iteration_started = time.time()
        print(f"\n{'=' * 60}")
        print(f"RL 迭代 {iteration}/{args.iterations}")
        print(f"{'=' * 60}")

        # ── 自我博弈收集数据（用当前最优模型产生对局，质量更稳） ──
        temp = args.temperature * (0.9 ** (iteration - 1))  # 温度递减
        use_parallel = args.batched_inference and args.workers > 1
        mode = f"{args.workers} workers + 批量推理" if use_parallel else "串行"
        print(f"自我博弈 ({args.battles} 局, {args.sims} sims, T={temp:.2f}, {mode})...")
        t0 = time.time()
        if use_parallel:
            X, P, v, reason_counts = collect_rl_samples_parallel(
                best_model, factory, sprite_skills,
                num_battles=args.battles,
                num_workers=args.workers,
                device=device,
                inference_batch_size=args.inference_batch_size,
                inference_timeout_ms=args.inference_timeout_ms,
                num_simulations=args.sims,
                max_turns=args.max_turns,
                draw_margin=args.draw_margin,
                temperature=temp,
                root_noise=args.root_noise,
                progress_every=args.progress_every,
                stall_timeout_s=args.worker_stall_timeout,
                battle_log_writer=battle_log,
            )
        else:
            X, P, v, reason_counts = collect_rl_samples(
                best_model, factory, sprite_skills,
                num_battles=args.battles,
                num_simulations=args.sims,
                device=device,
                max_turns=args.max_turns,
                draw_margin=args.draw_margin,
                temperature=temp,
                root_noise=args.root_noise,
                progress_every=args.progress_every,
                battle_log_writer=battle_log,
            )
        selfplay_sec = time.time() - t0
        rate = len(X) / selfplay_sec if selfplay_sec > 0 else 0.0
        print(f"  完成: {len(X)} 样本, {selfplay_sec:.1f}s ({rate:.0f} 样本/s)")

        pos, neg, zero = int((v > 0).sum()), int((v < 0).sum()), int((v == 0).sum())
        print(f"  结果分布: 本方赢={pos}  本方输={neg}  平={zero}")
        print(f"  终局原因(局): {format_reason_counts(reason_counts)}")

        if len(X) == 0:
            print("  本轮无样本，跳过。")
            if logger is not None:
                logger.record_iteration({
                    "iteration": iteration, "samples": 0, "skipped": True,
                })
            continue

        total_games = sum(reason_counts.values())
        decisive_games = sum(
            n for k, n in reason_counts.items() if not k.endswith("_draw")
        )
        draw_ratio = (zero / len(v)) if len(v) else 0.0

        # ── 合并回放缓冲 ──
        buffer.append((X, P, v))
        X_all = np.concatenate([b[0] for b in buffer])
        P_all = np.concatenate([b[1] for b in buffer])
        v_all = np.concatenate([b[2] for b in buffer])
        print(f"  回放缓冲: {len(buffer)} 轮 / {len(X_all)} 样本")

        # ── 训练（候选 = 在 best 基础上继续训练） ──
        print(f"训练 ({args.epochs} epochs)...")
        t0 = time.time()
        history = train_rl(
            model, X_all, P_all, v_all, args.epochs, args.batch_size, args.lr,
            device, weight_decay=args.weight_decay,
        )
        train_sec = time.time() - t0
        print(f"  完成: {train_sec:.1f}s")
        final_metrics = history[-1] if history else {}
        best_val_acc = max((h["val_acc"] for h in history), default=0.0)

        # ── 门控评估：候选 vs 最优 ──
        win_rate = None
        promoted = False
        eval_sec = 0.0
        checkpoint_sec = 0.0
        if args.eval_games > 0:
            eval_mode = f"{args.eval_workers} workers + 批量推理" if args.eval_workers > 1 else "串行"
            print(
                f"门控评估（候选 vs 最优, {args.eval_games} 局, "
                f"{args.eval_sims} sims, {eval_mode}）..."
            )
            t0 = time.time()
            if args.eval_workers > 1:
                win_rate = evaluate_parallel(
                    model, best_model, factory, sprite_skills,
                    n_games=args.eval_games,
                    num_workers=args.eval_workers,
                    device=device,
                    inference_batch_size=args.inference_batch_size,
                    inference_timeout_ms=args.inference_timeout_ms,
                    num_simulations=args.eval_sims,
                    max_turns=args.eval_max_turns,
                    draw_margin=args.draw_margin,
                    progress_every=args.progress_every,
                    stall_timeout_s=args.worker_stall_timeout,
                )
            else:
                win_rate = evaluate(
                    model, best_model, factory, sprite_skills,
                    n_games=args.eval_games, num_simulations=args.eval_sims, device=device,
                    max_turns=args.eval_max_turns,
                    draw_margin=args.draw_margin,
                )
            eval_sec = time.time() - t0
            print(f"  候选胜率: {win_rate:.2%}  ({eval_sec:.1f}s)")
            if win_rate >= args.gate:
                best_model.load_state_dict(model.state_dict())
                t_save = time.time()
                best_model.save(best_ckpt)
                checkpoint_sec += time.time() - t_save
                promoted = True
                best_iteration = iteration
                print(f"  ✓ 候选晋升为新最优，已保存: {best_ckpt}")
            else:
                # 回滚：候选退回到当前最优，避免越练越差
                model.load_state_dict(best_model.state_dict())
                print("  ✗ 未达门控阈值，回滚到最优模型")
        else:
            # 未启用门控：直接把候选当作最优（用于产生下一轮自我博弈）
            best_model.load_state_dict(model.state_dict())
            best_iteration = iteration

        # ── 保存检查点 ──
        ckpt = f"{checkpoints_dir}/model_rl_iter{iteration}.pt"
        t_save = time.time()
        model.save(ckpt)
        checkpoint_sec += time.time() - t_save
        print(f"  检查点已保存: {ckpt}")

        if logger is not None:
            iteration_sec = time.time() - iteration_started
            accounted_sec = selfplay_sec + train_sec + eval_sec + checkpoint_sec
            other_sec = max(0.0, iteration_sec - accounted_sec)
            phase_seconds = {
                "selfplay": round(selfplay_sec, 1),
                "train": round(train_sec, 1),
                "eval": round(eval_sec, 1),
                "checkpoint": round(checkpoint_sec, 1),
                "other": round(other_sec, 1),
            }
            phase_percent = {
                k: round((v / iteration_sec) * 100, 1) if iteration_sec > 0 else 0.0
                for k, v in phase_seconds.items()
            }
            logger.record_iteration({
                "iteration": iteration,
                "samples": int(len(X)),
                "win": pos, "loss": neg, "draw": zero,
                "draw_ratio": round(draw_ratio, 4),
                "total_games": total_games,
                "decisive_games": decisive_games,
                "reason_counts": reason_counts,
                "buffer_rounds": len(buffer),
                "buffer_samples": int(len(X_all)),
                "selfplay_sec": round(selfplay_sec, 1),
                "samples_per_sec": round(rate, 1),
                "train_sec": round(train_sec, 1),
                "eval_sec": round(eval_sec, 1),
                "checkpoint_sec": round(checkpoint_sec, 1),
                "other_sec": round(other_sec, 1),
                "iteration_sec": round(iteration_sec, 1),
                "phase_seconds": phase_seconds,
                "phase_percent": phase_percent,
                "epochs": args.epochs,
                "final_train_v_loss": round(final_metrics.get("train_v_loss", 0.0), 4),
                "final_val_v_loss": round(final_metrics.get("val_v_loss", 0.0), 4),
                "final_train_p_loss": round(final_metrics.get("train_p_loss", 0.0), 4),
                "final_val_p_loss": round(final_metrics.get("val_p_loss", 0.0), 4),
                "final_val_acc": round(final_metrics.get("val_acc", 0.0), 4),
                "best_val_acc": round(best_val_acc, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "gate": args.gate,
                "promoted": promoted,
            })

    best_model.save(f"{checkpoints_dir}/model_rl.pt")
    print(f"\n最终模型（最优）: {checkpoints_dir}/model_rl.pt")

    if logger is not None:
        logger.finalize(best_iteration=best_iteration)
    if battle_log is not None:
        battle_log.close()
        print(f"  对局技能日志: {battle_log.path}")


if __name__ == "__main__":
    main()
