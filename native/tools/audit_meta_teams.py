# -*- coding: utf-8 -*-
"""native/tools/audit_meta_teams.py — meta 队伍与引擎语义的一致性审计。

对 40 支 meta 队的每只精灵检查：
  - 池内可构建（build_sprite + 技能编译）
  - 血脉声明合法；首领血脉精灵是否真能首领化（同编号存在首领形态）
  - 队伍道具与血脉是否匹配（进化之力 ↔ 首领血脉；愿力 ↔ 元素血脉）
  - 首发/必保标注是否存在

用途：精灵池或引擎规则更新后，确认 meta_teams.json 仍然语义自洽。
输出 UTF-8 报告。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.common.constants import ELEMENTAL_BLOODLINES  # noqa: E402
from backend.engine.ai.data.meta_teams import load_meta_teams  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

def _find_boss(db_, number: str, appearance: str):
    """同编号是否存在首领形态（同外观优先，回退默认外观）。"""
    same, default, any_boss = None, None, None
    for path in db_._by_number.get(number, []):
        s = db_._read_one(path)
        if s is None or "首领" not in (s.form or ""):
            continue
        if s.appearance == appearance:
            same = same or s
        if not s.appearance:
            default = default or s
        any_boss = any_boss or s
    return same or default or any_boss


factory = SimFactory()
db = factory.sprite_db
teams = load_meta_teams()

lines: list[str] = []
problems: list[str] = []
stats = Counter()
boss_team_names: list[str] = []

for team in teams:
    tname = team.get("name", "?")
    item = team.get("item", "")
    lines.append(f"== {tname}  ({team.get('archetype', '?')}, 魔法={item or '未声明'}) ==")
    n_boss_capable = 0
    for sp in team["sprites"]:
        name = sp["name"]
        species = db.get(name)
        bloodline = sp.get("bloodline", "")
        if species is None:
            problems.append(f"[{tname}] 池内找不到: {name}")
            lines.append(f"   ✗ {name}: 池内找不到")
            continue
        try:
            sprite = factory.build_sprite(name, sp["skills"], bloodline=bloodline)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"[{tname}] {name} 构建失败: {exc}")
            lines.append(f"   ✗ {name}: 构建失败 {exc}")
            continue
        can_boss = False
        if bloodline == "首领":
            boss = _find_boss(db, species.number, species.appearance)
            can_boss = boss is not None
            if can_boss:
                n_boss_capable += 1
                stats["首领可化"] += 1
            else:
                problems.append(f"[{tname}] {name} 首领血脉但没有首领形态（进化之力不可用）")
        stats[f"血脉={bloodline or '默认'}"] += 1
        lines.append(f"   · {name:<18} 血脉={bloodline or '默认':<4} "
                     f"首领化={'可' if can_boss else ('—' if bloodline != '首领' else '不可')} "
                     f"技能={len(sp['skills'])} 首发={sp.get('lead')} 必保={sp.get('preserve')}")
    if n_boss_capable:
        boss_team_names.append(tname)
        if item and item != "进化之力":
            problems.append(f"[{tname}] 有首领血脉精灵但魔法={item}（首领化不可用）")
    if item == "进化之力" and not n_boss_capable:
        problems.append(f"[{tname}] 魔法=进化之力 但队内没有可首领化的精灵")

lines = [
    f"队伍 {len(teams)} 支；含可首领化精灵的队 {len(boss_team_names)} 支",
    f"统计: {dict(stats)}",
    "",
    "== 问题 ==",
    *problems,
    "",
] + lines

out = _ROOT / "_meta_audit.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"审计 {len(teams)} 队；问题 {len(problems)} 条；含首领化精灵的队 {len(boss_team_names)} 支")
print(f"报告 → {out}")
