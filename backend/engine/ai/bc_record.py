"""backend/engine/ai/bc_record.py — 行为克隆数据录制。

用规则 agent（RuleAgentV2 及其策略配置）驱动对局，在每个决策点记录：
  - 编码状态（encode_battle_state，己方视角）
  - agent 实际选择的动作索引（22 维动作空间）
  - 合法动作掩码（get_valid_actions，与自博弈训练同源）
  - 终局胜负（battle_outcome_a，双方视角取反）

与 MCTS 自博弈样本的差异：policy 标签是专家 agent 的 one-hot 动作
（而非访问分布），value 标签仍是终局结果。录制层与精灵池/队伍选择
完全解耦——gen_bc_data.py 负责组队，这里只负责"打一局、记一局"。
"""
from __future__ import annotations

import copy
import random
import time

import numpy as np

from backend.common.constants import ITEM_VARIANT_ACTION_BASE, ITEM_VARIANT_SLOTS
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.mcts import (
    ITEM_ACTION_IDX,
    NUM_ACTIONS,
    _bench_to_team_index,
    _replacement_mask,
    get_valid_actions,
)
from backend.engine.ai.core.outcome import battle_outcome_a
from backend.sim.action import Action
from backend.sim.player import Item


def _random_item() -> Item:
    """随机道具：进化之力 或 愿力（等概率）——与 train._random_item 一致。"""
    return Item.leader() if random.random() < 0.5 else Item.wish()


def _team_index_to_action_idx(player, team_idx: int) -> int | None:
    """换宠目标 team 索引 → 动作索引 10-14（板凳槽位）；非法返回 None。"""
    if not 0 <= team_idx < len(player.team) or team_idx == player.active_index:
        return None
    slot = team_idx if team_idx < player.active_index else team_idx - 1
    return 10 + slot if 0 <= slot < 5 else None


def action_to_index(player, action: Action) -> int | None:
    """Action → 22 维动作索引；无法映射返回 None（该样本跳过录制）。"""
    kind = action.kind
    if kind == "skill":
        return action.skill_index if 0 <= action.skill_index < 10 else None
    if kind == "switch":
        return _team_index_to_action_idx(player, action.switch_index)
    if kind == "gather":
        return 15
    if kind == "item":
        variant = action.variant
        if variant is None:
            # 未指定形态：与引擎 _resolve_item 一致，取候选首位（动作 17）
            return ITEM_VARIANT_ACTION_BASE if _item_is_leader(player) else ITEM_ACTION_IDX
        if 0 <= variant < ITEM_VARIANT_SLOTS:
            return ITEM_VARIANT_ACTION_BASE + variant
        return None
    return None


def _item_is_leader(player) -> bool:
    item = getattr(player, "item", None)
    return item is not None and item.name == "进化之力"


class RecordingAgent:
    """包装规则 agent：决策照常执行，同时在合法掩码内录制 (状态, 动作, 掩码)。

    动作与掩码冲突时（energy_cost 修正等边界）跳过录制但仍执行原动作，
    避免给 BC 喂非法标签。
    """

    def __init__(self, inner, tag: str, sink: list, game_id: int):
        self.inner = inner
        self.team = tag
        self._tag = tag
        self._sink = sink
        self._game_id = game_id
        self.n_recorded = 0
        self.n_skipped = 0

    def _player(self, battle):
        return battle.player_a if self._tag == "A" else battle.player_b

    def _record(self, battle, action_idx: int, mask: np.ndarray) -> None:
        state = encode_battle_state(battle, perspective=self._tag)
        self._sink.append((state, action_idx, mask.copy(), self._game_id, self._tag))
        self.n_recorded += 1

    def choose_lead(self, battle) -> int:
        return self.inner.choose_lead(battle)

    def choose_action(self, battle) -> Action:
        player = self._player(battle)
        _, mask = get_valid_actions(player, battle)
        action = self.inner.choose_action(battle)
        idx = action_to_index(player, action)
        if idx is not None and 0 <= idx < NUM_ACTIONS and mask[idx] > 0:
            self._record(battle, idx, mask)
        else:
            self.n_skipped += 1
        return action

    def choose_replacement(self, battle) -> int:
        player = self._player(battle)
        _, mask = _replacement_mask(player)
        team_idx = self.inner.choose_replacement(battle)
        idx = _team_index_to_action_idx(player, team_idx) if team_idx is not None else None
        if idx is not None and idx < NUM_ACTIONS and mask[idx] > 0:
            self._record(battle, idx, mask)
        else:
            self.n_skipped += 1
        return team_idx

    def on_game_end(self, winner: str) -> None:
        self.inner.on_game_end(winner)


def run_recorded_battle(
    factory,
    specs_a: list[dict],
    specs_b: list[dict],
    inner_a,
    inner_b,
    *,
    item_a: Item | None = None,
    item_b: Item | None = None,
    max_turns: int = 60,
    draw_margin: float = 0.15,
    game_id: int = 0,
    game_timeout_s: float = 120.0,
) -> tuple[list, float, str, int]:
    """打一局并录制双方视角样本。

    inner_a / inner_b: 规则 agent 实例，或 (tag, player) -> agent 的工厂
    callable（player 由本函数构建后注入）。
    item_a / item_b: 队伍道具（meta 队自带魔法）；缺省随机——首领血脉队的
    进化之力必须随队伍传入，否则「首领进化流」在对局里根本不出现。

    返回 (samples, outcome_a, end_reason, turns)。
    samples: [(state_dict, action_idx, mask, game_id, side), ...]（side 为
    "A"/"B"；胜负标签由调用方按 side 填充——A 取 outcome_a，B 取 -outcome_a）。
    """
    p1 = factory.build_player("A", copy.deepcopy(specs_a), item=item_a or _random_item())
    p2 = factory.build_player("B", copy.deepcopy(specs_b), item=item_b or _random_item())
    battle = factory.build_battle(p1, p2)

    if callable(inner_a):
        inner_a = inner_a("A", p1)
    if callable(inner_b):
        inner_b = inner_b("B", p2)
    agent_a = RecordingAgent(inner_a, "A", [], game_id)
    agent_b = RecordingAgent(inner_b, "B", [], game_id)

    started = time.monotonic()
    turns = 0
    while not battle.is_finished and turns < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turns += 1
        if time.monotonic() - started >= game_timeout_s:
            break

    outcome_a, end_reason = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin, gamma=1.0, tanh_k=0.0,
    )
    if time.monotonic() - started >= game_timeout_s:
        end_reason = "timeout"

    samples = agent_a._sink + agent_b._sink
    return samples, outcome_a, end_reason, turns
