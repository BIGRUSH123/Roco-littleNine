"""远端状态一览（避免 ANSI/引号问题）：进度、进程、关键判决。"""
import os
import re
import subprocess
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def tail_lines(path, pats, n=4):
    if not os.path.exists(path):
        return [f"(无 {path})"]
    txt = ANSI.sub("", Path(path).read_text(encoding="utf-8", errors="replace"))
    hits = [row_value for row_value in txt.splitlines() if any(p in row_value for p in pats)]
    return hits[-n:] or ["(暂无匹配行)"]


print("远端时间:", time.strftime("%m-%d %H:%M"))
print("负载:", Path("/proc/loadavg").read_text(encoding="utf-8").split()[0:3], "/ 23 核")
print("\n=== 关键进程 ===")
out = subprocess.run(["ps", "-eo", "pid,pcpu,etime,args"], capture_output=True, text=True).stdout
for row_value in out.splitlines():
    if any(k in row_value for k in ("engine.ai.train", "probe_reward_ab", "bc_pretrain", "gen_bc_data")) and "ps -eo" not in row_value:
        print("  " + row_value.strip()[:105])

RUNS = {
    "扫描 sims 200/300": ("/mnt/workspace/roco_remote/logs/ab_sims_200_300.log", ["得分", "search(", "进度"]),
    "v2 自博弈 sims=400": ("/mnt/workspace/roco_remote/logs/rl_v2_s400.log", ["RL 迭代", "自我博弈 (", "门控评估", "候选胜率", "晋升", "保留候选", "进度"]),
}
for name, (path, pats) in RUNS.items():
    print(f"\n=== {name} ===")
    for row_value in tail_lines(path, pats):
        print("  " + row_value.strip()[:120])
