"""backend/engine/ai/core/evaluator.py — 策略/价值网络推理抽象

支持：
  - TorchEvaluator：主进程或单进程内直接 forward
  - QueuePolicyEvaluator：worker 通过队列请求主进程批量推理
  - BatchedInferenceServer：主进程 CUDA 线程，合并多 worker 请求为 batch
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import torch

from multiprocessing.reduction import ForkingPickler as _ForkingPickler

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.engine.ai.core.model import EntityBottleneckNet

# 请求队列结束哨兵
INFERENCE_STOP = object()


# ═══════════════════════════════════════════════════════════════════
# SyncPickleQueue：同步 pickle 队列包装器
# ═══════════════════════════════════════════════════════════════════

class SyncPickleQueue:
    """mp.Queue 包装器：在 put() 中同步完成 pickle，消除 _feed 线程异步序列化风险。

    mp.Queue 默认将原始对象放入内部缓冲区，由后台 daemon _feed 线程异步序列化
    后写入管道。在 Windows 上传输大量 numpy 数组时，异步序列化存在以下脆弱性：

    1. _feed 线程在 pickle 大对象期间可能与管道错误并发，导致消息丢失或损坏
    2. Windows 管道 _wlock=None（依赖消息模式原子性），多 _feed 线程并发写
       同一管道的错误恢复路径行为不确定
    3. 间歇性触发 _pickle.UnpicklingError: Memo value not found

    本包装器在调用方线程中同步完成 pickle，底层 _feed 线程仅搬运不可变的
    bytes 对象，从根源上消除了上述竞态窗口。

    用法（完全兼容 mp.Queue API）：
        q = SyncPickleQueue(maxsize=16, ctx=mp.get_context("spawn"))
        q.put(large_numpy_dict)        # pickle 在调用方线程同步执行
        result = q.get(timeout=5.0)    # 自动反序列化为原始对象
    """

    def __init__(self, maxsize: int = 0, *, ctx):
        import multiprocessing as _mp
        self._queue: _mp.Queue = ctx.Queue(maxsize=maxsize)

    def put(self, obj, block: bool = True, timeout: float | None = None) -> None:
        """同步 pickle 后放入队列（线程安全，跨进程安全）。"""
        data = _ForkingPickler.dumps(obj)
        # Python 3.12 ForkingPickler.dumps() 返回 memoryview（零拷贝优化），
        # 而 memoryview 不可被 _feed 线程二次 pickle。强制转为 bytes。
        if isinstance(data, memoryview):
            data = bytes(data)
        self._queue.put(data, block=block, timeout=timeout)

    def get(self, block: bool = True, timeout: float | None = None):
        """从队列取出并自动反序列化为原始对象。"""
        data = self._queue.get(block=block, timeout=timeout)
        return _ForkingPickler.loads(data)

    def close(self) -> None:
        self._queue.close()

    def join_thread(self) -> None:
        self._queue.join_thread()

    def cancel_join_thread(self) -> None:
        self._queue.cancel_join_thread()


def _state_dict_to_tensors(state: dict[str, np.ndarray], device: str = "cpu") -> dict[str, torch.Tensor]:
    """将 numpy dict 转为带 batch 维度的 torch tensor dict。"""
    return {
        k: torch.from_numpy(v).unsqueeze(0).to(device)
        for k, v in state.items()
        if k in _STATE_KEYS
    }


def _batch_states(states: list[dict[str, np.ndarray]], device: str = "cpu") -> dict[str, torch.Tensor]:
    """将多个 state dict 堆叠为一个 batch dict。"""
    batch: dict[str, torch.Tensor] = {}
    for key in _STATE_KEYS:
        arrs = [s[key] for s in states]
        batch[key] = torch.from_numpy(np.stack(arrs, axis=0)).to(device)
    return batch


# 模型 forward 期望的键
_STATE_KEYS = frozenset({
    "sprite_stats", "sprite_elements", "sprite_states",
    "skill_stats", "skill_elements", "skill_states",
    "global_stats", "global_elements",
    "ast_tokens", "ast_values",
})


def _filter_state(state: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.asarray(v) for k, v in state.items() if k in _STATE_KEYS}


def _filter_states(states: list[dict[str, np.ndarray]]) -> list[dict[str, np.ndarray]]:
    return [_filter_state(state) for state in states]


def _masks_to_batch(masks: list[np.ndarray] | np.ndarray) -> np.ndarray:
    return (
        np.stack(masks, axis=0).astype(np.float32, copy=False)
        if isinstance(masks, list)
        else masks.astype(np.float32, copy=False)
    )


@runtime_checkable
class PolicyValueEvaluator(Protocol):
    """state (dict) + mask → (value, probs)。"""

    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        ...


class TorchEvaluator:
    """本地 PyTorch 推理（默认路径）。"""

    def __init__(self, model: EntityBottleneckNet, device: str = "cpu"):
        self._model = model
        self._device = device

    def evaluate(self, state: dict[str, np.ndarray], mask: np.ndarray) -> tuple[float, np.ndarray]:
        x = _state_dict_to_tensors(state, self._device)
        m = torch.from_numpy(mask.astype(np.float32, copy=False)).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            value_t, probs_t = self._model.forward_with_mask(x, m)
        value = float(value_t.item())
        probs = np.asarray(probs_t.squeeze(0).cpu().numpy(), dtype=np.float32)
        return value, probs

    def evaluate_batch(
        self,
        states: list[dict[str, np.ndarray]],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        x = _batch_states(states, self._device)
        m_np = (
            np.stack(masks, axis=0).astype(np.float32, copy=False)
            if isinstance(masks, list)
            else masks.astype(np.float32, copy=False)
        )
        m = torch.from_numpy(m_np).to(self._device)
        with torch.inference_mode():
            value_t, probs_t = self._model.forward_with_mask(x, m)
        values = np.asarray(value_t.squeeze(-1).cpu().numpy(), dtype=np.float32)
        probs = np.asarray(probs_t.cpu().numpy(), dtype=np.float32)
        return values, probs


class QueuePolicyEvaluator:
    """子进程 worker：将推理请求发往共享 request_queue，在 reply_queue 收结果。"""

    def __init__(
        self,
        worker_id: int,
        request_queue,
        reply_queue,
        reply_timeout_s: float = 300.0,
    ):
        self._worker_id = worker_id
        self._request_queue = request_queue
        self._reply_queue = reply_queue
        self._reply_timeout_s = reply_timeout_s

    def evaluate(self, state: dict[str, np.ndarray], mask: np.ndarray) -> tuple[float, np.ndarray]:
        self._request_queue.put((
            self._worker_id,
            _filter_state(state),
            np.asarray(mask, dtype=np.float32),
        ))
        try:
            result = self._reply_queue.get(timeout=self._reply_timeout_s)
        except queue.Empty as exc:
            raise TimeoutError(
                f"worker {self._worker_id} 等待批量推理回复超过 "
                f"{self._reply_timeout_s:.0f}s"
            ) from exc
        if result is None:
            raise RuntimeError(
                f"worker {self._worker_id} 推理失败（服务器端队列写入错误）"
            )
        value, probs = result
        return float(value), np.asarray(probs, dtype=np.float32)

    def evaluate_batch(
        self,
        states: list[dict[str, np.ndarray]],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(states)
        self._request_queue.put((
            self._worker_id,
            _filter_states(states),
            _masks_to_batch(masks),
            n,
        ))
        try:
            result = self._reply_queue.get(timeout=self._reply_timeout_s)
        except queue.Empty as exc:
            raise TimeoutError(
                f"worker {self._worker_id} 绛夊緟鎵归噺鎺ㄧ悊鍥炲瓒呰繃 "
                f"{self._reply_timeout_s:.0f}s"
            ) from exc
        if result is None:
            raise RuntimeError(
                f"worker {self._worker_id} 鎺ㄧ悊澶辫触锛堟湇鍔″櫒绔槦鍒楀啓鍏ラ敊璇級"
            )
        values, probs = result
        return np.asarray(values, dtype=np.float32), np.asarray(probs, dtype=np.float32)

class QueueModelEvaluator:
    """子进程 worker：按 model_id 请求主进程上的某个模型推理。"""

    def __init__(
        self,
        worker_id: int,
        model_id: str,
        request_queue,
        reply_queue,
        reply_timeout_s: float = 300.0,
    ):
        self._worker_id = worker_id
        self._model_id = model_id
        self._request_queue = request_queue
        self._reply_queue = reply_queue
        self._reply_timeout_s = reply_timeout_s

    def evaluate(self, state: dict[str, np.ndarray], mask: np.ndarray) -> tuple[float, np.ndarray]:
        self._request_queue.put((
            self._worker_id,
            self._model_id,
            _filter_state(state),
            np.asarray(mask, dtype=np.float32),
        ))
        try:
            result = self._reply_queue.get(timeout=self._reply_timeout_s)
        except queue.Empty as exc:
            raise TimeoutError(
                f"worker {self._worker_id} 等待模型 {self._model_id} "
                f"批量推理回复超过 {self._reply_timeout_s:.0f}s"
            ) from exc
        if result is None:
            raise RuntimeError(
                f"worker {self._worker_id} (model={self._model_id}) "
                "推理失败（服务器端队列写入错误）"
            )
        value, probs = result
        return float(value), np.asarray(probs, dtype=np.float32)

    def evaluate_batch(
        self,
        states: list[dict[str, np.ndarray]],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(states)
        self._request_queue.put((
            self._worker_id,
            self._model_id,
            _filter_states(states),
            _masks_to_batch(masks),
            n,
        ))
        try:
            result = self._reply_queue.get(timeout=self._reply_timeout_s)
        except queue.Empty as exc:
            raise TimeoutError(
                f"worker {self._worker_id} 绛夊緟妯″瀷 {self._model_id} "
                f"鎵归噺鎺ㄧ悊鍥炲瓒呰繃 {self._reply_timeout_s:.0f}s"
            ) from exc
        if result is None:
            raise RuntimeError(
                f"worker {self._worker_id} (model={self._model_id}) "
                "鎺ㄧ悊澶辫触锛堟湇鍔″櫒绔槦鍒楀啓鍏ラ敊璇級"
            )
        values, probs = result
        return np.asarray(values, dtype=np.float32), np.asarray(probs, dtype=np.float32)

class BatchedInferenceServer:
    """后台线程：从 request_queue 攒 batch，在 CUDA 上统一 forward。"""

    def __init__(
        self,
        model: EntityBottleneckNet,
        device: str,
        request_queue,
        reply_queues: dict[int, object],
        batch_size: int = 128,
        timeout_ms: float = 5.0,
    ):
        self._model = model
        self._device = device
        self._request_queue = request_queue
        self._reply_queues = reply_queues
        self._batch_size = max(1, batch_size)
        self._timeout_s = max(0.001, timeout_ms / 1000.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="BatchedInference", daemon=True)
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        """停止推理服务器。

        向 request_queue 注入 INFERENCE_STOP 哨兵以立即唤醒 daemon 线程，
        避免线程因阻塞在 queue.get() 上而延迟 join。
        """
        self._stop.set()
        # 推入哨兵解除 worker 阻塞 + 通知 _collect_batch 退出
        try:
            self._request_queue.put(INFERENCE_STOP, timeout=1.0)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=30.0 if drain else 1.0)
            self._thread = None

    def _collect_batch(self) -> list:
        batch: list = []
        item_count = 0
        deadline = time.monotonic() + self._timeout_s
        while item_count < self._batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and batch:
                break
            wait = max(0.005, remaining) if not batch else max(0.001, remaining)
            try:
                item = self._request_queue.get(timeout=wait)
            except queue.Empty:
                if batch:
                    break
                if self._stop.is_set():
                    break
                deadline = time.monotonic() + self._timeout_s
                continue
            if item is INFERENCE_STOP:
                self._stop.set()
                break
            if not (
                isinstance(item, tuple)
                and (len(item) == 3 or (len(item) == 4 and isinstance(item[3], int)))
            ):
                continue
            batch.append(item)
            item_count += item[3] if len(item) == 4 else 1
            if item_count >= self._batch_size:
                break
        return batch

    def _run(self) -> None:
        self._model.eval()
        while not self._stop.is_set():
            batch = self._collect_batch()
            if not batch:
                continue

            states: list[dict[str, np.ndarray]] = []
            masks: list[np.ndarray] = []
            reply_meta: list[tuple[int, int, int, bool]] = []
            for item in batch:
                worker_id = item[0]
                start = len(states)
                if len(item) == 4:
                    request_states = item[1]
                    request_masks = np.asarray(item[2], dtype=np.float32)
                    states.extend(request_states)
                    masks.extend(request_masks)
                    reply_meta.append((worker_id, start, len(states), True))
                else:
                    states.append(item[1])
                    masks.append(item[2])
                    reply_meta.append((worker_id, start, start + 1, False))

            x = _batch_states(states, self._device)
            m = torch.from_numpy(np.stack(masks, axis=0)).to(self._device)
            with torch.inference_mode():
                values_t, probs_t = self._model.forward_with_mask(x, m)
            values = values_t.squeeze(-1).cpu().numpy()
            probs = probs_t.cpu().numpy()
            for worker_id, start, end, is_batch in reply_meta:
                reply_q = self._reply_queues.get(worker_id)
                if reply_q is None:
                    continue
                if is_batch:
                    payload = (
                        values[start:end].astype(np.float32, copy=False),
                        probs[start:end].astype(np.float32, copy=False),
                    )
                else:
                    payload = (
                        float(values[start]),
                        probs[start].astype(np.float32, copy=False),
                    )
                try:
                    reply_q.put(payload, timeout=1.0)
                except Exception:
                    logger.error(
                        "BatchedInference: 无法向 worker %d 发送结果 (队列满/管道损坏)",
                        worker_id, exc_info=True,
                    )
                    try:
                        reply_q.put(None, timeout=1.0)
                    except Exception:
                        pass


class BatchedModelInferenceServer:
    """后台线程：从 request_queue 攒 batch，按 model_id 分组推理。

    reply_queues: {worker_id: {model_id: mp.Queue}}，每个 worker 的每个模型
    有独立的回复队列，避免两个 evaluator（candidate/best）共享同一 queue
    导致 Windows pipe 竞态死锁。
    """

    def __init__(
        self,
        models: dict[str, EntityBottleneckNet],
        device: str,
        request_queue,
        reply_queues: dict[int, dict[str, object]],
        batch_size: int = 128,
        timeout_ms: float = 5.0,
    ):
        self._models = models
        self._device = device
        self._request_queue = request_queue
        self._reply_queues = reply_queues
        self._batch_size = max(1, batch_size)
        self._timeout_s = max(0.001, timeout_ms / 1000.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="BatchedModelInference", daemon=True)
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        """停止推理服务器。

        向 request_queue 注入 INFERENCE_STOP 哨兵以立即唤醒 daemon 线程。
        """
        self._stop.set()
        try:
            self._request_queue.put(INFERENCE_STOP, timeout=1.0)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=30.0 if drain else 1.0)
            self._thread = None

    def _collect_batch(self) -> list:
        batch: list = []
        item_count = 0
        deadline = time.monotonic() + self._timeout_s
        while item_count < self._batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and batch:
                break
            wait = max(0.005, remaining) if not batch else max(0.001, remaining)
            try:
                item = self._request_queue.get(timeout=wait)
            except queue.Empty:
                if batch:
                    break
                if self._stop.is_set():
                    break
                deadline = time.monotonic() + self._timeout_s
                continue
            if item is INFERENCE_STOP:
                self._stop.set()
                break
            if not (
                isinstance(item, tuple)
                and (len(item) == 4 or (len(item) == 5 and isinstance(item[4], int)))
            ):
                continue
            batch.append(item)
            item_count += item[4] if len(item) == 5 else 1
            if item_count >= self._batch_size:
                break
        return batch

    def _run(self) -> None:
        for model in self._models.values():
            model.eval()
        while not self._stop.is_set():
            batch = self._collect_batch()
            if not batch:
                continue

            by_model: dict[str, list[tuple[int, object, object, bool]]] = {}
            for item in batch:
                if len(item) == 5:
                    worker_id, model_id, states, masks, _n = item
                    by_model.setdefault(model_id, []).append((worker_id, states, masks, True))
                else:
                    worker_id, model_id, state, mask = item
                    by_model.setdefault(model_id, []).append((worker_id, state, mask, False))

            for model_id, items in by_model.items():
                model = self._models.get(model_id)
                if model is None:
                    logger.error(
                        "BatchedModelInference: 未知 model_id=%s, "
                        "通知 %d 个 worker 推理失败",
                        model_id, len(items),
                    )
                    for (worker_id, _, _, _) in items:
                        q_map = self._reply_queues.get(worker_id)
                        reply_q = q_map.get(model_id) if q_map else None
                        if reply_q is not None:
                            try:
                                reply_q.put(None, timeout=1.0)
                            except Exception:
                                pass
                    continue

                states: list[dict[str, np.ndarray]] = []
                masks: list[np.ndarray] = []
                reply_meta: list[tuple[int, int, int, bool]] = []
                for worker_id, state_or_states, mask_or_masks, is_batch in items:
                    start = len(states)
                    if is_batch:
                        states.extend(state_or_states)
                        request_masks = np.asarray(mask_or_masks, dtype=np.float32)
                        masks.extend(request_masks)
                        reply_meta.append((worker_id, start, len(states), True))
                    else:
                        states.append(state_or_states)
                        masks.append(mask_or_masks)
                        reply_meta.append((worker_id, start, start + 1, False))

                x = _batch_states(states, self._device)
                m = torch.from_numpy(np.stack(masks, axis=0)).to(self._device)
                with torch.inference_mode():
                    values_t, probs_t = model.forward_with_mask(x, m)
                values = values_t.squeeze(-1).cpu().numpy()
                probs = probs_t.cpu().numpy()
                for worker_id, start, end, is_batch in reply_meta:
                    q_map = self._reply_queues.get(worker_id)
                    reply_q = q_map.get(model_id) if q_map else None
                    if reply_q is None:
                        continue
                    if is_batch:
                        payload = (
                            values[start:end].astype(np.float32, copy=False),
                            probs[start:end].astype(np.float32, copy=False),
                        )
                    else:
                        payload = (
                            float(values[start]),
                            probs[start].astype(np.float32, copy=False),
                        )
                    try:
                        reply_q.put(payload, timeout=1.0)
                    except Exception:
                        logger.error(
                            "BatchedModelInference: 无法向 worker %d 发送结果 "
                            "(model=%s, 队列满/管道损坏)",
                            worker_id, model_id, exc_info=True,
                        )
                        try:
                            reply_q.put(None, timeout=1.0)
                        except Exception:
                            pass
