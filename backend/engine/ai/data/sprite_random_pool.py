"""预构建的精灵随机池 —— 最高形态 + 非首领形态。

首次运行扫描 data/sprites/ 目录，将过滤后的池保存为同级
sprite_random_pool.json 文件。后续运行时直接加载 JSON 文件，
避免重复扫描所有精灵文件。
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
        form = data.get("form", "")
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
            "number": number,
            "pre_species": pre_species,
            "skills": skill_names,
        })

    pre_species_refs: set[str] = {
        s["pre_species"] for s in raw
        if s["pre_species"] and s["form"] != "首领形态"
    }

    result: dict[str, list[str]] = {}
    for s in raw:
        if s["form"] == "首领形态":
            continue
        if s["number"] in pre_species_refs:
            continue
        if s["skills"]:
            result[s["name"]] = s["skills"]
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