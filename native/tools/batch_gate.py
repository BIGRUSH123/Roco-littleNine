"""batch_gate — 阶段3 验收门：批量 spec 对拍（py oracle vs rust）。

用法：env\\python.exe native/tools/batch_gate.py <spec_dir> [--from N] [--to N]

输出：通过/失败统计 + 失败 spec 的首分歧位置。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec_dir")
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=10**9)
    ap.add_argument("--show", type=int, default=10, help="失败明细最多显示数")
    args = ap.parse_args()

    specs = sorted(Path(args.spec_dir).glob("spec_*.json"))
    total = passed = failed = 0
    t0 = time.perf_counter()
    py_time = 0.0
    rust_time = 0.0
    shown = 0
    fail_seeds: list[str] = []
    for sp in specs:
        seed_no = int(sp.stem.split("_")[1])
        if not (args.lo <= seed_no <= args.hi):
            continue
        total += 1
        spec = json.loads(sp.read_text(encoding="utf-8"))
        t = time.perf_counter()
        py_digests, py_winner = run_python(spec)
        py_time += time.perf_counter() - t
        t = time.perf_counter()
        result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
        rust_time += time.perf_counter() - t
        rust_digests = result["turns"]
        rust_winner = result.get("winner") or None  # rust 平局序列化为 ""
        ok = (
            rust_winner == py_winner
            and len(rust_digests) == len(py_digests)
            and rust_digests == py_digests
        )
        if ok:
            passed += 1
        else:
            failed += 1
            fail_seeds.append(sp.name)
            if shown < args.show:
                shown += 1
                reason = []
                if rust_winner != py_winner:
                    reason.append(f"winner py={py_winner} rust={rust_winner}")
                if len(rust_digests) != len(py_digests):
                    reason.append(f"turns py={len(py_digests)} rust={len(rust_digests)}")
                first = next(
                    (i for i, (a, b) in enumerate(zip(py_digests, rust_digests)) if a != b),
                    None,
                )
                if first is not None:
                    reason.append(f"first diff at index {first} (turn={py_digests[first].get('turn')})")
                print(f"FAIL {sp.name}: {'; '.join(reason)}")
    dt = time.perf_counter() - t0
    print(f"── 总计 {total}：通过 {passed}，失败 {failed}（耗时 {dt:.1f}s）──")
    if failed:
        print("失败 spec：", ", ".join(fail_seeds))
    n = max(total, 1)
    print(f"速度：py {py_time / n * 1000:.1f} ms/局，rust {rust_time / n * 1000:.2f} ms/局，"
          f"加速 {py_time / max(rust_time, 1e-9):.1f}x")


if __name__ == "__main__":
    main()
