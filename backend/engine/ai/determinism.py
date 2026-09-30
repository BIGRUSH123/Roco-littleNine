"""backend/engine/ai/determinism.py — 度量与训练的可复现前提。

两件直接决定"同 seed 同命令会不会给出同一个数"的事：

1. **字符串哈希随机化**（`PYTHONHASHSEED` 默认随机）：Python 每进程用随机盐哈希 str，
   于是 `set`/`dict` 里字符串的迭代顺序逐进程不同。这个顺序会漏进配装生成
   （`list(set(技能池))`）、特性注册等路径，最终表现为"同一命令两次运行抽到不同阵容"
   ——实测 1000 局能差 5 个百分点（`docs/博弈-概率预判口径.md` §4f）。唯一可靠的解法
   是进程启动前钉死 `PYTHONHASHSEED=0`，即重执行自己。
2. **未播种的全局 random**：`train._random_teams` 等函数默认用全局 `random` 模块；调用方
   若没有先 `random.seed()`，阵容就由进程启动时的 OS 熵决定。度量工具应显式传
   `random.Random(seed)`（`_random_teams(..., rng=)`）。

入口点用法：在 `main()` 第一行调用 `ensure_hash_seed()`。
"""
from __future__ import annotations

import os
import subprocess
import sys


def _reexec_argv() -> list[str]:
    """重执行自己时的 argv（保持 `-m` 语义；见 ensure_hash_seed 注释）。"""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    module_name = getattr(spec, "name", None)
    if module_name:
        return [sys.executable, "-m", module_name] + sys.argv[1:]
    return [sys.executable] + list(sys.argv)


def ensure_hash_seed() -> None:
    """确保本进程 `PYTHONHASHSEED=0`，否则带着该变量重执行自己。

    幂等：子进程读到环境变量后直接返回。调试时可用 `ROCO_KEEP_HASH_SEED=1` 跳过。

    **不用 `os.execv`**：Windows 上它会把 argv 用空格拼起来交给 C 运行时，
    解释器路径含空格（`C:\\Program Files\\Python3xx\\python.exe`）时命令行会被
    拆断 → 报 `can't open file '...\\Files\\Python3xx\\python.exe'`。本地实测
    直接起不来（远端 `/usr/bin/python3` 无空格所以从没暴露）。改用
    `subprocess.run(list)` 由标准库负责转义，再退出本进程。
    """
    if os.environ.get("PYTHONHASHSEED") == "0":
        return
    if os.environ.get("ROCO_KEEP_HASH_SEED") == "1":
        return
    os.environ["PYTHONHASHSEED"] = "0"
    argv = _reexec_argv()
    if os.name == "nt":
        sys.stdout.flush()
        sys.stderr.flush()
        raise SystemExit(subprocess.run(argv).returncode)
    os.execv(sys.executable, argv)
