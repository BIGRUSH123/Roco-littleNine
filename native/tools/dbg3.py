"""临时调试2：重算 强制重启 的期望伤害。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
a0 = spec["players"][0]["sprites"][0]
b2 = spec["players"][1]["sprites"][2]
print("A0:", a0["name"], "elements:", a0["attributes"], "initial:", a0["initial_stats"])
print("A0 skills:", [s["name"] for s in a0["skills"]])
print("B2:", b2["name"], "elements:", b2["attributes"], "initial:", b2["initial_stats"])
print("B2 skills:", [s["name"] for s in b2["skills"]])
for s in a0["skills"]:
    if s["name"] == "强制重启":
        print("强制重启:", json.dumps(s, ensure_ascii=False)[:400])
for s in b2["skills"]:
    if s["name"] == "回旋踢":
        print("回旋踢:", json.dumps(s, ensure_ascii=False)[:400])
