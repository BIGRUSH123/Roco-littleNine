"""mcts_gate — 阶段4 MCTS 树/PUCT/步进/RNG 对拍。

对拍对象：py `mcts_search` vs rust `mcts_search`，双方使用**同一个确定性桩
评估器**（value=0，policy = mask / max(sum,1)），从而把 encoder/网络排除在外，
只检验：PUCT 打分（numpy2 NEP50 f32 语义）、选择/扩展/回退、固定动作步进
（execute_turn_headless 等价）、以及两条 RNG 流（numpy legacy dirichlet +
对局 MT19937）。

比较项：概率位模式、根节点访问次数、每轮仿真动作轨迹、numpy/对局 RNG 状态。

用法：env\\python.exe native/tools/mcts_gate.py [spec_dir] [--from N] [--to N] [--sims 24]
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

import numpy as np  # noqa: E402
import roco_engine  # noqa: E402

from backend.engine.ai.core import mcts as mcts_mod  # noqa: E402
from backend.engine.ai.core.mcts import NetworkPolicyAgent, mcts_search  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

CFG = {
    "num_simulations": 24,
    "c_puct": 2.0,
    "root_noise": 0.25,
    "max_turns": 60,
    "opp_greedy": True,
    "opp_temperature": 1.0,
    "use_network_opponent": True,
    "leaf_batch_size": 1,
    "digest_trace": True,
}


class UniformStub:
    """桩评估器（与 rust UniformStub 同语义）：value=0，policy=归一化 mask。"""

    def evaluate(self, state, mask):
        return 0.0, (mask / max(mask.sum(), 1.0)).astype(np.float32)

    def evaluate_batch(self, states, masks):
        values = np.zeros(len(states), dtype=np.float32)
        priors = np.stack([self.evaluate(s, m)[1] for s, m in zip(states, masks)], axis=0)
        return values, priors


_TRACE: list[list[int]] = []
_DIGESTS: list[list[dict]] = []
_WANT_DIGESTS = True
_HOOK_INSTALLED = False


def _install_hooks() -> None:
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    from backend.engine.test_rust_gate import gate_digest

    orig_save = Battle.save_mutable_state

    def save_patched(self):
        _TRACE.append([])
        _DIGESTS.append([])
        return orig_save(self)

    Battle.save_mutable_state = save_patched

    # _step_battle 内动作索引→动作的调用顺序固定：第 1 次是 A，第 2 次（若
    # opp_policy 有值）是 B。借此拿到对手动作索引（rust step_battle 的 out_b）。
    orig_a2a = mcts_mod.action_index_to_action
    seen: list[int] = []

    def a2a_patched(player, idx):
        seen.append(int(idx))
        return orig_a2a(player, idx)

    mcts_mod.action_index_to_action = a2a_patched

    orig_step = mcts_mod._step_battle

    def step_patched(battle, action_idx, opponent_agent, **kw):
        if _TRACE:
            _TRACE[-1].append(int(action_idx))
        seen.clear()
        out = orig_step(battle, action_idx, opponent_agent, **kw)
        if _WANT_DIGESTS and _DIGESTS and out:
            _DIGESTS[-1].append(
                {
                    "b": seen[1] if len(seen) > 1 else -1,
                    "mti": int(random.getstate()[1][-1]),
                    "np": int(np.random.get_state()[2]),
                    "d": gate_digest(battle),
                }
            )
        return out

    mcts_mod._step_battle = step_patched
    _HOOK_INSTALLED = True


def run_python(spec: dict, cfg: dict) -> dict:
    _install_hooks()
    _TRACE.clear()
    _DIGESTS.clear()
    Battle.save_mutable_state  # noqa: B018  保持引用，避免被回收
    created: list = []
    orig_init = mcts_mod.MCTSNode.__init__

    def init_patched(self, valid_actions, prior):
        orig_init(self, valid_actions, prior)
        created.append(self)

    mcts_mod.MCTSNode.__init__ = init_patched
    try:
        random.seed(spec["seed"] + 1)
        np.random.seed((spec["seed"] + 1) % (2**32 - 1))
        battle = battle_from_spec(spec)
        a = RuleAgent("A", battle.player_a)
        b = RuleAgent("B", battle.player_b)
        battle.player_a.active_index = a.choose_lead(battle)
        battle.player_b.active_index = b.choose_lead(battle)
        battle._invalidate_ctx_team_cache()

        stub = UniformStub()
        opp = NetworkPolicyAgent(evaluator=stub, greedy=True)
        probs = mcts_search(
            battle,
            None,
            SimFactory(),
            opp,
            num_simulations=cfg["num_simulations"],
            c_puct=cfg["c_puct"],
            root_noise=cfg["root_noise"],
            max_turns=cfg["max_turns"],
            opp_greedy=cfg["opp_greedy"],
            evaluator=stub,
            draw_margin=0.15,
            gamma=1.0,
            tanh_k=0.0,
            leaf_batch_size=cfg["leaf_batch_size"],
        )
        root = next((n for n in created if n.valid_actions), None)
        counts = [0] * 17
        if root is not None:
            for ai in range(17):
                child = root.children[ai]
                if child is not None:
                    counts[ai] = int(child.visit_count)
        st = np.random.get_state()
        return {
            "probs_bits": probs.view(np.uint32).tolist(),
            "counts": counts,
            "trace": [list(t) for t in _TRACE],
            "digest_trace": [list(d) for d in _DIGESTS],
            "np_head": [int(x) for x in st[1][:8]],
            "np_pos": int(st[2]),
            "rng_head": list(random.getstate()[1][:8]),
            # MT 状态下标 = state[1][-1]（state[2] 是 gauss 缓存，通常为 None）
            "rng_mti": int(random.getstate()[1][-1]),
            "turn": int(battle.turn),
        }
    finally:
        mcts_mod.MCTSNode.__init__ = orig_init


def _first_diff(a, b, path: str = "") -> str:
    """定位两个 digest 的首个差异字段（对齐 test_rust_gate._first_diff）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k} 仅 rust 有: {b[k]!r}"
            if k not in b:
                return f"{path}.{k} 仅 py 有: {a[k]!r}"
            r = _first_diff(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} 长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = _first_diff(x, y, f"{path}[{i}]")
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
    ap.add_argument("--sims", type=int, default=CFG["num_simulations"])
    ap.add_argument("--batch", type=int, default=1,
                    help="leaf_batch_size（>1 走 py/rust 批量叶评估路径对拍）")
    ap.add_argument("--show", type=int, default=200)
    args = ap.parse_args()
    CFG["num_simulations"] = args.sims
    CFG["leaf_batch_size"] = args.batch

    specs = sorted(Path(args.spec_dir).glob("spec_*.json"))
    total = passed = failed = 0
    bad: list[str] = []
    shown = 0
    out: list[str] = []
    for sp in specs:
        seed_no = int(sp.stem.split("_")[1])
        if not (args.lo <= seed_no <= args.hi):
            continue
        total += 1
        spec = json.loads(sp.read_text(encoding="utf-8"))
        py = run_python(spec, CFG)
        ru = json.loads(roco_engine.py_mcts_stub(json.dumps(spec, ensure_ascii=False), json.dumps(CFG)))
        diffs = []
        for key in ("probs_bits", "counts", "np_head", "np_pos", "rng_head", "rng_mti", "turn"):
            if py[key] != ru[key]:
                diffs.append(f"{key}: py={py[key]!r} ru={ru[key]!r}")
        if len(py["trace"]) != len(ru["trace"]):
            diffs.append(f"trace_len py={len(py['trace'])} ru={len(ru['trace'])}")
        else:
            for i, (pt, rt) in enumerate(zip(py["trace"], ru["trace"])):
                if pt != rt:
                    diffs.append(f"trace[{i}] py={pt} ru={rt}")
                    break
        if len(py["digest_trace"]) != len(ru["digest_trace"]):
            diffs.append(
                f"digest_trace 轮数 py={len(py['digest_trace'])} ru={len(ru['digest_trace'])}"
            )
        else:
            for i, (pd, rd) in enumerate(zip(py["digest_trace"], ru["digest_trace"])):
                if pd == rd:
                    continue
                if len(pd) != len(rd):
                    diffs.append(f"digest[{i}] 步数 py={len(pd)} ru={len(rd)}")
                    break
                for j, (x, y) in enumerate(zip(pd, rd)):
                    if x != y:
                        diffs.append(
                            f"digest[{i}][{j}] 首个差异: {_first_diff(x, y)}"
                        )
                        break
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
    out.append(f"── MCTS 对拍 {total}：通过 {passed}，失败 {failed}"
               f"（sims={CFG['num_simulations']}, batch={CFG['leaf_batch_size']}）──")
    if bad:
        out.append("失败：" + ", ".join(bad))
    text = "\n".join(out)
    # 直接由 python 写 UTF-8 报告：经 PowerShell 管道会被按 ANSI 重编码成乱码
    (Path(__file__).parent / "_gate_last.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
