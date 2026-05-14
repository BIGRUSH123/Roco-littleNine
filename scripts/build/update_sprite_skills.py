"""scripts/build/update_sprite_skills.py — 将爬取的技能ID写入精灵JSON

读取 data/sprites/{编号}_{名称}_精灵技能.json 等文件，
将技能名转换为ID后写入对应精灵JSON的 skills / bloodline_skills / stone_skills 字段。
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"

# 三个爬取来源 → sprite JSON 字段名
SOURCE_TO_FIELD = {
    "精灵技能": "skills",
    "血脉技能": "bloodline_skills",
    "可学技能石": "stone_skills",
}


def build_skill_name_index() -> dict[str, int]:
    """构建技能名 → ID 映射。"""
    index = {}
    for sf in sorted(SKILLS_DIR.glob("*.json")):
        if sf.name.startswith("_"):
            continue
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
            index[d["name"]] = d["id"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return index


def main():
    name_to_id = build_skill_name_index()
    print(f"技能索引: {len(name_to_id)} 条")

    # 收集所有精灵 JSON（排除技能文件和索引文件）
    sprite_files = [
        sf for sf in sorted(SPRITES_DIR.glob("*.json"))
        if not sf.name.startswith("_")
        and "_精灵技能" not in sf.name
        and "_血脉技能" not in sf.name
        and "_可学技能石" not in sf.name
    ]
    print(f"精灵 JSON: {len(sprite_files)} 个")

    updated = 0
    no_skills = 0

    for sprite_path in sprite_files:
        # 读取精灵 JSON
        try:
            sprite_data = json.loads(sprite_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        number = str(sprite_data.get("number", "")).strip()
        if not number:
            continue

        modified = False

        for suffix, field_name in SOURCE_TO_FIELD.items():
            # 查找该编号的爬取技能文件
            skill_files = list(SPRITES_DIR.glob(f"{number}_*_{suffix}.json"))
            if not skill_files:
                # 尝试用 name 查找（部分文件可能以名称开头）
                name = sprite_data.get("name", "")
                if name:
                    skill_files = list(SPRITES_DIR.glob(f"*_{name}_{suffix}.json"))

            if not skill_files:
                continue

            # 读取第一个匹配的技能文件（同一编号的技能相同）
            try:
                scraped_skills = json.loads(
                    skill_files[0].read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue

            # 转换技能名为ID
            skill_ids = []
            for s in scraped_skills:
                skill_name = s.get("name", "").strip()
                if not skill_name:
                    continue
                sid = name_to_id.get(skill_name)
                if sid:
                    skill_ids.append(sid)
                else:
                    print(f"  警告: 未知技能名 '{skill_name}' (精灵 {number})")

            if skill_ids:
                sprite_data[field_name] = skill_ids
                modified = True

        if modified:
            # 写回
            sprite_path.write_text(
                json.dumps(sprite_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated += 1
            skills_count = len(sprite_data.get("skills", []))
            bl_count = len(sprite_data.get("bloodline_skills", []))
            st_count = len(sprite_data.get("stone_skills", []))
            print(f"  ✓ {sprite_data['name']} → 技能:{skills_count} 血脉:{bl_count} 石:{st_count}")
        else:
            no_skills += 1

    print(f"\n完成: 更新 {updated} 个, 无技能数据 {no_skills} 个")


if __name__ == "__main__":
    main()
