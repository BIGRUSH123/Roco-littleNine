# -*- coding: utf-8 -*-
"""native/tools/migrate_appearance_field.py — 为精灵文件新增 appearance 字段。

映射规则:
  - 同一 name 组内存在 form == '首领形态' 的文件 → 该组是首领阶段，
    所有条目 form='首领形态'；
  - 其余组 form=''（基础阶段）；
  - appearance = 原 form 值（原 form 为 '' 或 '首领形态' 时 appearance=''）。

结果：form 只承担阶段标记（''/'首领形态'），外观独立到 appearance。
幂等：已有 appearance 字段的文件跳过。

用法:
  python native/tools/migrate_appearance_field.py --dry-run 名字...   # 预览指定精灵
  python native/tools/migrate_appearance_field.py --dry-run          # 预览全部统计
  python native/tools/migrate_appearance_field.py                    # 实际写入
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

SPRITES_DIR = Path("data/sprites")
BOSS_FORM = "首领形态"


def load_files() -> list[dict]:
    items = []
    for p in sorted(SPRITES_DIR.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items.append({"path": p, "data": d})
    return items


def compute_migration(items: list[dict]) -> list[tuple[Path, dict, dict]]:
    """返回 [(path, old_data, new_data)]。"""
    boss_names = {
        it["data"].get("name", "")
        for it in items
        if str(it["data"].get("form", "")).strip() == BOSS_FORM
    }
    out = []
    for it in items:
        d = dict(it["data"])
        if "appearance" in d and "appearance" in it["data"]:
            continue  # 已迁移
        old_form = str(d.get("form", "") or "").strip()
        is_boss_group = d.get("name", "") in boss_names
        if old_form == BOSS_FORM:
            new_form, new_appearance = BOSS_FORM, ""
        elif old_form:
            new_form = BOSS_FORM if is_boss_group else ""
            new_appearance = old_form
        else:
            new_form = BOSS_FORM if is_boss_group else ""
            new_appearance = ""
        new = dict(d)
        new["form"] = new_form
        new["appearance"] = new_appearance
        # 字段顺序：form 后紧跟 appearance，便于人工阅读
        ordered = {}
        for k, v in new.items():
            ordered[k] = v
            if k == "form":
                ordered["appearance"] = new["appearance"]
        if "appearance" not in ordered:
            ordered["appearance"] = new["appearance"]
        out.append((it["path"], d, ordered))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("names", nargs="*", help="只预览这些精灵名（含外观组）")
    args = ap.parse_args()

    items = load_files()
    plans = compute_migration(items)
    if args.names:
        plans = [p for p in plans if p[1].get("name") in set(args.names)]

    stats = Counter()
    for path, old, new in plans:
        key = (old.get("form", ""), new["form"], new["appearance"])
        stats[key] += 1

    print(f"待迁移文件: {len(plans)} / 总 {len(items)}")
    print("映射统计 (旧form → 新form / appearance):")
    for (of, nf, na), cnt in stats.most_common(30):
        print(f"  {cnt:>4}  {of!r:>22} → {nf!r:>12} / {na!r}")
    if args.names:
        print("\n样本明细:")
        for path, old, new in plans[:40]:
            print(f"  {path.name}: form={old.get('form')!r} → "
                  f"form={new['form']!r}, appearance={new['appearance']!r}")

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return
    for path, _old, new in plans:
        path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"\n已写入 {len(plans)} 个文件")


if __name__ == "__main__":
    main()
