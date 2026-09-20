# -*- coding: utf-8 -*-
"""native/tools/diag_pool_removed.py — 诊断精灵为何不在随机池中。

池子构建的三条排除规则（见 sprite_random_pool._build_pool）：
  A. form == '首领形态'
  B. number 出现在其他精灵的 pre_species 中（存在更高形态 → 只保留最终形态）
  C. 技能 id 全部无法解析为 on-disk 技能名（skills 为空）

用法: python native/tools/diag_pool_removed.py 名字1 名字2 ...
输出: 打印 + UTF-8 摘要文件
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME

SPRITES_DIR = Path("data/sprites")
SKILLS_DIR = Path("data/skills")
OUT = Path("native/tools/_pool_removed_diag.txt")


def load_all() -> list[dict]:
    rows = []
    for path in SPRITES_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows.append({
            "file": path.name,
            "name": data.get("name", path.stem),
            "form": data.get("form", ""),
            "number": str(data.get("number", "")).strip(),
            "pre_species": str(data.get("pre_species", "")).strip(),
            "skills_raw": list(data.get("skills", [])),
            "stone": list(data.get("stone_skills", [])),
        })
    return rows


def main() -> None:
    names = sys.argv[1:]
    if not names:
        print("用法: python native/tools/diag_pool_removed.py 名字...")
        raise SystemExit(2)

    all_rows = load_all()
    on_disk = {p.stem for p in SKILLS_DIR.glob("*.json") if not p.stem.startswith("_")}
    pre_refs: dict[str, list[tuple[str, str]]] = {}
    for r in all_rows:
        if r["pre_species"] and r["form"] != "首领形态":
            pre_refs.setdefault(r["pre_species"], []).append((r["name"], r["form"]))

    lines: list[str] = []
    for name in names:
        matches = [r for r in all_rows if r["name"] == name]
        if not matches:
            lines.append(f"[{name}] 文件已不存在于 data/sprites（被删除或改名）")
            continue
        for r in matches:
            reasons = []
            if r["form"] == "首领形态":
                reasons.append("A: 首领形态（规则排除）")
            if r["number"] and r["number"] in pre_refs:
                succ = pre_refs[r["number"]]
                reasons.append(
                    f"B: 是更高形态的前形态 → 被 {succ} 取代（number={r['number']}）")
            ids = list(set(r["skills_raw"] + r["stone"]))
            resolved = [SKILL_ID_TO_NAME[s] for s in ids if SKILL_ID_TO_NAME.get(s) in on_disk]
            unmatched = [s for s in ids if SKILL_ID_TO_NAME.get(s) not in on_disk]
            if not resolved:
                reasons.append(f"C: 技能全部无法解析（{len(ids)} 个 id 无一命中）")
            lines.append(
                f"[{name}] file={r['file']} form={r['form'] or '-'} "
                f"number={r['number'] or '-'} pre={r['pre_species'] or '-'} "
                f"技能 {len(resolved)}/{len(ids)} 可解析"
                + (f" 未命中示例={unmatched[:5]}" if unmatched and not resolved else "")
            )
            lines.append(f"    → 原因: {'; '.join(reasons) if reasons else '仍在池中（无排除规则命中）'}")

    text = "\n".join(lines)
    print(text)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"\n摘要: {OUT}")


if __name__ == "__main__":
    main()
