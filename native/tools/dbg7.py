"""临时调试5：全局（含 traits、嵌套 then）搜索中毒 abnormal 定义。"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")


def walk(o, path):
    if isinstance(o, dict):
        if o.get("op") == "abnormal" and o.get("name") == "中毒":
            print(f"{path}: {json.dumps(o, ensure_ascii=False)[:300]}")
        for k, v in o.items():
            walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, f"{path}[{i}]")


for f in glob.glob("data/skills/*.json") + glob.glob("data/traits/*.json"):
    try:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        continue
    walk(data, str(Path(f).name))
