"""dbg_eff_rm — 追踪指定精灵的效果移除来源。

用法：env\\python.exe native/tools/dbg_eff_rm.py <spec> <turn> <名字片段>
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
from backend.sim.sprite import Sprite  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
turn = int(sys.argv[2])
tip = sys.argv[3]

orig_dec = Sprite.decrement_ttl
orig_clear = Sprite.clear_effects
orig_remove = Sprite.remove_effect


def dec(self):
    removed = orig_dec(self)
    for e in removed:
        if tip in str(e.name):
            print(f"[ttl-expire] {self.name} {e.name} (turn={getattr(self, '_dbg_turn', '?')})",
                  flush=True)
    return removed


def clear(self, scope):
    before = [e.name for e in self.active_effects if tip in str(e.name)]
    out = orig_clear(self, scope)
    after = [e.name for e in self.active_effects if tip in str(e.name)]
    if before and not after:
        print(f"[clear_effects] {self.name} scope={scope} 移除 {before}", flush=True)
    return out


def remove(self, name, category=""):
    had = any(e.name == name and tip in str(e.name) for e in self.active_effects)
    out = orig_remove(self, name, category)
    if had:
        print(f"[remove_effect] {self.name} name={name} cat={category}", flush=True)
    return out


Sprite.decrement_ttl = dec
Sprite.clear_effects = clear
Sprite.remove_effect = remove

# trait_loader 的两处直赋（load_for_sprite / unload_for_sprite）
from backend.engine import trait_loader as TL  # noqa: E402

orig_unload = TL.TraitLoader.unload_for_sprite


def unload(self, sprite, reason="leave"):
    before = [(e.name, e.scope) for e in sprite.active_effects if tip in str(e.name)]
    out = orig_unload(self, sprite, reason)
    after = [e.name for e in sprite.active_effects if tip in str(e.name)]
    if before and not after:
        print(f"[trait_unload] {sprite.name} reason={reason} 移除 {before}", flush=True)
    return out


orig_load = TL.TraitLoader.load_for_sprite


def load(self, sprite, *, apply_state=True):
    before = [(e.name, e.scope) for e in sprite.active_effects if tip in str(e.name)]
    out = orig_load(self, sprite, apply_state=apply_state)
    after = [e.name for e in sprite.active_effects if tip in str(e.name)]
    if before and not after:
        print(f"[trait_load] {sprite.name} apply_state={apply_state} 移除 {before}", flush=True)
    return out


TL.TraitLoader.unload_for_sprite = unload
TL.TraitLoader.load_for_sprite = load

random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
print("done turn", battle.turn)
for label, player in (("A", battle.player_a), ("B", battle.player_b)):
    for i, s in enumerate(player.team):
        hits = [e for e in s.active_effects if tip in str(e.name)]
        if hits:
            print(f"{label}[{i}] {s.name}: {[(e.name, e.ttl) for e in hits]}")
