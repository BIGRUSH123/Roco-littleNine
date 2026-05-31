"""backend/engine/train.py — 自我博弈训练对战状态评估网络

用法:
    python -m backend.engine.train                    # 默认参数训练
    python -m backend.engine.train --battles 2000     # 2000局自我博弈
    python -m backend.engine.train --epochs 50        # 50轮训练
    python -m backend.engine.train --resume model.pt  # 继续训练
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.encode import encode_battle_state
from backend.engine.model import BattleValueNet
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory

# ═══════════════════════════════════════════════════════════════════
# 数据生成
# ═══════════════════════════════════════════════════════════════════

def _load_sprite_skills() -> dict[str, list[str]]:
    """加载每只精灵的可选技能名列表（仅保留磁盘上存在的技能）。"""
    sprites_dir = PROJECT_ROOT / "data" / "sprites"
    skills_dir = PROJECT_ROOT / "data" / "skills"

    # 磁盘上存在的技能文件名集合
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
    """随机生成两支队伍。"""
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


def collect_battle_samples(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    max_turns: int = 200,
    verbose: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """运行多局自我博弈，收集 (states, labels)。

    Returns:
        X: (N, 446) float32 状态向量
        y: (N,) float32 标签 (+1=player_a赢, -1=player_b赢)
    """
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

        # 标签：玩家A赢=+1, 玩家B赢=-1, 平局(超回合)=0
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
# 训练
# ═══════════════════════════════════════════════════════════════════

def train(
    model: BattleValueNet,
    X: np.ndarray,
    y: np.ndarray,
    epochs: int,
    batch_size: int,
    lr: float,
    device: str,
    val_split: float = 0.1,
) -> list[dict]:
    """训练模型，返回每 epoch 的 loss 记录。"""
    # 划分训练/验证集
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
        # ── 训练 ──
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

        # ── 验证 ──
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val)
            val_loss = criterion(val_pred, y_val).item()

        history.append({"epoch": epoch + 1, "train_loss": train_loss, "val_loss": val_loss})

        if (epoch + 1) % 5 == 0 or epoch == 0:
            # 准确率（符号匹配）
            train_acc = ((model(X_train).squeeze().sign() == y_train.squeeze().sign()).float().mean().item())
            val_acc = ((val_pred.squeeze().sign() == y_val.squeeze().sign()).float().mean().item())
            print(f"  Epoch {epoch + 1:3d}/{epochs}  "
                  f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                  f"train_acc={train_acc:.3f}  val_acc={val_acc:.3f}")

    return history


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="训练对战状态评估网络")
    parser.add_argument("--battles", type=int, default=500, help="自我博弈局数 (default: 500)")
    parser.add_argument("--epochs", type=int, default=30, help="训练轮数 (default: 30)")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=str, default="256,128", help="隐藏层维度")
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--output", type=str, default="model.pt", help="模型保存路径")
    parser.add_argument("--resume", type=str, default="", help="继续训练已有模型")
    parser.add_argument("--device", type=str, default="", help="cuda / cpu")
    parser.add_argument("--data-cache", type=str, default="", help="预生成数据缓存路径")
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

    # ── 加载精灵/技能数据 ──
    print("加载精灵技能数据...")
    sprite_skills = _load_sprite_skills()
    print(f"  可用精灵: {len(sprite_skills)}")

    factory = SimFactory()

    # ── 生成或加载数据 ──
    if args.data_cache and Path(args.data_cache).exists():
        print(f"从缓存加载数据: {args.data_cache}")
        data = np.load(args.data_cache)
        X, y = data["X"], data["y"]
        print(f"  样本: {len(X)}")
    else:
        print(f"自我博弈收集数据 ({args.battles} 局)...")
        t0 = time.time()
        X, y = collect_battle_samples(factory, sprite_skills, args.battles)
        elapsed = time.time() - t0
        print(f"  完成: {len(X)} 样本, {elapsed:.1f}s ({len(X) / elapsed:.0f} 样本/s)")

        if args.data_cache:
            np.savez_compressed(args.data_cache, X=X, y=y)
            print(f"  已缓存: {args.data_cache}")

    # ── 标签分布 ──
    pos = (y > 0).sum()
    neg = (y < 0).sum()
    zero = (y == 0).sum()
    print(f"  标签分布: +1={pos}  -1={neg}  0={zero}")

    # ── 构建模型 ──
    hidden = tuple(int(h) for h in args.hidden.split(","))
    if args.resume:
        print(f"加载模型: {args.resume}")
        model = BattleValueNet.load(args.resume, device=device)
    else:
        model = BattleValueNet(hidden=hidden, dropout=args.dropout)
    model.to(device)
    print(f"模型参数量: {model.num_params:,}")

    # ── 训练 ──
    print(f"开始训练 ({args.epochs} epochs)...")
    t0 = time.time()
    history = train(model, X, y, args.epochs, args.batch_size, args.lr, device)
    elapsed = time.time() - t0
    print(f"训练完成: {elapsed:.1f}s")

    # ── 最终评估 ──
    model.eval()
    Xt = torch.from_numpy(X).to(device)
    yt = torch.from_numpy(y).unsqueeze(1).to(device)
    with torch.no_grad():
        pred = model(Xt)
        acc = (pred.squeeze().sign() == yt.squeeze().sign()).float().mean().item()
    print(f"最终准确率 (符号匹配): {acc:.3f}")

    # ── 保存 ──
    model.save(args.output)
    print(f"模型已保存: {args.output}")


if __name__ == "__main__":
    main()
