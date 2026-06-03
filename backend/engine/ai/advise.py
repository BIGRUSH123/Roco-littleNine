"""backend/engine/ai/advise.py — 实战 PVP 单回合建议（部署侧）

训练用完全信息自我博弈（学得快），但实战中对手信息不完整
（板凳精灵 / 技能配置 / 个体值未知）。这里用 **PIMC**
(Perfect Information Monte Carlo，完美信息蒙特卡洛) 处理：

    1. 决定化(determinize)：采样 K 套对手可能的隐藏配置，
       把每套补全成一个"完全信息"局面副本。
    2. 对每个副本跑一次 MCTS（关闭探索噪声、贪心倾向）。
    3. 把 K 次的根节点访问分布求平均 → 推荐访问最多的动作。

与训练的关键区别：
    - root_noise=0（要利用不要探索）
    - 多次决定化求平均（marginalize 掉未知信息）

用法（完全信息，例如复盘）::

    from backend.engine.ai.advise import advise_single
    adv = advise_single(battle, model, factory)
    print(adv.summary())

用法（实战，对手板凳未知）::

    from backend.engine.ai.advise import advise, make_determinizations
    dets = make_determinizations(battle, factory, bench_pool, k=20)
    adv = advise(dets, model, factory)
    print(adv.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import torch

from backend.engine.ai.encode import encode_battle_state
from backend.engine.ai.mcts import (
    NUM_ACTIONS,
    NetworkPolicyAgent,
    get_valid_actions,
    mcts_search,
)
from backend.engine.serializer import battle_from_dict, battle_to_dict

if TYPE_CHECKING:
    from backend.engine.ai.model import BattleNet
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.sim.player import Player


# ═══════════════════════════════════════════════════════════════════
# 建议结果
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Advice:
    """单回合建议结果。"""

    probs: np.ndarray                       # (11,) 平均访问分布
    ranked: list[tuple[int, str, float]]    # [(动作索引, 文字描述, 概率)]，降序
    win_prob: float                         # value 头估计的本方胜率 [0,1]
    num_determinizations: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def best_action(self) -> int | None:
        return self.ranked[0][0] if self.ranked else None

    def summary(self, top_k: int = 3) -> str:
        lines = [f"估计胜率: {self.win_prob:.1%}  (决定化×{self.num_determinizations})"]
        for rank, (idx, label, prob) in enumerate(self.ranked[:top_k], 1):
            lines.append(f"  {rank}. {label}  ({prob:.0%})")
        for note in self.notes:
            lines.append(f"  · {note}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 动作描述
# ═══════════════════════════════════════════════════════════════════

def describe_action(player: "Player", idx: int) -> str:
    """把动作索引转成人类可读描述（己方视角）。"""
    active = player.active if player.active_index < len(player.team) else None
    if idx < 4:
        skills = (active.skills if active else None) or []
        if idx < len(skills):
            sk = skills[idx]
            return f"技能: {sk.name}（耗能{sk.energy_cost}/威力{sk.power}）"
        return f"技能槽{idx}（空）"
    if idx < 9:
        bench_slot = idx - 4
        target = _bench_target(player, bench_slot)
        if target is not None:
            return f"换宠 → {target.name}（HP {target.current_hp}/{target.max_hp}）"
        return f"换宠槽{bench_slot}（空）"
    if idx == 9:
        item = getattr(player, "item", None)
        return f"使用道具: {item.name}" if item is not None else "使用道具（无）"
    return f"动作{idx}"


def _bench_target(player: "Player", bench_slot: int):
    count = 0
    for i, s in enumerate(player.team):
        if i == player.active_index or s.is_fainted:
            continue
        if count == bench_slot:
            return s
        count += 1
    return None


# ═══════════════════════════════════════════════════════════════════
# 核心：对单个（完全信息）局面给建议
# ═══════════════════════════════════════════════════════════════════

def advise_single(
    battle: "Battle",
    model: "BattleNet",
    factory: "SimFactory",
    opponent_agent=None,
    num_simulations: int = 400,
    device: str = "cpu",
) -> Advice:
    """对一个完全信息局面跑一次 MCTS，返回建议（己方 = player_a）。

    opponent_agent 默认用当前网络的策略头（NetworkPolicyAgent）。
    """
    if opponent_agent is None:
        opponent_agent = NetworkPolicyAgent(model, device=device, greedy=True)

    probs = mcts_search(
        battle, model, factory, opponent_agent,
        num_simulations=num_simulations, device=device,
        root_noise=0.0,  # 实战：纯利用，不加探索噪声
    )
    win_prob = _estimate_win_prob(battle, model, device)
    return _build_advice(battle.player_a, probs, win_prob, num_determinizations=1)


# ═══════════════════════════════════════════════════════════════════
# PIMC：对多套决定化求平均
# ═══════════════════════════════════════════════════════════════════

def advise(
    determinizations: list["Battle"],
    model: "BattleNet",
    factory: "SimFactory",
    opponent_agent=None,
    num_simulations: int = 200,
    device: str = "cpu",
    weights: list[float] | None = None,
) -> Advice:
    """对多套决定化局面分别跑 MCTS，加权平均访问分布。

    determinizations: 已补全对手隐藏信息的局面副本列表（己方 = player_a）。
    weights: 各决定化的先验权重（如来自对手档案的出场概率）；None = 等权。
    """
    if not determinizations:
        raise ValueError("determinizations 不能为空")

    if weights is None:
        weights = [1.0] * len(determinizations)
    if len(weights) != len(determinizations):
        raise ValueError("weights 长度需与 determinizations 一致")

    acc = np.zeros(NUM_ACTIONS, dtype=np.float64)
    win_acc = 0.0
    w_sum = 0.0
    for bt, w in zip(determinizations, weights):
        if w <= 0:
            continue
        opp = opponent_agent or NetworkPolicyAgent(model, device=device, greedy=True)
        probs = mcts_search(
            bt, model, factory, opp,
            num_simulations=num_simulations, device=device, root_noise=0.0,
        )
        acc += w * probs.astype(np.float64)
        win_acc += w * _estimate_win_prob(bt, model, device)
        w_sum += w

    if w_sum <= 0:
        raise ValueError("所有决定化权重为 0")

    avg_probs = (acc / w_sum).astype(np.float32)
    win_prob = win_acc / w_sum
    # 用第一个决定化的己方 player 做动作描述（己方信息一致）
    return _build_advice(
        determinizations[0].player_a, avg_probs, win_prob,
        num_determinizations=len([w for w in weights if w > 0]),
    )


def make_determinizations(
    battle: "Battle",
    factory: "SimFactory",
    bench_pool: list[dict] | None = None,
    k: int = 20,
    rng: np.random.Generator | None = None,
) -> list["Battle"]:
    """从当前局面生成 K 套决定化副本（重采样对手未知板凳）。

    bench_pool: 对手可能的板凳精灵规格列表，元素形如
        {"name": "...", "skills": [...], "nature": ..., "iv": {...}}
        （建议由"对手档案"先验提供）。为空时返回 K 份当前局面的克隆
        （退化为对当前可见信息的单一 MCTS，仍可用）。
    rng: 随机源，便于测试复现。

    注意：保留对手「当前出场精灵」的真实状态，只替换其板凳身份——
    因为出场精灵通常已暴露，而板凳才是未知信息。
    """
    if rng is None:
        rng = np.random.default_rng()

    snapshot = battle_to_dict(battle)
    out: list["Battle"] = []
    for _ in range(max(1, k)):
        clone = battle_from_dict(snapshot, factory.sprite_db, factory._build_skill_list)
        if bench_pool:
            try:
                _resample_opponent_bench(clone, factory, bench_pool, rng)
            except Exception:  # noqa: BLE001 — 决定化失败则退回纯克隆
                pass
        out.append(clone)
    return out


def _resample_opponent_bench(
    battle: "Battle",
    factory: "SimFactory",
    bench_pool: list[dict],
    rng: np.random.Generator,
) -> None:
    """把对手(player_b)的板凳替换为从 bench_pool 随机采样的精灵。"""
    opp = battle.player_b
    active = opp.active if opp.active_index < len(opp.team) else None
    n_bench = max(0, len(opp.team) - 1)
    if n_bench == 0 or not bench_pool:
        return

    picks = rng.choice(len(bench_pool), size=min(n_bench, len(bench_pool)), replace=False)
    new_bench = []
    for j in picks:
        spec = bench_pool[int(j)]
        sprite = factory.build_sprite(
            name=spec["name"],
            skills=spec.get("skills", []),
            nature=spec.get("nature"),
            iv=spec.get("iv"),
            form=spec.get("form", ""),
            bloodline=spec.get("bloodline"),
        )
        new_bench.append(sprite)

    new_team = ([active] if active is not None else []) + new_bench
    opp.team = new_team
    # 若 active 为 None，新队伍长度 ≠ 原长度，旧 active_index 可能越界
    if active is not None:
        opp.active_index = 0
    else:
        # 找第一个存活精灵作为 active，否则默认 0
        opp.active_index = 0
        for i, s in enumerate(new_team):
            if not s.is_fainted:
                opp.active_index = i
                break


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _estimate_win_prob(battle: "Battle", model: "BattleNet", device: str) -> float:
    """用 value 头估计己方(player_a)胜率，映射到 [0,1]。"""
    state = encode_battle_state(battle)
    tensor = torch.from_numpy(state).unsqueeze(0).to(device)
    with torch.no_grad():
        value, _ = model(tensor)
    v = float(value.item())  # [-1, 1]
    return (v + 1.0) / 2.0


def _build_advice(
    player: "Player",
    probs: np.ndarray,
    win_prob: float,
    num_determinizations: int,
) -> Advice:
    valid, _ = get_valid_actions(player)
    ranked: list[tuple[int, str, float]] = []
    for idx in range(NUM_ACTIONS):
        p = float(probs[idx])
        if p <= 0:
            continue
        ranked.append((idx, describe_action(player, idx), p))
    ranked.sort(key=lambda t: t[2], reverse=True)

    notes: list[str] = []
    if not valid:
        notes.append("当前无合法动作（可能需强制换宠/聚能）")
    return Advice(
        probs=probs,
        ranked=ranked,
        win_prob=win_prob,
        num_determinizations=num_determinizations,
        notes=notes,
    )
