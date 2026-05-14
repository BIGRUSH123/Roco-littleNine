"""scripts/build/parse_raw_skills.py — 解析 raw/技能.txt 并更新 data/sprites 精灵JSON

文件格式：精灵名(：可选) \n 精灵技能： ... \n 血脉技能： ... \n 可学技能石(可学技能)： ... \n --------------------精灵名
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"
RAW_FILE = PROJECT_ROOT / "raw" / "技能.txt"

# Section keywords (with colon)
SECTION_KEYWORDS = {"精灵技能", "血脉技能", "可学技能", "可学技能石"}
KNOWN_SECTIONS = ["精灵技能：", "血脉技能：", "可学技能石：", "可学技能："]
# 可学技能（无"石"）视为精灵技能
NORMAL_SKILL_KEYWORDS = ["精灵技能：", "精灵技能:", "可学技能：", "可学技能:"]
STONE_SKILL_KEYWORDS = ["可学技能石：", "可学技能石:"]


def build_skill_name_index() -> dict[str, int]:
    """构建技能名 → ID 映射。"""
    index = {}
    for sf in SKILLS_DIR.glob("*.json"):
        if sf.name.startswith("_"):
            continue
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
            index[d["name"]] = d["id"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return index


def parse_skills_section(lines: list[str], start: int, end: int) -> list[dict]:
    """解析技能区块，返回 [{name, element, energy, type, power, description}, ...]"""
    skills = []
    i = start
    while i < end:
        line = lines[i].strip()

        # Skip empty lines, LV markers, and section headers
        if not line or re.match(r'^LV\d+', line):
            i += 1
            continue

        # Stop if we hit a separator line or next section
        if re.match(r'^-{5,}$', line):
            break
        # Check if this line is a section keyword
        line_clean = line.rstrip('：:').strip()
        if line_clean in SECTION_KEYWORDS:
            break

        # Match skill icon line: optional "图标 宠物 属性 X.png" prefix, then "技能图标 NAME.png"
        m = re.match(r'(?:.*?属性\s*(\S+)\.png)?\s*技能图标\s*(\S+)\.png', line)
        if not m:
            i += 1
            continue

        element = m.group(1) or ""
        skill_name = m.group(2)

        i += 1
        # Skip the skill name text line if present
        if i < end and lines[i].strip() == skill_name:
            i += 1

        # Parse energy, type, power, description
        energy = ""
        skill_type = ""
        power = ""
        desc = ""

        while i < end:
            cl = lines[i].strip()
            if not cl:
                i += 1
                continue

            # Check if next skill starts
            if '技能图标' in cl:
                break
            if re.match(r'^LV\d+', cl):
                break
            if re.match(r'^-{5,}$', cl):
                break
            cl_clean = cl.rstrip('：:').strip()
            if cl_clean in SECTION_KEYWORDS:
                break

            # Energy: 星星背景.pngN
            em = re.search(r'星星背景\.png(\d+)', cl)
            if em:
                energy = em.group(1)
                i += 1
                continue

            # Type: 类别 TYPE.pngTYPE
            tm = re.search(r'类别\s*(\S+)\.png', cl)
            if tm:
                skill_type = tm.group(1)
                i += 1
                # Next line is power value (number)
                if i < end:
                    power_line = lines[i].strip()
                    if re.match(r'^\d+$', power_line):
                        power = power_line
                        i += 1
                continue

            # Description
            if cl.startswith('✦'):
                desc = cl
                i += 1
                break

            i += 1

        skills.append({
            "name": skill_name,
            "element": element,
            "energy": energy,
            "type": skill_type,
            "power": power,
            "description": desc,
        })

    return skills


def find_sprite_json(raw_name: str) -> Path | None:
    """根据 raw 文件中的精灵名（含形态）查找对应的JSON文件。"""
    # 1. 精确匹配 JSON 内 name 字段
    for sp in SPRITES_DIR.glob("*.json"):
        if sp.name.startswith("_") or "精灵技能" in sp.name or "血脉技能" in sp.name or "可学技能石" in sp.name:
            continue
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
            if d.get("name") == raw_name:
                return sp
        except (OSError, json.JSONDecodeError):
            pass

    # 2. raw_name 是文件名的一部分（如 丢丢（草地附近的样子） 匹配 44_丢丢（草地附近的样子）.json）
    for sp in SPRITES_DIR.glob("*.json"):
        if sp.name.startswith("_") or "精灵技能" in sp.name or "血脉技能" in sp.name or "可学技能石" in sp.name:
            continue
        if raw_name in sp.name:
            return sp

    # 3. raw_name 去掉括号内容后匹配 name 字段（如 棋契陛下 匹配 name=棋契陛下 的文件）
    base_name = re.split(r'[（(]', raw_name)[0].strip()
    for sp in SPRITES_DIR.glob("*.json"):
        if sp.name.startswith("_") or "精灵技能" in sp.name or "血脉技能" in sp.name or "可学技能石" in sp.name:
            continue
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
            if d.get("name") == base_name:
                return sp
        except (OSError, json.JSONDecodeError):
            pass

    return None


def find_section_line(lines: list[str], keywords: list[str], search_from: int, block_end: int) -> int | None:
    """查找第一个匹配的 section 行号。"""
    for i in range(search_from, block_end):
        stripped = lines[i].strip()
        for kw in keywords:
            if stripped.startswith(kw):
                return i
    return None


def get_element_from_skill_json(skill_name: str) -> str:
    """从 data/skills 中查找技能的系别。"""
    for sf in SKILLS_DIR.glob("*.json"):
        if sf.name.startswith("_"):
            continue
        try:
            sd = json.loads(sf.read_text(encoding="utf-8"))
            if sd["name"] == skill_name:
                return sd.get("element", "")
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return ""


def main():
    name_to_id = build_skill_name_index()
    print(f"技能索引: {len(name_to_id)} 条")

    text = RAW_FILE.read_text(encoding="utf-8")
    all_lines = text.splitlines()

    # 找到所有分隔线（全 --- 的行）
    separators = []
    for idx, line in enumerate(all_lines):
        stripped = line.strip()
        if re.match(r'^-{5,}$', stripped):
            separators.append(idx)

    # 块划分: [0, sep1), [sep1+1, sep2), ..., [sepN+1, end)
    block_starts = [0] + [s + 1 for s in separators]
    block_ends = separators + [len(all_lines)]

    updated = 0
    not_found = 0
    no_skill_match = []

    for bs, be in zip(block_starts, block_ends):
        # 找到精灵名：第一个非空、非分隔线的行
        # 名可能以 ： 结尾（如 翠顶夫人：），也可能无冒号（如 丢丢（草地附近的样子））
        name_idx = None
        raw_name = ""
        for i in range(bs, be):
            stripped = all_lines[i].strip()
            if not stripped:
                continue
            if re.match(r'^-{5,}$', stripped):
                continue
            # Section keywords are not names
            line_clean = stripped.rstrip('：:').strip()
            if line_clean in SECTION_KEYWORDS:
                # This block has no sprite name (starts directly with a section)
                break
            name_idx = i
            raw_name = stripped.rstrip('：:').strip()
            break

        if not raw_name:
            # Try to use the previous sprite's name (continuation block)
            continue

        sprite_path = find_sprite_json(raw_name)
        if not sprite_path:
            print(f"  ✗ 未找到: {raw_name}")
            not_found += 1
            continue

        sprite_data = json.loads(sprite_path.read_text(encoding="utf-8"))

        # 找三个 section
        search_start = name_idx + 1 if name_idx is not None else bs
        sec_normal = find_section_line(all_lines, NORMAL_SKILL_KEYWORDS, search_start, be)
        sec_blood = find_section_line(all_lines, ["血脉技能：", "血脉技能:"], search_start, be)
        sec_stone = find_section_line(all_lines, STONE_SKILL_KEYWORDS, search_start, be)

        modified = False

        # 解析精灵技能
        if sec_normal is not None:
            next_sec = be
            for s in [sec_blood, sec_stone]:
                if s is not None and s < next_sec:
                    next_sec = s
            skills = parse_skills_section(all_lines, sec_normal + 1, next_sec)
            skill_ids = []
            for s in skills:
                sid = name_to_id.get(s["name"])
                if sid:
                    skill_ids.append(sid)
                else:
                    no_skill_match.append((raw_name, s["name"], "精灵技能"))
            if skill_ids:
                sprite_data["skills"] = skill_ids
                modified = True

        # 解析血脉技能 → {element: id} 格式
        if sec_blood is not None:
            next_sec = be
            for s in [sec_stone, sec_normal]:
                if s is not None and s > sec_blood and s < next_sec:
                    next_sec = s
            skills = parse_skills_section(all_lines, sec_blood + 1, next_sec)
            bloodline: dict[str, int] = {}
            for s in skills:
                sid = name_to_id.get(s["name"])
                if not sid:
                    no_skill_match.append((raw_name, s["name"], "血脉技能"))
                    continue
                elem = s["element"] or get_element_from_skill_json(s["name"])
                if elem:
                    bloodline[elem] = sid
                else:
                    print(f"  警告: 无法确定系别 '{s['name']}' ({raw_name})")
            if bloodline:
                sprite_data["bloodline_skills"] = bloodline
                modified = True

        # 解析可学技能石
        if sec_stone is not None:
            next_sec = be
            for s in [sec_normal, sec_blood]:
                if s is not None and s > sec_stone and s < next_sec:
                    next_sec = s
            skills = parse_skills_section(all_lines, sec_stone + 1, next_sec)
            stone_ids = []
            for s in skills:
                sid = name_to_id.get(s["name"])
                if sid:
                    stone_ids.append(sid)
                else:
                    no_skill_match.append((raw_name, s["name"], "可学技能石"))
            if stone_ids:
                sprite_data["stone_skills"] = stone_ids
                modified = True

        if modified:
            sprite_path.write_text(
                json.dumps(sprite_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated += 1
            sc = len(sprite_data.get("skills", []))
            bc = len(sprite_data.get("bloodline_skills", {}))
            stc = len(sprite_data.get("stone_skills", []))
            print(f"  ✓ {raw_name} → 技能:{sc} 血脉:{bc} 石:{stc}")
        else:
            print(f"  - {raw_name}: 无数据")

    print(f"\n完成: 更新 {updated} 个, 未找到 {not_found} 个")

    if no_skill_match:
        print(f"\n未匹配技能名 ({len(no_skill_match)}):")
        for sn, sk, sec in no_skill_match[:20]:
            print(f"  {sn} / {sec}: '{sk}'")


if __name__ == "__main__":
    main()
