"""dbg_np_trace — py/rust np 随机事件流对齐比较。

用法：
  python dbg_np_trace.py all   # 跑 py（hook）+ 子进程跑 rust（stderr 跟踪）+ 对比
事件：
  ("dir", k, n0)  — dirichlet 调用（k=len(alpha)，n0=噪声首值）
  ("u", u, idx)   — choice 采样（u=消耗的均匀数，idx=返回下标）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402


def build_common():
    import torch  # noqa: F401
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet

    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1
    return torch_ev, spec, seed


def run_py() -> list:
    """py 侧：hook np.random.dirichlet / np.random.choice，重放 py_game。"""
    from gate_phase5 import py_game

    events: list = []
    orig_dirichlet = np.random.dirichlet
    orig_choice = np.random.choice

    def dirichlet(alpha, size=None):
        k = len(alpha)
        st = np.random.get_state()
        _rs = np.random.RandomState()
        _rs.set_state(st)
        n0 = float(_rs.dirichlet(alpha)[0])
        r = orig_dirichlet(alpha, size)
        events.append(("dir", int(k), n0))
        return r

    def choice(a, p=None):
        st = np.random.get_state()
        _rs = np.random.RandomState()
        _rs.set_state(st)
        u = float(_rs.random_sample())
        r = orig_choice(a, p=p)
        nz = [(int(i), float(p[i])) for i in np.nonzero(p)[0]] if p is not None else []
        events.append(("u", u, int(r), nz))
        return r

    np.random.dirichlet = dirichlet
    np.random.choice = choice
    try:
        torch_ev, spec, seed = build_common()
        py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
        print(f"py turns={py['turns']} winner={py['winner']}", flush=True)
    finally:
        np.random.dirichlet = orig_dirichlet
        np.random.choice = orig_choice
    return events


def run_rust(out_path: Path) -> None:
    """rust 侧：子进程内运行，stderr 的 [rust-*] 行写入 out_path。"""
    code = (
        "import sys; sys.argv=['dbg'];\n"
        f"sys.path.insert(0, r'{ROOT}'); sys.path.insert(0, r'{Path(__file__).parent}');\n"
        "import os\n"
        f"os.chdir(r'{ROOT}')\n"
        "from dbg_np_trace import build_common\n"
        "from gate_phase5 import _RustEvalAdapter, rust_game\n"
        "import json\n"
        "torch_ev, spec, seed = build_common()\n"
        "adapter = _RustEvalAdapter(torch_ev)\n"
        "ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)\n"
        "print('rust turns=%s winner=%s' % (ru['turns'], ru['winner']))\n"
    )
    env = dict(os.environ)
    env["ROCO_NP_TRACE"] = "1"
    r = subprocess.run(
        [str(ROOT / "env" / "python.exe"), "-X", "utf8", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    out_path.write_text(r.stderr, encoding="utf-8")
    print("rust stdout:", r.stdout.strip()[:200], flush=True)


def parse_rust(txt: str) -> list:
    import re

    events = []
    for line in txt.splitlines():
        if line.startswith("[rust-dir] "):
            body = line[len("[rust-dir] "):]
            kv = dict(p.split("=") for p in body.split())
            events.append(("dir", int(kv["k"]), float(kv["n0"])))
        elif line.startswith("[rust-choice] "):
            u = float(re.search(r"u=([0-9.e+-]+)", line).group(1))
            idx = int(re.search(r"idx=(\d+)", line).group(1))
            m = re.search(r"nz=\[(.*)\]", line)
            nz = []
            if m and m.group(1):
                nz = [(int(a), float(b)) for a, b in
                      re.findall(r"\((\d+),([0-9.e+-]+)\)", m.group(1))]
            events.append(("u", u, idx, nz))
    return events


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "py":
        ev = run_py()
        (ROOT / "native" / "tools" / "_dbg_np_py.json").write_text(
            json.dumps(ev), encoding="utf-8")
        print(f"py events={len(ev)}", flush=True)
        return
    if mode == "rust":
        run_rust(ROOT / "native" / "tools" / "_dbg_np_rust.txt")
        return

    # all：py 事件 + rust 事件都现跑
    ev = run_py()
    (ROOT / "native" / "tools" / "_dbg_np_py.json").write_text(
        json.dumps(ev), encoding="utf-8")
    run_rust(ROOT / "native" / "tools" / "_dbg_np_rust.txt")
    py_ev = json.loads((ROOT / "native" / "tools" / "_dbg_np_py.json").read_text("utf-8"))
    py_ev = [tuple(e) for e in py_ev]
    ru_ev = parse_rust((ROOT / "native" / "tools" / "_dbg_np_rust.txt").read_text("utf-8"))
    print(f"py events={len(py_ev)}  rust events={len(ru_ev)}", flush=True)

    def fmt(e):
        if e[0] == "dir":
            return f"dir k={e[1]} n0={e[2]:.17e}"
        nz = e[3] if len(e) > 3 else []
        return (f"u={e[1]:.17e} idx={e[2]} "
                f"Pnz={[(i, round(v, 6)) for i, v in nz]}")

    n = min(len(py_ev), len(ru_ev))
    for i in range(n):
        a, b = py_ev[i], ru_ev[i]
        if a[0] != b[0]:
            print(f"✗ 事件 {i} 类型不同: py={fmt(a)} rust={fmt(b)}", flush=True)
            ctx = [f"  [{j}] py={fmt(py_ev[j])} | rust={fmt(ru_ev[j])}"
                   for j in range(max(0, i - 6), min(n, i + 3))]
            print("\n".join(ctx), flush=True)
            return
        if a[0] == "dir" and (a[1] != b[1] or a[2] != b[2]):
            print(f"✗ 事件 {i} dirichlet 分叉:\n    py  {fmt(a)}\n    rust {fmt(b)}",
                  flush=True)
            ctx = [f"  [{j}] py={fmt(py_ev[j])} | rust={fmt(ru_ev[j])}"
                   for j in range(max(0, i - 6), min(n, i + 3))]
            print("\n".join(ctx), flush=True)
            return
        if a[0] == "u" and (a[1] != b[1] or a[2] != b[2]):
            same_u = a[1] == b[1]
            print(f"✗ 事件 {i} choice 分叉（u {'一致' if same_u else '不同'}）:",
                  flush=True)
            print(f"    py  {fmt(a)}", flush=True)
            print(f"    rust {fmt(b)}", flush=True)
            ctx = [f"  [{j}] py={fmt(py_ev[j])} | rust={fmt(ru_ev[j])}"
                   for j in range(max(0, i - 6), min(n, i + 3))]
            print("\n".join(ctx), flush=True)
            return
    print(f"✓ 前 {n} 个事件一致", flush=True)
    if len(py_ev) != len(ru_ev):
        print(f"事件数不同: py={len(py_ev)} rust={len(ru_ev)}", flush=True)
        for i in range(n, min(len(py_ev), len(ru_ev) + 4)):
            if i < len(py_ev):
                print(f"    py [{i}] {fmt(py_ev[i])}", flush=True)
            if i < len(ru_ev):
                print(f"    rust [{i}] {fmt(ru_ev[i])}", flush=True)


if __name__ == "__main__":
    main()
