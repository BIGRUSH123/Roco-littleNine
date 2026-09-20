"""验证 SyncPickleQueue 同步 pickle 行为"""

import multiprocessing as mp
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from backend.engine.ai.core.evaluator import SyncPickleQueue
from backend.engine.ai.core.mcts import NUM_ACTIONS


def test_basic_put_get():
    """单个进程内基本 put/get 测试"""
    ctx = mp.get_context("spawn")
    q = SyncPickleQueue(maxsize=4, ctx=ctx)

    # 模拟真实数据：一个包含多种 numpy 数组的 state dict
    state = {
        "sprite_stats": np.random.randn(12, 7).astype(np.float32),
        "sprite_elements": np.random.randint(0, 10, (12, 2), dtype=np.int32),
        "sprite_states": np.random.randn(12, 105).astype(np.float32),
        "ast_tokens": np.random.randint(0, 100, (384,), dtype=np.int32),
        "ast_values": np.random.randn(384).astype(np.float32),
    }
    payload = (0, "candidate", state, np.ones(NUM_ACTIONS, dtype=np.float32))

    q.put(payload)
    result = q.get()

    # 验证数据完整
    assert result[0] == 0
    assert result[1] == "candidate"
    assert result[3].shape == (NUM_ACTIONS,)
    assert result[2]["sprite_stats"].shape == (12, 7)
    assert result[2]["ast_tokens"].shape == (384,)
    assert np.allclose(result[2]["sprite_stats"], state["sprite_stats"])

    q.close()
    q.join_thread()
    print("✓ test_basic_put_get PASSED")


def test_empty_queue_raises():
    """空队列超时应抛 Empty"""
    import queue
    ctx = mp.get_context("spawn")
    q = SyncPickleQueue(maxsize=4, ctx=ctx)

    try:
        q.get(timeout=0.1)
        assert False, "应抛出 queue.Empty"
    except queue.Empty:
        pass

    q.close()
    q.join_thread()
    print("✓ test_empty_queue_raises PASSED")


def _worker_put(q):
    """跨进程 put 的全局入口函数（spawn 要求可 pickle）"""
    import numpy as np
    state = {
        "sprite_stats": np.ones((12, 7), dtype=np.float32),
        "ast_tokens": np.arange(384, dtype=np.int32),
    }
    q.put((42, "best", state, np.ones(17, dtype=np.float32)))


def test_cross_process():
    """跨进程 put 测试"""
    ctx = mp.get_context("spawn")
    q = SyncPickleQueue(maxsize=4, ctx=ctx)

    p = ctx.Process(target=_worker_put, args=(q,))
    p.start()
    p.join()

    result = q.get()
    assert result[0] == 42
    assert result[1] == "best"
    assert np.allclose(result[2]["sprite_stats"], 1.0)
    assert np.array_equal(result[2]["ast_tokens"], np.arange(384, dtype=np.int32))

    q.close()
    q.join_thread()
    print("✓ test_cross_process PASSED")


if __name__ == "__main__":
    test_basic_put_get()
    test_empty_queue_raises()
    test_cross_process()
    print("\n所有测试通过 ✓")
