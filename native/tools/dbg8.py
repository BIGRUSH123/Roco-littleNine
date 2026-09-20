"""临时调试6：编译候选技能，打印 abnormal op 的 scope。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.vm.compiler.skill_compiler import SkillCompiler  # noqa: E402

compiler = SkillCompiler()
for name in ["毒液渗透", "藤鞭", "腐蚀酸液", "落井下石", "摇摇铃", "虫鸣", "假寐", "复写", "折射"]:
    p = Path("data/skills") / f"{name}.json"
    if not p.exists():
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    try:
        rec = compiler.compile(data)
    except Exception as e:
        print(name, "compile error:", e)
        continue
    for op in rec.effects:
        if type(op).__name__ == "AbnormalOp":
            print(f"{name}: AbnormalOp name={op.name!r} stacks={op.stacks} scope={op.scope!r} value={op.value!r}")
