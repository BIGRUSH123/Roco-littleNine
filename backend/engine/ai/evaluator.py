"""backend/engine/ai/evaluator.py — 策略/价值网络推理抽象

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

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.engine.ai.model import BattleNet

# 请求队列结束哨兵
INFERENCE_STOP = object()


@runtime_checkable
class PolicyValueEvaluator(Protocol):
    """state (466,) + mask (11,) → (value, probs)。"""

    def evaluate(self, state: np.ndarray, mask: np.ndarray) -> tuple[float, np.ndarray]:
        ...


class TorchEvaluator:
    """本地 PyTorch 推理（默认路径）。"""

    def __init__(self, model: BattleNet, device: str = "cpu"):
        self._model = model
        self._device = device

    def evaluate(self, state: np.ndarray, mask: np.ndarray) -> tuple[float, np.ndarray]:
        x = torch.from_numpy(state.astype(np.float32)).unsqueeze(0).to(self._device)
        m = torch.from_numpy(mask.astype(np.float32)).unsqueeze(0).to(self._device)
        with torch.no_grad():
            value_t, probs_t = self._model.forward_with_mask(x, m)
        value = float(value_t.item())
        probs = probs_t.squeeze(0).cpu().numpy().astype(np.float32)
        return value, probs


class QueuePolicyEvaluator:
    """子进程 worker：将推理请求发往共享 request_queue，在 reply_queue 收结果。

    请求载荷为 (worker_id, state, mask)，不可把 Queue 对象放入队列（Windows spawn 会报错）。
    """

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

    def evaluate(self, state: np.ndarray, mask: np.ndarray) -> tuple[float, np.ndarray]:
        self._request_queue.put((
            self._worker_id,
            np.asarray(state, dtype=np.float32),
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

    def evaluate(self, state: np.ndarray, mask: np.ndarray) -> tuple[float, np.ndarray]:
        self._request_queue.put((
            self._worker_id,
            self._model_id,
            np.asarray(state, dtype=np.float32),
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


class BatchedInferenceServer:
    """后台线程：从 request_queue 攒 batch，在 CUDA 上统一 forward。"""

    def __init__(
        self,
        model: BattleNet,
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
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30.0 if drain else 1.0)
            self._thread = None

    def _collect_batch(self) -> list:
        batch: list = []
        deadline = time.monotonic() + self._timeout_s
        while len(batch) < self._batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and batch:
                break
            # Windows: multiprocessing.Queue 超时 < 1ms 可能因底层管道实现
            # 在低频请求时漏收消息。最短超时设为 5ms 避免漏收 → worker 永久阻塞。
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
            if not (isinstance(item, tuple) and len(item) == 3):
                continue
            batch.append(item)
            if len(batch) >= self._batch_size:
                break
        return batch

    def _run(self) -> None:
        self._model.eval()
        while not self._stop.is_set():
            batch = self._collect_batch()
            if not batch:
                continue
            states = np.stack([item[1] for item in batch], axis=0)
            masks = np.stack([item[2] for item in batch], axis=0)
            x = torch.from_numpy(states).to(self._device)
            m = torch.from_numpy(masks).to(self._device)
            with torch.no_grad():
                values_t, probs_t = self._model.forward_with_mask(x, m)
            values = values_t.squeeze(-1).cpu().numpy()
            probs = probs_t.cpu().numpy()
            for i, (worker_id, _, _) in enumerate(batch):
                reply_q = self._reply_queues.get(worker_id)
                if reply_q is None:
                    continue
                try:
                    reply_q.put((float(values[i]), probs[i].astype(np.float32)),
                                timeout=1.0)
                except Exception:
                    logger.error(
                        "BatchedInference: 无法向 worker %d 发送结果 (队列满/管道损坏)",
                        worker_id, exc_info=True,
                    )
                    # 放入 None 通知 worker 本次推理失败，避免 worker 永久阻塞
                    try:
                        reply_q.put(None, timeout=1.0)
                    except Exception:
                        pass


class BatchedModelInferenceServer:
    """后台线程：从 request_queue 攒 batch，按 model_id 分组推理。"""

    def __init__(
        self,
        models: dict[str, BattleNet],
        device: str,
        request_queue,
        reply_queues: dict[int, object],
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
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30.0 if drain else 1.0)
            self._thread = None

    def _collect_batch(self) -> list:
        batch: list = []
        deadline = time.monotonic() + self._timeout_s
        while len(batch) < self._batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and batch:
                break
            # Windows: multiprocessing.Queue 超时 < 1ms 可能因底层管道实现
            # 在低频请求时漏收消息。最短超时设为 5ms 避免漏收 → worker 永久阻塞。
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
            if not (isinstance(item, tuple) and len(item) == 4):
                continue
            batch.append(item)
            if len(batch) >= self._batch_size:
                break
        return batch

    def _run(self) -> None:
        for model in self._models.values():
            model.eval()
        while not self._stop.is_set():
            batch = self._collect_batch()
            if not batch:
                continue

            by_model: dict[str, list[tuple[int, np.ndarray, np.ndarray]]] = {}
            for worker_id, model_id, state, mask in batch:
                by_model.setdefault(model_id, []).append((worker_id, state, mask))

            for model_id, items in by_model.items():
                model = self._models.get(model_id)
                if model is None:
                    logger.error(
                        "BatchedModelInference: 未知 model_id=%s, "
                        "通知 %d 个 worker 推理失败",
                        model_id, len(items),
                    )
                    for (worker_id, _, _) in items:
                        reply_q = self._reply_queues.get(worker_id)
                        if reply_q is not None:
                            try:
                                reply_q.put(None, timeout=1.0)
                            except Exception:
                                pass
                    continue
                states = np.stack([item[1] for item in items], axis=0)
                masks = np.stack([item[2] for item in items], axis=0)
                x = torch.from_numpy(states).to(self._device)
                m = torch.from_numpy(masks).to(self._device)
                with torch.no_grad():
                    values_t, probs_t = model.forward_with_mask(x, m)
                values = values_t.squeeze(-1).cpu().numpy()
                probs = probs_t.cpu().numpy()
                for i, (worker_id, _, _) in enumerate(items):
                    reply_q = self._reply_queues.get(worker_id)
                    if reply_q is None:
                        continue
                    try:
                        reply_q.put((float(values[i]), probs[i].astype(np.float32)),
                                    timeout=1.0)
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
