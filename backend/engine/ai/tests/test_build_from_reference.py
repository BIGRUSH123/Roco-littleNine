# -*- coding: utf-8 -*-
"""build_from_reference 的门禁：合法集、无冲突、槽 0、分布保真、兜底。

契约见 `docs/培养方案-pvp口径.md`。这里刻意**独立重算**规则期望值（不复用模块内部函数），
避免"规则写错但测试跟着错"。
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parents[4]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.constants import STAT_KEYS
from backend.common.nature import NATURE_TABLE
from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.ai.data.build_from_reference import (item_for_team, legal_bloodlines,
                                                         legal_skills, optimal_build,
                                                         sample_build, validate_build)
from backend.engine.ai.data.role_from_reference import load_reference, pick_entry
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.sim.factory import SimFactory

_POOL = dict(SPRITE_RANDOM_POOL)
_FACTORY = SimFactory()
_DB = _FACTORY.sprite_db
_BY_NUMBER = load_reference()["by_number"]
_SKILLS_DIR = _PROJ / "data" / "skills"

_ATTACK_KEYS = {"物攻": "atk", "魔攻": "sp_atk"}


def _meta(name: str) -> tuple[str, str, int]:
    p = _SKILLS_DIR / f"{name}.json"
    if not p.exists():
        return ("", "", 0)
    d = json.loads(p.read_text(encoding="utf-8"))
    return (d.get("element", "") or "", d.get("skill_type", "") or "", int(d.get("power", 0) or 0))


def _legal(name: str) -> set[str]:
    sp = _DB.get(name)
    return legal_skills(sp, _POOL.get(name, []))


def _expected_slot0(skills: list[str], elements: tuple[str, ...]) -> str:
    """独立实现槽 0 规则：同属性技能（威力最低）→ 最弱攻击 → 最弱技能。"""
    same = [s for s in skills if _meta(s)[0] in elements]
    if same:
        return min(same, key=lambda s: (_meta(s)[2], s))
    attacks = [s for s in skills if _meta(s)[1] in ("物攻", "魔攻")]
    if attacks:
        return min(attacks, key=lambda s: (_meta(s)[2], s))
    return min(skills, key=lambda s: (_meta(s)[2], s))


def test_optimal_build_is_legal_and_deterministic():
    """全部池条目都能生成合规 build，且逐次调用结果一致（BC 数据要可复现）。"""
    bad: list[tuple[str, list[str]]] = []
    for name in _POOL:
        b1 = optimal_build(_DB, name, _POOL, _BY_NUMBER)
        b2 = optimal_build(_DB, name, _POOL, _BY_NUMBER)
        assert b1 == b2, f"{name} 两次最优 build 不一致: {b1} vs {b2}"
        problems = validate_build(b1, _DB, _POOL, _BY_NUMBER)
        if problems:
            bad.append((name, problems))
    assert not bad, f"{len(bad)} 个条目违规，前 3: {bad[:3]}"


def test_sample_build_has_zero_conflicts():
    """自博弈抽样同样零违规（技能合法、主攻/天赋/性格一致、血脉可选、槽 0 正确）。"""
    rng = random.Random(20260921)
    bad: list[tuple[str, dict, list[str]]] = []
    for name in _POOL:
        for _ in range(3):
            b = sample_build(_DB, name, _POOL, _BY_NUMBER, rng)
            problems = validate_build(b, _DB, _POOL, _BY_NUMBER)
            if problems:
                bad.append((name, b, problems))
    assert not bad, f"{len(bad)} 个抽样违规，首例 {bad[0] if bad else ''}"


def test_conflict_invariants_hold_by_construction():
    """逐条独立核对硬不变量（主攻、天赋、性格），而不只看 validate_build 的汇总。"""
    rng = random.Random(7)
    checked = 0
    for name in list(_POOL)[:60]:
        sp = _DB.get(name)
        elements = tuple(sp.elements or ())
        for build in (optimal_build(_DB, name, _POOL, _BY_NUMBER),
                      *(sample_build(_DB, name, _POOL, _BY_NUMBER, rng) for _ in range(3))):
            skills = build["skills"]
            keys = [k for k, v in build["iv"].items() if v]
            up, down = NATURE_TABLE[build["nature"]]
            assert len(keys) == 3, f"{name}: 天赋应拉满 3 项"
            assert up in keys, f"{name}: 性格加项 {up} 不在天赋 {keys}"
            assert down not in keys, f"{name}: 性格减项 {down} 在天赋 {keys}"
            atk_types = {_meta(s)[1] for s in skills if _meta(s)[1] in ("物攻", "魔攻")}
            invested = {_ATTACK_KEYS[t] for t in atk_types} & set(keys)
            # 契约：投了天赋的攻击项必须在技能里有对应攻击（"投了物攻打不出物攻"就是冲突）；
            # 未投攻击天赋的纯防御向配方不要求攻击技能。
            for stat in invested:
                label = "物攻" if stat == "atk" else "魔攻"
                assert label in atk_types, f"{name}: 天赋投了 {label} 但技能 {skills} 里没有"
            assert down not in invested, \
                f"{name}: 性格减了已投天赋的攻击项（{down} ∈ 天赋 {keys}）"
            assert skills[0] == _expected_slot0(skills, elements), f"{name}: 槽 0 规则不符"
            checked += 1
    assert checked >= 200


def test_skills_come_from_legal_set_and_usually_four():
    """技能必须落在合法集内；技能充足时应当是 4 招。"""
    rng = random.Random(11)
    four = 0
    total = 0
    for name in _POOL:
        legal = _legal(name)
        if not legal:
            continue
        for build in (optimal_build(_DB, name, _POOL, _BY_NUMBER),
                      sample_build(_DB, name, _POOL, _BY_NUMBER, rng)):
            skills = build["skills"]
            assert set(skills) <= legal, f"{name}: 出现非法技能 {set(skills) - legal}"
            total += 1
            four += (len(skills) == 4)
    assert total and four / total >= 0.95, f"4 招 build 占比仅 {four}/{total}"


def test_chief_bloodline_only_when_evolvable():
    """「首领」血脉只在真能首领化时出现；棋棋（黑子）必须回退到其他血脉。"""
    sp_chief = _DB.get("棋棋（黑子）")
    assert not _DB.leader_form_candidates(sp_chief.number, sp_chief.appearance or sp_chief.form)
    assert "首领" not in legal_bloodlines(sp_chief, _DB)

    rng = random.Random(3)
    for build in (optimal_build(_DB, "棋棋（黑子）", _POOL, _BY_NUMBER),
                  *(sample_build(_DB, "棋棋（黑子）", _POOL, _BY_NUMBER, rng) for _ in range(20))):
        assert build["bloodline"] != "首领", "棋棋（黑子）不能首领化，不该拿首领血脉"

    # 能首领化的精灵：合法集里必须有首领，抽样占比应贴合 wiki 权重（水灵 首领 51.8% / 水 35.5%）
    sp_ok = _DB.get("水灵")
    assert "首领" in legal_bloodlines(sp_ok, _DB)
    block, _how = pick_entry(_BY_NUMBER, sp_ok.number, sp_ok.name, sp_ok.appearance or sp_ok.form)
    legal = legal_bloodlines(sp_ok, _DB)
    ranked = [(n, float(w)) for n, w in (block.get("blood") or []) if n in legal]
    total = sum(w for _n, w in ranked)
    want = {n: w / total for n, w in ranked}
    rng = random.Random(2024)
    got = Counter(sample_build(_DB, "水灵", _POOL, _BY_NUMBER, rng)["bloodline"]
                  for _ in range(400))
    for elem, expect in list(want.items())[:3]:
        assert abs(got.get(elem, 0) / 400 - expect) <= 0.10, \
            f"水灵 {elem}: 抽样 {got.get(elem, 0) / 400:.3f} vs wiki {expect:.3f}"


def test_item_follows_team_bloodline():
    """队内有首领血脉 → 进化之力；否则愿力。"""
    chief = optimal_build(_DB, "水灵", _POOL, _BY_NUMBER)
    plain = sample_build(_DB, "花衣蝶", _POOL, _BY_NUMBER, random.Random(5))
    assert chief["bloodline"] == "首领"
    assert item_for_team([chief]).name == "进化之力"
    assert item_for_team([plain]).name == "愿力"
    assert item_for_team([plain, chief]).name == "进化之力"


def test_fallback_build_for_sprite_without_recommendation():
    """无 wiki 推荐的池条目（编号 445–466 那批）也要拿到合规 build。"""
    name = "银月狼王"
    assert name in _POOL and _BY_NUMBER.get(_DB.get(name).number.zfill(3)) is None
    for build in (optimal_build(_DB, name, _POOL, _BY_NUMBER),
                  sample_build(_DB, name, _POOL, _BY_NUMBER, random.Random(1))):
        assert build["_source"] == "fallback"
        assert validate_build(build, _DB, _POOL, _BY_NUMBER) == []


def test_sampling_keeps_wiki_proportions():
    """抽样分布应贴合 wiki 权重（抽 400 次，偏差 ≤ 0.12），而不是退化成均匀分布。"""
    name = "岚鸟（本来的样子）"          # 翼 4898 / 首领 3313 / …：两种血脉都常见
    block = None
    sp = _DB.get(name)
    from backend.engine.ai.data.role_from_reference import pick_entry
    block, _how = pick_entry(_BY_NUMBER, sp.number, sp.name, sp.appearance or sp.form)
    ranked = [(n, w) for n, w in (block.get("blood") or []) if n in legal_bloodlines(sp, _DB)]
    total = sum(w for _n, w in ranked)
    want = {n: w / total for n, w in ranked}

    rng = random.Random(99)
    got = Counter(sample_build(_DB, name, _POOL, _BY_NUMBER, rng, role="attacker")["bloodline"]
                  for _ in range(400))
    for elem, expect in want.items():
        if expect < 0.05:
            continue
        assert abs(got.get(elem, 0) / 400 - expect) <= 0.12, \
            f"{elem}: 抽样占比 {got.get(elem, 0) / 400:.3f} vs wiki {expect:.3f}"


def test_validate_build_catches_conflicts():
    """校验器本身要能抓到违规（防止"校验器永远返回空"）。"""
    good = optimal_build(_DB, "花衣蝶", _POOL, _BY_NUMBER)
    assert validate_build(good, _DB, _POOL, _BY_NUMBER) == []

    broken = dict(good)
    broken["skills"] = ["不存在的技能"]
    assert validate_build(broken, _DB, _POOL, _BY_NUMBER)

    broken2 = dict(good)
    up, down = NATURE_TABLE[good["nature"]]
    keys = [k for k, v in good["iv"].items() if v]
    broken2["iv"] = {k: (10 if k in (set(keys) - {keys[0]}) | {down} else 0) for k in STAT_KEYS}
    assert validate_build(broken2, _DB, _POOL, _BY_NUMBER), "性格减项落在天赋拉满项应被抓住"

