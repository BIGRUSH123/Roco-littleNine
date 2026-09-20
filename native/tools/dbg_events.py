"""dbg_events — 打印 py 对局某回合之后的事件串（定位 lives 双扣）。

用法：env\\python.exe native/tools/dbg_events.py <spec_path> <turn>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402


def main() -> None:
    spec_path = sys.argv[1]
    target_turn = int(sys.argv[2])
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    random.seed(spec["seed"] + 1)
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    while not battle.is_finished and battle.turn < target_turn:
        rec = battle.execute_turn(a, b)
        evs = list(rec.turn_start_events) + list(rec.faint_check_events) + list(rec.turn_end_events)
        print(f"── turn {battle.turn} ({len(evs)} events) "
              f"lives=({battle.player_a.lives},{battle.player_b.lives}) "
              f"hp_a={[s.current_hp for s in battle.player_a.team]} ──")
        if battle.turn >= target_turn - 2:
            for e in evs:
                print("   ", e)
            for label, ar in (("A", rec.action_a), ("B", rec.action_b)):
                for e in (getattr(ar, "events", None) or []):
                    print(f"   [{label}] {e}")
    print("lives after t", battle.turn, ":",
          battle.player_a.lives, battle.player_b.lives, "winner:", battle.winner)
    print("lives:", battle.player_a.lives, battle.player_b.lives,
          "winner:", battle.winner)


if __name__ == "__main__":
    main()
