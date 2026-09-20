"""dbg_vm_probe — 直接用 py VM process_effects 执行指定技能效果，打印 journal。

用法：env\\python.exe native/tools/dbg_vm_probe.py <技能名> <opp_switched=1|0>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.vm.executor import process_effects  # noqa: E402
from backend.vm.ctx import Ctx, EventContext  # noqa: E402

skill_name = sys.argv[1]
flag = (sys.argv[2] == "1") if len(sys.argv) > 2 else True

skill_json = json.loads((ROOT / "data" / "skills" / f"{skill_name}.json").read_text(encoding="utf-8"))
print("effects:", json.dumps(skill_json.get("effects"), ensure_ascii=False))

ctx = Ctx()
ctx.event = EventContext()
ctx.event.opp_switched = flag
ctx.skill_index = 0

journal = process_effects(ctx, skill_json["effects"])
for m in journal:
    print("  ", type(m).__name__, m)
