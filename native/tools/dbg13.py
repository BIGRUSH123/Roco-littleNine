"""临时调试13：打印叶绿光幕技能定义。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
skill_name = None
for ps in spec["players"]:
    for ss in ps["sprites"]:
        for sk in ss["skills"]:
            if sk["name"] == "叶绿光幕":
                skill_name = sk["name"]
                skill_file = Path("data/skills") / f"{skill_name}.json"
                data = json.loads(skill_file.read_text(encoding="utf-8"))
                print(json.dumps(data, ensure_ascii=False, indent=1))
                break

