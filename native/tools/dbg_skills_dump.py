"""dbg_skills_dump — 打印某回合双方某精灵的全技能（名+修饰符）对比。

用法：env\\python.exe native/tools/dbg_skills_dump.py <spec> <player:A|B> <idx> <turn_index>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
t = int(sys.argv[4])

py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]

ps = py_digests[t]["players"][pi]["sprites"][si]
rs = rust_digests[t]["players"][pi]["sprites"][si]
print(f"=== t{t} {ps['name']} ===")
for i, (a, b) in enumerate(zip(ps["skills"], rs["skills"])):
    flag = "  <<<" if a != b else ""
    print(f"  [{i}] py {a['name']:<8} mods={a['modifiers']}{flag}")
    print(f"      ru {b['name']:<8} mods={b['modifiers']}")
