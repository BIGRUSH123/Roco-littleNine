# -*- coding: utf-8 -*-
"""逐阵容 A/B 分片驱动：把 40 支 meta 阵容切成 N 片，每片顺序跑 --only-team。

用法（远端）:
    python -X utf8 ab_per_team.py --ab shipped --games 120 --max-turns 60 --shards 8 --shard 0
产物: ab/<ab>_s<i>.json （该片每队的 胜/负/平、胜率、CI、动作分布）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("ROCO_REMOTE_ROOT", "/mnt/workspace/roco_remote"))
sys.path.insert(0, str(ROOT))

from backend.engine.ai.data.meta_teams import load_meta_teams  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ab", required=True)
    ap.add_argument("--games", type=int, default=120, help="每队总对局数（成对，两侧各半）")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--shards", type=int, default=8)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--seeds", default="")
    a = ap.parse_args()

    names = [(t.get("name") or f"#{i}") for i, t in enumerate(load_meta_teams())]
    mine = [n for i, n in enumerate(names) if i % a.shards == a.shard]
    out_dir = ROOT / "ab"
    out_dir.mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    res: dict[str, dict] = {}
    t0 = time.time()
    for n in mine:
        tmp = out_dir / f"_tmp_{a.ab}_{a.shard}.json"
        cmd = [sys.executable, "-X", "utf8", "native/tools/eval_expert_change.py",
               "--ab", a.ab, "--games", str(a.games), "--max-turns", str(a.max_turns),
               "--only-team", n, "--per-team", "--json-out", str(tmp)]
        if a.seeds:
            cmd += ["--seeds", a.seeds]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if tmp.exists():
            d = json.loads(tmp.read_text(encoding="utf-8"))
            res[n] = {k: d[k] for k in ("games", "new_wins", "new_losses", "new_draws",
                                        "win_rate", "ci_half", "mean_turns")}
            res[n]["action_mix"] = d["action_mix"]
            tmp.unlink()
        else:
            res[n] = {"error": ((p.stderr or "") + (p.stdout or ""))[-500:]}
        print(f"[shard{a.shard}] {n}: WR={res[n].get('win_rate')} "
              f"平={res[n].get('new_draws')} ({time.time() - t0:.0f}s)", flush=True)
    (out_dir / f"{a.ab}_s{a.shard}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[shard{a.shard}] 完成 {len(res)} 队，用时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
