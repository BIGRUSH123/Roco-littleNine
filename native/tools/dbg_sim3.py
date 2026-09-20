"""dbg_sim3 — 只在搜索 3（B[1]）窗口内插桩 py 的 abnormal/damage 来源。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_sim3_log.txt", "w", encoding="utf-8")


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

import numpy as np  # noqa: E402

from gate_phase5 import py_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
import backend.vm.ops.abnormal as _abn_mod  # noqa: E402
import backend.sim.sprite as _sprite_mod  # noqa: E402

state = {"search": -1, "active": False}

_orig_abn = _abn_mod.op_abnormal


def op_abnormal(ctx, effect):
    ms = _orig_abn(ctx, effect)
    if state["active"]:
        if isinstance(effect, dict):
            desc = {k: effect.get(k) for k in ("name", "target", "stacks", "value", "scope")}
        else:
            desc = repr(effect)[:120]
        print(f"[abn] effect={desc} -> {ms}", flush=True)
    return ms


_abn_mod.op_abnormal = op_abnormal

# executor 引用的是 from .ops.abnormal import op_abnormal —— 补丁 executor 的引用
import backend.vm.executor as _exec_mod  # noqa: E402

_exec_mod.op_abnormal = op_abnormal

_orig_td = _sprite_mod.Sprite.take_damage


def take_damage(self, amount, *a, **k):
    if state["active"]:
        print(f"[dmg] {self.name} -{amount} (hp {self.current_hp} -> "
              f"{self.current_hp - amount})", flush=True)
    return _orig_td(self, amount, *a, **k)


_sprite_mod.Sprite.take_damage = take_damage

_orig_dirichlet = np.random.dirichlet


def dirichlet(alpha, size=None):
    state["search"] += 1
    state["active"] = state["search"] == 3
    if state["active"]:
        print(f"════ 进入搜索 3（B[1]）════", flush=True)
    return _orig_dirichlet(alpha, size)


np.random.dirichlet = dirichlet


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"py turns={py['turns']} winner={py['winner']}", flush=True)


if __name__ == "__main__":
    main()
