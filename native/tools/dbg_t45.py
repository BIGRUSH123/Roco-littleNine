"""dbg_t45 — 回合45 双亡终局：_check_faint_interrupt 内部数据全记录。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_t45_log.txt", "w", encoding="utf-8")


class _Tee:
    def __init__(self, *streams):
        self._s = streams

    def write(self, s):
        for x in self._s:
            x.write(s)

    def flush(self):
        for x in self._s:
            x.flush()


sys.stdout = _Tee(sys.stdout, _logf)

from gate_phase5 import py_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.sim.battle_mechanics import BattleMechanicsMixin  # noqa: E402

_orig_cf = BattleMechanicsMixin._check_faint_interrupt


def _cf(self, team, events):
    if self.turn >= 44:
        pi = self.player_a if team == "A" else self.player_b
        agent = self._get_agent(team)
        act = pi.active
        bench = [(i, s.name, s.current_hp, bool(s.is_fainted))
                 for i, s in enumerate(pi.team)]
        sim = getattr(self, "_mcts_sim", False)
        print(f"[cf] >> turn={self.turn} team={team} sim={sim} "
              f"agent={type(agent).__name__} player_is_engine={agent.player is pi} "
              f"active={act.name} hp={act.current_hp} lives={pi.lives} winner={self.winner}",
              flush=True)
        print(f"[cf]     team={bench}", flush=True)
        if not sim:
            cls = type(agent)
            orig_cr = cls.choose_replacement

            def cr(self, btl, _o=orig_cr):
                r = _o(self, btl)
                print(f"[cf]     choose_replacement -> {r}", flush=True)
                return r

            cls.choose_replacement = cr
        try:
            _orig_cf(self, team, events)
        finally:
            if not sim:
                cls.choose_replacement = orig_cr
        print(f"[cf] << turn={self.turn} team={team} active_idx={pi.active_index} "
              f"lives={pi.lives} winner={self.winner}", flush=True)
        return
    _orig_cf(self, team, events)


BattleMechanicsMixin._check_faint_interrupt = _cf


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"py turns={py['turns']} winner={py['winner']} lives={py['lives']}", flush=True)


if __name__ == "__main__":
    main()
