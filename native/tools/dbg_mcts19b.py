"""dbg_mcts19b - log py energy writes per sprite during MCTS sims (spec_0019)."""

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

from backend.sim.sprite import Sprite  # noqa: E402

import mcts_gate  # noqa: E402
from mcts_gate import CFG, run_python  # noqa: E402

LOG = []
_cur = {"sim": -1}


def _install() -> None:
    def getter(self):
        return self.__dict__.get("_energy_logged", 0)

    def setter(self, v):
        old = self.__dict__.get("_energy_logged", 0)
        if old != v:
            frames = []
            f = sys._getframe(1)
            for _ in range(4):
                if f is None:
                    break
                frames.append(f"{f.f_code.co_name}:{f.f_lineno}")
                f = f.f_back
            fr = sys._getframe(2) if sys._getframe(2) is not None else sys._getframe(1)
            LOG.append((_cur["sim"], getattr(self, "name", "?"), old, v,
                        " < ".join(frames), 0))
        self.__dict__["_energy_logged"] = v

    Sprite.energy = property(getter, setter)


_install()

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0019.json").read_text(encoding="utf-8"))
CFG["num_simulations"] = 3

# 记录每次 save_mutable_state → 新仿真序号
orig_save = mcts_gate.Battle.save_mutable_state


def save_patched(self):
    _cur["sim"] += 1
    return orig_save(self)


mcts_gate.Battle.save_mutable_state = save_patched

py = run_python(spec, CFG)
print("trace:", py["trace"])
print("--- energy 写日志（变化项）---")
for rec in LOG:
    sim, name, old, new, fn, ln = rec
    if "幻影灵蕈" in name or True:
        print(f"  sim={sim} {name} {old}->{new} @{fn}:{ln}")
