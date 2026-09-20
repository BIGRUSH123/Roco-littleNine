# -*- coding: utf-8 -*-
"""角色分桶的覆盖与语义门禁。

背景：旧 `train.py::_sprite_roles` 用「双攻前 40% / 耐久前 30% / 工具技能 ≥60%」
三个阈值分桶且**无兜底桶**，实测 344 个池条目里 114 个（77 个物种）永远进不了
随机阵容、约 19% 物种在训练数据里零出场（`native/tools/audit_selfplay_pool.py`）。
现在主来源换成线上 wiki 的培养参考（按编号对齐），本文件锁死两件事：

  1. **零遗漏**：随机池每个条目必须至少落一个桶（回归门禁）；
  2. **语义正确**：抽样若干只精灵，分桶结果与其 wiki 推荐培养一致
     （如 喵喵 生命/物防优先 → 坦克；快攻型 → 攻击手），防止规则被改坏。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.common.sprite_db import SpriteDB
from backend.engine.ai.data.role_from_reference import classify_roles, load_reference, pick_entry
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.engine.ai.train import _sprite_roles
from backend.sim.factory import SimFactory

factory = SimFactory()


def _roles() -> dict:
    return _sprite_roles(factory, dict(SPRITE_RANDOM_POOL))


def test_every_pool_sprite_has_a_role():
    """零遗漏：池条目必须全部落桶（旧实现漏掉 114 条）。"""
    roles = _roles()
    covered = roles["attackers"] | roles["tanks"] | roles["supports"]
    missing = sorted(set(SPRITE_RANDOM_POOL) - covered)
    assert not missing, f"{len(missing)} 个池条目没有角色桶: {missing[:12]}"


def test_wiki_reference_actually_used():
    """主来源必须是 wiki 培养参考，而不是退化成纯数值分位法。"""
    wiki = classify_roles(factory.sprite_db, dict(SPRITE_RANDOM_POOL))
    hit = sum(1 for how in wiki["source"].values() if "无" not in how)
    assert hit >= len(SPRITE_RANDOM_POOL) * 0.7, (
        f"wiki 命中率过低: {hit}/{len(SPRITE_RANDOM_POOL)}")


def test_reference_file_is_loadable_and_structured():
    ref = load_reference()
    assert ref.get("by_number"), "training_reference.json 缺失或为空"
    node = ref["by_number"].get("011")
    assert node and node["entries"], "编号 011 应有参考数据"
    entry = node["entries"][0]
    assert entry.get("form") is not None, "条目必须带 form（外观名），否则精确匹配全失效"
    for mode in ("pvp",):
        blk = entry.get(mode) or entry.get("default") or {}
        for cat in ("talent", "nature", "skill", "blood"):
            assert blk.get(cat), f"编号 011 首条缺 {mode}.{cat}"


def test_known_sprites_classified_by_recommendation():
    """抽样：分桶需与其 wiki 推荐培养一致（期望值取自修正对位后的实测权重）。

    2026-09-20 修正：`training_reference.json` 原先按「3000 + Catalog 序号」取
    TrainingReference，但真身是块内 `game_id`（613/621 = 98.7% 不一致），推荐数据
    整体张冠李戴（花衣蝶曾拿到一只翼系精灵的配置）。修正并重导入后重新实测了下表。
    """
    roles = _roles()
    expect = {
        # 岚鸟（本来的样子）：速度 7172 / 物攻 7051 / 生命 5308 → atk 0.457 > bulk 0.410 → 攻击手
        "岚鸟（本来的样子）": "attackers",
        # 古卷执政官：魔攻 9291 / 生命 8395 / 速度 6290 → atk 0.470 > bulk 0.424 → 攻击手
        "古卷执政官": "attackers",
        # 鸭吉吉（起来鸭）：魔攻 7533 / 速度 7208 / 生命 6924 → atk 0.448 > bulk 0.431；
        # 工具技能占比 0.517 → 同时进辅助桶（大世界口径下它才是坦克，别混）
        "鸭吉吉（起来鸭）": "attackers",
        # 花衣蝶：生命 8985 / 物防 6095 / 魔防 5847 → bulk 0.708 → 坦克（工具占比 0.693 也进辅助）
        "花衣蝶": "tanks",
        # 水灵：魔攻 9023 但 生命/魔防 也高 → bulk 0.586 ≥ 0.55 → 坦克
        "水灵": "tanks",
    }
    for name, bucket in expect.items():
        if name not in SPRITE_RANDOM_POOL:
            continue
        assert name in roles[bucket], f"{name} 应在 {bucket}，实际落入其余桶"


def _learnable_skills(name: str) -> set[str]:
    """该精灵的可学技能集 = 池内技能（含技能石）∪ 全部血脉技能。

    权威源是 `data/sprites/*.json` 的 skills/stone_skills/bloodline_skills；
    池子由前者构建，血脉技能不在池内需单独并进来。
    """
    sp = SpriteDB(_PROJ).get(name)
    if sp is None:
        return set()
    out = set(SPRITE_RANDOM_POOL.get(name, []))
    for sid in (sp.bloodline_skills or {}).values():
        nm = SKILL_ID_TO_NAME.get(int(sid)) if str(sid).isdigit() else None
        if nm:
            out.add(nm)
    return out


def test_wiki_recommended_skills_are_learnable():
    """哨兵：wiki 推荐的技能必须落在该精灵的可学集内。

    这条门禁就是为「Catalog 对位」这类错误设的：用 3000+序号 取推荐时，池内
    前八技能命中率只有 23.8%（前四全命中 ~4%）；改用 game_id 后实测 98.9%
    （前四全命中 92.8%）。阈值放在 0.85，留线上数据领先本地的余量。
    """
    db = SpriteDB(_PROJ)
    by_number = load_reference().get("by_number", {})
    hit = total = 0
    full = checked = 0
    for name in SPRITE_RANDOM_POOL:
        sp = db.get(name)
        if sp is None:
            continue
        block, _how = pick_entry(by_number, sp.number, sp.name, sp.appearance or sp.form)
        if not block:
            continue
        top = [s for s, _w in (block.get("skill") or [])][:8]
        if not top:
            continue
        legal = _learnable_skills(name)
        got = sum(1 for s in top if s in legal)
        checked += 1
        hit += got
        total += len(top)
        full += (got == len(top))
    assert checked >= 200, f"可比对的池条目只有 {checked} 个，参考数据可能没导入"
    rate = hit / total
    assert rate >= 0.85, (
        f"推荐技能命中可学集仅 {rate:.1%}（{hit}/{total}）——大概率是 Catalog 对位错"
        "（TrainingReference 的键应取块内 game_id）或 data/related 落后于线上"
    )
    assert full / checked >= 0.80, f"前四技能全命中的条目只有 {full}/{checked}"


def test_roles_have_enough_supply_for_team_templates():
    """三种配队模板最多要 3 攻击手/3 辅助/2 坦克，各桶供给必须充足。"""
    roles = _roles()
    assert len(roles["attackers"]) >= 30
    assert len(roles["supports"]) >= 30
    assert len(roles["tanks"]) >= 30
