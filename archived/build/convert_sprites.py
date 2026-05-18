"""CSV → /data/sprites/*.json 一次性转换"""
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CSV_PATH = ROOT / "wiki" / "meta" / "sprites.csv"
OUT_DIR = ROOT / "data" / "sprites"

def run():
    if not CSV_PATH.is_file():
        print(f"CSV not found: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 先收集所有 number→name 映射，用于填充 pre_species
    # 进化链通过编号连续推断：n→n+1 属于同一链则 pre_species(n+1)=n
    all_rows = []
    with open(CSV_PATH, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            all_rows.append(row)

    # 构建 number 集合用于 pre_species 查找
    numbers = {row['no'].strip() for row in all_rows if row['no'].strip()}

    cnt = 0
    for row in all_rows:
        name = row.get('name', '').strip()
        if not name:
            continue
        number = row.get('no', '').strip()
        form = row.get('form', '').strip()
        attr_raw = row.get('attributes', '').strip()
        attributes = [a.strip() for a in attr_raw.split(',') if a.strip()]

        # pre_species: 上溯编号 n-1（同一链的进化通常编号连续）
        # 默认空，需手动修正跨链/不连续的情况
        pre_species = ""
        if number:
            try:
                prev_no = str(int(number) - 1)
                if prev_no in numbers:
                    pre_species = prev_no
            except ValueError:
                pass

        data = {
            "number": number,
            "name": name,
            "form": form,
            "attributes": attributes,
            "hp": int(row.get('hp', '0') or '0'),
            "atk": int(row.get('atk', '0') or '0'),
            "sp_atk": int(row.get('sp_atk', '0') or '0'),
            "def": int(row.get('def', '0') or '0'),
            "sp_def": int(row.get('sp_def', '0') or '0'),
            "speed": int(row.get('spd', '0') or '0'),
            "ability": row.get('ability_name', '').strip(),
            "pre_species": pre_species,
        }

        # 文件名: {number}_{name}.json，有形态则 {number}_{name}（{form}）.json
        if form:
            filename = f'{number}_{name}（{form}）.json'
        else:
            filename = f'{number}_{name}.json'

        with open(OUT_DIR / filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        cnt += 1

    print(f"Converted {cnt} sprites → {OUT_DIR}")

if __name__ == '__main__':
    run()
