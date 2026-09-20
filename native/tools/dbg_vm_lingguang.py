"""dbg_vm_probe — 直接用 py VM 执行指定技能，打印 journal。

用法：env\\python.exe native/tools/dbg_vm_probe.py <技能名> <cond_flag=1|0>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.vm_engine import BattleVMEngine  # noqa: E402
from backend.engine.snapshot import build_ctx  # noqa: E402
from backend.vm.journal import ModifierInjection  # noqa: E402

skill_name = sys.argv[1]
flag = sys.argv[2] == "1" if len(sys.argv) > 2 else True

skill_json = json.loads((ROOT / "data" / "skills" / f"{skill_name}.json").read_text(encoding="utf-8"))
print("effects:", json.dumps(skill_json.get("effects"), ensure_ascii=False))

# 最小 ctx
from backend.vm.ctx import Ctx, EventContext  # noqa: E402

ctx = Ctx()
ctx.event = EventContext()
ctx.event.opp_switched = flag
ctx.skill_index = 0

engine = BattleVMEngine(data_dir=str(ROOT / "data"))
# 技能效果直接喂原始 JSON（模拟 compiled 输入）
try:
    journal = engine.execute_effects(ctx, skill_json["effects"])
    print("journal:", journal)
except Exception as e:
    print("execute_effects failed:", type(e).__name__, e)
    # 回退：process_effects
    from backend.vm.executor import process_effects
    journal = process_effects(ctx, skill_json["effects"])
    print("journal(process):", journal)
