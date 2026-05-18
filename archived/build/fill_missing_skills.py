"""scripts/build/fill_missing_skills.py — 用已有数据填充缺失精灵的技能

规则：
- 棋棋进化链 (#188-192): 仅同编号形式间拷贝（不同进化阶段技能不同）
- 其他精灵: 同进化链或同编号形式间拷贝（技能相同）
"""

import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"

QI_CHAIN_NUMBERS = {"188", "189", "190", "191", "192"}


def is_sprite_json(name: str) -> bool:
    if name.startswith("_") or not name.endswith(".json"):
        return False
    for kw in ["_精灵技能", "_血脉技能", "_可学技能石"]:
        if kw in name:
            return False
    return True


def main():
    # 加载所有精灵
    all_sprites = []
    for sp in sorted(SPRITES_DIR.glob("*.json")):
        if not is_sprite_json(sp.name):
            continue
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        all_sprites.append((sp, data))

    print(f"加载精灵: {len(all_sprites)} 个")

    # 构建索引: number -> [sprite_paths_with_data]
    by_number: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for sp_path, sp_data in all_sprites:
        num = str(sp_data.get("number", "")).strip()
        if num:
            by_number[num].append((sp_path, sp_data))

    # 构建进化链: number -> chain_representative (第一个有技能的祖先或后代)
    # 先建立 pre_species 反向索引: who evolves from me
    children_of: dict[str, list[str]] = defaultdict(list)
    for sp_path, sp_data in all_sprites:
        num = str(sp_data.get("number", ""))
        pre = str(sp_data.get("pre_species", "")).strip()
        if pre and num:
            children_of[pre].append(num)

    def get_all_chain_numbers(num: str) -> set[str]:
        """获取包含该编号的完整进化链的所有编号。"""
        visited = set()
        stack = [num]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            # 找前身
            for sp_path, sp_data in all_sprites:
                if str(sp_data.get("number", "")) == n:
                    pre = str(sp_data.get("pre_species", "")).strip()
                    if pre and pre not in visited:
                        stack.append(pre)
                    break
            # 找后代
            for child in children_of.get(n, []):
                if child not in visited:
                    stack.append(child)
        return visited

    def find_source(num: str, in_qi_chain: bool) -> tuple[Path, dict] | None:
        """找到有技能数据的源精灵。"""
        candidates = by_number.get(num, [])
        if in_qi_chain:
            # 只找同编号的
            for sp_path, sp_data in candidates:
                if sp_data.get("skills"):
                    return (sp_path, sp_data)
            return None

        # 先在同编号内找
        for sp_path, sp_data in candidates:
            if sp_data.get("skills"):
                return (sp_path, sp_data)

        # 在进化链内找
        chain_nums = get_all_chain_numbers(num)
        for cn in sorted(chain_nums):
            if cn == num:
                continue
            for sp_path, sp_data in by_number.get(cn, []):
                if sp_data.get("skills"):
                    return (sp_path, sp_data)

        return None

    filled = 0
    no_source = 0

    for sp_path, sp_data in all_sprites:
        num = str(sp_data.get("number", "")).strip()
        if not num:
            continue

        # 跳过已有技能的
        if sp_data.get("skills"):
            continue

        in_qi_chain = num in QI_CHAIN_NUMBERS
        source = find_source(num, in_qi_chain)

        if not source:
            no_source += 1
            print(f"  ✗ 无源: #{num} {sp_data['name']} ({sp_data.get('form', '')})")
            continue

        src_path, src_data = source

        # 拷贝三个字段
        modified = False
        for field in ["skills", "bloodline_skills", "stone_skills"]:
            if field in src_data and field not in sp_data:
                sp_data[field] = src_data[field]
                modified = True

        if modified:
            sp_path.write_text(
                json.dumps(sp_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            filled += 1
            sc = len(sp_data.get("skills", []))
            bc = len(sp_data.get("bloodline_skills", {}))
            stc = len(sp_data.get("stone_skills", []))
            src_name = src_data.get("name", "?")
            print(f"  ✓ #{num} {sp_data['name']}（{sp_data.get('form','')}）← {src_name}  技能:{sc} 血脉:{bc} 石:{stc}")
        else:
            no_source += 1

    print(f"\n完成: 填充 {filled} 个, 无源 {no_source} 个")


if __name__ == "__main__":
    main()
