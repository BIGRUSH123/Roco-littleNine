"""dbg_probe64 — 追踪 spec 0064 中花魂蜂后(待席)的 trait 效果重排时机。

在 py MCTS（mcts_gate.run_python）期间钩住：
  - trait_loader.load_for_sprite / unload_for_sprite
  - Sprite.clear_effects / add_effect / remove_effect
  - traits.dispatch_entry / dispatch_leave
只打印涉及 花魂蜂后 或其效果名(虫群鼓舞/atk/物攻) 的调用，按序输出到文件。
用法：env\\python.exe native/tools/dbg_probe64.py <spec_no> [out.txt]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "_probe64.txt"
_spec_no = int(sys.argv[1]) if len(sys.argv) > 1 else 64

import io

import backend.sim.traits as traits_mod
import backend.sim.traits.__init__ as traits_pkg
from backend.engine.trait_loader import TraitLoader
from backend.sim.sprite import Sprite

_lines: list[str] = []


def log(msg: str) -> None:
    _lines.append(msg)


def _fx_summary(sprite) -> str:
    try:
        return ",".join(getattr(e, "name", str(e)) for e in (sprite.active_effects or []))
    except Exception:
        return "<n/a>"


WATCH = {"花魂蜂后", "虫群鼓舞", "atk", "物攻"}


def _relevant(*names) -> bool:
    return any(n in WATCH for n in names if isinstance(n, str))


# ── trait_loader ──
_orig_load = TraitLoader.load_for_sprite
_orig_unload = TraitLoader.unload_for_sprite


def load_patched(self, sprite, *, apply_state=True):
    before = _fx_summary(sprite)
    log(f"load_for_sprite name={sprite.name} apply_state={apply_state} before=[{before}]")
    r = _orig_load(self, sprite, apply_state=apply_state)
    log(f"  after=[{_fx_summary(sprite)}]")
    return r


def unload_patched(self, sprite, reason="leave"):
    log(f"unload_for_sprite name={sprite.name} reason={reason} before=[{_fx_summary(sprite)}]")
    return _orig_unload(self, sprite, reason)


TraitLoader.load_for_sprite = load_patched
TraitLoader.unload_for_sprite = unload_patched

# ── Sprite effects ──
_orig_clear = Sprite.clear_effects
_orig_add = Sprite.add_effect
_orig_remove = Sprite.remove_effect


def clear_patched(self, scope):
    if _relevant(self.name) or True:
        log(f"clear_effects name={self.name} scope={scope} before=[{_fx_summary(self)}]")
    return _orig_clear(self, scope)


def add_patched(self, effect):
    ename = getattr(effect, "name", str(effect))
    if _relevant(self.name, ename):
        log(f"add_effect name={self.name} effect={ename} before=[{_fx_summary(self)}]")
    return _orig_add(self, effect)


def remove_patched(self, name, category=""):
    if _relevant(self.name, name):
        log(f"remove_effect name={self.name} effect={name} cat={category}")
    return _orig_remove(self, name, category)


Sprite.clear_effects = clear_patched
Sprite.add_effect = add_patched
Sprite.remove_effect = remove_patched

# ── dispatch entry/leave ──
_orig_entry = traits_pkg.dispatch_entry
_orig_leave = traits_pkg.dispatch_leave


def entry_patched(sprite, battle, team):
    log(f"dispatch_entry team={team} name={sprite.name} before=[{_fx_summary(sprite)}]")
    r = _orig_entry(sprite, battle, team)
    log(f"  after=[{_fx_summary(sprite)}]")
    return r


def leave_patched(sprite, battle, team, is_faint=False):
    log(f"dispatch_leave team={team} name={sprite.name} faint={is_faint}")
    return _orig_leave(sprite, battle, team, is_faint)


traits_pkg.dispatch_entry = entry_patched
traits_pkg.dispatch_leave = leave_patched

# resolve_switch 也会调用 dispatch_*，经 traits_pkg 转发即可

import mcts_gate as G  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / f"spec_{_spec_no:04d}.json").read_text("utf-8"))
cfg = dict(G.CFG)
cfg["num_simulations"] = 24

# sim 边界探针：包在 mcts_gate 的 save 钩子外层
from backend.sim.battle import Battle  # noqa: E402

_orig_save_battle = Battle.save_mutable_state


def save_probe(self):
    log(f"=== sim save (turn={self.turn}) ===")
    return _orig_save_battle(self)


Battle.save_mutable_state = save_probe

py = G.run_python(spec, cfg)

for i, acts in enumerate(py["trace"]):
    log(f"sim {i} A动作: {acts}")

# 每个 sim 的 step0 时 sprites[0] 顺序
for i, steps in enumerate(py["digest_trace"]):
    if steps:
        fx = steps[0]["d"]["players"][0]["sprites"][0]["effects"]
        log(f"sim {i} step0 digest sprites[0] effects: {[e[0] for e in fx]}")

OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
print(f"written {OUT} lines={len(_lines)}")
