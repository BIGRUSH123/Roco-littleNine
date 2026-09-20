"""mask_gate — 阶段4 合法动作掩码对拍（py get_valid_actions vs rust mcts_actions）。

用法：env\\python.exe native/tools/mask_gate.py [spec_dir] [--from N] [--to N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

from backend.engine.ai.core.mcts import get_valid_actions  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402


def run_python_masks(spec: dict):
    random.seed(spec["seed"] + 1)
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    masks = []
    while not battle.is_finished:
        _, ma = get_valid_actions(battle.player_a, battle)
        _, mb = get_valid_actions(battle.player_b, battle)
        masks.append([ma.tolist(), mb.tolist()])
        battle.execute_turn(a, b)
    return masks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec_dir", nargs="?", default=str(ROOT / "native" / "gate_specs"))
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=10**9)
    args = ap.parse_args()

    specs = sorted(Path(args.spec_dir).glob("spec_*.json"))
    total = passed = failed = 0
    bad = []
    for sp in specs:
        seed_no = int(sp.stem.split("_")[1])
        if not (args.lo <= seed_no <= args.hi):
            continue
        total += 1
        spec = json.loads(sp.read_text(encoding="utf-8"))
        py_masks = run_python_masks(spec)
        result = json.loads(roco_engine.py_run_battle_masks(json.dumps(spec, ensure_ascii=False)))
        ru_masks = result["masks"]
        ok = True
        reason = ""
        if len(py_masks) != len(ru_masks):
            ok = False
            reason = f"len py={len(py_masks)} ru={len(ru_masks)}"
        else:
            for t, (pm, rm) in enumerate(zip(py_masks, ru_masks)):
                for side in (0, 1):
                    a = [round(x) for x in pm[side]]
                    b = [round(x) for x in rm[side]]
                    if a != b:
                        ok = False
                        reason = f"t{t} side={'AB'[side]} py={a} ru={b}"
                        break
                if not ok:
                    break
        if ok:
            passed += 1
        else:
            failed += 1
            bad.append(sp.name)
            print(f"FAIL {sp.name}: {reason}")
    print(f"── 掩码对拍 {total}：通过 {passed}，失败 {failed} ──")
    if bad:
        print("失败：", ", ".join(bad[:20]))


if __name__ == "__main__":
    main()
