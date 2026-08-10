"""跨平台控制台输出工具。"""

from __future__ import annotations

import sys


def safe_print(message: str, **kwargs) -> None:
    """替换当前输出编码不支持的字符，避免日志终止长时间任务。"""
    stream = kwargs.get("file") or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe_message = message.encode(encoding, errors="replace").decode(encoding)
    print(safe_message, **kwargs)
