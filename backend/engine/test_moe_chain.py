"""萌化退化链回归 — 首领形态 pre_species 自引用 + 外观保持。

背景（2026-09-20 定位）：BC 数据生成第 414 局卡死，单回合实测 1065 秒；栈
每 60 秒转储一次完全一致：

    Sprite.apply_moe → _build_moe_chain
      → Battle.lookup_species_by_number → SpriteDB.lookup_by_number
      → SpriteDB._read_one → pathlib.read_text     ← 每迭代一次读一个 JSON

两条缺陷叠加：
  1. 首领形态的 pre_species 指向**自身编号**（数据约定：先退回同编号基础形态，
     见 sprite_random_pool.py 的注释），但旧 lookup_by_number 只把
     appearance=='' 的条目当基础形态；238 月亮砣这类家族的基础形态全带外观
     （上弦/下弦…），于是 base_exact/base_any 皆空 → 兜底 boss_any 返回了同编号
     的**首领**形态 → 链原地打转，永不收敛。
  2. _build_moe_chain 无去重保护，环上每跳都要读盘，于是「不收敛」表现为
     单回合上千秒（16 worker 版则是父进程静默等待，看起来像死锁）。

本文件把三条不变量锁死：基础形态优先（首领形态绝不被当基础形态返回）、
链必收敛无重复、萌化前后保持外观。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.sprite_db import SpriteDB
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory

factory = SimFactory()
db: SpriteDB = factory.sprite_db

CHAIN_CAP = 16  # 实测最长演化链 3 跳，留足余量


def _battle(name_a: str, name_b: str = "花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": ["猛烈撞击", "甩水", "防御"]}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": ["猛烈撞击", "甩水", "防御"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db  # 形态变换（萌化链）查询用
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    return battle


def _walk(species, cap: int = CHAIN_CAP):
    """按 _build_moe_chain 的规则走链（带断言，不依赖 Sprite 实例）。

    身份键含名字：204 的伊兰龙(首领)/伊兰亚龙(基础) 编号与外观都相同。
    """
    chain = [species]
    seen = {(species.number, species.name, species.appearance)}
    cur = species
    while cur.pre_species:
        assert len(chain) < cap, (
            f"萌化链不收敛（>{cap} 跳）: {species.number} {species.display_name()}")
        pre = db.lookup_by_number(cur.pre_species, cur.appearance)
        if pre is None:
            break
        key = (pre.number, pre.name, pre.appearance)
        assert key not in seen, (
            f"萌化链成环: {species.display_name()} → {pre.display_name()}")
        seen.add(key)
        chain.append(pre)
        cur = pre
    return chain


# ═══════════════════════════════════════════════════════════════════
# 不变量 1：基础形态优先，首领形态绝不被当基础形态返回
# ═══════════════════════════════════════════════════════════════════

def test_lookup_by_number_never_returns_boss_form():
    """全部编号：pre_species 查询不得返回首领形态（否则调用方原地打转）。"""
    offenders = []
    for number in sorted(db._by_number):
        hit = db.lookup_by_number(number)
        if hit is not None and "首领" in (hit.form or ""):
            offenders.append(f"{number} → {hit.display_name()}({hit.form})")
    assert not offenders, "lookup_by_number 返回了首领形态: " + "; ".join(offenders[:10])


def test_lookup_by_number_prefers_same_appearance_base():
    """238 首领形态（满月砣）的前身应是**同外观**基础形态（月亮砣）。"""
    hit = db.lookup_by_number("238", "下弦的样子")
    assert hit is not None
    assert hit.name == "月亮砣", hit.display_name()
    assert hit.appearance == "下弦的样子"
    assert "首领" not in (hit.form or "")
    assert hit.pre_species == "237", hit.pre_species


# ═══════════════════════════════════════════════════════════════════
# 不变量 2：随机池里的每只精灵，萌化链都收敛且不重复
# ═══════════════════════════════════════════════════════════════════

def test_moe_chain_terminates_for_every_pool_sprite():
    """数据侧体检——旧实现在 11 个自引用编号上会无限读盘，这条测试会直接失败。"""
    lengths = {}
    for display in sorted(SPRITE_RANDOM_POOL):
        species = db.get(display)
        assert species is not None, f"池中精灵无法解析: {display}"
        chain = _walk(species)
        lengths[display] = len(chain)
    # 至少有家族真的能退化（否则测试形同虚设）
    assert any(n > 1 for n in lengths.values()), "没有任何精灵有可退化链"


def test_moe_chain_terminates_for_every_sprite_file():
    """不止随机池：全部条目（含 meta 队用的首领形态）都要收敛。"""
    for display in sorted(db._by_display):
        species = db.get(display)
        if species is None:
            continue
        _walk(species)


# ═══════════════════════════════════════════════════════════════════
# 不变量 3：首领形态萌化 → 退回同编号基础形态，且外观保持
# ═══════════════════════════════════════════════════════════════════

def test_boss_moe_reverts_to_same_number_base_form():
    battle = _battle("满月砣（下弦的样子）")
    a = battle.player_a.active
    assert a.name == "满月砣"
    assert a.species.appearance == "下弦的样子"
    assert a._moe_position == 0

    a.apply_moe(1, battle)
    assert a._moe_position == 1
    assert a.name == "月亮砣", a.name
    assert a.species.appearance == "下弦的样子", a.species.appearance

    # 解除萌化 → 回到首领形态（且仍是下弦）
    a.remove_moe(1, battle)
    assert a._moe_position == 0
    assert a.name == "满月砣", a.name
    assert a.species.appearance == "下弦的样子", a.species.appearance


def test_moe_chain_snapshot_is_bounded():
    """_moe_chain 必须是有界快照（旧实现会随迭代无限增长）。"""
    battle = _battle("满月砣（下弦的样子）")
    a = battle.player_a.active
    a.apply_moe(1, battle)
    assert 1 <= len(a._moe_chain) <= CHAIN_CAP
    keys = [(s.number, s.name, s.appearance) for s in a._moe_chain]
    assert len(keys) == len(set(keys)), f"链内有重复形态: {keys}"
