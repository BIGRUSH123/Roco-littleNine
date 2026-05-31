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
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.ai.encode import encode_battle_state
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

    def build_team(label: str) -> list[dict]:
        size = random.randint(1, min(max_team_size, len(names) // 2))
        specs: list[dict] = []
        used: set[str] = set()
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
        model: BattleNet,
        factory: SimFactory,
        opponent_agent,
        num_simulations: int = 200,
        device: str = "cpu",
        temperature: float = 1.0,
        root_noise: float = 0.25,
        record: bool = False,
    ):
        self.team = team
        self.player = player
        self._model = model
        self._factory = factory
        self._opponent = opponent_agent
        self._num_simulations = num_simulations
        self._device = device
        self._temperature = temperature
        self._root_noise = root_noise
        self._record = record
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
            # 在（必要时已交换的）本方视角下编码并搜索，二者坐标系一致
            state = encode_battle_state(battle) if self._record else None
            probs = mcts_search(
                battle, self._model, self._factory, self._opponent,
                num_simulations=self._num_simulations, device=self._device,
                root_noise=self._root_noise,
            )
            if self._record and state is not None:
                self.history.append((state, probs.copy()))
        finally:
            if swapped:
                battle.player_a, battle.player_b = battle.player_b, battle.player_a

        action_idx = _sample_action(probs, self._temperature)
        player = battle.player_a if self.team == "A" else battle.player_b
        return action_index_to_action(player, action_idx)

    def choose_replacement(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        return alive[0] if alive else 0

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
    max_turns: int = 150,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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

    for i in range(num_battles):
        team_a, team_b = _random_teams(factory, sprite_skills)
        p1 = factory.build_player("A", team_a)
        p2 = factory.build_player("B", team_b)
        battle = factory.build_battle(p1, p2)

        # 搜索内的对手 = 当前网络策略头（槽位驱动，对 A/B 两侧搜索通用）
        opp_a = NetworkPolicyAgent(model, device=device, temperature=temperature)
        opp_b = NetworkPolicyAgent(model, device=device, temperature=temperature)

        agent_a = MCTSAgent(
            "A", p1, model, factory, opp_a, num_simulations, device,
            temperature, root_noise=root_noise, record=True,
        )
        agent_b = MCTSAgent(
            "B", p2, model, factory, opp_b, num_simulations, device,
            temperature, root_noise=root_noise, record=True,
        )

        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1

        if battle.winner == "A":
            outcome_a = 1.0
        elif battle.winner == "B":
            outcome_a = -1.0
        else:
            outcome_a = 0.0

        for state, probs in agent_a.history:
            all_states.append(state)
            all_probs.append(probs)
            all_outcomes.append(outcome_a)
        for state, probs in agent_b.history:
            all_states.append(state)
            all_probs.append(probs)
            all_outcomes.append(-outcome_a)

        if verbose and (i + 1) % 10 == 0:
            print(f"  RL 自我博弈 {i + 1}/{num_battles} 局, 样本 {len(all_states)}")

    if not all_states:
        return (
            np.zeros((0, 446), dtype=np.float32),
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
        )

    X = np.stack(all_states).astype(np.float32)
    P = np.stack(all_probs).astype(np.float32)
    v = np.array(all_outcomes, dtype=np.float32)
    return X, P, v


# ═══════════════════════════════════════════════════════════════════
# 监督学习数据收集 — RuleAgent 自我博弈（兼容旧模式）
# ═══════════════════════════════════════════════════════════════════

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
            policy_loss = -torch.sum(pb * F.log_softmax(logits, dim=-1), dim=-1).mean()
            loss = value_loss + policy_loss

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_value_loss += value_loss.item() * len(batch_idx)
            total_policy_loss += policy_loss.item() * len(batch_idx)

        train_v_loss = total_value_loss / len(X_train)
        train_p_loss = total_policy_loss / len(X_train)

        model.eval()
        with torch.no_grad():
            val_v, val_logits = model(X_val)
            val_v_loss = F.mse_loss(val_v, v_val).item()
            val_p_loss = -torch.sum(P_val * F.log_softmax(val_logits, dim=-1), dim=-1).mean().item()

        history.append({
            "epoch": epoch + 1,
            "train_v_loss": train_v_loss,
            "train_p_loss": train_p_loss,
            "val_v_loss": val_v_loss,
            "val_p_loss": val_p_loss,
        })

        if (epoch + 1) % 5 == 0 or epoch == 0:
            val_acc = ((val_v.squeeze().sign() == v_val.squeeze().sign()).float().mean().item())
            print(f"  Epoch {epoch + 1:3d}/{epochs}  "
                  f"v_loss={train_v_loss:.4f}/{val_v_loss:.4f}  "
                  f"p_loss={train_p_loss:.4f}/{val_p_loss:.4f}  "
                  f"val_acc={val_acc:.3f}")

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

def _clone_model(model: BattleNet, device: str) -> BattleNet:
    clone = BattleNet(hidden=model.hidden_dims)
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
    max_turns: int = 150,
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

        opp_a = NetworkPolicyAgent(model_a, device=device, greedy=True)
        opp_b = NetworkPolicyAgent(model_b, device=device, greedy=True)
        agent_a = MCTSAgent(
            "A", p1, model_a, factory, opp_a, num_simulations, device,
            temperature=0.0, root_noise=0.0, record=False,
        )
        agent_b = MCTSAgent(
            "B", p2, model_b, factory, opp_b, num_simulations, device,
            temperature=0.0, root_noise=0.0, record=False,
        )

        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1

        w = battle.winner
        if w is None:
            wins += 0.5
        elif (w == "A") == cand_is_a:
            wins += 1.0

        if verbose and (g + 1) % 10 == 0:
            print(f"    评估 {g + 1}/{n_games} 局, 当前胜率 {wins / (g + 1):.2%}")

    return wins / n_games


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
    parser.add_argument("--gate", type=float, default=0.55,
                        help="晋升阈值：候选胜率≥该值才替换最优模型 (default: 0.55)")
    parser.add_argument("--root-noise", type=float, default=0.25,
                        help="自我博弈根节点 Dirichlet 噪声强度 (default: 0.25)")
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
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

        model.save(args.output)
        print(f"模型已保存: {args.output}")
        return

    # ── RL 模式 ──
    from collections import deque

    if args.resume:
        print(f"加载模型: {args.resume}")
        model = BattleNet.load(args.resume, device=device)
    else:
        model = BattleNet(hidden=hidden, dropout=args.dropout)
    model.to(device)
    print(f"模型参数量: {model.num_params:,}")

    # 最优模型副本（门控基准）
    best_model = _clone_model(model, device)
    best_ckpt = args.output.replace(".pt", "_best.pt")

    # 经验回放缓冲：保留最近 N 轮 (X, P, v)
    buffer: deque[tuple[np.ndarray, np.ndarray, np.ndarray]] = deque(maxlen=max(1, args.buffer))

    for iteration in range(1, args.iterations + 1):
        print(f"\n{'=' * 60}")
        print(f"RL 迭代 {iteration}/{args.iterations}")
        print(f"{'=' * 60}")

        # ── 自我博弈收集数据（用当前最优模型产生对局，质量更稳） ──
        temp = args.temperature * (0.9 ** (iteration - 1))  # 温度递减
        print(f"自我博弈 ({args.battles} 局, {args.sims} sims, T={temp:.2f})...")
        t0 = time.time()
        X, P, v = collect_rl_samples(
            best_model, factory, sprite_skills,
            num_battles=args.battles,
            num_simulations=args.sims,
            device=device,
            temperature=temp,
            root_noise=args.root_noise,
        )
        elapsed = time.time() - t0
        rate = len(X) / elapsed if elapsed > 0 else 0.0
        print(f"  完成: {len(X)} 样本, {elapsed:.1f}s ({rate:.0f} 样本/s)")

        pos, neg, zero = (v > 0).sum(), (v < 0).sum(), (v == 0).sum()
        print(f"  结果分布: 本方赢={pos}  本方输={neg}  平={zero}")

        if len(X) == 0:
            print("  本轮无样本，跳过。")
            continue

        # ── 合并回放缓冲 ──
        buffer.append((X, P, v))
        X_all = np.concatenate([b[0] for b in buffer])
        P_all = np.concatenate([b[1] for b in buffer])
        v_all = np.concatenate([b[2] for b in buffer])
        print(f"  回放缓冲: {len(buffer)} 轮 / {len(X_all)} 样本")

        # ── 训练（候选 = 在 best 基础上继续训练） ──
        print(f"训练 ({args.epochs} epochs)...")
        t0 = time.time()
        train_rl(
            model, X_all, P_all, v_all, args.epochs, args.batch_size, args.lr,
            device, weight_decay=args.weight_decay,
        )
        print(f"  完成: {time.time() - t0:.1f}s")

        # ── 门控评估：候选 vs 最优 ──
        if args.eval_games > 0:
            print(f"门控评估（候选 vs 最优, {args.eval_games} 局, {args.eval_sims} sims）...")
            t0 = time.time()
            win_rate = evaluate(
                model, best_model, factory, sprite_skills,
                n_games=args.eval_games, num_simulations=args.eval_sims, device=device,
            )
            print(f"  候选胜率: {win_rate:.2%}  ({time.time() - t0:.1f}s)")
            if win_rate >= args.gate:
                best_model.load_state_dict(model.state_dict())
                best_model.save(best_ckpt)
                print(f"  ✓ 候选晋升为新最优，已保存: {best_ckpt}")
            else:
                # 回滚：候选退回到当前最优，避免越练越差
                model.load_state_dict(best_model.state_dict())
                print("  ✗ 未达门控阈值，回滚到最优模型")
        else:
            # 未启用门控：直接把候选当作最优（用于产生下一轮自我博弈）
            best_model.load_state_dict(model.state_dict())

        # ── 保存检查点 ──
        ckpt = args.output.replace(".pt", f"_iter{iteration}.pt")
        model.save(ckpt)
        print(f"  检查点已保存: {ckpt}")

    best_model.save(args.output)
    print(f"\n最终模型（最优）: {args.output}")


if __name__ == "__main__":
    main()
