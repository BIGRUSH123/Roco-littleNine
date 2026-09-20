"""预构建的精灵随机池 —— 最终形态 + 全部外观 + 非首领形态。

首次运行扫描 data/sprites/ 目录，将过滤后的池保存为同级
sprite_random_pool.json 文件。后续运行时直接加载 JSON 文件，
避免重复扫描所有精灵文件。

池条目 = （名字, 外观）：每个外观是独立条目（面板/技能可不同）。
过滤规则（form 只作阶段标记，外观在 appearance 字段）：
  1. 首领阶段（form 含『首领』）不入选——PVE 首领不进训练池；
  2. 前形态不入选：若某编号 N 是其他非首领条目的 pre_species，
     说明存在更高形态，则编号 N 的基础阶段剔除；
  3. 同编号第二形态（pre_species == 自身编号）是该编号的最终形态，保留。
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POOL_FILE = _HERE / "sprite_random_pool.json"
_PROJECT_ROOT = _HERE.parent.parent.parent.parent

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME

SPRITE_RANDOM_POOL: dict[str, list[str]] = {}
"""精灵名 → 可用技能名列表（仅保留最高形态，排除首领形态）"""


def _build_pool() -> dict[str, list[str]]:
    sprites_dir = _PROJECT_ROOT / "data" / "sprites"
    skills_dir = _PROJECT_ROOT / "data" / "skills"
    on_disk: set[str] = {p.stem for p in skills_dir.glob("*.json") if not p.stem.startswith("_")}

    raw: list[dict] = []
    for path in sprites_dir.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            continue
        name = data.get("name", path.stem.split("_", 1)[-1])
        form = str(data.get("form", "") or "").strip()
        appearance = str(data.get("appearance", "") or "").strip()
        if not appearance and form and "首领" not in form:
            appearance, form = form, ""  # 兼容旧数据：form 存的是外观名
        number = str(data.get("number", "")).strip()
        pre_species = str(data.get("pre_species", "")).strip()
        skill_ids = list(set(data.get("skills", []) + data.get("stone_skills", [])))
        skill_names: list[str] = []
        for sid in skill_ids:
            sname = SKILL_ID_TO_NAME.get(sid)
            if sname and sname in on_disk:
                skill_names.append(sname)
        raw.append({
            "name": name,
            "form": form,
            "appearance": appearance,
            "number": number,
            "pre_species": pre_species,
            "skills": skill_names,
        })

    def is_boss(entry: dict) -> bool:
        return "首领" in entry["form"]

    # 被他人进化走的编号。首领阶段不参与：同编号首领化是"形态切换"而非进化链，
    # 基础形态与首领形态在战斗中都是合法形态，两者都进池。
    evolved_from: set[str] = {
        e["pre_species"] for e in raw
        if e["pre_species"] and e["pre_species"] != e["number"] and not is_boss(e)
    }

    result: dict[str, list[str]] = {}
    for e in raw:
        if not e["skills"]:
            continue
        pre, number = e["pre_species"], e["number"]
        if pre and pre == number:
            pass  # 同编号第二形态（首领/觉醒）→ 保留
        elif number in evolved_from:
            continue  # 该编号是某他人形态的前形态 → 剔除
        display = f"{e['name']}（{e['appearance']}）" if e["appearance"] else e["name"]
        result[display] = e["skills"]
    return result


def _init() -> None:
    global SPRITE_RANDOM_POOL
    if _POOL_FILE.exists():
        SPRITE_RANDOM_POOL.update(json.loads(_POOL_FILE.read_text(encoding="utf-8")))
    else:
        pool = _build_pool()
        _POOL_FILE.write_text(
            json.dumps(pool, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        SPRITE_RANDOM_POOL.update(pool)


_init()