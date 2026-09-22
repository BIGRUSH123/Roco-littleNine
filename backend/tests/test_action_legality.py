"""行动合法性：唯一判据 `Battle.action_legality` 与动作掩码的一致性门禁。

背景（2026-09-22 审计）：同一条合法性判据曾写了两份——掩码手写一份、引擎门控
手写另一份，漂移的结果是「掩码说合法、引擎拒绝」，于是静默吃掉一次行动
（实测：释放蓄力技能被守卫挡下 → 精灵被永久锁死在蓄力中）。

这里钉住三件事：
1. 掩码的合法集 **必须**等于引擎判据的合法集（随机对局逐步核对）；
2. 规则 agent 实际选的招必须合法（选了非法招 = agent 与判据漂移）；
3. 回合记录盖章 status/code/turn_consumed，非法/被吃掉的行动可见。
"""
import random
from pathlib import Path

from backend.engine.ai.core.mcts import action_index_to_action, get_valid_actions
from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory

_PROJ = Path(__file__).resolve().parent.parent.parent

_TEAM_A = [
    {"name": "水灵", "skills": ["升龙咆哮", "虫刺", "猛烈撞击", "聚盐"]},
    {"name": "水灵", "skills": ["音波弹", "撒娇", "猛烈撞击"]},
]
_TEAM_B = [
    {"name": "水灵", "skills": ["抛石", "午夜噪音", "猛烈撞击", "毒针"]},
    {"name": "水灵", "skills": ["双响炮", "切裂", "猛烈撞击"]},
]


def _battle():
    factory = SimFactory()
    p1 = factory.build_player("A", [dict(s) for s in _TEAM_A])
    p2 = factory.build_player("B", [dict(s) for s in _TEAM_B])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


class _CheckingAgent:
    """包住规则 agent，逐决策点核对「掩码 vs 引擎判据」和「选招是否合法」。"""

    def __init__(self, inner: RuleAgentV2, team: str, battle: Battle):
        self.inner = inner
        self.team = team
        self.battle = battle
        self.mask_mismatch: list[str] = []
        self.illegal_choice: list[str] = []
        self.records: list = []

    def choose_action(self, battle: Battle) -> Action:
        player = battle.get_player(self.team)
        # ① 掩码 vs 引擎判据（技能 0-9 / 换宠 10-14 / 聚能 15；道具不在判据范围内）
        _, mask = get_valid_actions(player, battle)
        for i in range(16):
            act = action_index_to_action(player, i)
            if act is None:
                continue
            ok = battle.action_legality(self.team, act).ok
            if bool(mask[i] > 0) != ok:
                self.mask_mismatch.append(
                    f"T{battle.turn} 动作{i}({act.kind}) 掩码={mask[i]:.0f} 判据={ok} "
                    f"code={battle.action_legality(self.team, act).code}")
        # ② 选招必须合法
        act = self.inner.choose_action(battle)
        chk = battle.action_legality(self.team, act)
        if not chk.ok:
            self.illegal_choice.append(
                f"T{battle.turn} 选了非法动作 {act.kind}:{act.skill_index} code={chk.code}")
        return act

    def choose_replacement(self, battle: Battle) -> int:
        return self.inner.choose_replacement(battle)


def _run_games(turns: int = 14) -> _CheckingAgent:
    checker = None
    for seed in (1, 2, 3):
        random.seed(seed)
        b = _battle()
        strat = TeamStrategy(default=SpriteStrategy())
        a = _CheckingAgent(RuleAgentV2('A', b.player_a, strategy=strat), 'A', b)
        c = _CheckingAgent(RuleAgentV2('B', b.player_b, strategy=strat), 'B', b)
        for _ in range(turns):
            if b.is_finished:
                break
            rep = b.execute_turn(a, c)
            for ar in (rep.action_a, rep.action_b):
                if ar is not None:
                    a.records.append(ar)
        checker = a if checker is None else checker
        # 累积两个 agent 的问题
        a.mask_mismatch += c.mask_mismatch
        a.illegal_choice += c.illegal_choice
    return checker


def test_mask_matches_engine_legality_in_real_games():
    """掩码合法集 == 引擎判据合法集（3 局 × 14 回合逐决策点核对）。"""
    checker = _run_games()
    assert not checker.mask_mismatch, "掩码与引擎判据漂移:\n" + "\n".join(checker.mask_mismatch[:10])


def test_agents_never_choose_illegal_actions():
    """规则 agent 实际选的招必须合法。"""
    checker = _run_games()
    assert not checker.illegal_choice, "选到非法动作:\n" + "\n".join(checker.illegal_choice[:10])


def test_round_record_stamps_status_and_turn_consumed():
    """每个行动记录都盖了 status/code/turn_consumed（默认 ok/True）。"""
    checker = _run_games(turns=6)
    recs = [r for r in checker.records if r is not None]
    assert recs, "应记录到行动"
    assert all(isinstance(r.turn_consumed, bool) for r in recs)
    assert all(r.status in ("ok", "illegal", "state_skip") for r in recs)
    assert any(r.status == "ok" for r in recs), "正常行动应为 ok"


# ══════════════════════════════════════════════════════════════════
# 判据本身：逐条对着引擎行为
# ══════════════════════════════════════════════════════════════════

def test_legality_charging_release_switch_and_locks():
    b = _battle()
    me = b.player_a.active
    team = 'A'

    # 未蓄力：蓄力技能合法
    assert b.action_legality(team, Action(kind='skill', skill_index=0)).ok
    # 进入蓄力
    b._set_charge_target(me, me.skills[0], 0)
    # 蓄力中：释放合法；其它技能/聚能非法；换宠合法（会打断蓄力）
    assert b.action_legality(team, Action(kind='skill', skill_index=0)).ok
    other = b.action_legality(team, Action(kind='skill', skill_index=1))
    assert (other.ok, other.code) == (False, "charging_locked")
    gather = b.action_legality(team, Action(kind='gather'))
    assert (gather.ok, gather.code) == (False, "charging_locked")
    assert b.action_legality(team, Action(kind='switch', switch_index=1)).ok


def test_legality_switch_locked_by_jinyu_and_fainted_target():
    b = _battle()
    me = b.player_a.active
    # 禁足：换宠非法
    me.locked_turns = 1
    chk = b.action_legality('A', Action(kind='switch', switch_index=1))
    assert (chk.ok, chk.code) == (False, "switch_locked")
    me.locked_turns = 0
    # 目标力竭 / 原地换
    b.player_a.team[1].current_hp = 0
    assert b.action_legality('A', Action(kind='switch', switch_index=1)).code == "target_fainted"
    assert b.action_legality('A', Action(kind='switch', switch_index=0)).code == "same_slot"


def test_legality_energy_and_stun_and_faint():
    b = _battle()
    me = b.player_a.active
    # 能量不足：抛石 30 费（B 队）——A 队先用一个贵技能模拟
    me.energy = 0
    chk = b.action_legality('A', Action(kind='skill', skill_index=1))
    assert (chk.ok, chk.code) in ((False, "insufficient_energy"), (False, "no_hp_price"))
    assert chk.detail and chk.detail["energy"] == 0
    # 眩晕：动作合法但被吃掉（state_skip）
    me.energy = 10
    me.add_effect(__import__('backend.vm.effect', fromlist=['AbnormalEffect']).AbnormalEffect(
        name="眩晕", source="test", scope="battlefield", stacks=1))
    chk = b.action_legality('A', Action(kind='skill', skill_index=1))
    assert (chk.status, chk.code) == ("state_skip", "stunned")
    # 力竭
    me.current_hp = 0
    assert b.action_legality('A', Action(kind='skill', skill_index=1)).code == "fainted"


def test_item_action_does_not_consume_turn():
    """道具不消耗回合（`_select_action` 结算后重新选招）——判据里显式标出。"""
    b = _battle()
    chk = b.action_legality('A', Action(kind='item'))
    assert chk.ok and chk.turn_consumed is False


# ══════════════════════════════════════════════════════════════════
# 第二步：非法动作不推进回合（拒绝 + 重选 + 记账）
# ══════════════════════════════════════════════════════════════════

class _StubbornAgent:
    """永远返回同一个（可能是非法的）动作。"""

    def __init__(self, action: Action):
        self.action = action
        self.calls = 0

    def choose_action(self, battle):
        self.calls += 1
        return self.action

    def choose_replacement(self, battle):
        return -1


def test_illegal_action_refused_and_reasked_without_consuming_turn():
    """蓄力中选了别的技能 → 引擎拒绝、不推进回合、重选并记账（第二步）。"""
    b = _battle()
    me = b.player_a.active
    b._set_charge_target(me, me.skills[0], 0)
    me.add_effect(_state_effect("charging"))

    agent = _StubbornAgent(Action(kind='skill', skill_index=1))   # 蓄力中非法
    action, item = b._select_action(agent, 'A')

    assert agent.calls >= 2, "应重新征询 agent（非法动作第一次被拒）"
    assert b.action_legality('A', action).ok, f"兜底动作必须合法: {action}"
    assert action.skill_index == 0, "掩码兜底应给「释放蓄力技能」"
    assert b._rejected_actions.get('A'), "被拒的动作要记账"
    assert "charging_locked" in b._rejected_actions['A'][0]


def test_agent_returning_gather_while_charging_gets_charged_skill():
    """蓄力中想聚能 → 被拒 → 兜底到释放蓄力技能（聚能此时不合法）。"""
    b = _battle()
    me = b.player_a.active
    b._set_charge_target(me, me.skills[0], 0)
    me.add_effect(_state_effect("charging"))

    action, _ = b._select_action(_StubbornAgent(Action(kind='gather')), 'A')
    assert action.kind == 'skill' and action.skill_index == 0


def test_no_rejections_in_normal_games():
    """正常对局不该出现任何拒收（判据与 agent 候选一致，无假阳性）。"""
    checker = _run_games(turns=10)
    bad = [r.rejected for r in checker.records if r.rejected]
    assert not bad, f"正常对局出现拒收: {bad[:5]}"


def test_switch_out_fully_cancels_charge():
    """换宠离场：`_charging` 属性与 `StateEffect('charging')` 一起清（此前只清属性）。"""
    b = _battle()
    me = b.player_a.active
    b._set_charge_target(me, me.skills[0], 0)
    me.add_effect(_state_effect("charging"))

    b._resolve_switch('A', Action(kind='switch', switch_index=1))

    assert getattr(me, '_charging', False) is False
    assert not [e for e in me.active_effects if getattr(e, 'state_type', '') == 'charging'], \
        "离场后不该留下 charging 状态效果"


def _state_effect(state: str):
    from backend.vm.effect import StateEffect
    return StateEffect(name=state, state_type=state, scope="persistent", source="test")
