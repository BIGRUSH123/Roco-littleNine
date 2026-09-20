"""dbg_rust_trans — 打开 rust 调试日志跑单场（stderr 重定向到 UTF-8 文件）。

用法：env\\python.exe native/tools/dbg_rust_trans.py <spec_id> [输出文件]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

sid = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "native" / "tools" / "rust_dbg.log")

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
os.environ["ROCO_DEBUG_DMG"] = "1"

# fd2 级重定向：绕开 PowerShell 的 stderr 编码转换
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 2)
os.close(fd)

result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
print(f"done -> {out}, turns={len(result['turns'])}")
