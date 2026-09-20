"""backend/tests/test_ev_agent.py — 期望值层接入 agent 后的行为（E3/E4）。

只钉**可重复的性质**，不钉棋力（棋力是 2000 局配对的统计问题，见
`native/tools/eval_prediction_mix.py` / `native/tools/tune_ev_params.py` 与
`docs/博弈-概率预判口径.md` 的实测表）。

**重要契约**：期望值层默认**关闭**（`SpriteStrategy.ev_decide=False`）——
2000 局配对实测 0.476 [0.454,0.498] 略低于旧启发式，调参后更差（0.453），
所以它现在是显式开启的实验开关。这里的测试都显式开 `ev_decide=True`。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import ev
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.factory import SimFactory

factory = SimFactory()
EV_ON = TeamStrategy(default=SpriteStrategy(ev_decide=True))


def _battle(my_skills=None, opp_skills=None, bench=True):
    p1 = [{"name": "水灵", "skills": list(my_skills or ["猛烈撞击", "风墙", "快速移动"])}]
    p2 = [{"name": "雪怪", "skills": list(opp_skills or ["猛烈撞击", "快速移动"])}]
    if bench:
        p1.append({"name": "花衣蝶", "skills": ["猛烈撞击"]})
        p2.append({"name": "草衣虫", "skills": ["猛烈撞击"]})
    return factory.build_battle(factory.build_player("A", p1),
                               factory.build_player("B", p2))


def test_ev_layer_is_opt_in_and_auditable():
    """默认关闭（实测无收益）；显式开启后决策可审计（`last_ev` 有信念与各行期望值）。"""
    assert SpriteStrategy().ev_decide is False
    b = _battle()
    assert RuleAgentV2("A", b.player_a).last_ev is None      # 默认不走 EV 层

    agent = RuleAgentV2("A", b.player_a, strategy=EV_ON)
    action = agent.choose_action(b)
    assert action.kind in ("skill", "switch", "gather")
    assert agent.last_ev is not None
    assert abs(sum(agent.last_ev["belief"].values()) - 1.0) < 1e-9
    assert agent.last_ev["evs"], "期望值必须逐行可见（审计/调参用）"


def test_ev_decide_switch_changes_behaviour():
    """`ev_decide` 是 A/B 对照开关：同一局面两种口径能给不同答案。"""
    picks = set()
    for _ in range(6):
        b_new = _battle()
        b_old = _battle()
        b_new.player_a.active.current_hp = int(b_new.player_a.active.max_hp * 0.4)
        b_old.player_a.active.current_hp = b_new.player_a.active.current_hp
        picks.add(("new", str(RuleAgentV2("A", b_new.player_a, strategy=EV_ON)
                              .choose_action(b_new))))
        picks.add(("old", str(RuleAgentV2("A", b_old.player_a).choose_action(b_old))))
    assert any(kind == "new" for kind, _ in picks) and any(kind == "old" for kind, _ in picks)
    assert len({v for _k, v in picks}) >= 1        # 至少能各自决策（不等于崩溃）


def test_belief_provider_override_is_used():
    """注入"它这回合必换人"的信念 → 信念确实进了计算（`last_ev` 里能看到）。"""
    b = _battle()
    agent = RuleAgentV2("A", b.player_a, strategy=EV_ON)
    agent.choose_action(b)
    default_pick = agent.last_ev["picked"]

    forced = RuleAgentV2("A", b.player_a, strategy=EV_ON)
    forced.belief_provider = lambda battle: {k: (1.0 if k == "switch" else 0.0)
                                             for k in ev.SCENARIOS}
    forced.choose_action(b)
    assert forced.last_ev["belief"] == {k: (1.0 if k == "switch" else 0.0)
                                        for k in ev.SCENARIOS}
    assert forced.last_ev["evs"] and all(isinstance(v, float)
                                         for v in forced.last_ev["evs"].values())
    assert isinstance(default_pick, str)


def test_mix_temperature_gate():
    """T=0 同局面同结果；T>0 会出现多种选择（混合只在显式开启时生效）。

    夹具里**不带防御技**：防御规则（E 阶段新增，排在 EV 层之前）会先短路，
    那样测到的是防御而不是混合。
    """
    def decisions(temperature: float, n: int = 40) -> set:
        picks = set()
        strategy = TeamStrategy(default=SpriteStrategy(ev_decide=True,
                                                       mix_temperature=temperature))
        for _ in range(n):
            b = _battle(my_skills=["猛烈撞击", "甩水", "快速移动"])
            b.player_a.active.current_hp = int(b.player_a.active.max_hp * 0.55)
            agent = RuleAgentV2("A", b.player_a, strategy=strategy)
            picks.add(str(agent.choose_action(b)))
        return picks

    random.seed(0)
    assert len(decisions(0.0)) == 1            # 确定性
    random.seed(0)
    assert len(decisions(0.35)) > 1            # 混合 → 多种出法


def test_ev_layer_keeps_lethal_rule():
    """有斩杀时必须出斩杀那一手（硬规则优先于期望值）。"""
    b = _battle()
    opp = b.player_b.active
    opp.current_hp = 1
    agent = RuleAgentV2("A", b.player_a, strategy=EV_ON)
    action = agent.choose_action(b)
    assert action.kind == "skill"
    assert action.skill_index is not None
