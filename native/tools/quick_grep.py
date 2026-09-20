# -*- coding: utf-8 -*-
"""quick_grep.py — 查 Sprite.speed / BattleSkill 字段 / agent 动作助手。"""
import subprocess

pats = [
    ("backend/sim/sprite.py", ["self.speed", "speed:", "self.energy"]),
    ("backend/sim/battleskill.py", ["def is_attack", "def is_defense", "skill_type", "energy_cost"]),
    ("backend/sim/agent.py", ["def _skill_action", "def _switch_action", "_GATHER_ACTION", "_ITEM_ACTION"]),
]
for path, kws in pats:
    print("==", path)
    for kw in kws:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Select-String -Path {path} -Pattern '{kw}' | Select-Object -First 3 | "
             "ForEach-Object { Write-Output ($_.LineNumber.ToString() + ': ' + $_.Line.Trim()) }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            print("  ", line)
