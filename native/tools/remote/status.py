# -*- coding: utf-8 -*-
"""远端状态一览（避免 ANSI/引号问题）：进度、进程、关键判决。"""
import glob
import os
import re
import subprocess
import time

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def tail_lines(path, pats, n=4):
    if not os.path.exists(path):
        return [f"(无 {path})"]
    txt = ANSI.sub("", open(path, encoding="utf-8", errors="replace").read())
    hits = [l for l in txt.splitlines() if any(p in l for p in pats)]
    return hits[-n:] or ["(暂无匹配行)"]


print("远端时间:", time.strftime("%m-%d %H:%M"))
print("负载:", open("/proc/loadavg").read().split()[0:3], "/ 23 核")
print("\n=== 关键进程 ===")
out = subprocess.run(["ps", "-eo", "pid,pcpu,etime,args"], capture_output=True, text=True).stdout
for l in out.splitlines():
    if any(k in l for k in ("engine.ai.train", "probe_reward_ab", "bc_pretrain", "gen_bc_data")) and "ps -eo" not in l:
        print("  " + l.strip()[:105])

RUNS = {
    "扫描 sims 200/300": ("/mnt/workspace/roco_remote/logs/ab_sims_200_300.log", ["得分", "search(", "进度"]),
    "v2 自博弈 sims=400": ("/mnt/workspace/roco_remote/logs/rl_v2_s400.log", ["RL 迭代", "自我博弈 (", "门控评估", "候选胜率", "晋升", "保留候选", "进度"]),
}
for name, (path, pats) in RUNS.items():
    print(f"\n=== {name} ===")
    for l in tail_lines(path, pats):
        print("  " + l.strip()[:120])
