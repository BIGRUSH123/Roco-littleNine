"""临时调试14：导出 spec 中 B0 精灵的全部技能定义。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
ss = spec["players"][1]["sprites"][0]
out = {"ability": ss.get("ability"), "skills": ss.get("skills")}
Path("native/tools/dbg_b0_skills.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("sprites[0] name bytes-safe dump written; skill count:", len(ss.get("skills", [])))
for i, sk in enumerate(ss.get("skills", [])):
    print(i, json.dumps(sk.get("name"), ensure_ascii=False), "power:", sk.get("power"),
          "type:", sk.get("skill_type"), "has_hit_op:", any(
              (e.get("op") == "hit") for e in sk.get("effects", [])))
