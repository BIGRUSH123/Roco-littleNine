"""dbg_skill_src - compare spec-embedded skill effects vs data/skills/<name>.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

spec = json.load(open("native/gate_specs/spec_0037.json", encoding="utf-8"))

for pi, name in ((0, "吓退"), (1, "精神扰乱")):
    for ss in spec["players"][pi]["sprites"][0]["skills"]:
        if ss["name"] == name:
            print(f"== spec embed {name} keys={list(ss.keys())}")
            print(json.dumps(ss.get("effects"), ensure_ascii=False, indent=1)[:2500])
    disk_path = f"data/skills/{name}.json"
    disk = json.load(open(disk_path, encoding="utf-8"))
    print(f"== disk {name} keys={list(disk.keys()) if isinstance(disk, dict) else type(disk)}")
    print(json.dumps(disk, ensure_ascii=False, indent=1)[:2500])
    print()
