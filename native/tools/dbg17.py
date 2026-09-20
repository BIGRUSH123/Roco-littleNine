"""临时调试17：追踪 post_enemy_leave 触发细节。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import backend.engine.battle as eb  # noqa: E402

orig_fire = eb.BattleVMEngine._fire_post_event


def patched_fire(self, trigger, ctx, replayer):
    if trigger == "post_enemy_leave":
        owner = None
        if replayer.self is not None:
            owner = f"{replayer.self.name}"
        fired = []
        for obs in self.registry._observers:
            if "post_enemy_leave" in obs.listen:
                fired.append((obs.name or obs.source, obs.owner_sprite_id is not None,
                              list(obs.listen)))
        print(f"[py fire] {trigger} self={owner} candidates={len(fired)}")
        for item in fired[:6]:
            print(f"    candidate: {item}")
    return orig_fire(self, trigger, ctx, replayer)


eb.BattleVMEngine._fire_post_event = patched_fire

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while battle.turn < 13 and not battle.is_finished:
    battle.execute_turn(a, b)
    if battle.turn in (11, 12):
        a2 = battle.player_a.team[2]
        print(f"=== after turn {battle.turn}: A2={a2.name} hp={a2.current_hp} "
              f"energy={a2.energy} mod_keys={sorted(a2._modifiers.keys())}")
