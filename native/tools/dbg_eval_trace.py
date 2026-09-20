"""dbg_eval_trace — py/rust MCTS 评估调用轨迹对比（定位树 walk 分叉）。

两侧的每次 evaluator 调用（root/opp/leaf batch）都输出到 stderr：
  [py-eval] s=<搜索序号> k=<段内序号> fp=<指纹> v=<value> ph=<prior 哈希> n=<batch>
  [py-dir]  <dirichlet 序号>
指纹 = FNV-1a64(10 个数组按序字节)。rust 侧经 _RustEvalAdapter 记录。

用法：python dbg_eval_trace.py py|rust|all
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

TARGET_FP = "79931a2ffe06fb5a"  # 搜索3（B[1]）leaf batch 首条指纹

KEYS = [
    "sprite_stats", "sprite_elements", "sprite_states",
    "skill_stats", "skill_elements", "skill_states",
    "global_stats", "global_elements", "ast_tokens", "ast_values",
]


def fp_state(st) -> int:
    h = 0xCBF29CE484222325
    for k in KEYS:
        a = np.ascontiguousarray(st[k])
        for b in a.tobytes():
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def fp_mask(m) -> int:
    h = 0xCBF29CE484222325
    for b in np.ascontiguousarray(np.asarray(m, dtype=np.float32)).tobytes():
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


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


def emit(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def install_py_hooks(ev):
    """包装 TorchEvaluator 实例 + dirichlet 计数。"""
    import backend.engine.ai.core.mcts as mcts_mod

    sid = {"n": -1}
    orig_dirichlet = np.random.dirichlet

    def dirichlet(alpha, size=None):
        sid["n"] += 1
        emit(f"[py-dir] {sid['n']}")
        return orig_dirichlet(alpha, size)

    np.random.dirichlet = dirichlet

    k = {"n": 0}
    orig_e = ev.evaluate
    orig_b = ev.evaluate_batch

    def evaluate(state, mask):
        v, p = orig_e(state, mask)
        k["n"] += 1
        emit(f"[py-eval] s={sid['n']} j={k['n']} fp={fp_state(state):016x} "
             f"mfp={fp_mask(mask):016x} v={float(np.asarray(v).ravel()[0]):.9f} "
             f"ph={fp_mask(np.asarray(p, dtype=np.float32)):016x} n=1")
        return v, p

    def evaluate_batch(states, masks):
        v, p = orig_b(states, masks)
        fps = ",".join(f"{fp_state(s):016x}" for s in states)
        mfps = ",".join(f"{fp_mask(m):016x}" for m in masks)
        vv = ",".join(f"{float(x):.9f}" for x in np.asarray(v).ravel())
        ph = ",".join(f"{fp_mask(q):016x}" for q in np.asarray(p, dtype=np.float32))
        k["n"] += len(states)
        emit(f"[py-eval] s={sid['n']} j={k['n']} fp={fps} mfp={mfps} v={vv} ph={ph} "
             f"n={len(states)}")
        if fps.startswith(TARGET_FP) and not (ROOT / "native" / "tools" / "_dump_py.pkl").exists():
            import pickle
            dump = [{kk: np.array(s[kk]).copy() for kk in KEYS} for s in states]
            pickle.dump(dump, open(ROOT / "native" / "tools" / "_dump_py.pkl", "wb"))
            emit("[py-dump] saved")
        return v, p

    ev.evaluate = evaluate
    ev.evaluate_batch = evaluate_batch
    mcts_mod._dbg_sid = sid
    return ev


def run_py() -> None:
    from gate_phase5 import py_game

    torch_ev, spec, seed = build_common()
    install_py_hooks(torch_ev)
    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    emit(f"[py-done] turns={py['turns']} winner={py['winner']}")


def run_rust(out: Path) -> None:
    code = (
        "import sys, os\n"
        f"sys.path.insert(0, r'{ROOT}'); sys.path.insert(0, r'{Path(__file__).parent}')\n"
        f"os.chdir(r'{ROOT}')\n"
        "import numpy as np\n"
        "from dbg_eval_trace import build_common, fp_state, fp_mask, emit\n"
        "from gate_phase5 import rust_game\n"
        "torch_ev, spec, seed = build_common()\n"
        "class Adapter:\n"
        "    def __init__(self, ev):\n"
        "        self._ev = ev\n"
        "    def evaluate_batch(self, states, masks):\n"
        "        v, p = self._ev.evaluate_batch(states, masks)\n"
        "        fps = ','.join(f'{fp_state(s):016x}' for s in states)\n"
        "        mfps = ','.join(f'{fp_mask(m):016x}' for m in masks)\n"
        "        vv = ','.join(f'{float(x):.9f}' for x in np.asarray(v).ravel())\n"
        "        ph = ','.join(f'{fp_mask(q):016x}' for q in np.asarray(p, dtype=np.float32))\n"
        "        emit(f'[ru-eval] fp={fps} mfp={mfps} v={vv} ph={ph} n={len(states)}')\n"
        "        import os as _os\n"
        "        if fps.startswith('79931a2ffe06fb5a') and "
        "not _os.path.exists(r'D:\\projects\\Roco-LittleNine\\native\\tools\\_dump_rust.pkl'):\n"
        "            import pickle as _pk\n"
        "            _dump = [{kk: np.array(s[kk]).copy() for kk in "
        "['sprite_stats','sprite_elements','sprite_states','skill_stats',"
        "'skill_elements','skill_states','global_stats','global_elements',"
        "'ast_tokens','ast_values']} for s in states]\n"
        "            _pk.dump(_dump, open("
        "r'D:\\projects\\Roco-LittleNine\\native\\tools\\_dump_rust.pkl', 'wb'))\n"
        "            emit('[ru-dump] saved')\n"
        "        return v, p\n"
        "adapter = Adapter(torch_ev)\n"
        "ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)\n"
        "emit('[ru-done] ok')\n"
    )
    env = dict(os.environ)
    env["ROCO_NP_TRACE"] = "1"
    r = subprocess.run(
        [str(ROOT / "env" / "python.exe"), "-X", "utf8", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    out.write_text(r.stderr, encoding="utf-8")
    print("rust stdout:", r.stdout.strip()[:200], flush=True)
    if r.returncode != 0:
        print("rust stderr tail:", r.stderr[-800:], flush=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    py_f = ROOT / "native" / "tools" / "_ev_py.txt"
    ru_f = ROOT / "native" / "tools" / "_ev_rust.txt"
    if mode == "py":
        run_py()
        return
    if mode == "cmp":
        import pickle
        pyd = pickle.load(open(ROOT / "native" / "tools" / "_dump_py.pkl", "rb"))
        rud = pickle.load(open(ROOT / "native" / "tools" / "_dump_rust.pkl", "rb"))
        print(f"batch 大小 py={len(pyd)} rust={len(rud)}", flush=True)
        for i in range(min(len(pyd), len(rud))):
            diffs = []
            for kk in KEYS:
                a = np.asarray(pyd[i][kk])
                b = np.asarray(rud[i][kk])
                if a.shape != b.shape:
                    diffs.append(f"{kk} 形状 {a.shape} vs {b.shape}")
                    continue
                if not np.array_equal(a, b):
                    d = np.abs(a.astype(np.float64) - b.astype(np.float64))
                    idx = np.unravel_index(np.argmax(d), d.shape)
                    diffs.append(f"{kk} 非零Δ={int((d > 0).sum())} "
                                 f"max@{tuple(int(x) for x in idx)} "
                                 f"py={a[idx]} rust={b[idx]}")
            if diffs:
                print(f"— 条目 {i}: {len(diffs)} 项不同", flush=True)
                for x in diffs:
                    print("    ", x, flush=True)
        return
    if mode == "rust":
        run_rust(ru_f)
        return

    # all
    r0 = subprocess.run(
        [str(ROOT / "env" / "python.exe"), "-X", "utf8", str(Path(__file__)), "py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )
    py_f.write_text(r0.stderr, encoding="utf-8")
    print("py done:", r0.stdout.strip()[:100], r0.stderr[-200:] if r0.returncode else "",
          flush=True)
    run_rust(ru_f)

    # 解析对比：按文件行序处理，[X-dir] 切换搜索段，[X-eval] 归入当前段
    import re

    def parse_segments(path: Path, dir_tag: str, eval_tag: str):
        segs: list[list[str]] = []
        cur = -1
        for line in path.read_text("utf-8", errors="replace").splitlines():
            if line.startswith(f"[{dir_tag}-dir] "):
                cur += 1
                while len(segs) <= cur:
                    segs.append([])
            elif line.startswith(f"[{eval_tag}-eval] "):
                body = re.sub(r"^s=-?\d+ j=\d+ ", "", line.split(" ", 1)[1])
                if cur < 0:
                    cur = 0
                    segs.append([])
                segs[cur].append(body)
        return segs

    py_segs = parse_segments(py_f, "py", "py")
    ru_segs = parse_segments(ru_f, "rust", "ru")
    print(f"py: {len(py_segs)} 个搜索段  rust: {len(ru_segs)} 个搜索段", flush=True)

    n = min(len(py_segs), len(ru_segs))
    for s in range(n):
        a, b = py_segs[s], ru_segs[s]
        if a == b:
            continue
        print(f"✗ 搜索 {s} 评估序列不同（py {len(a)} 条 vs rust {len(b)} 条）", flush=True)
        for i in range(min(len(a), len(b))):
            if a[i] != b[i]:
                print(f"  首个不同 eval #{i}:", flush=True)
                print(f"    py   {a[i][:500]}", flush=True)
                print(f"    rust {b[i][:500]}", flush=True)
                break
        else:
            print(f"  前 {min(len(a), len(b))} 条一致，之后长度不同", flush=True)
        break
    else:
        print(f"✓ 前 {n} 个搜索的评估序列完全一致", flush=True)


if __name__ == "__main__":
    main()
