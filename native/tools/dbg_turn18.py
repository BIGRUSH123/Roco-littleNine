"""dbg_turn18 — 回合18末分歧聚焦调试（v2）。

仅记录真实对局（过滤 _mcts_sim），日志由 python 直接写 UTF-8 文件，
再对 py/rust 的 digest 做全量 diff（不只首个差异）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_turn18_log.txt", "w", encoding="utf-8")


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

import torch  # noqa: E402

from gate_phase5 import _RustEvalAdapter, py_game, rust_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.engine.ai.train import MCTSAgent  # noqa: E402
from backend.sim.battle_mechanics import BattleMechanicsMixin  # noqa: E402

TURN_GATE = 14  # 只记录 turn>=该值的真实对局日志

# ── py 插桩（真实对局专用） ──────────────────────────────────────────
_orig_repl = MCTSAgent.choose_replacement


def _repl(self, battle):
    r = _orig_repl(self, battle)
    if not getattr(battle, "_mcts_sim", False) and battle.turn >= TURN_GATE:
        p = self.player
        bench = [(i, s.name, s.current_hp, bool(s.is_fainted)) for i, s in enumerate(p.team)]
        print(f"[py-repl] turn={battle.turn} team={self.team} active={p.active_index} "
              f"ret={r} team={bench}", flush=True)
    return r


MCTSAgent.choose_replacement = _repl

_orig_cf = BattleMechanicsMixin._check_faint_interrupt


def _cf(self, team, events):
    sim = getattr(self, "_mcts_sim", False)
    if not sim and self.turn >= TURN_GATE:
        pi = self.player_a if team == "A" else self.player_b
        act = pi.active
        print(f"[py-faint] >> turn={self.turn} team={team} active={act.name} "
              f"hp={act.current_hp}/{act.max_hp} lives={pi.lives} winner={self.winner}",
              flush=True)
    _orig_cf(self, team, events)
    if not sim and self.turn >= TURN_GATE:
        pi = self.player_a if team == "A" else self.player_b
        print(f"[py-faint] << turn={self.turn} team={team} active_idx={pi.active_index} "
              f"lives={pi.lives} winner={self.winner}", flush=True)


BattleMechanicsMixin._check_faint_interrupt = _cf

_orig_sw = BattleMechanicsMixin._resolve_switch


def _sw(self, team, action, *a, **k):
    sim = getattr(self, "_mcts_sim", False)
    if not sim and self.turn >= TURN_GATE:
        pi = self.player_a if team == "A" else self.player_b
        old = pi.active.name
    else:
        old = None
    evs = _orig_sw(self, team, action, *a, **k)
    if old is not None:
        pi = self.player_a if team == "A" else self.player_b
        print(f"[py-sw] turn={self.turn} team={team} {old} -> {pi.active.name} "
              f"idx={pi.active_index}", flush=True)
    return evs


BattleMechanicsMixin._resolve_switch = _sw

_orig_item = BattleMechanicsMixin._resolve_item


def _item(self, team):
    sim = getattr(self, "_mcts_sim", False)
    if not sim and self.turn >= TURN_GATE:
        pi = self.player_a if team == "A" else self.player_b
        print(f"[py-item] >> turn={self.turn} team={team} idx={pi.active_index}", flush=True)
    r = _orig_item(self, team)
    if not sim and self.turn >= TURN_GATE:
        print(f"[py-item] << turn={self.turn} team={team} r={r} idx={pi.active_index}",
              flush=True)
    return r


BattleMechanicsMixin._resolve_item = _item


# ── 全量 diff ────────────────────────────────────────────────────────
def all_diffs(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k} rust-only={b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k} py-only={a[k]!r}")
            else:
                out += all_diffs(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} 长度 py={len(a)} rust={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out += all_diffs(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: py={a!r} rust={b!r}")
    return out


def digest_summary(d):
    if isinstance(d, str):
        d = json.loads(d)
    lines = [f"turn={d['turn']} winner={d['winner']}"]
    for nm, p in zip("AB", d["players"]):
        lines.append(f"  {nm}: lives={p['lives']} active={p['active_index']}")
        for i, s in enumerate(p["sprites"]):
            eff = ";".join(f"{e[0]}({e[2]})" for e in s["effects"] if e[0])
            lines.append(f"    [{i}] {s['name']} hp={s['hp']}/{s['max_hp']} en={s['energy']}"
                         + (f" eff={eff}" if eff else ""))
    return "\n".join(lines)


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1

    print("═" * 28 + " PY " + "═" * 28, flush=True)
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"py: turns={py['turns']} winner={py['winner']} lives={py['lives']} "
          f"active={py['active']} a_len={py['a_len']}", flush=True)
    print("py log_tail:", flush=True)
    for x in py["log_tail"]:
        print("   ", x, flush=True)

    print("═" * 28 + " RUST " + "═" * 28, flush=True)
    adapter = _RustEvalAdapter(torch_ev)
    ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)
    print(f"rust: turns={ru['turns']} winner={ru['winner']} lives={ru['lives']} "
          f"active={ru['active']}", flush=True)

    py_dig = py["turn_digests"]
    ru_dig = json.loads(ru["digests"]) if isinstance(ru["digests"], str) else ru["digests"]
    n = min(len(py_dig), len(ru_dig))
    print("═" * 24 + f" digest 摘要（py={len(py_dig)} rust={len(ru_dig)}） " + "═" * 24,
          flush=True)
    for t in range(max(0, n - 5), n):
        print(f"── turn {t} py:", flush=True)
        print(digest_summary(py_dig[t]), flush=True)
        print(f"── turn {t} rust:", flush=True)
        print(digest_summary(ru_dig[t]), flush=True)
        ds = all_diffs(py_dig[t], ru_dig[t])
        if ds:
            print(f"  ✗ turn {t} 共 {len(ds)} 处差异：", flush=True)
            for d in ds[:60]:
                print("    ", d, flush=True)
            if len(ds) > 60:
                print(f"    …（其余 {len(ds) - 60} 条略）", flush=True)
        else:
            print(f"  ✓ turn {t} 一致", flush=True)


if __name__ == "__main__":
    main()
