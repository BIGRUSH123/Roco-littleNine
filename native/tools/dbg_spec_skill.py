"""dbg_spec_skill — 打印 spec 中指定技能的嵌入数据。

用法：env\\python.exe native/tools/dbg_spec_skill.py <spec_id> <技能名>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sid, skill_name = sys.argv[1], sys.argv[2]
spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))

def walk(obj, path=""):
    if isinstance(obj, dict):
        if obj.get("name") == skill_name and ("power" in obj or "energy_cost" in obj or "effects" in obj):
            print(f"--- at {path}")
            print(json.dumps(obj, ensure_ascii=False, indent=1)[:2000])
            return True
        for k, v in obj.items():
            if walk(v, f"{path}.{k}"):
                return True
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if walk(v, f"{path}[{i}]"):
                return True
    return False

if not walk(spec, "spec"):
    print(f"{skill_name} not embedded in spec (skills by name?)")
