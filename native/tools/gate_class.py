"""gate_class — 统计失败 spec 中「精灵名/species 不同」的数量（形态变换类）。

用法：env\\python.exe native/tools/gate_class.py <spec_dir> [--from N] [--to N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402


def names_of(digest) -> list[str]:
    return [s["name"] for p in digest["players"] for s in p["sprites"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec_dir")
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=10**9)
    args = ap.parse_args()

    transform_specs: list[str] = []
    other = 0
    total = 0
    for sp in sorted(Path(args.spec_dir).glob("spec_*.json")):
        seed_no = int(sp.stem.split("_")[1])
        if not (args.lo <= seed_no <= args.hi):
            continue
        total += 1
        spec = json.loads(sp.read_text(encoding="utf-8"))
        py_digests, py_winner = run_python(spec)
        result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
        rust_digests = result["turns"]
        if (result.get("winner") == py_winner and len(rust_digests) == len(py_digests)
                and rust_digests == py_digests):
            continue
        hit = False
        for a, b in zip(py_digests, rust_digests):
            if names_of(a) != names_of(b):
                hit = True
                break
        if hit:
            transform_specs.append(sp.name)
        else:
            other += 1
    print(f"总 {total}；失败中形态变换（名字不同）类：{len(transform_specs)}，其他：{other}")
    print("样例：", ", ".join(transform_specs[:15]))


if __name__ == "__main__":
    main()
