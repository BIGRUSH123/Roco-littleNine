"""backend/engine/ai/data/meta_teams.py — meta 队伍配置的加载、扰动与校验。

meta_teams.json 由 native/tools/scrape_meta_teams.py 从 rocopvp 共享阵容生成
（站点的 排位/比赛/体系 队），也可手写。格式:
{
  "teams": [
    {
      "name": "快攻推队",
      "archetype": "hyper_offense",
      "item": "进化之力",             # 可选：队伍道具（魔法），缺省按随机
      "sprites": [
        {
          "name": "精灵名",           # 池内显示名（可含「（外观）」）
          "role": "attack",          # attack/support/tank/closer（仅标签）
          "lead": true,              # 首发候选（可多只，由 agent 评分选一）
          "preserve": false,         # 必保精灵（终结手）
          "energy_hold": 0,          # 能量预算
          "skills": ["技能1", ...],  # 3-4 个，须在 池内 ∪ 血脉技能 内
          "bloodline": "幽",          # 可选：血脉（'首领' = 首领血脉，可首领化）
          "iv_fixed": ["hp", "atk", "speed"],  # 3 项=精确拉满；2 项=第三项随机
          "nature_fixed": "开朗",     # 可选：精确性格（爬取阵容用）
          "nature_plus": ["atk", "speed"],     # 可选：性格加项候选（手写队伍用）
          "alts": [ {...同结构...} ]  # 可选：扰动替换位（同角色替补）
        }, ...
      ]
    }, ...
  ]
}

训练接入：_random_teams 以 meta_frac 概率改用 meta 队 spec（IV/性格/替补位逐局
扰动 + 队伍道具优先用队伍声明的魔法），其余仍走角色化随机采样——既覆盖 meta
对局分布，又保留随机阵容泛化。路径可用环境变量 ROCO_META_TEAMS 覆盖。
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

DEFAULT_META_TEAMS_PATH = Path("backend/engine/ai/data/meta_teams.json")

# (mtime, teams) 进程级缓存：worker 子进程各自加载一次
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def meta_teams_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("ROCO_META_TEAMS")
    return Path(env) if env else DEFAULT_META_TEAMS_PATH


def load_meta_teams(path: str | Path | None = None) -> list[dict]:
    """加载 meta 队伍列表；文件不存在返回 []（训练自动退化为纯随机采样）。"""
    p = meta_teams_path(path)
    if not p.exists():
        return []
    key = str(p)
    mtime = p.stat().st_mtime
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = json.loads(p.read_text(encoding="utf-8"))
    teams = data.get("teams", [])
    _CACHE[key] = (mtime, teams)
    return teams


def validate_meta_teams(
    teams: list[dict],
    sprite_skills: dict[str, list[str]],
) -> list[str]:
    """校验每队精灵/技能/道具均可从池中构建，返回问题列表（空 = 通过）。

    技能合法集 = 池内技能 ∪ 该精灵所选血脉的血脉技能（爬取阵容可携带血脉技能）。
    首领形态条目按 `resolve_entry_name` 改写为基础形态后再校验——首领形态不能
    直接上场，只能由基础形态 + 首领血脉 + 进化之力变身得到。
    """
    from backend.common.constants import BLOODLINES
    from backend.sim.player import Item

    valid_items = {"进化之力", "愿力"}
    problems: list[str] = []
    for team in teams:
        tname = team.get("name", "?")
        sprites = team.get("sprites", [])
        if len(sprites) != 6:
            problems.append(f"[{tname}] 精灵数 {len(sprites)} != 6")
        item = team.get("item", "")
        if item and item not in valid_items:
            problems.append(f"[{tname}] 道具名无效: {item}")
        elif item:
            Item.leader() if item == "进化之力" else Item.wish()  # 构造性校验
        for entry in sprites:
            for variant in [entry] + list(entry.get("alts") or []):
                raw_name = variant.get("name", "")
                name, _engine_name, rewritten = resolve_entry_name(raw_name)
                if name not in sprite_skills:
                    problems.append(f"[{tname}] 精灵不在池中: {raw_name}")
                    continue
                if rewritten and (variant.get("bloodline") or "首领") != "首领":
                    problems.append(
                        f"[{tname}] {raw_name} 由首领形态改写为 {name}，血脉必须是首领")
                bloodline = variant.get("bloodline", "")
                if bloodline and bloodline not in BLOODLINES:
                    problems.append(f"[{tname}] {name} 血脉无效: {bloodline}")
                allowed = set(sprite_skills[name]) | _bloodline_skill_names(name, bloodline)
                for sk in variant.get("skills", []):
                    if sk not in allowed:
                        problems.append(f"[{tname}] {name} 技能不在池/血脉技能内: {sk}")
                nature = variant.get("nature_fixed", "")
                if nature and nature not in _all_natures():
                    problems.append(f"[{tname}] {name} 性格无效: {nature}")
                iv_fixed = variant.get("iv_fixed") or []
                if len(iv_fixed) > 3:
                    problems.append(f"[{tname}] {name} iv_fixed 超过 3 项: {iv_fixed}")
    return problems


def _bloodline_skill_names(name: str, bloodline: str) -> set[str]:
    """该精灵所选血脉对应的血脉技能名（无则空集）。"""
    if not bloodline:
        return set()
    from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
    from backend.sim.factory import SimFactory

    species = SimFactory().sprite_db.get(name)
    if species is None:
        return set()
    sid = (species.bloodline_skills or {}).get(bloodline)
    if sid is None:
        return set()
    nm = SKILL_ID_TO_NAME.get(int(sid))
    return {nm} if nm else set()


_RESOLVE_CACHE: dict[str, tuple[str, str, bool]] = {}


def resolve_entry_name(name: str) -> tuple[str, str, bool]:
    """池/站点显示名 → (spec 用的显示名, 引擎可见名 Sprite.name, 是否由首领形态改写)。

    两条规则：
      1. **首领形态不能直接上场**（2026-09-20 定稿）：站点阵容里写的若是变身后的
         首领形态（如「深渊罗隐」），改写为同编号的基础形态（「罗隐」），由
         首领血脉 + 进化之力在局内变身得到；
      2. 策略表按引擎实际查找的键索引：`Sprite.name` 是**基础名**
         （`build_sprite("岚鸟（本来的样子）").name == "岚鸟"`），
         所以外观变体必须用基础名做键，否则 `for_species()` 静默退回 default。
    """
    cached = _RESOLVE_CACHE.get(name)
    if cached is not None:
        return cached
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
    from backend.sim.factory import SimFactory

    out = (name, name, False)
    db = SimFactory().sprite_db
    species = db.get(name)
    if species is None:
        _RESOLVE_CACHE[name] = out
        return out
    if species.is_leader_stage():
        for candidate in SPRITE_RANDOM_POOL:
            base = db.get(candidate)
            if base is None or base.number != species.number:
                continue
            if base.is_leader_stage():
                continue
            out = (candidate, base.name, True)
            break
    else:
        out = (name, species.name, False)
    _RESOLVE_CACHE[name] = out
    return out


def spec_from_entry(entry: dict, rng=random) -> dict:
    """单只精灵 entry（含可选 alts）→ build_player spec。

    IV 规则：iv_fixed 为 3 项时按精确集合拉满（爬取阵容的 plusStats）；
    为 2 项时第三项随机扰动（自选队伍的多样性来源）；更少则随机补满一项。
    性格：nature_fixed 优先（精确还原站点配置），否则从 nature_plus 加项候选里抽。
    首领形态条目会被改写为基础形态，并补上「首领」血脉（否则局内无法变身）。
    """
    from backend.common.constants import STAT_KEYS

    alts = entry.get("alts") or []
    variant = rng.choice([entry] + alts) if alts else entry
    spec_name, _engine_name, rewritten = resolve_entry_name(variant["name"])
    iv_fixed = list(variant.get("iv_fixed", ["hp", "speed"]))
    if len(iv_fixed) >= 3:
        iv_keys = iv_fixed[:3]
    else:
        third_pool = [k for k in STAT_KEYS if k not in iv_fixed]
        extra = rng.sample(third_pool, min(3 - len(iv_fixed), len(third_pool)))
        iv_keys = iv_fixed + extra
    iv = {k: (10 if k in iv_keys else 0) for k in STAT_KEYS}

    nature = variant.get("nature_fixed", "")
    if not nature:
        options = _nature_options(variant.get("nature_plus", ["hp", "speed"]))
        nature = rng.choice(options) if options else rng.choice(_all_natures())

    spec = {
        "name": spec_name,
        "skills": list(variant["skills"]),
        "nature": nature,
        "iv": iv,
    }
    bloodline = variant.get("bloodline") or ("首领" if rewritten else "")
    if bloodline:
        spec["bloodline"] = bloodline
    return spec


def item_from_team(team: dict) -> "Item | None":
    """meta 队伍声明的道具（魔法）→ Item 实例；未声明返回 None（由调用方随机）。"""
    from backend.sim.player import Item

    name = team.get("item", "")
    if name == "进化之力":
        return Item.leader()
    if name == "愿力":
        return Item.wish()
    return None


def spec_from_team(team: dict, rng=random) -> tuple[list[dict], set[str]]:
    """整队 → spec 列表 + 精灵名集合（供跨队排重）。"""
    specs = [spec_from_entry(e, rng) for e in team["sprites"]]
    return specs, {s["name"] for s in specs}


def strategy_from_team(team: dict, jitter_rng: random.Random | None = None) -> "TeamStrategy":
    """meta 队 dict → RuleAgentV2 的 TeamStrategy；逐局阈值抖动增加对局多样性。

    键用**引擎可见名**（`Sprite.name` = 基础名）：RuleAgentV2 是
    `strategy.for_species(sprite.name)` 查找，用显示名（带外观）做键会静默失配。
    """
    from backend.sim.agent_v2 import SpriteStrategy, TeamStrategy

    jr = jitter_rng
    sprites: dict[str, SpriteStrategy] = {}
    for entry in team["sprites"]:
        st = SpriteStrategy(
            role=entry.get("role", "attack"),
            lead=bool(entry.get("lead", False)),
            preserve=bool(entry.get("preserve", False)),
            energy_hold=int(entry.get("energy_hold", 0)),
            threat_switch_hp=jr.uniform(0.8, 1.0) if jr else 0.9,
            switch_hp=jr.uniform(0.25, 0.45) if jr else 0.35,
        )
        for variant in [entry] + list(entry.get("alts") or []):
            sprites[resolve_entry_name(variant["name"])[1]] = st
    return TeamStrategy(name=team.get("name", ""), sprites=sprites)


def _nature_options(plus_stats) -> list[str]:
    from backend.common.nature import NATURE_TABLE

    return [n for n, (up, _down) in NATURE_TABLE.items() if up in plus_stats]


def _all_natures() -> list[str]:
    from backend.common.nature import NATURE_TABLE

    return list(NATURE_TABLE)
