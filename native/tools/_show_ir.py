"""临时：打印某技能编译后的 IR。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.vm.compiler.skill_compiler import SkillCompiler

name = sys.argv[1]
data = json.loads((ROOT / "data" / "skills" / f"{name}.json").read_text(encoding="utf-8"))
rec = SkillCompiler().compile(data)
print(type(rec).__name__)
for i, op in enumerate(rec.effects):
    print(i, type(op).__name__, repr(op)[:300])
