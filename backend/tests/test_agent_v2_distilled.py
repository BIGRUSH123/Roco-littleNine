"""backend/tests/test_agent_v2_distilled.py — 从"决策审计"里蒸馏出的两条规则。

流程（用户提的"先当老师再改规则"）：
  1. `native/tools/audit_ruleagent_decisions.py` 记录每个决策点的状态/选择/事后结果，
     自动标出可疑模式；
  2. 人工（教师）复核：真错留、假阳性丢；
  3. 把经验写成规则 → 本文件的测试；
  4. `native/tools/eval_expert_change.py` 用配对胜率验收（A/B 模块常量）。

**验收结论（必须如实记着）**：两条改动在配对胜率上都是**中性**——
交换价值 1200 局 0.500 [0.469,0.531]；防空转 4000 局 0.509 [0.492,0.526]（+0.9 点，未过 0.5 门槛）。
也就是说手写规则层的专家已经接近饱和，留着的理由是"方向正确、无副作用"，不是"更强"。
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import agent_v2
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.factory import SimFactory

factory = SimFactory()


def _battle():
    """我（A）水灵 + 替补花衣蝶；它（B）雪怪 + 替补草衣虫，且雪怪先手（龙卷风 +1）。"""
    p1 = [{"name": "水灵", "skills": ["猛烈撞击", "风墙"]},
          {"name": "花衣蝶", "skills": ["猛烈撞击"]}]
    p2 = [{"name": "雪怪", "skills": ["龙卷风", "猛烈撞击"]},
          {"name": "草衣虫", "skills": ["猛烈撞击"]}]
    return factory.build_battle(factory.build_player("A", p1),
                               factory.build_player("B", p2))


def test_trade_kill_when_my_sprite_is_the_damaged_one():
    """能一击斩杀、但我出手前会死：我比它更残 → 吃下这个一换一（不再无脑撤）。

    审计里这类"白丢斩杀"占 2.8% 的决策点；旧口径一律撤人，等于"拿残的换好的也不敢打"。
    """
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    assert agent_v2._TRADE_MARGIN <= 0.15
    me.current_hp = int(me.max_hp * 0.20)          # 我很残
    opp.current_hp = int(opp.max_hp * 0.90)        # 它很健康
    # 我先手权在它之下（它带先手值技），且它的最强一击能杀我
    from backend.sim import tactics

    assert tactics.moves_first(b, me, "A", opp, "B") is False
    action = RuleAgentV2("A", b.player_a).choose_action(b)
    assert action.kind == "skill", "一换一划算时应该打，而不是撤"


def _make_opponent_lethal(battle, times: int = 6) -> None:
    """把对手的攻击面板拉高，让"它先手且这一击能杀我"成立（触发交换价值判定）。"""
    for key in ("atk", "sp_atk"):
        battle.player_b.active.initial_stats[key] *= times


def test_keep_healthy_sprite_by_switching_instead_of_trading():
    """反过来：我（按血量比例）比它健康得多 → 不做一换一（撤人或举盾都行，就是别硬换）。"""
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    _make_opponent_lethal(b)
    me.max_hp = 100
    me.current_hp = 85                             # 我很健康（比例 0.85）
    opp.current_hp = int(opp.max_hp * 0.10)        # 它很残
    action = RuleAgentV2("A", b.player_a).choose_action(b)
    # 举盾（防御技）或撤人都算"不硬换"；关键是不能选攻击去对拼
    assert not (action.kind == "skill"
                and me.skills[action.skill_index].is_attack), \
        "我很健康时不该吃这个一换一"


def test_preserve_sprite_does_not_trade_when_healthy():
    """`preserve`（必保）精灵血量还够时不做一换一。"""
    strategy = TeamStrategy(default=SpriteStrategy(preserve=True))
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    _make_opponent_lethal(b)
    me.max_hp = 100
    me.current_hp = 45                             # 比例 0.45 > _PRESERVE_HOLD_RATIO
    opp.current_hp = int(opp.max_hp * 0.95)        # 它很健康（比例差 0.5 → 本可换命）
    assert me.current_hp / me.max_hp > agent_v2._PRESERVE_HOLD_RATIO
    assert (opp.current_hp / opp.max_hp) - (me.current_hp / me.max_hp) >= agent_v2._TRADE_MARGIN
    action = RuleAgentV2("A", b.player_a, strategy=strategy).choose_action(b)
    assert not (action.kind == "skill"
                and me.skills[action.skill_index].is_attack)


def test_defend_when_threatened():
    """被重击（对手这一击 ≥ 当前血量的 defend_threshold）、又打不死它 → 举盾。

    依据（`docs/博弈-概率预判口径.md` §4e）：三份洛神杯复盘里防御/应对是胜负基础，
    而我们专家原来的防御出招占比是 0%——"被威胁就防御"的粗糙对手对出厂专家 **0.571**。
    ⚠️ 但 §4f-④ 的同局对照实测：**对手也会用状态反制**时，举盾方 0.457 [0.445,0.469]
    （-4.3 点，平局记 0.5 的分数 -0.12）→ 这条规则**默认关闭**（`_DEFEND_THRESHOLD = 0.0`）。
    本测试只验证"开关打开后的行为"，不代表它是当前默认口径。
    """
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    _make_opponent_lethal(b)
    shield_idx = next(i for i, sk in enumerate(me.skills) if sk.is_defense)
    me.current_hp = int(me.max_hp * 0.5)
    on = RuleAgentV2("A", b.player_a, strategy=TeamStrategy(
        default=SpriteStrategy(defend_threshold=0.30)))
    action = on.choose_action(b)
    assert action.kind == "skill" and action.skill_index == shield_idx

    # 阈值设 0 = 关闭这条规则（当前默认值）
    b2 = _battle()
    me2 = b2.player_a.active
    _make_opponent_lethal(b2)
    me2.current_hp = int(me2.max_hp * 0.5)
    off = RuleAgentV2("A", b2.player_a, strategy=TeamStrategy(
        default=SpriteStrategy(defend_threshold=0.0)))
    action2 = off.choose_action(b2)
    assert not (action2.kind == "skill"
                and b2.player_a.active.skills[action2.skill_index].is_defense)


def test_rule_switches_are_per_strategy_instance(monkeypatch):
    """规则开关必须能**逐实例**不同 —— 否则同局对照 A/B 不成立（§4f-③ 的教训）。

    旧版把开关放在模块级常量上：一局里两侧都是 RuleAgentV2、读到同一个值，只能比较
    "整局新口径 vs 整局旧口径"两个镜像 → 量到的是先手/侧别偏差，不是规则差异。
    """
    monkeypatch.setattr(agent_v2, "_DEFEND_THRESHOLD", 0.55)
    # 显式传值 → 实例保留自己的值；不传 → 取**构造那一刻**的模块常量
    assert SpriteStrategy(defend_threshold=0.0).defend_threshold == 0.0
    assert SpriteStrategy().defend_threshold == 0.55
    assert SpriteStrategy(trade_margin=1e9).trade_margin == 1e9
    assert SpriteStrategy(anti_switch_loop=False).anti_switch_loop is False
    # 就地改写（工具用它把 A/B 值盖到 meta 队自带的策略上）
    base = SpriteStrategy()
    assert dataclasses.replace(base, defend_threshold=0.1).defend_threshold == 0.1
    assert base.defend_threshold == 0.55          # 原对象不变（frozen dataclass）


def test_status_counter_uses_counter_skill_when_opponent_will_shield():
    """应对三角第三条腿：预判它举盾 → 用"状态克防御"的技能（剧毒 3 层 → 应对时 8 层）。

    审计实测：它举盾的 2829 个决策点里我们"有可用状态技却没用"1491 次、真正用状态反制 1 次。
    """
    from backend.sim.item_policy import best_attack_damage

    p1 = [{"name": "水灵", "skills": ["猛烈撞击", "剧毒"]}]
    p2 = [{"name": "雪怪", "skills": ["龙卷风", "风墙"]}]        # 风墙 = 防御技（能举盾）
    b = factory.build_battle(factory.build_player("A", p1),
                             factory.build_player("B", p2))
    me, opp = b.player_a.active, b.player_b.active
    dmg = best_attack_damage(b, me, opp, "A")
    assert dmg > 0
    opp.current_hp = max(2, int(dmg * 2))       # 我这一击 ≈ 它半血：威胁到阈值，但打不死
    poison_idx = next(i for i, sk in enumerate(me.skills) if sk.name == "剧毒")

    on = RuleAgentV2("A", b.player_a,
                     strategy=TeamStrategy(default=SpriteStrategy(status_counter=True)))
    action = on.choose_action(b)
    assert action.kind == "skill" and action.skill_index == poison_idx, \
        "预判它举盾时应该用状态技反制，而不是硬撞它的减伤"

    off = RuleAgentV2("A", b.player_a, strategy=TeamStrategy(
        default=SpriteStrategy(status_counter=False)))
    action2 = off.choose_action(b)
    assert action2.kind == "skill" and me.skills[action2.skill_index].is_attack, \
        "关掉这条规则时仍是普通攻击（保证 A/B 的两臂只差这一条）"


def test_status_counter_skips_when_no_kill_threat():
    """我不构成威胁（打它不痛）→ 它不会举盾，这条腿不该触发。"""
    p1 = [{"name": "水灵", "skills": ["猛烈撞击", "剧毒"]}]
    p2 = [{"name": "雪怪", "skills": ["龙卷风", "风墙"]}]
    b = factory.build_battle(factory.build_player("A", p1),
                             factory.build_player("B", p2))
    me = b.player_a.active
    b.player_b.active.current_hp = b.player_b.active.max_hp    # 满血 → 我这一击远不到 30%
    agent = RuleAgentV2("A", b.player_a,
                        strategy=TeamStrategy(default=SpriteStrategy(status_counter=True)))
    action = agent.choose_action(b)
    assert action.kind != "skill" or not me.skills[action.skill_index].name == "剧毒"


def test_no_defend_when_kill_available():
    """能一击斩杀时不防御（先杀）。"""
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    _make_opponent_lethal(b)
    opp.current_hp = 1                              # 随便打一下就能杀
    action = RuleAgentV2("A", b.player_a).choose_action(b)
    assert action.kind == "skill"
    assert me.skills[action.skill_index].is_attack


def test_no_switch_out_right_after_switching_in():
    """刚换上来的那只不参与"残血换位"，避免"换出去又换回来"的空转（审计占换人决策 11%）。"""
    assert agent_v2._ANTI_SWITCH_LOOP is True
    b = _battle()
    me = b.player_a.active
    b.turn = 9
    me.entry_turn = 9                              # 本回合刚换上来
    me.current_hp = int(me.max_hp * 0.30)          # 残血（旧规则会想换）
    b.player_b.active.current_hp = int(b.player_b.active.max_hp * 0.95)
    action = RuleAgentV2("A", b.player_a).choose_action(b)
    assert action.kind != "switch", "刚上场就再换 = 空转"

    b.turn = 12                                    # 站了几回合之后 → 允许残血换位
    me.entry_turn = 9
    action2 = RuleAgentV2("A", b.player_a).choose_action(b)
    assert action2.kind in ("switch", "skill", "gather")   # 不再被这条规则挡住
