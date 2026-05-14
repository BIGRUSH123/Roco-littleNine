"""scripts/build/add_skill_descriptions.py — 从 data/sprites 爬取数据中提取技能描述，写入 data/skills"""

import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"


def main():
    # 1. 从所有 *_技能*.json 中收集 {name: description}
    name_to_descs: dict[str, set[str]] = defaultdict(set)

    scrape_files = 0
    for sf in SPRITES_DIR.glob("*.json"):
        if sf.name.startswith("_"):
            continue
        if not ("_精灵技能" in sf.name or "_血脉技能" in sf.name or "_可学技能石" in sf.name):
            continue
        scrape_files += 1
        try:
            skills = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for s in skills:
            name = s.get("name", "").strip()
            desc = s.get("description", "").strip()
            if name and desc:
                name_to_descs[name].add(desc)

    print(f"爬取文件: {scrape_files} 个")
    print(f"收集技能描述: {len(name_to_descs)} 个技能")

    # 2. 检查重复描述
    dupes = 0
    for name, descs in name_to_descs.items():
        if len(descs) > 1:
            dupes += 1
            print(f"  重复描述: {name} → {descs}")
    print(f"有多个描述的技能: {dupes} 个")

    # 3. 写入 data/skills
    updated = 0
    no_desc = 0
    for sf in sorted(SKILLS_DIR.glob("*.json")):
        if sf.name.startswith("_"):
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        skill_name = data.get("name", "")
        descs = name_to_descs.get(skill_name)
        if not descs:
            no_desc += 1
            print(f"  无描述: {skill_name}")
            continue

        # 取第一个描述（如有多个，取最短的，因为可能是去掉了噪音）
        desc = min(descs, key=len) if len(descs) > 1 else list(descs)[0]
        data["description"] = desc

        sf.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        updated += 1

    print(f"\n完成: 写入 {updated} 个, 无描述 {no_desc} 个")


if __name__ == "__main__":
    main()
