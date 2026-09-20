"""dbg_lives — 每回合 lives/active/hp 轨迹对比（spec_0001 t20+ 分歧定位）。

用法：env\\python.exe native/tools/dbg_lives.py <spec_path> [from_turn]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402


def main() -> None:
    spec_path = sys.argv[1]
    from_turn = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    py_digests, _ = run_python(spec)
    result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    rust_digests = result["turns"]
    for pd, rd in zip(py_digests, rust_digests):
        t = pd.get("turn")
        if t < from_turn:
            continue
        pa = pd["players"][0]
        ra = rd["players"][0]
        pb = pd["players"][1]
        rb = rd["players"][1]
        ph = []
        for ps, rs in zip(pd["players"], rd["players"]):
            tag = ps["lives"] if ps["lives"] == rs["lives"] else f"{ps['lives']}!={rs['lives']}"
            hp = "/".join(str(s["hp"]) for s in ps["sprites"])
            ph.append(f"lives {tag} act {ps['active_index']} hp [{hp}]")
        print(f"t{t:>2} | A {ph[0]} | B {ph[1]}")
        if any(p["lives"] != r["lives"] for p, r in zip(pd["players"], rd["players"])):
            for label, dg in (("py  ", pd), ("rust", rd)):
                for pi, p in enumerate(dg["players"]):
                    det = [f"{s['name']}:{s['hp']}" for s in p["sprites"]]
                    print(f"     {label} P{pi} {det}")


if __name__ == "__main__":
    main()
