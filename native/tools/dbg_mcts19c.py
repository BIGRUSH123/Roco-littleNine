"""dbg_mcts19c - log which observer trigger produces Steal mutations (spec_0019)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "native" / "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.battle import BattleVMEngine  # noqa: E402
from backend.engine.replayer import JournalReplayer  # noqa: E402

import mcts_gate  # noqa: E402
from mcts_gate import CFG, run_python  # noqa: E402

CTX = {"trigger": "?"}
_cur = {"sim": -1}

orig_fire = BattleVMEngine._fire_post_event
orig_replay = JournalReplayer.replay


def fire_patched(self, trigger, ctx, replayer):
    prev = CTX["trigger"]
    CTX["trigger"] = trigger
    try:
        return orig_fire(self, trigger, ctx, replayer)
    finally:
        CTX["trigger"] = prev


def replay_patched(self, journal):
    types = [type(m).__name__ for m in journal]
    if any("Steal" in t for t in types):
        who = getattr(self.self, "name", "?")
        opp = getattr(self.opp, "name", "?")
        print(f"  [steal] sim={_cur['sim']} trigger={CTX['trigger']} self={who} opp={opp} journal={types}")
    return orig_replay(self, journal)


BattleVMEngine._fire_post_event = fire_patched
JournalReplayer.replay = replay_patched

orig_save = mcts_gate.Battle.save_mutable_state


def save_patched(self):
    _cur["sim"] += 1
    return orig_save(self)


mcts_gate.Battle.save_mutable_state = save_patched

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0019.json").read_text(encoding="utf-8"))
CFG["num_simulations"] = 3
py = run_python(spec, CFG)
print("trace:", py["trace"])
