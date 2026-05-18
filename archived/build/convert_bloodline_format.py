"""scripts/build/convert_bloodline_format.py — 将 bloodline_skills 从 [id, ...] 转为 {element: id, ...}"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"


def build_id_to_name() -> dict[int, str]:
    """构建技能 ID → 名称 映射。"""
    mapping = {}
    for sf in SKILLS_DIR.glob("*.json"):
        if sf.name.startswith("_"):
            continue
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
            mapping[d["id"]] = d["name"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return mapping


def main():
    id_to_name = build_id_to_name()
    print(f"技能索引: {len(id_to_name)} 条")

    converted = 0
    skipped_no_scrape = 0
    skipped_no_bl = 0

    for sprite_path in sorted(SPRITES_DIR.glob("*.json")):
        name = sprite_path.name
        if name.startswith("_") or "_精灵技能" in name or "_血脉技能" in name or "_可学技能石" in name:
            continue

        try:
            sprite_data = json.loads(sprite_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        bl_skills = sprite_data.get("bloodline_skills")
        if not bl_skills or not isinstance(bl_skills, list):
            # 空数组或已是 dict 格式 → 跳过
            skipped_no_bl += 1
            continue

        # 查找对应的 _血脉技能.json
        number = str(sprite_data.get("number", "")).strip()
        if number:
            scrape_files = list(SPRITES_DIR.glob(f"{number}_*_血脉技能.json"))
        else:
            scrape_files = []

        if not scrape_files:
            skipped_no_scrape += 1
            continue

        try:
            scraped = json.loads(scrape_files[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped_no_scrape += 1
            continue

        # 构建 name → element 映射
        name_to_elem = {}
        for entry in scraped:
            sn = entry.get("name", "").strip()
            se = entry.get("element", "").strip()
            if sn and se:
                name_to_elem[sn] = se

        # 转换: ID → (name → element)
        new_bl: dict[str, int] = {}
        for skill_id in bl_skills:
            skill_name = id_to_name.get(skill_id)
            if not skill_name:
                print(f"  警告: 未知技能ID {skill_id} (精灵 {sprite_data.get('name')})")
                continue
            elem = name_to_elem.get(skill_name)
            if not elem:
                print(f"  警告: 未找到元素 '{skill_name}' (精灵 {sprite_data.get('name')})")
                continue
            new_bl[elem] = skill_id

        sprite_data["bloodline_skills"] = new_bl

        sprite_path.write_text(
            json.dumps(sprite_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        converted += 1
        print(f"  ✓ {sprite_data['name']} → {len(new_bl)} 血脉技能")

    print(f"\n完成: 转换 {converted} 个, 无血脉数据 {skipped_no_bl} 个, 无爬取文件 {skipped_no_scrape} 个")


if __name__ == "__main__":
    main()
