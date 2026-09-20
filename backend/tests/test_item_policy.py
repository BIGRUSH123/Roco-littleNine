"""backend/tests/test_item_policy.py — 道具使用规则（RuleAgent / RuleAgentV2 共用）。

规则与依据见 `backend/sim/item_policy.py` 的模块 docstring（2026-09-20 改口径）：

  - 道具**不消耗回合**（结算后引擎重新让 agent 选行动），所以「先道具、再出招」
    在同一回合内完成，用它的门槛是值不值，而不是抢不抢回合；
  - **进化之力**：当前场上首领血脉精灵能首领化就立刻变（旧规则的 `turn <= 2`
    让七成首领队整局变不了身）；
  - **愿力**：换出的血脉技能比本回合最强可负担攻击更疼、或能直接斩杀，才用
    （旧规则是"残血 <50% 就用"，既不看换出技能的质量也不看对位）。

两个 agent 都走同一套判据，所以这里把 `RuleAgent` 与 `RuleAgentV2` 参数化跑同一矩阵。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import item_policy
from backend.sim.agent import RuleAgent
from backend.sim.agent_v2 import RuleAgentV2
from backend.sim.factory import SimFactory
from backend.sim.player import Item

factory = SimFactory()

# 这些精灵名/技能名来自定点探针（数值在断言里复核，数据改了会立刻红）
_AGENTS = (RuleAgent, RuleAgentV2)


def _agents():
    return sorted(_AGENTS, key=lambda a: a.__name__)


def _battle(my: str, bloodline: str, my_skills: list[str], item: Item,
            opp: str = "花衣蝶", opp_skills: list[str] | None = None):
    """构建一对一对局（走 factory，保证 battle.skill_loader 已注入）。"""
    p1 = factory.build_player(
        "A", [{"name": my, "skills": list(my_skills), **({"bloodline": bloodline} if bloodline else {})}],
        item=item)
    p2 = factory.build_player("B", [{"name": opp, "skills": list(opp_skills or ["猛烈撞击", "甩水"])}])
    return factory.build_battle(p1, p2)


# ══ 进化之力：能变就变，与回合数无关 ══


@pytest.mark.parametrize("agent_cls", _agents())
def test_evolution_power_used_on_late_turn(agent_cls):
    """首领血脉精灵在场 + 引擎判定可用 → 第 7 回合也照样首领化。"""
    b = _battle("水灵", "首领", ["猛烈撞击", "甩水"], Item.leader(), opp="雪怪")
    b.turn = 7
    assert b.item_usable("A"), "水灵 应能首领化（同编号有首领形态）"
    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind == "item"


@pytest.mark.parametrize("agent_cls", _agents())
def test_evolution_power_skipped_without_leader_form(agent_cls):
    """同编号没有首领形态（花衣蝶）→ 引擎判定不可用，agent 不空放道具。"""
    b = _battle("花衣蝶", "首领", ["猛烈撞击", "甩水"], Item.leader())
    b.turn = 7
    assert not b.item_usable("A")
    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind != "item"


# ══ 愿力：换出的血脉技能更疼 / 能斩杀才用 ══


@pytest.mark.parametrize("agent_cls", _agents())
def test_wish_used_when_bloodline_skill_hits_harder(agent_cls):
    """草衣虫 · 血脉光 → 虹光冲击（100 威力魔攻）比自身攻击更疼 → 用愿力。"""
    b = _battle("草衣虫", "光", ["猛烈撞击", "甩水"], Item.wish())
    s, opp = b.player_a.active, b.player_b.active
    wish = item_policy.bloodline_skill(b, s)
    assert wish is not None and wish.name == "虹光冲击"
    wish_dmg = item_policy.estimate_damage(b, s, opp, wish, "A")
    best_dmg = item_policy.best_attack_damage(b, s, opp, "A")
    assert wish_dmg > best_dmg, f"夹具失效：愿力技 {wish_dmg} 未超过最强攻击 {best_dmg}"

    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind == "item"


@pytest.mark.parametrize("agent_cls", _agents())
def test_wish_used_when_bloodline_skill_kills(agent_cls):
    """血脉技能能斩杀（对面 50 血 < 68 伤害）而自身攻击杀不掉 → 用愿力斩杀。"""
    b = _battle("草衣虫", "光", ["猛烈撞击", "甩水"], Item.wish())
    s, opp = b.player_a.active, b.player_b.active
    best_dmg = item_policy.best_attack_damage(b, s, opp, "A")
    opp.current_hp = best_dmg + 1          # 自身打不死、血脉技能能打死
    assert item_policy.wish_decision(b, "A", s, opp) == "kill"

    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind == "item"


@pytest.mark.parametrize("agent_cls", _agents())
def test_wish_skipped_when_bloodline_skill_is_status(agent_cls):
    """血脉火 → 引燃是状态技（伤害 0）→ 愿力换不出输出，不用。"""
    b = _battle("草衣虫", "火", ["猛烈撞击", "甩水"], Item.wish())
    wish = item_policy.bloodline_skill(b, b.player_a.active)
    assert wish is not None and not wish.is_attack
    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind != "item"


@pytest.mark.parametrize("agent_cls", _agents())
def test_wish_skipped_when_bloodline_skill_is_weaker(agent_cls):
    """血脉草 → 荆棘爪（35 伤害）不如自身最强攻击（46）→ 不用。"""
    b = _battle("草衣虫", "草", ["猛烈撞击", "甩水"], Item.wish())
    s, opp = b.player_a.active, b.player_b.active
    wish = item_policy.bloodline_skill(b, s)
    wish_dmg = item_policy.estimate_damage(b, s, opp, wish, "A")
    best_dmg = item_policy.best_attack_damage(b, s, opp, "A")
    assert wish_dmg <= best_dmg, f"夹具失效：愿力技 {wish_dmg} 超过了最强攻击 {best_dmg}"

    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind != "item"


@pytest.mark.parametrize("agent_cls", _agents())
def test_wish_skipped_with_non_elemental_bloodline(agent_cls):
    """首领血脉用不了愿力（愿力要求元素血脉）→ 引擎判定不可用，agent 不空放。

    注：不传血脉时 `factory.build_sprite` 会默认取自体属性（`species.elements[0]`），
    所以"没有血脉"这种状态在引擎里其实不存在，只能拿首领血脉做反例。
    """
    b = _battle("草衣虫", "首领", ["猛烈撞击", "甩水"], Item.wish())
    assert b.player_a.active.bloodline == "首领"
    assert not b.item_usable("A")
    action = agent_cls("A", b.player_a).choose_action(b)
    assert action.kind != "item"
