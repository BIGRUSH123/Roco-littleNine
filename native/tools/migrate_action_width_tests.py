# -*- coding: utf-8 -*-
"""把测试里硬编码的动作维度断言改为从 NUM_ACTIONS 派生（动作空间 17→22）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8")

# (文件, 替换对)
EDITS: list[tuple[str, list[tuple[str, str]]]] = [
    ("backend/engine/test_mcts_parallel.py", [
        ("assert visits.shape == (17,)", "assert visits.shape == (NUM_ACTIONS,)"),
        ("assert probs.shape == (17,)", "assert probs.shape == (NUM_ACTIONS,)"),
        ("assert mask.shape == (17,)", "assert mask.shape == (NUM_ACTIONS,)"),
    ]),
    ("backend/engine/ai/tests/test_benchmark_mcts.py", [
        ("assert policy.shape == (17,)", "assert policy.shape == (NUM_ACTIONS,)"),
    ]),
    ("backend/engine/ai/tests/test_sync_pickle_queue.py", [
        ("assert result[3].shape == (17,)", "assert result[3].shape == (NUM_ACTIONS,)"),
    ]),
    ("native/tools/smoke_hook.py", [
        ("assert all(p.shape == (17,) for p in probs)",
         "assert all(p.shape == (NUM_ACTIONS,) for p in probs)"),
    ]),
]

for rel, subs in EDITS:
    path = _ROOT / rel
    text = path.read_text(encoding="utf-8")
    changed = 0
    for old, new in subs:
        if old in text:
            text = text.replace(old, new)
            changed += text.count(new) if False else 1
    if changed:
        # 确保 NUM_ACTIONS 已导入
        if "NUM_ACTIONS" in text and not re.search(r"import .*\bNUM_ACTIONS\b", text):
            if "from backend.engine.ai.core.mcts import (" in text:
                text = text.replace("from backend.engine.ai.core.mcts import (",
                                    "from backend.engine.ai.core.mcts import (\n    NUM_ACTIONS,", 1)
            elif "from backend.engine.ai.core.mcts import NUM_ACTIONS" not in text:
                # 插到最后一个 backend import 之后
                lines = text.splitlines()
                idx = max((i for i, l in enumerate(lines) if l.startswith("from backend")), default=-1)
                if idx >= 0:
                    lines.insert(idx + 1, "from backend.engine.ai.core.mcts import NUM_ACTIONS")
                    text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        path.write_text(text, encoding="utf-8")
        print(f"OK   {rel}（{changed} 处）")
    else:
        print(f"MISS {rel}")
