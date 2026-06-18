"""backend/engine/ai/core/replay_buffer.py — 字典型经验回放池

为 EntityBottleneckNet 的 dict 输入专门设计：
  - 为字典中每个 key 独立预分配 numpy 数组（避免对象数组开销）
  - 支持 push/push_batch/sample，可直接搭配 PyTorch DataLoader
  - 兼容 AlphaZero 训练风格：(state_dict, policy_target, mask, outcome)
"""

from __future__ import annotations

from collections import deque

import numpy as np
import torch

from backend.engine.ai.core.encoder import MAX_SEQ_LEN, SPRITE_STATES_DIM
from backend.engine.ai.core.mcts import NUM_ACTIONS

# ── 观测空间元信息（与 encoder.py 输出严格对齐） ──
_OBS_SPEC: dict[str, tuple[tuple[int, ...], np.dtype]] = {
    "sprite_stats":    ((12, 7), np.float32),
    "sprite_elements": ((12, 2), np.int32),
    "sprite_states":   ((12, SPRITE_STATES_DIM), np.float32),
    "skill_stats":     ((10, 2), np.float32),
    "skill_elements":  ((10, 2), np.int32),
    "skill_states":    ((10, 9), np.float32),
    "global_stats":    ((15,), np.float32),
    "global_elements": ((1,), np.int32),
    "ast_tokens":      ((MAX_SEQ_LEN,), np.int32),
    "ast_values":      ((MAX_SEQ_LEN,), np.float32),
}

_OBS_KEYS = tuple(_OBS_SPEC.keys())


class DictReplayBuffer:
    """专为实体瓶颈网络设计的字典型经验回放池。

    每个 sample 为 5 元组:
        (state_dict, policy_target, mask, outcome, game_turn)
    其中 state_dict 包含 _OBS_SPEC 中定义的全部 key。

    Usage:
        buf = DictReplayBuffer(capacity=100000)
        buf.push_batch(states_list, P, M, v)         # AlphaZero 风格
        batch = buf.sample(256)                        # → dict of tensors
        dataset = DictReplayDataset(buf)               # PyTorch DataLoader 兼容
    """

    def __init__(self, capacity: int):
        self.capacity = max(1, int(capacity))
        self.ptr = 0
        self.size = 0

        # 为观测字典中的每个 key 独立分配预形状数组
        self.buffers: dict[str, np.ndarray] = {}
        for key, (shape, dtype) in _OBS_SPEC.items():
            self.buffers[key] = np.zeros((capacity, *shape), dtype=dtype)

        # AlphaZero 特有字段
        self.policy_buffer = np.zeros((capacity, NUM_ACTIONS), dtype=np.float32)
        self.mask_buffer = np.zeros((capacity, NUM_ACTIONS), dtype=np.float32)
        self.outcome_buffer = np.zeros(capacity, dtype=np.float32)

    # ── 写入 ──

    def push(
        self,
        state: dict[str, np.ndarray],
        policy: np.ndarray,
        mask: np.ndarray,
        outcome: float,
    ) -> None:
        """写入单条经验。"""
        idx = self.ptr
        for key in _OBS_KEYS:
            self.buffers[key][idx] = state[key]
        self.policy_buffer[idx] = policy
        self.mask_buffer[idx] = mask
        self.outcome_buffer[idx] = outcome

        self.ptr = (self.ptr + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def push_batch(
        self,
        states: list[dict[str, np.ndarray]],
        policies: np.ndarray,   # (N, 17)
        masks: np.ndarray,       # (N, 17)
        outcomes: np.ndarray,    # (N,)
    ) -> int:
        """批量写入（比逐条 push 更快，避免多次取模）。返回实际写入条数。"""
        n = min(len(states), len(policies), len(masks), len(outcomes))
        if n == 0:
            return 0

        original_n = n
        start = self.ptr
        if n > self.capacity:
            skip = n - self.capacity
            states = states[skip:n]
            policies = policies[skip:n]
            masks = masks[skip:n]
            outcomes = outcomes[skip:n]
            n = self.capacity
            start = (start + skip) % self.capacity

        end = start + n
        if end <= self.capacity:
            target = slice(start, end)
            for key in _OBS_KEYS:
                self.buffers[key][target] = np.stack(
                    [state[key] for state in states], axis=0,
                )
            self.policy_buffer[target] = policies
            self.mask_buffer[target] = masks
            self.outcome_buffer[target] = outcomes
        else:
            first = self.capacity - start
            second = n - first
            first_target = slice(start, self.capacity)
            second_target = slice(0, second)
            for key in _OBS_KEYS:
                stacked = np.stack([state[key] for state in states], axis=0)
                self.buffers[key][first_target] = stacked[:first]
                self.buffers[key][second_target] = stacked[first:]
            self.policy_buffer[first_target] = policies[:first]
            self.policy_buffer[second_target] = policies[first:]
            self.mask_buffer[first_target] = masks[:first]
            self.mask_buffer[second_target] = masks[first:]
            self.outcome_buffer[first_target] = outcomes[:first]
            self.outcome_buffer[second_target] = outcomes[first:]

        self.ptr = (start + n) % self.capacity
        self.size = min(self.size + original_n, self.capacity)
        return original_n

    # ── 采样 ──

    def sample(self, batch_size: int) -> dict[str, torch.Tensor]:
        """均匀随机采样一个 batch，返回可直接送入 model.forward() 的 tensor dict。

        Returns:
            {
                "sprite_stats": (B, 12, 7),
                ...
                "ast_values":   (B, 384),
                "policy":       (B, 17),
                "mask":         (B, 17),
                "outcome":      (B,),
            }
        """
        n = min(batch_size, self.size)
        if n == 0:
            raise RuntimeError("DictReplayBuffer 为空，无法采样")
        idxs = np.random.choice(self.size, size=n, replace=False)

        batch = {}
        for key in _OBS_KEYS:
            batch[key] = torch.from_numpy(self.buffers[key][idxs].copy())
        batch["policy"] = torch.from_numpy(self.policy_buffer[idxs].copy())
        batch["mask"] = torch.from_numpy(self.mask_buffer[idxs].copy())
        batch["outcome"] = torch.from_numpy(self.outcome_buffer[idxs].copy())
        return batch

    def sample_obs_only(self, batch_size: int) -> dict[str, torch.Tensor]:
        """仅采样观测（用于无监督或状态表征预训练）。"""
        n = min(batch_size, self.size)
        if n == 0:
            raise RuntimeError("DictReplayBuffer 为空，无法采样")
        idxs = np.random.choice(self.size, size=n, replace=False)

        batch = {}
        for key in _OBS_KEYS:
            batch[key] = torch.from_numpy(self.buffers[key][idxs].copy())
        return batch

    # ── 查询 ──

    def __len__(self) -> int:
        return self.size

    def is_full(self) -> bool:
        return self.size >= self.capacity

    def clear(self) -> None:
        self.ptr = 0
        self.size = 0
        for buf in self.buffers.values():
            buf.fill(0)
        self.policy_buffer.fill(0)
        self.mask_buffer.fill(0)
        self.outcome_buffer.fill(0)


class RecentIterationsReplayBuffer:
    """按 iteration 保留最近 N 轮完整样本的经验回放池。

    语义与 DictReplayBuffer 不同：
      - `buffer=3` 代表严格保留最近 3 轮自博数据
      - 超过 N 轮时，整轮丢弃最旧数据，不保留残缺旧轮次

    为兼容现有训练逻辑，暴露与 DictReplayBuffer 相同的只读视图字段：
      - buffers / policy_buffer / mask_buffer / outcome_buffer
      - __len__ / sample / sample_obs_only / clear
    """

    def __init__(self, keep_iterations: int):
        self.keep_iterations = max(1, int(keep_iterations))
        self._iterations: deque[dict[str, object]] = deque(maxlen=self.keep_iterations)
        self.buffers: dict[str, np.ndarray] = {}
        self.policy_buffer = np.zeros((0, NUM_ACTIONS), dtype=np.float32)
        self.mask_buffer = np.zeros((0, NUM_ACTIONS), dtype=np.float32)
        self.outcome_buffer = np.zeros((0,), dtype=np.float32)
        self.size = 0
        self._reset_obs_buffers()

    def _reset_obs_buffers(self) -> None:
        self.buffers = {
            key: np.zeros((0, *shape), dtype=dtype)
            for key, (shape, dtype) in _OBS_SPEC.items()
        }

    def _stack_states(self, states: list[dict[str, np.ndarray]], n: int) -> dict[str, np.ndarray]:
        return {
            key: np.stack([state[key] for state in states[:n]], axis=0)
            for key in _OBS_KEYS
        }

    def _rebuild_view(self) -> None:
        if not self._iterations:
            self._reset_obs_buffers()
            self.policy_buffer = np.zeros((0, NUM_ACTIONS), dtype=np.float32)
            self.mask_buffer = np.zeros((0, NUM_ACTIONS), dtype=np.float32)
            self.outcome_buffer = np.zeros((0,), dtype=np.float32)
            self.size = 0
            return

        self.buffers = {
            key: np.concatenate(
                [chunk["buffers"][key] for chunk in self._iterations], axis=0,
            )
            for key in _OBS_KEYS
        }
        self.policy_buffer = np.concatenate(
            [chunk["policy"] for chunk in self._iterations], axis=0,
        )
        self.mask_buffer = np.concatenate(
            [chunk["mask"] for chunk in self._iterations], axis=0,
        )
        self.outcome_buffer = np.concatenate(
            [chunk["outcome"] for chunk in self._iterations], axis=0,
        )
        self.size = int(self.outcome_buffer.shape[0])

    def push_batch(
        self,
        states: list[dict[str, np.ndarray]],
        policies: np.ndarray,
        masks: np.ndarray,
        outcomes: np.ndarray,
    ) -> int:
        """将一整轮样本作为一个 chunk 追加到 replay。"""
        n = min(len(states), len(policies), len(masks), len(outcomes))
        if n == 0:
            return 0

        chunk = {
            "buffers": self._stack_states(states, n),
            "policy": np.asarray(policies[:n], dtype=np.float32).copy(),
            "mask": np.asarray(masks[:n], dtype=np.float32).copy(),
            "outcome": np.asarray(outcomes[:n], dtype=np.float32).copy(),
        }
        self._iterations.append(chunk)
        self._rebuild_view()
        return n

    def sample(self, batch_size: int) -> dict[str, torch.Tensor]:
        n = min(batch_size, self.size)
        if n == 0:
            raise RuntimeError("RecentIterationsReplayBuffer 为空，无法采样")
        idxs = np.random.choice(self.size, size=n, replace=False)

        batch = {}
        for key in _OBS_KEYS:
            batch[key] = torch.from_numpy(self.buffers[key][idxs].copy())
        batch["policy"] = torch.from_numpy(self.policy_buffer[idxs].copy())
        batch["mask"] = torch.from_numpy(self.mask_buffer[idxs].copy())
        batch["outcome"] = torch.from_numpy(self.outcome_buffer[idxs].copy())
        return batch

    def sample_obs_only(self, batch_size: int) -> dict[str, torch.Tensor]:
        n = min(batch_size, self.size)
        if n == 0:
            raise RuntimeError("RecentIterationsReplayBuffer 为空，无法采样")
        idxs = np.random.choice(self.size, size=n, replace=False)

        batch = {}
        for key in _OBS_KEYS:
            batch[key] = torch.from_numpy(self.buffers[key][idxs].copy())
        return batch

    def __len__(self) -> int:
        return self.size

    def is_full(self) -> bool:
        return len(self._iterations) >= self.keep_iterations

    def clear(self) -> None:
        self._iterations.clear()
        self._rebuild_view()


# ═══════════════════════════════════════════════════════════════════
# PyTorch DataLoader 集成
# ═══════════════════════════════════════════════════════════════════

class DictReplayDataset(torch.utils.data.Dataset):
    """将 DictReplayBuffer 包装为 PyTorch Dataset，支持 DataLoader 多 worker 加载。

    __getitem__ 返回一個扁平 tuple (sprite_stats, sprite_elements, ..., policy, mask, outcome)，
    配合 collate_fn 拼回 batch dict。这样避免默认 collate 递归遍历 dict 时的 O(N_keys) 开销。
    """

    def __init__(self, buffer: DictReplayBuffer):
        self._buffer = buffer

    def __len__(self) -> int:
        return self._buffer.size

    def __getitem__(self, idx: int):
        row: list[np.ndarray] = []
        for key in _OBS_KEYS:
            row.append(self._buffer.buffers[key][idx])
        row.append(self._buffer.policy_buffer[idx])
        row.append(self._buffer.mask_buffer[idx])
        row.append(np.asarray(self._buffer.outcome_buffer[idx], dtype=np.float32))
        return tuple(row)


def dict_replay_collate(batch: list[tuple]) -> dict[str, torch.Tensor]:
    """collate_fn：将 N 个扁平 tuple 堆叠为 batch dict。"""
    n_keys_obs = len(_OBS_KEYS)
    result: dict[str, torch.Tensor] = {}
    for i, key in enumerate(_OBS_KEYS):
        result[key] = torch.from_numpy(np.stack([row[i] for row in batch], axis=0))
    result["policy"] = torch.from_numpy(np.stack([row[n_keys_obs] for row in batch], axis=0))
    result["mask"] = torch.from_numpy(np.stack([row[n_keys_obs + 1] for row in batch], axis=0))
    result["outcome"] = torch.from_numpy(np.stack([row[n_keys_obs + 2] for row in batch], axis=0))
    return result
