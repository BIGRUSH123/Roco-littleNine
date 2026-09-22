"""`mark` op 的两个新增能力（data/IR_GUIDE.md §3B mark）。

- `name: "random_positive"` / `"random_negative"`（薄纱环「随机获得1种正面/负面印记」）
- `action: "enhance_all"`（许愿池「双方已有的印记层数+1」，只加层不新建）
"""

from __future__ import annotations

import random

from backend.engine.mark_config import NEGATIVE_MARK_NAMES, POSITIVE_MARK_NAMES
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.ops.mark import resolve_random_mark_name

factory = SimFactory()


def make_battle(skills_a=("薄纱环",)):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    p1.team[0].energy = 10
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def use(battle, team, idx, branch=None, **kw):
    return battle._execute_skill_vm(
        team, Action("skill", skill_index=idx, branch=branch), **kw)


def marks(battle, team):
    return {m.name: m.stacks for m in battle.globals.mark_effects.get(team, [])}


# ═════════════════ 随机正/负印记 ═════════════════

def test_resolve_random_mark_name_hits_template_pools():
    names = {resolve_random_mark_name("random_positive") for _ in range(50)}
    assert names and names <= set(POSITIVE_MARK_NAMES)
    names = {resolve_random_mark_name("random_negative") for _ in range(50)}
    assert names and names <= set(NEGATIVE_MARK_NAMES)
    assert resolve_random_mark_name("攻击印记") is None


def test_baoshahuan_branch_ming_gives_enemy_random_negative_mark():
    battle = make_battle()
    random.seed(7)
    use(battle, "A", 0, branch=0, is_first=True)
    assert marks(battle, "A") == {}                     # 不给自己
    opp = marks(battle, "B")
    assert len(opp) == 1
    assert set(opp) <= set(NEGATIVE_MARK_NAMES)
    assert set(opp.values()) == {1}


def test_baoshahuan_branch_an_gives_self_random_positive_mark():
    battle = make_battle()
    random.seed(7)
    use(battle, "A", 0, branch=1, is_first=True)
    assert marks(battle, "B") == {}                     # 不给敌方
    own = marks(battle, "A")
    assert len(own) == 1
    assert set(own) <= set(POSITIVE_MARK_NAMES)


def test_random_mark_is_deterministic_under_seed():
    got = []
    for _ in range(2):
        battle = make_battle()
        random.seed(11)
        use(battle, "A", 0, branch=1, is_first=True)
        got.append(marks(battle, "A"))
    assert got[0] == got[1]


# ═════════════════ enhance_all ═════════════════

def test_xuyuanchi_enhances_every_existing_mark_on_both_teams():
    battle = make_battle(["许愿池"])
    battle.globals.apply_mark("A", "攻击印记", "positive", 3)
    battle.globals.apply_mark("B", "棘刺", "negative", 2)

    use(battle, "A", 0, is_first=True)

    assert marks(battle, "A") == {"攻击印记": 4}
    assert marks(battle, "B") == {"棘刺": 3}


def test_xuyuanchi_does_not_create_marks():
    battle = make_battle(["许愿池"])
    use(battle, "A", 0, is_first=True)
    assert marks(battle, "A") == {}
    assert marks(battle, "B") == {}


def test_enhance_all_is_idempotent_per_use():
    battle = make_battle(["许愿池", "许愿池"])
    battle.globals.apply_mark("A", "光合印记", "positive", 1)
    use(battle, "A", 0, is_first=True)
    assert marks(battle, "A") == {"光合印记": 2}
    use(battle, "A", 1, is_first=False)
    assert marks(battle, "A") == {"光合印记": 3}
