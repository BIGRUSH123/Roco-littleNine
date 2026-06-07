"""对局终局结果解析：减少 max_turns 平局对 RL 价值头的噪声。

支持回合衰减（gamma）让速胜价值高于拖沓胜，助力 MCTS 搜索学到最优解。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.player import Player

# 归一化局面分差低于此阈值时记为真平局 (z=0)
DEFAULT_DRAW_MARGIN = 0.15

# self-play 默认回合上限（训练）；评估可单独设更高
DEFAULT_SELFPLAY_MAX_TURNS = 60
DEFAULT_EVAL_MAX_TURNS = 150

# 默认回合衰减因子（None = 不衰减，保持原有二值行为）
DEFAULT_GAMMA = 1.0

# tanh 软裁决缩放系数（0 = 硬阈值，>0 = 连续映射）
DEFAULT_TANH_K = 0.0


def team_battle_score(player: "Player") -> float:
    """综合存活数、队伍 HP 比例、魔力、在场能量，用于打满回合裁决。"""
    alive = len(player.alive_sprites)
    hp_cur = sum(s.current_hp for s in player.team)
    hp_max = sum(max(s.max_hp, 1) for s in player.team)
    hp_ratio = hp_cur / hp_max if hp_max > 0 else 0.0
    try:
        active_energy = player.active.energy / 10.0
    except (IndexError, AttributeError):
        active_energy = 0.0
    lives = max(0, player.lives)
    return alive * 1.0 + hp_ratio * 0.5 + lives * 0.25 + active_energy * 0.05


def battle_outcome_a(
    battle: "Battle",
    max_turns: int,
    *,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    gamma: float = DEFAULT_GAMMA,
    tanh_k: float = DEFAULT_TANH_K,
) -> tuple[float, str]:
    """返回 (outcome_a, end_reason)。outcome_a: +1=A 胜, -1=B 胜, 0=平。

    Args:
        battle: 对战对象。
        max_turns: 回合上限。
        draw_margin: 局面分差低于此值记平局（仅非决胜对局生效）。
        gamma: 回合衰减因子。gamma < 1 时，胜利价值随回合数指数衰减，
              迫使 MCTS 偏好速胜路径（gamma=1.0 = 不衰减，等价旧行为）。
        tanh_k: tanh 软裁决缩放系数。k > 0 时将非决胜对局的局面分差
                通过 tanh(k * margin) 连续映射到 (-1, 1)，替代硬阈值 ±1。
                k=0   → 硬阈值（旧行为）。
                k=1.8 → 分差 1.0 ≈ 0.95 reward。
    """
    if battle.winner == "A":
        raw = 1.0
        reason = "decisive_a"
    elif battle.winner == "B":
        raw = -1.0
        reason = "decisive_b"
    else:
        score_a = team_battle_score(battle.player_a)
        score_b = team_battle_score(battle.player_b)
        margin = score_a - score_b
        prefix = "max_turns" if battle.turn >= max_turns else "stalemate"

        if abs(margin) < draw_margin:
            return 0.0, f"{prefix}_draw"

        if tanh_k > 0:
            raw = math.tanh(tanh_k * margin)
        else:
            raw = 1.0 if margin > 0 else -1.0
        reason = f"{prefix}_a" if margin > 0 else f"{prefix}_b"
        # 非决胜对局同样适用回合衰减
        if gamma < 1.0:
            raw *= gamma ** battle.turn
        return raw, reason

    # 决胜对局：应用回合衰减
    if gamma < 1.0:
        raw *= gamma ** battle.turn
    return raw, reason


def merge_reason_counts(*parts: dict[str, int]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for part in parts:
        merged.update(part)
    return dict(merged)


def eval_score_for_candidate(outcome_a: float, cand_is_a: bool) -> float:
    """门控评估：根据 A 视角终局结果换算 candidate 得分（胜=1，平=0.5，负=0）。"""
    if cand_is_a:
        if outcome_a > 0:
            return 1.0
        if outcome_a < 0:
            return 0.0
        return 0.5
    if outcome_a < 0:
        return 1.0
    if outcome_a > 0:
        return 0.0
    return 0.5


def format_reason_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "(无)"
    keys = (
        "decisive_a", "decisive_b",
        "max_turns_a", "max_turns_b", "max_turns_draw",
        "stalemate_a", "stalemate_b", "stalemate_draw",
    )
    ordered = [f"{k}={counts[k]}" for k in keys if k in counts]
    extra = [f"{k}={v}" for k, v in sorted(counts.items()) if k not in keys]
    return "  ".join(ordered + extra)
