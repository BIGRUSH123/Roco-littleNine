"""gate_encoder — 阶段4-5 编码器对拍门。

py encode_battle_state vs rust py_run_battle_encoded：RuleAgent 同轨迹
（种子协议与引擎门一致），逐回合比较 8 个实体数组（ast_tokens/ast_values
在 4-5b 接入前两侧都为 0，跳过不比）。双视角（A/B）各跑一遍。

用法：env\\python.exe native/tools/gate_encoder.py [spec_dir] [--from N] [--to N] [--show N]
报告写 native/tools/_gate_encoder_last.txt（UTF-8，避免管道乱码）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.engine.ai.core.encoder import encode_battle_state  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

ENCODING_KEYS = [
    "sprite_stats",
    "sprite_elements",
    "sprite_states",
    "skill_stats",
    "skill_elements",
    "skill_states",
    "global_stats",
    "global_elements",
    "ast_tokens",
    "ast_values",
]


def run_python(spec: dict, perspective: str):
    random.seed(spec["seed"] + 1)
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    encs = [encode_battle_state(battle, perspective=perspective)]
    while not battle.is_finished:
        battle.execute_turn(a, b)
        encs.append(encode_battle_state(battle, perspective=perspective))
    return encs, battle.winner


def first_diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} 键缺失"
            r = first_diff(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} 长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = first_diff(x, y, f"{path}[{i}]")
            if r:
                return r
        return ""
    if a != b:
        return f"{path}: py={a!r} rust={b!r}"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec_dir", nargs="?", default=str(ROOT / "native" / "gate_specs"))
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=10**9)
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    specs = sorted(Path(args.spec_dir).glob("spec_*.json"))
    total = passed = failed = 0
    bad: list[str] = []
    out: list[str] = []
    shown = 0
    for sp in specs:
        seed_no = int(sp.stem.split("_")[1])
        if not (args.lo <= seed_no <= args.hi):
            continue
        total += 1
        spec = json.loads(sp.read_text(encoding="utf-8"))
        spec_json = json.dumps(spec, ensure_ascii=False)
        diffs = []
        for perspective in ("A", "B"):
            py_encs, py_winner = run_python(spec, perspective)
            ru = json.loads(
                roco_engine.py_run_battle_encoded(spec_json, 0 if perspective == "A" else 1, False)
            )
            if py_winner != (ru["winner"] or None):
                diffs.append(f"[{perspective}] 胜者 py={py_winner} rust={ru['winner']}")
                continue
            if len(py_encs) != len(ru["encodings"]):
                diffs.append(f"[{perspective}] 回合数 py={len(py_encs)} rust={len(ru['encodings'])}")
                continue
            for ti, (pe, re_) in enumerate(zip(py_encs, ru["encodings"])):
                py_arrs = {k: pe[k].tolist() for k in ENCODING_KEYS}
                ru_arrs = {k: re_[k] for k in ENCODING_KEYS}
                d = first_diff(py_arrs, ru_arrs, f"turn{ti}")
                if d:
                    diffs.append(f"[{perspective}] {d}")
                    break
        if not diffs:
            passed += 1
        else:
            failed += 1
            bad.append(sp.name)
            if shown < args.show:
                shown += 1
                out.append(f"FAIL {sp.name}")
                for d in diffs:
                    out.append("    " + d[:400])
    out.append(f"── 编码器对拍 {total}：通过 {passed}，失败 {failed} ──")
    if bad:
        out.append("失败：" + ", ".join(bad))
    text = "\n".join(out)
    (Path(__file__).parent / "_gate_encoder_last.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
