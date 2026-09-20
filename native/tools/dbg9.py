"""临时调试7：编译全部技能，找 scope=persistent 的中毒 op。"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.vm.compiler.skill_compiler import SkillCompiler  # noqa: E402

compiler = SkillCompiler()
found = []


def walk_ops(ops, path):
    for op in ops:
        if type(op).__name__ == "AbnormalOp" and op.name == "中毒" and op.scope == "persistent":
            found.append((path, op.stacks))
        for attr in ("then", "else_", "elif_"):
            sub = getattr(op, attr, None)
            if sub is None:
                continue
            if isinstance(sub, tuple):
                walk_ops(sub, f"{path}.{attr}")
                continue
            try:
                iter(sub)
            except TypeError:
                continue
            for b in sub:
                bt = getattr(b, "then", None)
                if bt is not None:
                    walk_ops(bt, f"{path}.{attr}.then")


for p in sorted(glob.glob("data/skills/*.json")) + sorted(glob.glob("data/traits/*.json")):
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        rec = compiler.compile(data)
    except Exception:
        continue
    walk_ops(rec.effects, str(Path(p).name))
for p, s in found:
    print(p, "stacks=", s)
print("total:", len(found))
