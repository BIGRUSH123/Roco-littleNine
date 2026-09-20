"""export_battle_spec — 生成 BattleSpec JSON 供 Rust 引擎双跑对拍。

种子协议：random.seed(seed) 生成阵容 → random.seed(seed+1) 供对局内 RNG
（Python 端 random.seed(seed+1) 后 Battle.run；Rust 端 PyRandom(seed+1)）。

用法：env\\python.exe native/tools/export_battle_spec.py --seeds 1-200 --out native/gate_specs
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.common.formulas import StatsCalc
from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.differential.recorder import _seeded_item, _seeded_teams
from backend.sim.factory import SimFactory

ELEMENTAL_BLOODLINES = [
    "普通", "火", "水", "草", "电", "冰", "地", "石",
    "武", "虫", "翼", "萌", "毒", "幽", "恶", "幻",
    "光", "龙", "机械",
]


def species_dict(ss) -> dict:
    return {
        "name": ss.name,
        "number": ss.number,
        "attributes": ss.attributes,
        "bloodline": ss.bloodline,
        "ability": ss.ability,
        "ability_id": getattr(ss, "ability_id", 0),
        "pre_species": getattr(ss, "pre_species", ""),
        "bloodline_skills": dict(getattr(ss, "bloodline_skills", {}) or {}),
    }


def find_leader_form(species_db, number: str):
    """同编号首领形态 + 重算后的 initial_stats（进化之力预计算）。"""
    if not number:
        return None
    for sp in species_db._by_number.get(number, []):
        s = species_db._read_one(sp)
        if s and "首领" in (s.form or ""):
            result = StatsCalc().compute(s)
            return {
                **species_dict(s),
                "initial_stats": dict(result.final_stats),
                "max_hp": result.final_stats["hp"],
            }
    return None


def build_sprite_spec(species_db, spec: dict) -> dict:
    from backend.common.constants import STAT_KEYS

    species = species_db.get(spec["name"], spec.get("form", ""))
    calc = StatsCalc()
    result = calc.compute(
        species,
        nature=spec.get("nature"),
        iv=spec.get("iv") or {k: 0 for k in STAT_KEYS},
    )
    skills_raw = []
    for sk_name in spec.get("skills", []):
        p = Path("data/skills") / f"{sk_name}.json"
        if p.exists():
            skills_raw.append(json.loads(p.read_text(encoding="utf-8")))
    bloodline = spec.get("bloodline") or (species.elements[0] if species.elements else "")
    # 愿力血脉技能预查
    bloodline_skill = None
    bl_id = (species.bloodline_skills or {}).get(bloodline)
    if bl_id is not None:
        sk_name = SKILL_ID_TO_NAME.get(bl_id)
        if sk_name:
            p = Path("data/skills") / f"{sk_name}.json"
            if p.exists():
                bloodline_skill = json.loads(p.read_text(encoding="utf-8"))
    return {
        **species_dict(species),
        "initial_stats": dict(result.final_stats),
        "nature": spec.get("nature"),
        "iv": dict(spec.get("iv") or {}),
        "energy": 10,
        "skills": skills_raw,
        "bloodline": bloodline,
        "leader_form": find_leader_form(species_db, species.number),
        "bloodline_skill": bloodline_skill,
    }


def build_spec(seed: int) -> dict:
    random.seed(seed)
    factory = SimFactory()
    team_a, team_b = _seeded_teams()
    item_a, item_b = _seeded_item(), _seeded_item()
    # 对局内 RNG 种子（协议：seed+1）
    random.seed(seed + 1)

    def player_spec(name, team, item):
        return {
            "name": name,
            "lives": 4,
            "item": {
                "name": item.name,
                "max_uses": item.max_uses,
                "cooldown_turns": item.cooldown_turns,
                "uses": 0,
                "last_use_turn": 0,
            },
            "sprites": [build_sprite_spec(factory.sprite_db, s) for s in team],
        }

    return {
        "seed": seed,
        "weather": "",
        "players": [
            player_spec("A", team_a, item_a),
            player_spec("B", team_b, item_b),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="1-200")
    parser.add_argument("--out", default="native/gate_specs")
    args = parser.parse_args()

    from backend.engine.differential.generate_fixtures import parse_seeds

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for seed in parse_seeds(args.seeds):
        spec = build_spec(seed)
        (out_dir / f"spec_{seed:04d}.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        print(f"seed={seed} written")


if __name__ == "__main__":
    main()
