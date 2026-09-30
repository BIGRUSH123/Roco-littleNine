# -*- coding: utf-8 -*-
"""本轮检查点必须**在门控之前**落盘。

理由（2026-09-25 实盘教训）：400 sims 的门控要给 300 局 × 双方搜索，实测约 1.5 小时；
实例到期/进程被杀正好落在门控里时，若保存放在门控之后，整轮自我博弈（300 局
@400 sims ≈ 2.5 小时）会一起丢掉。先落盘 → 门控随时可用
`probe_reward_ab --only ckpt --ckpt <iterN> --ckpt-other <best>` 单独补跑。

这条只能靠源码顺序断言（没法在单测里跑完整个 main 循环）。
"""
from __future__ import annotations

import inspect
import sys

sys.path.insert(0, ".")

from backend.engine.ai import train as train_module  # noqa: E402


def test_iter_checkpoint_saved_before_gate():
    src = inspect.getsource(train_module.main)
    save_at = src.find("model_rl_iter{iteration}.pt")
    gate_at = src.find("门控评估（候选 vs 最优")
    assert save_at > 0, "找不到本轮检查点保存"
    assert gate_at > 0, "找不到门控评估"
    assert save_at < gate_at, (
        "本轮检查点保存必须早于门控评估：否则门控被中断（实例到期等）会把整轮"
        "自我博弈的成果一起丢掉")
    # 不允许出现第二次保存（重复保存说明旧块没清理干净）
    assert src.count("model_rl_iter{iteration}.pt") == 1
