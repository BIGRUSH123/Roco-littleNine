"""AI 决策层：满能量时不得空过（"聚能"在满能量下是严格劣着）。

背景：E2 规划层（`plan_depth` 默认 1）曾在对称局面里给"满能量聚能"略高于真实攻击的叶子分，
双方因此互不进攻、打满回合上限（实测星陨队镜像 114/120 局平局）。修复：满能量时聚能
**不进候选**，兜底也先出招。见 docs/引擎机制对账-游戏描述图鉴.md「僵局」。

引擎侧"聚能"仍然合法（游戏里随时可做）——这条只约束决策层。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.agent import gather_is_noop  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.agent_v3 import RuleAgentV3  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.traits import dispatch_entry  # noqa: E402
factory = SimFactory()


def _battle(team_a: list[dict], team_b: list[dict]) -> Battle:
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _agent(battle, cls=RuleAgentV2):
    return cls("A", battle.player_a,
               strategy=TeamStrategy(default=SpriteStrategy()))


# ── 判据本身 ──────────────────────────────────────────────────────

def test_gather_is_noop_semantics():
    battle = _battle([{"name": "草衣虫", "skills": ["猛烈撞击"]}],
                     [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    a = battle.player_a.active
    a.energy = a.max_energy
    assert gather_is_noop(a) is True
    a.energy = a.max_energy - 1
    assert gather_is_noop(a) is False


# ── 候选列表 ──────────────────────────────────────────────────────

def test_plan_candidates_drop_gather_when_energy_full():
    battle = _battle([{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复"]}],
                     [{"name": "仪式巨像", "skills": ["防御"]}])
    s, opp = battle.player_a.active, battle.player_b.active
    agent = _agent(battle)
    st = agent._st(s)
    table = agent._attack_table(battle, s, opp)

    s.energy = s.max_energy
    kinds_full = [act.kind for act in agent._plan_candidates(battle, s, opp, table, st)]
    assert "gather" not in kinds_full, "满能量时聚能不该进规划层候选"

    s.energy = 2
    kinds_low = [act.kind for act in agent._plan_candidates(battle, s, opp, table, st)]
    assert "gather" in kinds_low, "能量不满时聚能仍是候选"


def test_ev_candidates_drop_gather_when_energy_full():
    battle = _battle([{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复"]}],
                     [{"name": "仪式巨像", "skills": ["防御"]}])
    s, opp = battle.player_a.active, battle.player_b.active
    agent = _agent(battle)
    st = agent._st(s)
    table = agent._attack_table(battle, s, opp)

    s.energy = s.max_energy
    assert all(c.kind != "gather" for c in agent._ev_candidates(battle, s, opp, table, st))
    s.energy = 2
    assert any(c.kind == "gather" for c in agent._ev_candidates(battle, s, opp, table, st))


# ── 真实决策：满能量不许空过 ────────────────────────────────────────

def test_choose_action_attacks_instead_of_passing():
    """满能量、攻击打不动（错乱 23 打 498）时，规划层必须出招而不是空过。"""
    battle = _battle([{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}],
                     [{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}])
    for side in ("A", "B"):
        s = battle.get_player(side).active
        s.max_hp, s.current_hp, s.energy = 498, 498, s.max_energy
    agent = _agent(battle)
    act = agent.choose_action(battle)
    assert act.kind == "skill", f"满能量不许空过，实际选了 {act.kind}"


def test_fallback_attacks_instead_of_passing(monkeypatch):
    """兜底路径（无候选可评估、攻击表为空）同样不许空过：能出招就出招。"""
    battle = _battle([{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}],
                     [{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}])
    s = battle.player_a.active
    s.energy = s.max_energy
    agent = _agent(battle)
    # 构造"规划层无候选 + 攻击表为空"的兜底局面（不打桩就是前面两条测试覆盖的常规路径）
    monkeypatch.setattr(type(agent), "_plan_candidates", lambda *a, **k: [])
    monkeypatch.setattr(type(agent), "_attack_table", lambda *a, **k: [])
    act = agent.choose_action(battle)
    assert act.kind == "skill", f"兜底不许空过，实际选了 {act.kind}"


def test_v3_also_guards_gather():
    battle = _battle([{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}],
                     [{"name": "怖哭菇", "skills": ["错乱", "吓退", "报复", "休息回复"]}])
    s = battle.player_a.active
    s.energy = s.max_energy
    agent = _agent(battle, cls=RuleAgentV3)
    act = agent.choose_action(battle)
    assert act.kind != "gather"
