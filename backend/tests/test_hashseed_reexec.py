# -*- coding: utf-8 -*-
"""`ensure_hash_seed` 的重执行必须在**含空格的解释器路径**下也能工作。

背景（2026-09-26）：原实现用 `os.execv`，Windows 上它把 argv 用空格拼起来交给 C
运行时，`C:\\Program Files\\Python3xx\\python.exe` 因此被拆断 →
`can't open file '...\\Files\\Python3xx\\python.exe'`，**本地任何训练入口都起不来**
（远端 `/usr/bin/python3` 无空格，所以从没暴露）。这条用一个真子进程钉住它。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")

_SCRIPT = """
import os, sys
sys.path.insert(0, {root!r})
from backend.engine.ai.determinism import ensure_hash_seed
print("BEFORE", os.environ.get("PYTHONHASHSEED"))
sys.stdout.flush()
ensure_hash_seed()
print("AFTER", os.environ.get("PYTHONHASHSEED"), sys.executable)
"""


def test_reexec_survives_spaced_interpreter_path(tmp_path: Path):
    script = tmp_path / "check_seed.py"
    root = str(Path(__file__).resolve().parents[2])
    script.write_text(_SCRIPT.format(root=root), encoding="utf-8")

    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)          # 模拟未钉住的进程
    env.pop("ROCO_KEEP_HASH_SEED", None)
    out = subprocess.run([sys.executable, "-X", "utf8", str(script)],
                         capture_output=True, text=True, env=env, timeout=180)
    text = out.stdout + out.stderr
    assert "can't open file" not in text, f"重执行把命令行拼坏了：{text[-400:]}"
    assert "AFTER 0" in text, f"重执行没生效：{text[-400:]}"
