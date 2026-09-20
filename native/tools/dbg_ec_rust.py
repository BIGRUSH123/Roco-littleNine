"""dbg_ec_rust — rust 侧逐回合打印指定精灵技能 energy_cost 修饰符。

用法：env\\python.exe native/tools/dbg_ec_rust.py <spec> <player:A|B> <idx> <to_turn>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

spec = json.loads((ROOT / sys.argv[1]).resolve().read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
to = int(sys.argv[4])
r = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
for t in range(0, min(to, len(r["turns"]) - 1) + 1):
    s = r["turns"][t]["players"][pi]["sprites"][si]
    mods = [(i, sk["name"], sk["modifiers"]) for i, sk in enumerate(s["skills"]) if sk["modifiers"]]
    print(f"t{t}: hp={s['hp']} sprite_ec={s['modifiers'].get('energy_cost')}", mods)
