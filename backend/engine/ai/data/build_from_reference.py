# -*- coding: utf-8 -*-
"""按线上 wiki 的 PVP 培养参考生成精灵 build（P1 契约见 `docs/培养方案-pvp口径.md`）。

两条生成路径：
  - `optimal_build()`：**BC 预训练数据**用——技能按权重取合规前四、天赋取前三（必含主攻）、
    性格取满足一致性规则的最高权重者、血脉取最高权重且**真能生效**者；
  - `sample_build()`：**自博弈**用——在合法子集上按 wiki 权重抽样（性格违规时条件重抽），
    等价于"合法子集上的条件分布"，既按占比随机又天然无冲突。

五元组：`{name, skills, nature, iv, bloodline}`（+ 队级 `item_for_team`）。

一致性规则（硬不变量，`validate_build` 逐条检查）：
  1. 技能 ⊆ 合法集（池内技能 ∪ 所选血脉的血脉技能），无重复，1–4 个；
  2. 天赋三项 = wiki 天赋前三（抽样时按权重抽三项），并**与技能方向对齐**：
     - 前三里投了攻击项 → 技能里必须有该类型攻击技能，性格不得减该属性；
     - 前三里投的是**另一种**攻击项（技能实际用另一系）→ 把权重最低的攻击项换成技能实际用的那项；
     - 前三里没有攻击项（纯防御向坦克/工具人配方）→ 保持 wiki 原样，不强行塞攻击天赋；
  3. 性格：**加项 ∈ 天赋三项**、**减项 ∉ 天赋三项**（于是"投了物攻却被性格减"这类冲突不可能出现）；
  4. 「首领」血脉只在**真能首领化**（非首领阶段 + 同编号有首领形态）时可选；
  5. 槽 0 放**同属性技能**（威力最低者）→ 否则威力最低的攻击技能 → 否则威力最低的技能，
     因为愿力会把本回合第一个技能换成血脉技能，槽 0 的损失要最小。

合法数据来源：`data/sprites/*.json` 的 `skills`/`stone_skills`（已并入 `sprite_random_pool.json`）
与 `bloodline_skills`；wiki 推荐来自 `training_reference.json`（`role_from_reference.pick_entry`）。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from backend.common.constants import STAT_KEYS
from backend.common.nature import NATURE_TABLE
from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.ai.data.role_from_reference import pick_entry

ROOT = Path(__file__).resolve().parents[4]
_SKILLS_DIR = ROOT / "data" / "skills"

BLOODLINE_CHIEF = "首领"
MAX_SKILLS = 4
NATURE_RESAMPLE_LIMIT = 8

# wiki 天赋标签 → 本地 stat key
TALENT_LABEL_TO_KEY: dict[str, str] = {
    "生命": "hp", "物攻": "atk", "魔攻": "sp_atk",
    "物防": "def", "魔防": "sp_def", "速度": "speed",
}
KEY_TO_TALENT_LABEL = {v: k for k, v in TALENT_LABEL_TO_KEY.items()}
ATTACK_LABELS = ("物攻", "魔攻")
ATTACK_STAT = {"物攻": "atk", "魔攻": "sp_atk"}

_META_CACHE: dict[str, tuple[str, str, int]] = {}


def _skill_meta(name: str) -> tuple[str, str, int]:
    """(属性, 技能类型, 威力) —— 缺文件返回 ('', '', 0)。"""
    cached = _META_CACHE.get(name)
    if cached is not None:
        return cached
    out = ("", "", 0)
    p = _SKILLS_DIR / f"{name}.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out = (d.get("element", "") or "", d.get("skill_type", "") or "",
                   int(d.get("power", 0) or 0))
        except (OSError, json.JSONDecodeError):
            pass
    _META_CACHE[name] = out
    return out


def _is_attack(skill: str) -> bool:
    return _skill_meta(skill)[1] in ("物攻", "魔攻")


def _power(skill: str) -> int:
    return _skill_meta(skill)[2]


def legal_skills(sp, pool_skills: list[str] | dict[str, int]) -> set[str]:
    """该精灵的技能合法集 = 池内技能（含技能石）∪ 全部血脉技能。"""
    out = set(pool_skills)
    for sid in (sp.bloodline_skills or {}).values():
        nm = SKILL_ID_TO_NAME.get(int(sid)) if str(sid).isdigit() else None
        if nm:
            out.add(nm)
    return out


def legal_bloodlines(sp, db) -> set[str]:
    """可选血脉 = `bloodline_skills` 的元素键 ∪（真能首领化时的「首领」）。"""
    out = {k for k in (sp.bloodline_skills or {}) if k and k != BLOODLINE_CHIEF}
    if leader_forms(db, sp):
        out.add(BLOODLINE_CHIEF)
    return out


def leader_forms(db, sp) -> list:
    """可首领化目标形态（空 = 不能首领化）。"""
    if sp.is_leader_stage():
        return []
    try:
        return db.leader_form_candidates(sp.number, sp.appearance or sp.form)
    except AttributeError:
        return []


def _ranked(block: dict, cat: str) -> list[tuple[str, float]]:
    """wiki 排名 → [(名字, 权重)]，去重（同技能多次出现只保留首次）。"""
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for name, weight in (block.get(cat) or []):
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, float(weight)))
    return out


def _invested_attack(keys: list[str], ma: tuple[str, str] | None) -> str | None:
    """天赋里投了的攻击项（用来限制性格减项）；没投攻击天赋 → None。"""
    if ma is None:
        return None
    return ma[1] if ma[1] in keys else None


def _talent_ranked_for(sp, by_number: dict | None) -> list[tuple[str, float]]:
    """该精灵的 wiki 天赋排名（无参考返回空表）——主攻 tie-break 用。"""
    if not by_number:
        return []
    block, _how = pick_entry(by_number, sp.number, sp.name, sp.appearance or sp.form)
    return _ranked(block, "talent") if block else []


def _weighted_pick(ranked: list[tuple[str, float]], rng: random.Random) -> str:
    if not ranked:
        raise ValueError("_weighted_pick 收到空列表：调用方应先保证候选非空（见 _pick_bloodline）")
    total = sum(w for _n, w in ranked)
    if total <= 0:
        return ranked[rng.randrange(len(ranked))][0]
    r = rng.random() * total
    acc = 0.0
    for name, weight in ranked:
        acc += weight
        if r <= acc:
            return name
    return ranked[-1][0]


def _weighted_sample(ranked: list[tuple[str, float]], k: int,
                     rng: random.Random) -> list[str]:
    """按权重不放回抽 k 个（k ≥ 个数时全取）。"""
    pool = list(ranked)
    out: list[str] = []
    while pool and len(out) < k:
        pick = _weighted_pick(pool, rng)
        out.append(pick)
        pool = [x for x in pool if x[0] != pick]
    return out


# ══════════════════════════════════════════════════════════════════
# 技能：选、补、排序
# ══════════════════════════════════════════════════════════════════

def _top_up_skills(chosen: list[str], legal: set[str], elements: tuple[str, ...],
                   need_attack: bool) -> list[str]:
    """不足 4 个时按 同属性攻击 → 其他攻击 → 同属性 → 其他 的优先级补齐。"""
    def sort_key(s: str) -> tuple:
        return (s in elements, _power(s), s)

    same_elem_atk = sorted((s for s in legal if _is_attack(s) and _skill_meta(s)[0] in elements),
                           key=sort_key, reverse=True)
    other_atk = sorted((s for s in legal if _is_attack(s) and _skill_meta(s)[0] not in elements),
                       key=sort_key, reverse=True)
    same_elem = sorted((s for s in legal if s not in same_elem_atk and s not in other_atk
                        and _skill_meta(s)[0] in elements), key=sort_key, reverse=True)
    other = sorted((s for s in legal - set(chosen) - set(same_elem_atk) - set(other_atk)
                    - set(same_elem)), key=sort_key, reverse=True)

    out = list(chosen)
    if need_attack and not any(_is_attack(s) for s in out):
        for cand in same_elem_atk + other_atk:
            if cand not in out:
                # 用最强的攻击技替掉权重最低（列表末位）的非攻击技能
                drop = next((i for i in range(len(out) - 1, -1, -1) if not _is_attack(out[i])), None)
                if drop is None:
                    break
                out[drop] = cand
                break
    for group in (same_elem_atk, other_atk, same_elem, other):
        for cand in group:
            if len(out) >= MAX_SKILLS:
                return out
            if cand not in out:
                out.append(cand)
    return out[:MAX_SKILLS]


def _slot0_of(skills: list[str], elements: tuple[str, ...]) -> str:
    """槽 0 的确定规则：同属性技能（威力最低）→ 最弱攻击 → 最弱技能。"""
    same = [s for s in skills if _skill_meta(s)[0] in elements]
    if same:
        return min(same, key=lambda s: (_power(s), s))
    attacks = [s for s in skills if _is_attack(s)]
    if attacks:
        return min(attacks, key=lambda s: (_power(s), s))
    return min(skills, key=lambda s: (_power(s), s))


def order_slots(skills: list[str], elements: tuple[str, ...]) -> list[str]:
    """槽 0 = 同属性技能（取威力最低者）→ 否则最弱攻击技能 → 否则最弱技能；其余保持原序。

    愿力会把**本回合第一个技能**换成血脉技能，所以槽 0 要放最不心疼的那一个。
    """
    if not skills:
        return []
    slot0 = _slot0_of(skills, elements)
    return [slot0] + [s for s in skills if s != slot0]


# ══════════════════════════════════════════════════════════════════
# 主攻 / 天赋 / 性格 / 血脉
# ══════════════════════════════════════════════════════════════════

def main_attack(skills: list[str], tal_ranked: list[tuple[str, float]],
                base_stats: dict[str, int] | None = None) -> tuple[str, str] | None:
    """(主攻标签, 主攻 stat)；无攻击技能返回 None。

    两类攻击都在时：wiki 技能权重更高的一类优先，平手看天赋权重，再平手看种族面板。
    """
    types = [t for t in ATTACK_LABELS if any(_skill_meta(s)[1] == t for s in skills)]
    if not types:
        return None
    if len(types) == 1:
        return types[0], ATTACK_STAT[types[0]]

    def atk_score(t: str) -> tuple:
        wiki = sum(w for s, w in tal_ranked if s == t)
        base = (base_stats or {}).get(ATTACK_STAT[t], 0)
        return (wiki, base, -ATTACK_LABELS.index(t))

    best = max(types, key=atk_score)
    return best, ATTACK_STAT[best]


def _talent_keys(ranked: list[tuple[str, float]], rng: random.Random | None) -> list[str]:
    """wiki 天赋排名 → 3 项 stat（最优取权重前三；抽样按权重不放回抽三）。"""
    ranked = [(TALENT_LABEL_TO_KEY[n], w) for n, w in ranked if n in TALENT_LABEL_TO_KEY]
    if rng is None:
        return [k for k, _w in sorted(ranked, key=lambda x: (-x[1], x[0]))[:3]]
    return list(dict.fromkeys(_weighted_sample(ranked, 3, rng)))


def _align_attack(keys: list[str], need_stat: str | None) -> list[str]:
    """天赋三项与技能方向对齐。

    只在"三项里投了**另一种**攻击项、却不是技能实际用的那种"时才替换（把权重最低的攻击项换掉）；
    三项里根本没有攻击项时**保持** wiki 的防御向配方（坦克/工具人不强塞攻击天赋）。
    """
    if not need_stat or need_stat in keys:
        return keys
    if not ({k for k in keys} & {"atk", "sp_atk"}):
        return keys
    out = list(keys)
    for i in range(len(out) - 1, -1, -1):
        if out[i] in ("atk", "sp_atk"):
            out[i] = need_stat
            break
    seen: list[str] = []
    for k in out:
        if k not in seen:
            seen.append(k)
    for k in STAT_KEYS:
        if len(seen) >= 3:
            break
        if k not in seen:
            seen.append(k)
    return seen[:3]


def _iv_from_keys(keys: list[str]) -> dict[str, int]:
    return {k: (10 if k in keys else 0) for k in STAT_KEYS}


def _nature_ok(nature: str, talent_keys: list[str], need_stat: str | None) -> bool:
    up, down = NATURE_TABLE.get(nature, (None, None))
    if up is None:
        return False
    if up not in talent_keys or down in talent_keys:
        return False
    return not (need_stat and down == need_stat)


def _pick_nature(ranked: list[tuple[str, float]], talent_keys: list[str],
                 need_stat: str | None, rng: random.Random | None) -> str:
    """按 wiki 权重取满足一致性规则的性格。

    最优：权重降序第一个合规者；抽样：按权重试抽（上限 8 次），超限取合规者里权重最高的。
    兜底链：wiki 榜里放宽（先"减项 ∉ 天赋"、再"加项 ∈ 天赋"）→ **全 30 性格表**里找合规者
    （wiki 榜整列都不合规时会走到这里，例如推荐全是"胆小/开朗"这类减主攻的配置）。
    """
    order = sorted(ranked, key=lambda x: (-x[1], x[0])) if rng is None else list(ranked)
    if rng is not None:
        for _ in range(NATURE_RESAMPLE_LIMIT):
            if not order:
                break
            cand = _weighted_pick(order, rng)
            if cand and _nature_ok(cand, talent_keys, need_stat):
                return cand
        order = sorted(order, key=lambda x: (-x[1], x[0]))

    full = order + [(n, 0.0) for n in sorted(NATURE_TABLE)
                    if n not in {c for c, _w in order}]
    for cand, _w in full:
        if _nature_ok(cand, talent_keys, need_stat):
            return cand
    for cand, _w in full:
        if NATURE_TABLE.get(cand, (None, None))[1] not in talent_keys:
            return cand
    for cand, _w in full:
        if NATURE_TABLE.get(cand, (None, None))[0] in talent_keys:
            return cand
    return full[0][0] if full else "坦率"


def _pick_bloodline(sp, db, ranked: list[tuple[str, float]],
                    rng: random.Random | None) -> str:
    """按权重取**合法**血脉；首领只在真能首领化时参与。"""
    legal = legal_bloodlines(sp, db)
    if not legal:
        return (sp.elements[0] if sp.elements else "普通")
    pool = [(n, w) for n, w in ranked if n in legal]
    if rng is None:
        if pool:
            return sorted(pool, key=lambda x: (-x[1], x[0]))[0][0]
    else:
        pool = pool or sorted((n, 1.0) for n in legal)
        return _weighted_pick(pool, rng)
    # 无推荐或推荐全不合法：取合法集里最稳的一个（元素优先级 = 自身属性优先）
    for elem in (sp.elements or ()):
        if elem in legal:
            return elem
    return sorted(legal)[0]


# ══════════════════════════════════════════════════════════════════
# build 生成
# ══════════════════════════════════════════════════════════════════

def _fallback_build(db, name: str, sp, legal: set[str], elements: tuple[str, ...],
                    rng: random.Random | None, role: str | None) -> dict:
    """无 wiki 推荐时的兜底（池内最新一批 12 只走这里）：按面板/角色模板配 4 招。

    - 角色 attacker：尽量 3–4 攻击；tank/support：1–2 攻击 + 工具技能；
    - 天赋：attacker 拉 主攻/速度 + 第三项；tank 拉 生命/物防 + 第三项；否则 生命/速度 + 第三项。
    """
    base = {"hp": sp.hp, "atk": sp.atk, "sp_atk": sp.sp_atk,
            "def": sp.def_, "sp_def": sp.sp_def, "speed": sp.speed}
    atk_stat = "atk" if base["atk"] >= base["sp_atk"] else "sp_atk"
    if role is None:
        offense = max(base["atk"], base["sp_atk"])
        bulk = base["hp"] + base["def"] + base["sp_def"]
        role = "tank" if bulk >= offense * 2.5 else "attacker"

    attacks = sorted((s for s in legal if _is_attack(s)),
                     key=lambda s: (_skill_meta(s)[0] in elements, _power(s), s), reverse=True)
    others = sorted((s for s in legal if not _is_attack(s)),
                    key=lambda s: (_power(s), s), reverse=True)
    n_atk = min(len(attacks), 3 if role == "attacker" else 1)
    chosen = attacks[:n_atk]
    for cand in others + attacks:
        if len(chosen) >= min(MAX_SKILLS, len(legal)):
            break
        if cand not in chosen:
            chosen.append(cand)

    # 主攻由**选中的技能**决定（与 wiki 路径同一规则），再据此定天赋与性格
    atk_types = [t for t in ATTACK_LABELS if any(_skill_meta(s)[1] == t for s in chosen)]
    need = None
    if atk_types:
        best = max(atk_types, key=lambda t: (base[ATTACK_STAT[t]], -ATTACK_LABELS.index(t)))
        need = ATTACK_STAT[best]

    if role == "tank":
        base_keys = ["hp", "def"]
    elif role == "support":
        base_keys = ["hp", "speed"]
    else:
        base_keys = [atk_stat, "speed"]
    keys = [k for k in base_keys if k in STAT_KEYS]
    if need and need not in keys:
        keys = keys[:1] + [need]
    for k in STAT_KEYS:
        if len(keys) >= 3:
            break
        if k not in keys:
            keys.append(k)
    keys = keys[:3]

    legal_bl = legal_bloodlines(sp, db)
    if rng is not None and legal_bl:
        bloodline = sorted(legal_bl)[rng.randrange(len(legal_bl))]
    else:
        bloodline = next((e for e in (sp.elements or ()) if e in legal_bl), None) \
            or (sorted(legal_bl)[0] if legal_bl else (sp.elements[0] if sp.elements else "普通"))
    return {
        "name": name,
        "skills": order_slots(chosen, elements),
        "nature": _pick_nature([(n, 1.0) for n in NATURE_TABLE], keys, need, rng),
        "iv": _iv_from_keys(keys),
        "bloodline": bloodline,
        "_source": "fallback",
    }


def _build_from_block(name: str, sp, block: dict, legal: set[str],
                      elements: tuple[str, ...], rng: random.Random | None,
                      db) -> dict:
    tal_ranked = _ranked(block, "talent")
    nat_ranked = _ranked(block, "nature")
    skl_ranked = [(s, w) for s, w in _ranked(block, "skill") if s in legal]
    blood_ranked = _ranked(block, "blood")

    base = {"hp": sp.hp, "atk": sp.atk, "sp_atk": sp.sp_atk,
            "def": sp.def_, "sp_def": sp.sp_def, "speed": sp.speed}

    if rng is None:
        chosen = [s for s, _w in skl_ranked][:MAX_SKILLS]
        need_atk = bool([n for n, _w in tal_ranked[:3] if n in ATTACK_LABELS])
        chosen = _top_up_skills(chosen, legal, elements, need_atk)
        ma = main_attack(chosen, tal_ranked, base)
        keys = _align_attack(_talent_keys(tal_ranked, None), ma[1] if ma else None)
        nature = _pick_nature(nat_ranked, keys, _invested_attack(keys, ma), None)
    else:
        chosen = _weighted_sample(skl_ranked, MAX_SKILLS, rng)
        need_atk = bool([n for n, _w in tal_ranked[:3] if n in ATTACK_LABELS])
        chosen = _top_up_skills(chosen, legal, elements, need_atk)
        ma = main_attack(chosen, tal_ranked, base)
        keys = _align_attack(_talent_keys(tal_ranked, rng), ma[1] if ma else None)
        nature = _pick_nature(nat_ranked, keys, _invested_attack(keys, ma), rng)

    return {
        "name": name,
        "skills": order_slots(chosen, elements),
        "nature": nature,
        "iv": _iv_from_keys(keys),
        "bloodline": _pick_bloodline(sp, db, blood_ranked, rng),
        "_source": "wiki",
    }


def _resolve(db, name: str):
    sp = db.get(name)
    if sp is None:
        raise KeyError(f"精灵不在库中: {name}")
    return sp


def optimal_build(db, name: str, sprite_skills: dict, by_number: dict) -> dict:
    """BC 预训练用：各维度取合规的最优（wiki 权重最高者）。"""
    sp = _resolve(db, name)
    elements = tuple(sp.elements or ())
    legal = legal_skills(sp, sprite_skills.get(name, []))
    block, _how = pick_entry(by_number, sp.number, sp.name, sp.appearance or sp.form)
    if not block:
        return _fallback_build(db, name, sp, legal, elements, None, None)
    return _build_from_block(name, sp, block, legal, elements, None, db)


def sample_build(db, name: str, sprite_skills: dict, by_number: dict,
                 rng: random.Random, role: str | None = None) -> dict:
    """自博弈用：在合法子集上按 wiki 权重抽样（性格条件重抽）。"""
    sp = _resolve(db, name)
    elements = tuple(sp.elements or ())
    legal = legal_skills(sp, sprite_skills.get(name, []))
    block, _how = pick_entry(by_number, sp.number, sp.name, sp.appearance or sp.form)
    if not block:
        return _fallback_build(db, name, sp, legal, elements, rng, role)
    return _build_from_block(name, sp, block, legal, elements, rng, db)


def item_for_team(specs: list[dict]):
    """队级道具：队内存在「首领」血脉 → 进化之力；否则愿力。"""
    from backend.sim.player import Item

    has_chief = any(s.get("bloodline") == BLOODLINE_CHIEF for s in specs)
    return Item.leader() if has_chief else Item.wish()


# ══════════════════════════════════════════════════════════════════
# 校验
# ══════════════════════════════════════════════════════════════════

def validate_build(build: dict, db, sprite_skills: dict, by_number: dict) -> list[str]:
    """返回违规清单（空 = 合规）。规则见模块 docstring。

    `by_number` 必须传：主攻的判定要用 wiki 天赋权重做 tie-break（与生成器同一套输入），
    否则双攻精灵会被误判（生成器按 wiki 权重选主攻、校验按种族面板选主攻）。
    """
    problems: list[str] = []
    name = build.get("name", "?")
    sp = db.get(name)
    if sp is None:
        return [f"{name}: 不在精灵库"]
    elements = tuple(sp.elements or ())
    legal = legal_skills(sp, sprite_skills.get(name, []))

    skills = list(build.get("skills") or [])
    if not 1 <= len(skills) <= MAX_SKILLS:
        problems.append(f"{name}: 技能数 {len(skills)} 不在 1–{MAX_SKILLS}")
    if len(skills) != len(set(skills)):
        problems.append(f"{name}: 技能有重复 {skills}")
    for s in skills:
        if s not in legal:
            problems.append(f"{name}: 技能不在合法集 {s!r}")

    bloodline = build.get("bloodline", "")
    if bloodline and bloodline not in legal_bloodlines(sp, db):
        problems.append(f"{name}: 血脉 {bloodline!r} 不可选（首领需非首领阶段且有候选形态）")

    iv = build.get("iv") or {}
    keys = [k for k, v in iv.items() if v]
    if len(keys) != 3:
        problems.append(f"{name}: 天赋拉满项 {len(keys)} 个（应 3 个）")
    nature = build.get("nature", "")
    up, down = NATURE_TABLE.get(nature, (None, None))
    if up is None:
        problems.append(f"{name}: 未识别性格 {nature!r}")
        return problems

    ma = main_attack(skills, _talent_ranked_for(sp, by_number),
                     {"hp": sp.hp, "atk": sp.atk, "sp_atk": sp.sp_atk,
                      "def": sp.def_, "sp_def": sp.sp_def, "speed": sp.speed})
    # 契约（与 docs/培养方案 §1.2 一致）：只有**投了天赋的攻击项**要求技能里真有对应攻击技能、
    # 且性格不减它；纯防御向天赋（坦克/工具人）不强求攻击技能。
    if ma and ma[1] in keys:
        want_label = ma[0]
        if not any(_skill_meta(s)[1] == want_label for s in skills):
            problems.append(f"{name}: 天赋投了 {want_label} 但技能里没有该类型攻击")
        if down == ma[1]:
            problems.append(f"{name}: 性格 {nature} 减了已投天赋的攻击项 {want_label}")
    if down in keys:
        problems.append(f"{name}: 性格 {nature} 减了天赋拉满项 {down}")
    if up not in keys:
        problems.append(f"{name}: 性格 {nature} 加项 {up} 不在天赋拉满项 {keys}")

    # 槽 0 规则：同属性技能（威力最低）→ 最弱攻击 → 最弱技能
    if len(skills) > 1:
        expect = _slot0_of(skills, elements)
        if skills[0] != expect:
            problems.append(f"{name}: 槽 0 应为 {expect!r}（同属性/最弱攻击），实际 {skills[0]!r}")
    return problems
