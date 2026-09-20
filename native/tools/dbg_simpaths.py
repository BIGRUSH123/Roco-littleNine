"""dbg_simpaths — MCTS 模拟路径对比（定位树 walk 分叉的精确步）。

phase all:  py/rust 每次搜索的仿真动作序列对比 → 找第一个分叉的搜索 k 与位置
phase digest <k>: 对第 k 次搜索倾存每步的 gate_digest 状态摘要
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))


def build_common():
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet

    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1
    return torch_ev, spec, seed


def run_py() -> None:
    import numpy as np  # noqa: F401

    import backend.engine.ai.core.mcts as mcts_mod
    import backend.engine.ai.train as train_mod
    from gate_phase5 import py_game

    from backend.engine.test_rust_gate import gate_digest

    st = {"k": -1, "acts": [], "digest": False}

    orig_search = mcts_mod.mcts_search
    orig_step = mcts_mod._step_battle

    def step(battle, action_idx, *a, **kw):
        r = orig_step(battle, action_idx, *a, **kw)
        st["acts"].append(int(action_idx))
        if st["digest"]:
            st["dig"].append(gate_digest(battle))
        return r

    def search(battle, *a, **kw):
        st["k"] += 1
        st["acts"] = []
        st["dig"] = []
        st["digest"] = st["k"] == st.get("target", -999)
        out = orig_search(battle, *a, **kw)
        print(f"[py-sims] k={st['k']} paths={'|'.join(map(str, st['acts']))}",
              flush=True)
        if st["digest"]:
            for i, d in enumerate(st["dig"]):
                print(f"[py-digest] k={st['k']} step={i} {json.dumps(d, ensure_ascii=False)}",
                      flush=True)
        return out

    mcts_mod._step_battle = step
    mcts_mod.mcts_search = search
    train_mod.mcts_search = search

    torch_ev, spec, seed = build_common()
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"[py-done] turns={py['turns']}", flush=True)


def run_rust(out: Path, digest_k: int | None) -> None:
    code = (
        "import sys, os\n"
        f"sys.path.insert(0, r'{ROOT}'); sys.path.insert(0, r'{Path(__file__).parent}')\n"
        f"os.chdir(r'{ROOT}')\n"
        "from dbg_simpaths import build_common\n"
        "from gate_phase5 import _RustEvalAdapter, rust_game\n"
        "torch_ev, spec, seed = build_common()\n"
        "adapter = _RustEvalAdapter(torch_ev)\n"
        "ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["ROCO_NP_TRACE"] = "1"
    if digest_k is not None:
        env["ROCO_SIM_DIGEST"] = str(digest_k)
    r = subprocess.run(
        [str(ROOT / "env" / "python.exe"), "-X", "utf8", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    out.write_text(r.stderr, encoding="utf-8")
    if r.returncode != 0:
        print("rust stderr tail:", r.stderr[-600:], flush=True)


def flatten_paths(paths_str: str) -> list[int]:
    out: list[int] = []
    for p in paths_str.split("|"):
        if p:
            out.extend(int(x) for x in p.split(","))
    return out


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    ru_f = ROOT / "native" / "tools" / "_sims_rust.txt"
    py_f = ROOT / "native" / "tools" / "_sims_py.txt"

    if mode == "all":
        # py 子进程
        r0 = subprocess.run(
            [str(ROOT / "env" / "python.exe"), "-X", "utf8", __file__, "py"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        py_f.write_text(r0.stdout, encoding="utf-8")
        run_rust(ru_f, None)

        py_lines = [l for l in py_f.read_text(encoding="utf-8").splitlines()
                    if l.startswith("[py-sims] ")]
        ru_lines = [l for l in ru_f.read_text(encoding="utf-8").splitlines()
                    if l.startswith("[rust-sims] ")]
        print(f"py 搜索数={len(py_lines)} rust 搜索数={len(ru_lines)}", flush=True)
        for i in range(min(len(py_lines), len(ru_lines))):
            pk = int(re.search(r"k=(\d+)", py_lines[i]).group(1))
            rk = int(re.search(r"k=(\d+)", ru_lines[i]).group(1))
            pp = py_lines[i].split(" paths=", 1)[1]
            rp = ru_lines[i].split(" paths=", 1)[1]
            # rust paths 以 ';' 分隔每条仿真
            rp_flat = flatten_paths(rp.replace(";", "|"))
            pp_flat = flatten_paths(pp.replace(";", "|"))
            if pp_flat == rp_flat:
                continue
            pos = next((j for j in range(min(len(pp_flat), len(rp_flat)))
                        if pp_flat[j] != rp_flat[j]), None)
            print(f"✗ 搜索 k={pk}（py）/ k={rk}（rust）路径分叉 "
                  f"@ 扁平位置 {pos}（长度 py={len(pp_flat)} rust={len(rp_flat)}）",
                  flush=True)
            lo = max(0, (pos or 0) - 6)
            print(f"    py  [{lo}:] = {pp_flat[lo:(pos or 0) + 4]}", flush=True)
            print(f"    rust[{lo}:] = {rp_flat[lo:(pos or 0) + 4]}", flush=True)
            print(f"    完整 py  paths = {pp[:300]}", flush=True)
            print(f"    完整 rust paths = {rp[:300]}", flush=True)
            return
        print(f"✓ 前 {min(len(py_lines), len(ru_lines))} 个搜索路径全部一致", flush=True)
        return

    if mode == "digest":
        k = int(sys.argv[2])
        r0 = subprocess.run(
            [str(ROOT / "env" / "python.exe"), "-X", "utf8", __file__, "py", str(k)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        py_f.write_text(r0.stdout, encoding="utf-8")
        run_rust(ru_f, k)
        pd = [l for l in py_f.read_text(encoding="utf-8").splitlines()
              if l.startswith("[py-digest] ")]
        rd = [l for l in ru_f.read_text(encoding="utf-8").splitlines()
              if l.startswith("[rust-digest] ")]
        print(f"py digest 步数={len(pd)} rust={len(rd)}", flush=True)
        for i in range(min(len(pd), len(rd))):
            a = json.loads(pd[i][pd[i].index("{"):])
            rb = json.loads(rd[i][rd[i].index("{"):])
            b = rb.get("d", rb) if isinstance(rb, dict) else rb
            if a != b:
                import difflib
                print(f"✗ 首个不同 digest @ step {i}:", flush=True)
                sa = json.dumps(a, ensure_ascii=False, sort_keys=True, indent=1)
                sb = json.dumps(b, ensure_ascii=False, sort_keys=True, indent=1)
                for line in list(difflib.unified_diff(sa.splitlines(), sb.splitlines(),
                                                      "py", "rust", lineterm=""))[:80]:
                    print("  " + line, flush=True)
                return
        print("✓ 该搜索各步 digest 全部一致", flush=True)
        return

    if mode == "py":
        target = int(sys.argv[2]) if len(sys.argv) > 2 else -999
        import backend.engine.ai.core.mcts as _m  # noqa: F401  确保模块加载顺序

        st = {"k": -1, "acts": [], "digest": False, "dig": [], "target": target}
        # 重新绑定（run_py 里闭包引用 st 的独立实例，这里共享同一 dict 即可）
        run_py_shared(st)
        return


def run_py_shared(st: dict) -> None:
    import backend.engine.ai.core.mcts as mcts_mod
    import backend.engine.ai.train as train_mod
    from gate_phase5 import py_game

    from backend.engine.test_rust_gate import gate_digest

    orig_search = mcts_mod.mcts_search
    orig_step = mcts_mod._step_battle

    def step(battle, action_idx, *a, **kw):
        r = orig_step(battle, action_idx, *a, **kw)
        st["acts"].append(int(action_idx))
        if st["digest"]:
            st["dig"].append(gate_digest(battle))
        return r

    def search(battle, *a, **kw):
        st["k"] += 1
        st["acts"] = []
        st["dig"] = []
        st["digest"] = st["k"] == st.get("target", -999)
        out = orig_search(battle, *a, **kw)
        print(f"[py-sims] k={st['k']} paths={'|'.join(map(str, st['acts']))}",
              flush=True)
        if st["digest"]:
            for i, d in enumerate(st["dig"]):
                print(f"[py-digest] k={st['k']} step={i} {json.dumps(d, ensure_ascii=False)}",
                      flush=True)
        return out

    mcts_mod._step_battle = step
    mcts_mod.mcts_search = search
    train_mod.mcts_search = search

    torch_ev, spec, seed = build_common()
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"[py-done] turns={py['turns']}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "py":
        # 带目标 k 的 py 运行（digest 模式子进程）
        import backend.engine.ai.core.mcts as mcts_mod
        import backend.engine.ai.train as train_mod
        from gate_phase5 import py_game

        from backend.engine.test_rust_gate import gate_digest

        target = int(sys.argv[2])
        st = {"k": -1, "acts": [], "digest": False, "dig": [], "target": target}

        orig_search = mcts_mod.mcts_search
        orig_step = mcts_mod._step_battle

        def step(battle, action_idx, *a, **kw):
            r = orig_step(battle, action_idx, *a, **kw)
            st["acts"].append(int(action_idx))
            if st["digest"]:
                st["dig"].append(gate_digest(battle))
            return r

        def search(battle, *a, **kw):
            st["k"] += 1
            st["acts"] = []
            st["dig"] = []
            st["digest"] = st["k"] == target
            out = orig_search(battle, *a, **kw)
            if st["digest"]:
                for i, d in enumerate(st["dig"]):
                    print(f"[py-digest] k={target} step={i} "
                          f"{json.dumps(d, ensure_ascii=False)}", flush=True)
            return out

        mcts_mod._step_battle = step
        mcts_mod.mcts_search = search
        train_mod.mcts_search = search

        torch_ev, spec, seed = build_common()
        py_game(spec, torch_ev, seed, 24, 1.0, 16)
    else:
        main()
