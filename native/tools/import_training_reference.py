# -*- coding: utf-8 -*-
"""native/tools/import_training_reference.py — 导入线上 wiki 的「培养参考」数据。

数据源（wiki.biligame.com/nrc，2026-09-20 定位）：
  - 模块:Pets/data/TrainingReference —— 每只精灵（按 pet id）在 pvp / world 两种
    模式下，血脉 / 性格 / 技能 / 天赋 的**使用分布排名**（`{标签索引, 权重}`），
    即页面上「培养参考」的横向条形图数据；用 `模块:Pets/data/Config`（血脉词表）
    与自身 labels 解索引。
  - 模块:Pets/data/Catalog —— pet id → 精灵名。

为什么需要它：`train.py::_sprite_roles` 目前用「双攻前 40% / 耐久前 30% /
工具技能占比 ≥60%」的阈值分桶，**没有兜底桶**，导致 344 个池条目里有 114 个
（77 个物种）永远进不了随机阵容，约 19% 物种在训练数据里零出场。改用 wiki 的
推荐培养（玩家实际配置分布）做定位分类，既语义正确（wiki 是权威）又不会漏精灵。

输出 backend/engine/ai/data/training_reference.json：
  {"fetched_at": ..., "labels": {...}, "pets": {"<精灵名>": {"id": 3001,
    "pvp": {"talent": [["生命", 8387], ...], "nature": [...], "skill": [...],
            "blood": [...]}, "world": {...}}}}

用法:
  python native/tools/import_training_reference.py            # 抓取并写文件
  python native/tools/import_training_reference.py --stats    # 只打印覆盖统计
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "backend" / "engine" / "ai" / "data" / "training_reference.json"
CACHE = ROOT / "backend" / "engine" / "ai" / "data" / "nrc_cache"

BASE = "https://wiki.biligame.com/nrc"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RocoLittleNine/1.0"}
MODULES = {
    "TrainingReference": "模块:Pets/data/TrainingReference",
    "Catalog": "模块:Pets/data/Catalog",
    "Config": "模块:Pets/data/Config",
}
RETRY_CODES = {429, 500, 502, 503, 504, 567}


def fetch(session: requests.Session, title: str, *, attempts: int = 6) -> str:
    """带指数退避抓模块源码；成功后就地缓存，失败则回落到缓存。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{title}.lua"
    url = f"{BASE}/{quote(MODULES[title])}"
    delay = 1.0
    for _ in range(attempts):
        try:
            r = session.get(url, params={"action": "raw"}, headers=UA, timeout=60)
        except requests.RequestException as exc:
            print(f"   网络异常 {exc.__class__.__name__}，退避 {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code in RETRY_CODES:
            print(f"   HTTP {r.status_code}，退避 {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 200 and r.text.strip():
            path.write_text(r.text, encoding="utf-8")
            print(f"   抓取成功 {title}: {len(r.text)} 字符", flush=True)
            return r.text
        print(f"   HTTP {r.status_code}（放弃本次）", flush=True)
        break
    if path.exists():
        print(f"   回落到本地缓存 {path.name}", flush=True)
        return path.read_text(encoding="utf-8")
    raise SystemExit(f"模块抓取失败且无缓存: {title}")


def find_block(text: str, key: str, start: int = 0) -> tuple[int, str] | tuple[None, None]:
    """取 key={...} 的花括号配平块（压缩 Lua 无换行，只能靠配平）。"""
    for m in re.finditer(re.escape(key) + r"\s*=\s*\{", text[start:]):
        i = start + m.start()
        if i > 0 and (text[i - 1].isalnum() or text[i - 1] in "._"):
            continue
        j = start + m.end() - 1
        depth = 0
        for k in range(j, len(text)):
            c = text[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i, text[j:k + 1]
    return None, None


def parse_ranked(block: str) -> list[list[int]]:
    """解析 {{1,8387},{2,7870},...} → [[1,8387],[2,7870],...]。"""
    return [[int(a), int(b)] for a, b in re.findall(r"\{(\d+),(\d+)\}", block)]


def parse_labels(ref: str) -> dict[str, list[str]]:
    """labels 块：blood/nature/skill/talent 的 索引→名字（索引从 1 开始）。"""
    _, labels = find_block(ref, "labels")
    out: dict[str, list[str]] = {}
    if not labels:
        return out
    for key in ("blood", "nature", "skill", "talent"):
        _, blk = find_block(labels, key)
        if not blk:
            continue
        slots: dict[int, str] = {}
        for m in re.finditer(r"\[(\d+)\]\s*=\s*\{([^{}]*)\}", blk):
            nm = re.search(r'name="([^"]*)"', m.group(2))
            slots[int(m.group(1))] = nm.group(1) if nm else ""
        top = max(slots) if slots else 0
        out[key] = [slots.get(i, "") for i in range(1, top + 1)]
    return out


def top_level_scalars(block: str) -> dict[str, str]:
    """块内深度 1 的标量字段（跳过嵌套表）——用于取精灵自己的 name/number。"""
    out: dict[str, str] = {}
    i, n, depth = 1, len(block), 1
    while i < n:
        c = block[i]
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        if depth == 1:
            m = re.match(r'([a-z_]+)\s*=\s*(?:"([^"]*)"|([^,{}]+))', block[i:])
            if m:
                out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3).strip()
                i += m.end()
                continue
        i += 1
    return out


def parse_catalog(catalog: str) -> list[dict]:
    """Catalog → [{id, number, name}]。

    **对齐键用编号（number）而不是名字**：同一编号下最多有 12 个形态条目，
    外观变体会重名（棋契陛下的白/黑棋分支…）；而编号在 Catalog 与本地
    data/sprites 是同一套（两边都是 466 个），可 1:1 join。
    pet_0000NN ↔ TrainingReference 的 id = 3000 + NN（由 Index.avatars 证实）。
    """
    rows: list[dict] = []
    for m in re.finditer(r"pet_(\d{6})\s*=\s*\{", catalog):
        seq = int(m.group(1))
        _, blk = find_block(catalog, f"pet_{m.group(1)}")
        if not blk:
            continue
        sc = top_level_scalars(blk)
        rows.append({"id": 3000 + seq,
                     "number": sc.get("number", ""),
                     "name": sc.get("name", ""),
                     "form": sc.get("form", ""),      # 外观名（如「起来鸭」「上弦的样子」）
                     "title": sc.get("title", "")})   # 完整名（如「鸭吉吉（起来鸭）」）
    return rows


def parse_pets(ref: str, labels: dict[str, list[str]]) -> dict[int, dict]:
    """pets[<id>] → {mode: {talent/nature/skill/blood: [[名字, 权重], ...]}}。"""
    _, pets = find_block(ref, "pets")
    if not pets:
        return {}
    out: dict[int, dict] = {}
    for m in re.finditer(r"\[(\d{3,5})\]\s*=\s*\{", pets):
        pid = int(m.group(1))
        i, blk = find_block(pets, f"[{pid}]")
        if not blk:
            continue
        entry: dict[str, dict] = {}
        # 顶层（默认/综合）四类
        flat: dict[str, list] = {}
        for cat in ("talent", "nature", "skill", "blood"):
            _, cb = find_block(blk, cat)
            if not cb:
                continue
            ranked = parse_ranked(cb)
            table = labels.get(cat, [])
            flat[cat] = [[table[idx - 1] if 0 < idx <= len(table) else str(idx), w]
                         for idx, w in ranked]
        if flat:
            entry["default"] = flat
        # 模式专属块（pvp / world）
        for mode in ("pvp", "world"):
            _, mb = find_block(blk, mode)
            if not mb:
                continue
            md: dict[str, list] = {}
            for cat in ("talent", "nature", "skill", "blood"):
                _, cb = find_block(mb, cat)
                if not cb:
                    continue
                table = labels.get(cat, [])
                md[cat] = [[table[idx - 1] if 0 < idx <= len(table) else str(idx), w]
                           for idx, w in parse_ranked(cb)]
            if md:
                entry[mode] = md
        if entry:
            out[pid] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="只打印统计，不抓取")
    args = ap.parse_args()

    if args.stats:
        data = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        s = requests.Session()
        print("抓取线上 wiki 模块…", flush=True)
        ref = fetch(s, "TrainingReference")
        catalog = fetch(s, "Catalog")
        config = fetch(s, "Config")

        labels = parse_labels(ref)
        print(f"  词表: talent={len(labels.get('talent', []))} nature={len(labels.get('nature', []))} "
              f"skill={len(labels.get('skill', []))} blood={len(labels.get('blood', []))}")
        rows = parse_catalog(catalog)
        pets = parse_pets(ref, labels)
        have_num = [r for r in rows if r["number"]]
        print(f"  Catalog 精灵 {len(rows)} 条（有编号 {len(have_num)}），"
              f"编号 {len({r['number'] for r in have_num})} 个；"
              f"TrainingReference 记录 {len(pets)} 条")

        # ── 按编号组织（同编号下的形态条目各自保留推荐培养）──
        by_number: dict[str, dict] = {}
        linked = 0
        for r in have_num:
            entry = pets.get(r["id"])
            if not entry:
                continue
            linked += 1
            node = by_number.setdefault(r["number"], {"number": r["number"], "entries": []})
            node["entries"].append({"id": r["id"], "name": r["name"],
                                    "form": r["form"], "title": r["title"], **entry})
        OUT.write_text(json.dumps({
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "source": f"{BASE}/{quote(MODULES['TrainingReference'])}",
            "labels": labels,
            "by_number": by_number,
        }, ensure_ascii=False), encoding="utf-8")
        data = {"by_number": by_number}
        print(f"  写出 {OUT}（{OUT.stat().st_size / 1024:.0f} KB）："
              f"编号 {len(by_number)} 个 / 关联条目 {linked} 条")

    # ── 覆盖统计：池子里的精灵有多少能对上推荐培养（按编号 join）──
    from backend.common.sprite_db import SpriteDB
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL

    by_number = data.get("by_number", {})
    db = SpriteDB(ROOT)
    pool_nums: dict[str, list[str]] = {}
    for name in SPRITE_RANDOM_POOL:
        sp = db.get(name)
        if sp and sp.number:
            pool_nums.setdefault(sp.number.zfill(3), []).append(name)

    hit = sorted(n for n in pool_nums if n in by_number)
    miss = sorted(n for n in pool_nums if n not in by_number)
    print(f"\n池子覆盖编号 {len(pool_nums)} 个：有推荐培养 {len(hit)} "
          f"（{len(hit) / max(1, len(pool_nums)):.1%}），缺 {len(miss)}")
    if miss:
        print(f"  缺数据的编号: {miss[:20]}")

    cats = ("talent", "nature", "skill", "blood")
    for mode in ("pvp", "world", "default"):
        cnt = {c: 0 for c in cats}
        for node in by_number.values():
            for e in node["entries"]:
                blk = e.get(mode) or {}
                for c in cats:
                    if blk.get(c):
                        cnt[c] += 1
        print(f"  模式 {mode:<8} " + "  ".join(f"{c}={cnt[c]}" for c in cats))

    for probe in ("011", "238"):
        node = by_number.get(probe)
        if not node:
            continue
        print(f"\n抽样 编号 {probe}（{len(node['entries'])} 条形态）:")
        for e in node["entries"][:3]:
            tal = (e.get("pvp") or e.get("default") or {}).get("talent", [])[:4]
            print(f"    pet_id={e['id']} {e['name']}: 天赋排名 {tal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
