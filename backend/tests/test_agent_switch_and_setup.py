"""AI 决策层两条反僵局规则：

1. **连续换人上限**（默认 5）——到顶后本轮不再主动换人（换人空转会让双方无限轮转）；
   力竭顶替等**非主动**换人不计数，且把计数清零。
2. **打不动 → 先增益自己**——最强一发 < 对手血量 12%（缺乏有效输出）且对手这一击不致命时，
   用 IR 画像判定自身增益技（`skill_ir.self_buff_value`，与 V3 `_try_setup` 同一实现）。

两者都只约束**决策层**（引擎侧"换人/聚能"始终合法）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.action import Action  # noqa: E402
from backend.sim.agent import MAX_CONSECUTIVE_SWITCHES, SwitchStreak  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.agent_v3 import RuleAgentV3  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.traits import dispatch_entry  # noqa: E402

factory = SimFactory()

TEAM_A = [
    {"name": "草衣虫", "skills": ["猛烈撞击", "力量增效", "甩水"]},
    {"name": "花衣蝶", "skills": ["落雷", "防御"]},
    {"name": "怖哭菇", "skills": ["错乱", "防御"]},
]
TEAM_B = [{"name": "仪式巨像", "skills": ["防御", "猛烈撞击"]},
          {"name": "水灵", "skills": ["甩水"]}]


def _battle(team_a=None, team_b=None) -> Battle:
    p1 = factory.build_player("A", team_a or TEAM_A)
    p2 = factory.build_player("B", team_b or TEAM_B)
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _agent(battle, st: SpriteStrategy | None = None, cls=RuleAgentV2):
    return cls("A", battle.player_a,
               strategy=TeamStrategy(default=st or SpriteStrategy()))


# ── 1. 连续换人上限 ────────────────────────────────────────────────

def test_switch_streak_counts_and_resets():
    tracker = SwitchStreak()
    battle = type("B", (), {"turn": 0})()
    for turn in range(1, 6):
        battle.turn = turn
        tracker.note(battle, Action(kind="switch", switch_index=1))
    assert tracker.streak == 5 and tracker.blocked(MAX_CONSECUTIVE_SWITCHES)

    battle.turn = 6
    tracker.note(battle, Action(kind="skill", skill_index=0))
    assert tracker.streak == 0 and not tracker.blocked(MAX_CONSECUTIVE_SWITCHES)


def test_switch_streak_forced_switch_does_not_count():
    """力竭顶替（非主动换人）不累计连续数，并把计数清零。"""
    tracker = SwitchStreak()
    battle = type("B", (), {"turn": 0})()
    for turn in range(1, 4):
        battle.turn = turn
        tracker.note(battle, Action(kind="switch", switch_index=1))
    assert tracker.streak == 3
    battle.turn = 4
    tracker.note(battle, Action(kind="switch", switch_index=2), forced=True)
    assert tracker.streak == 0


def test_same_turn_reask_counts_once():
    tracker = SwitchStreak()
    battle = type("B", (), {"turn": 7})()
    tracker.note(battle, Action(kind="switch", switch_index=1))
    tracker.note(battle, Action(kind="switch", switch_index=2))   # 同回合重选
    assert tracker.streak == 1


def test_plan_candidates_drop_switches_when_capped():
    battle = _battle()
    s, opp = battle.player_a.active, battle.player_b.active
    agent = _agent(battle)
    st = agent._st(s)
    table = agent._attack_table(battle, s, opp)

    kinds = [a.kind for a in agent._plan_candidates(battle, s, opp, table, st)]
    assert "switch" in kinds, "未达上限时换人应进候选"

    agent._switches.streak = MAX_CONSECUTIVE_SWITCHES
    kinds = [a.kind for a in agent._plan_candidates(battle, s, opp, table, st)]
    assert "switch" not in kinds, "到上限后换人不该再进候选"

    # 关闭该规则（0）→ 仍允许换人
    agent._switches.streak = 99
    off = SpriteStrategy(max_consecutive_switches=0)
    kinds = [a.kind for a in agent._plan_candidates(battle, s, opp, table,
                                                    agent._st(s), off)] \
        if False else [a.kind for a in agent._plan_candidates(
            battle, s, opp, table, SpriteStrategy(max_consecutive_switches=0))]
    assert "switch" in kinds, "max_consecutive_switches=0 应关闭上限"


def test_v3_plan_candidates_drop_switches_when_capped():
    battle = _battle()
    s, opp = battle.player_a.active, battle.player_b.active
    agent = _agent(battle, cls=RuleAgentV3)
    table = agent._attack_table(battle, s, opp)
    assert "switch" in [a.kind for a in agent._plan_candidates(battle, s, table, opp)]
    agent._switches.streak = 5
    assert "switch" not in [a.kind for a in agent._plan_candidates(battle, s, table, opp)]


# ── 2. 打不动 → 先增益自己 ─────────────────────────────────────────

def test_weak_attack_prefers_self_buff():
    battle = _battle()
    opp = battle.player_b.active
    opp.max_hp = opp.current_hp = 900          # 打不动：猛烈撞击远低于 12%
    me = battle.player_a.active
    me.energy = me.max_energy
    agent = _agent(battle)
    act = agent.choose_action(battle)
    assert act.kind == "skill", act.kind
    assert me.skills[act.skill_index].name == "力量增效", \
        f"打不动时应先叠增益，实际用了 {me.skills[act.skill_index].name}"
    assert agent.last_setup and agent.last_setup["skill"] == "力量增效"


def test_weak_attack_rule_can_be_disabled():
    battle = _battle()
    opp = battle.player_b.active
    opp.max_hp = opp.current_hp = 900
    me = battle.player_a.active
    me.energy = me.max_energy
    agent = _agent(battle, st=SpriteStrategy(setup_min_gain=0.0))
    agent.choose_action(battle)
    assert agent.last_setup is None, "setup_min_gain=0 时这条规则不该触发"


def test_setup_rule_skipped_when_threatened():
    """对手这一击够疼（≥ 我血量 65%）时不叠层，先自保。"""
    battle = _battle()
    opp = battle.player_b.active
    opp.max_hp = opp.current_hp = 900
    me = battle.player_a.active
    me.current_hp = 40                          # 残血：对手一击就能收
    me.energy = me.max_energy
    agent = _agent(battle)
    act = agent.choose_action(battle)
    used = me.skills[act.skill_index].name if act.kind == "skill" else act.kind
    assert used != "力量增效", f"有致死威胁时不该叠层，实际 {used}"
