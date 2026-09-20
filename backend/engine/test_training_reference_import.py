# -*- coding: utf-8 -*-
"""wiki 培养参考导入的对位门禁。

回归背景（2026-09-20）：`native/tools/import_training_reference.py::parse_catalog`
曾用 `3000 + Catalog 序号` 当精灵 id 去取 `TrainingReference`，但真身是 Catalog 块
里的 `game_id` 字段——实测 613/621（98.7%）条目的 `game_id != 3000 + 序号`。
后果：推荐数据整体张冠李戴（花衣蝶（编号 101）拿到某只翼系精灵的配置），
池内"推荐前四技能 ∈ 该精灵可学集"的比例从 92.8% 崩到 ~4%。

本文件锁死两件事：
  1. `parse_catalog` 必须从 `game_id` 取 id，且名字/编号只从 depth-1 取
     （块内嵌套表如 `activities={name="命定花种"}` 里有同名 `name`，会串）。
  2. 已入库产物的定点回归：编号 101「花衣蝶」的推荐技能必须落在它的可学集内。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.common.sprite_db import SpriteDB
from backend.engine.ai.data.role_from_reference import load_reference
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL

_IMPORTER = _PROJ / "native" / "tools" / "import_training_reference.py"


def _importer_module():
    spec = importlib.util.spec_from_file_location("_nrc_importer", _IMPORTER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 一段最小 Catalog 样本：pet_000127 的 game_id(3141) != 3000+序号(3127)，
# 且块内嵌套表里藏着干扰用的 name。
_CATALOG_SAMPLE = (
    'return {pet_000127={number="101",name="花衣蝶",game_id=3141,stage="3",'
    'learnset_id="learnset_x",activities={{name="命定花种"}},title="花衣蝶"},'
    'pet_000001={number="002",name="喵喵",game_id=3001,stage="1"},'
    'pet_000999={number="999",name="没ID宠物"}}'
)


def test_parse_catalog_uses_game_id_not_sequence():
    rows = {r["name"]: r for r in _importer_module().parse_catalog(_CATALOG_SAMPLE)}
    assert rows["花衣蝶"]["id"] == 3141, "id 必须取块内 game_id，而不是 3000+序号"
    assert rows["花衣蝶"]["number"] == "101"
    assert rows["花衣蝶"]["title"] == "花衣蝶"
    assert rows["花衣蝶"]["stage"] == "3"
    assert rows["花衣蝶"]["learnset_id"] == "learnset_x"
    assert rows["喵喵"]["id"] == 3001
    # 嵌套表里的 name（命定花种）不能顶掉 depth-1 的 name
    assert rows["花衣蝶"]["name"] == "花衣蝶"
    # 缺 game_id 的条目跳过，不污染数据
    assert "没ID宠物" not in rows


def test_imported_reference_matches_learnable_skills_for_known_sprite():
    """定点回归：花衣蝶（编号 101）的推荐技能必须能在它的可学集里构建。

    可学集 = 池内技能（含技能石）+ 血脉技能，权威源是 data/sprites 的
    skills/stone_skills/bloodline_skills 字段。
    """
    ref = load_reference()
    node = ref.get("by_number", {}).get("101")
    assert node and node["entries"], "编号 101 应有推荐数据"
    matches = [e for e in node["entries"] if e.get("name") == "花衣蝶"]
    assert matches, "编号 101 里应有名为「花衣蝶」的条目"
    entry = next((e for e in matches if not (e.get("form") or "")), matches[0])
    top = [s for s, _w in (entry.get("pvp") or {}).get("skill", [])][:4]
    assert top, "花衣蝶应有 pvp 技能推荐"

    sp = SpriteDB(_PROJ).get("花衣蝶")
    legal = set(SPRITE_RANDOM_POOL.get("花衣蝶", []))
    for sid in (sp.bloodline_skills or {}).values():
        nm = SKILL_ID_TO_NAME.get(int(sid)) if str(sid).isdigit() else None
        if nm:
            legal.add(nm)
    miss = [s for s in top if s not in legal]
    assert not miss, (
        f"花衣蝶推荐技能 {top} 里有不在可学集的：{miss}——对位又错了？"
        "（TrainingReference 的键应取 Catalog 块内 game_id）"
    )
