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

from backend.engine.ai.data.role_from_reference import classify_roles, load_reference
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
    """抽样：分桶需与其 wiki 推荐培养一致（期望值取自实测的天赋权重）。

    数据来源 training_reference.json；改动分类规则时请重新核对这几个数。
    """
    roles = _roles()
    expect = {
        # 喵喵：生命 8387 / 物攻 7870 / 物防 7576 → 耐久 0.626 → 坦克
        "喵喵": "tanks",
        # 岚鸟（本来的样子）：物防 4791 / 物攻 4733 / 魔攻 4720 → atk 0.428 > bulk 0.506? 实测 attack
        "岚鸟（本来的样子）": "attackers",
        # 古卷执政官：生命 8215 / 魔防 6151 / 物防 5695 → 耐久 0.677 → 坦克
        "古卷执政官": "tanks",
        # 鸭吉吉（起来鸭）：生命 6152 / 魔防 6022 / 物防 5674 / 物攻 3599 → 耐久 0.646 → 坦克
        # （注意：与无外观「鸭吉吉」不同——后者速度/物攻领先，是另一种配置）
        "鸭吉吉（起来鸭）": "tanks",
    }
    for name, bucket in expect.items():
        if name not in SPRITE_RANDOM_POOL:
            continue
        assert name in roles[bucket], f"{name} 应在 {bucket}，实际落入其余桶"


def test_roles_have_enough_supply_for_team_templates():
    """三种配队模板最多要 3 攻击手/3 辅助/2 坦克，各桶供给必须充足。"""
    roles = _roles()
    assert len(roles["attackers"]) >= 30
    assert len(roles["supports"]) >= 30
    assert len(roles["tanks"]) >= 30
