"""dbg_py_eff — 打印某回合结束时指定精灵的效果对象明细。

用法：env\\python.exe native/tools/dbg_py_eff.py <spec_id> <turn> <A|B> <idx> [输出文件]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

sid, turn_s = sys.argv[1], sys.argv[2]
pi = 0 if sys.argv[3].upper() == "A" else 1
si = int(sys.argv[4])
turn = int(turn_s)

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)

sprite = (battle.player_a if pi == 0 else battle.player_b).team[si]
lines = [f"== {sprite.name} after t{turn}", f"modifiers={dict(sorted(sprite._modifiers.items()))}"]
for e in sprite.active_effects:
    kind = type(e).__name__
    extra = ""
    if hasattr(e, "stat_key"):
        extra = f" stat_key={e.stat_key!r} display_mult={getattr(e, 'display_mult', None)} display_value={getattr(e, 'display_value', None)}"
    lines.append(f"  {kind} name={e.name!r} scope={e.scope} steps={getattr(e, 'steps', None)} stacks={getattr(e, 'stacks', None)} source={e.source!r}{extra}")
text = "\n".join(lines)
if len(sys.argv) > 5:
    Path(sys.argv[5]).write_text(text, encoding="utf-8")
    print("written")
else:
    print(text)
