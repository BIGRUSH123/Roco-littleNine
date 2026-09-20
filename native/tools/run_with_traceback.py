# -*- coding: utf-8 -*-
"""native/tools/run_with_traceback.py — 带栈转储地跑生成脚本（定位卡死）。

用法:
  python native/tools/run_with_traceback.py <脚本路径> [脚本参数...]

每 --interval 秒把**所有线程**的调用栈写到 stderr（faulthandler），
用于定位「某一局卡死」这类问题：卡住时最后一份栈就是卡点。
"""
from __future__ import annotations

import faulthandler
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    script = Path(sys.argv[1])
    rest = sys.argv[2:]
    interval = 45
    if "--interval" in rest:
        i = rest.index("--interval")
        interval = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    args = [a for a in rest if a != "--"]
    faulthandler.enable()
    faulthandler.dump_traceback_later(interval, repeat=True, exit=False)
    sys.argv = [str(script)] + args
    sys.path.insert(0, str(_ROOT))
    print(f"[traceback-runner] {script.name} {' '.join(args)}；每 {interval}s 转储栈", flush=True)
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
