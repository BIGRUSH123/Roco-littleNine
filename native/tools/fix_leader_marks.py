# -*- coding: utf-8 -*-
"""native/tools/fix_leader_marks.py — 扫描未标首领的第二形态，并可批量标记。

用法:
  python native/tools/fix_leader_marks.py                 # 只扫描列出
  python native/tools/fix_leader_marks.py --mark 满月砣 暮风隐者   # 标记为首领阶段

判定"未标首领的第二形态"：pre_species == 自身 number 且 form 不含『首领』。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SPRITES_DIR = Path("data/sprites")
OUT = Path("native/tools/_leader_marks.txt")


def load_all() -> list[tuple[Path, dict]]:
    items = []
    for p in sorted(SPRITES_DIR.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            items.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark", nargs="*", default=[], help="把这些名字标为首领阶段")
    args = ap.parse_args()

    items = load_all()
    lines: list[str] = []

    # ── 扫描：未标首领的第二形态 ──
    candidates: dict[str, list[Path]] = {}
    for p, d in items:
        number = str(d.get("number", "")).strip()
        pre = str(d.get("pre_species", "")).strip()
        form = str(d.get("form", "") or "")
        if pre and pre == number and "首领" not in form:
            candidates.setdefault(d.get("name", ""), []).append(p)

    lines.append(f"未标首领的同编号第二形态: {len(candidates)} 组")
    for name, paths in sorted(candidates.items()):
        apps = []
        for p in paths:
            d = json.loads(p.read_text(encoding="utf-8"))
            apps.append(d.get("appearance", "") or "(默认)")
        lines.append(f"  {name}: {len(paths)} 个文件, 外观={apps}")

    # ── 首领形态的 attributes（决定自动血脉） ──
    boss_attrs = {}
    for p, d in items:
        form = str(d.get("form", "") or "")
        if "首领" in form and not d.get("appearance"):
            boss_attrs[d.get("name", "")] = d.get("attributes")
    lines.append("")
    lines.append("=== 首领形态 attributes（前 12）===")
    for name, attrs in list(boss_attrs.items())[:12]:
        lines.append(f"  {name}: {attrs}")

    # ── 标记 ──
    if args.mark:
        marked = 0
        for p, d in items:
            if d.get("name") in set(args.mark):
                d["form"] = "首领形态"
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
                marked += 1
        lines.append("")
        lines.append(f"已标记 {marked} 个文件为首领阶段: {args.mark}")

    text = "\n".join(lines)
    OUT.write_text(text + "\n", encoding="utf-8")
    print("written", OUT, len(text))


if __name__ == "__main__":
    main()
