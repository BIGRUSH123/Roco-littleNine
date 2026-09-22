"""上回合系别寄存器 `last_turn_element:<系别>` 与 10 条依赖它的 data。

口径（data/IR_GUIDE.md §1.2 / §五）：
- 回合末**覆盖写**（绝不累加）：`Battle._last_turn_elements` + 队伍计数器
  `last_turn_element:<系别>`；累计口径的 `element:<系别>` 不受影响。
- Ctx 暴露 `last_turn_element_own/_opp/_both`（both = 双方合计，任一方用过即 >0）
  与 `last_turn_energy_sum_own/_opp/_both`；条件 `last_turn_had_element` 只读寄存器。
- 游戏内文本「若上回合双方有精灵使用 X 系技能」按**存在性**落地（of 默认 team_both）。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.pipeline import TurnPipeline
from backend.sim.traits import dispatch_entry
from backend.vm.resolve import resolve

factory = SimFactory()

_SKILL_DB = {}
for _p in Path("data/skills").glob("*.json"):
    try:
        _d = json.loads(_p.read_text(encoding="utf-8"))
    except Exception:
        continue
    if "name" in _d and "element" in _d:
        _SKILL_DB[_d["name"]] = _d


def skill_of_element(element: str, max_cost: int = 3) -> str:
    """取一个该系别、低能耗的真实技能名（作为「上回合前置技能」）。"""
    cands = [
        d for d in _SKILL_DB.values()
        if d.get("element") == element and (d.get("energy_cost") or 0) <= max_cost
        and d.get("skill_type") in ("状态", "物攻", "魔攻", "动态攻击")
    ]
    cands.sort(key=lambda d: (d.get("energy_cost") or 0, d["name"]))
    return cands[0]["name"]


def make_battle(skills_a, skills_b=("猛烈撞击",), trait_a=None, ability_id=0,
                team_b_species=None):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": list(skills_a)}])
    specs = team_b_species or ["花衣蝶"]
    p2 = factory.build_player("B", [{"name": n, "skills": list(skills_b)} for n in specs])
    if trait_a:
        p1.team[0].species.ability = trait_a
        p1.team[0].species.ability_id = ability_id
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    for sprite in list(p1.team) + list(p2.team):
        sprite.energy = 10
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def use(battle, team, idx, branch=None, **kw):
    return battle._execute_skill_vm(
        team, Action("skill", skill_index=idx, branch=branch), **kw)


def team_marks(battle, team):
    return {m.name: m.stacks for m in battle.globals.mark_effects.get(team, [])}


def run_two_turn(target_skill, prep_element, observer, trait=None, ability_id=0):
    """第一回合用 prep_element 系技能（或不满足），第二回合用 target_skill。

    返回 (条件不成立时的观测值, 条件成立时的观测值)。
    """
    prep = skill_of_element(prep_element)
    got = []
    for prepping in (False, True):
        battle = make_battle([prep, target_skill, "猛烈撞击", "猛烈撞击"],
                             trait_a=trait, ability_id=ability_id)
        if prepping:
            use(battle, "A", 0, is_first=True)
        else:
            use(battle, "A", 2, is_first=True)   # 普通系干扰
        use(battle, "B", 0, is_first=False)
        battle._write_turn_match_counters()
        got.append(observer(battle))
    return got, prep


# ═════════════════ 寄存器 ═════════════════

def test_register_is_overwritten_not_accumulated():
    battle = make_battle(["甩水", "奇点"])
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active, None, None,
                           battle.globals, team="A", turn=battle.turn)
    assert ctx.last_turn_element_both == {}

    use(battle, "A", 0, is_first=True)      # 甩水 = 水系
    use(battle, "B", 0, is_first=False)     # 猛烈撞击 = 普通系
    battle._write_turn_match_counters()

    assert battle._last_turn_elements["A"] == {"水": 1}
    assert battle._last_turn_elements["B"] == {"普通": 1}
    assert battle.team_counters["A"]["last_turn_element:水"] == 1
    assert battle.team_counters["B"]["last_turn_element:普通"] == 1
    assert battle.team_counters["A"]["last_turn_energy_sum"] == 0

    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active, None, None,
                           battle.globals, team="A", turn=battle.turn)
    assert ctx.last_turn_element_own == {"水": 1}
    assert ctx.last_turn_element_opp == {"普通": 1}
    assert ctx.last_turn_element_both == {"水": 1, "普通": 1}
    assert resolve(ctx, {"q": "last_turn_element", "of": "team_own", "name": "水"}) == 1
    assert resolve(ctx, {"q": "last_turn_element", "of": "team_opp", "name": "水"}) == 0

    # 第二回合只用幻系 → 上一回合的水系键必须消失（覆盖写，不累加/不残留）
    use(battle, "A", 1, is_first=True)      # 奇点 = 幻系
    battle._write_turn_match_counters()
    assert battle._last_turn_elements["A"] == {"幻": 1}
    assert "last_turn_element:水" not in battle.team_counters["A"]
    # 累计口径不受影响
    assert battle.team_counters["A"]["element:水"] == 1


class _Gatherer:
    """占位 agent（动作由 fixed_action_* 提供）。"""

    def choose_action(self, battle):
        from backend.sim.agent import _GATHER_ACTION
        return _GATHER_ACTION

    def choose_lead(self, battle):
        return 0

    def choose_replacement(self, battle):
        return 0

    def on_game_end(self, winner):
        pass


def test_real_turn_loop_writes_register_and_unlocks_qidian():
    """走完整回合管线（execute_turn_headless）：回合末自动写寄存器，第二回合条件成立。"""
    battle = make_battle(["水弹", "奇点"])
    water = Action("skill", skill_index=0)
    hit = Action("skill", skill_index=0)
    battle.execute_turn_headless(_Gatherer(), _Gatherer(),
                                 fixed_action_a=water, fixed_action_b=hit)
    assert battle._last_turn_elements["A"] == {"水": 1}

    qidian = Action("skill", skill_index=1)
    battle.execute_turn_headless(_Gatherer(), _Gatherer(),
                                 fixed_action_a=qidian, fixed_action_b=hit)
    assert team_marks(battle, "B").get("星陨印记", 0) == 4


def test_ctx_view_swaps_last_turn_registers():
    battle = make_battle(["甩水"])
    use(battle, "A", 0, is_first=True)
    use(battle, "B", 0, is_first=False)
    battle._write_turn_match_counters()
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active, None, None,
                           battle.globals, team="A", turn=battle.turn)
    swapped = ctx.swapped_view()
    assert swapped.last_turn_element_own == ctx.last_turn_element_opp
    assert swapped.last_turn_element_opp == ctx.last_turn_element_own


# ═════════════════ 7 个技能 ═════════════════

def test_qidian_water_last_turn_grants_starfall_marks():
    (off, on), _ = run_two_turn("奇点", "水", lambda b: (
        use(b, "A", 1, is_first=True), team_marks(b, "B").get("星陨印记", 0))[1])
    assert off == 0
    assert on == 4


def test_lueying_fire_last_turn_steals_energy():
    def obs(b):
        before_self = b.player_a.active.energy
        before_opp = b.player_b.active.energy
        use(b, "A", 1, is_first=True)
        return (b.player_b.active.energy - before_opp,
                b.player_a.active.energy - before_self)

    (off, on), _ = run_two_turn("掠影", "火", obs)
    assert off == (0, -3)      # 只付了技能能耗，没有偷取
    assert on == (-3, 0)       # 偷回 3 能量，抵消能耗


def test_xinghuo_light_last_turn_adds_burn():
    def obs(b):
        use(b, "A", 1, is_first=True)
        return b.player_b.active._cached_abnormals.get("灼烧", 0)

    (off, on), _ = run_two_turn("星火", "光", obs)
    assert (off, on) == (8, 20)


def test_maimang_grass_last_turn_restores_energy():
    def obs(b):
        before = b.player_a.active.energy
        use(b, "A", 1, is_first=True)
        return b.player_a.active.energy - before

    (off, on), _ = run_two_turn("麦芒", "萌", obs)
    assert off == -5           # 只有技能能耗
    assert on == 0             # 5 能耗 + 回 7（上限 10 截断）


def test_yueying_jiaocuo_illusion_last_turn_grants_swift():
    def obs(b):
        bs = b.player_a.active.skills[1]
        use(b, "A", 1, is_first=True)
        return float(bs._modifiers.get("swift", 0) or 0)

    (off, on), _ = run_two_turn("月影交错", "幻", obs)
    assert off == 0.0
    assert on == 1.0
    # 迅捷的消费点（入场自动出招）认这个标记
    battle = make_battle(["月影交错"])
    battle.player_a.active.skills[0]._modifiers["swift"] = 1
    idx, bs = battle._find_first_swift_skill(battle.player_a.active)
    assert idx == 0 and bs is not None


def test_xinsu_ground_last_turn_grants_extra_devotion():
    def obs(b):
        b.get_player("A").devotion.clear()
        use(b, "A", 1, is_first=True)
        return sum(b.get_player("A").devotion.values())

    (off, on), _ = run_two_turn("信息素", "地", obs)
    assert off == 1
    assert on == 4


def test_huishou_uses_opp_switched_event_not_last_turn_register():
    """回收的 desc 是「敌方**本回合**更换精灵」——用 opp_switched 事件，不读上回合寄存器。"""
    for enemy_switched in (False, True):
        battle = make_battle(["回收"], skills_b=["猛烈撞击"],
                             team_b_species=["花衣蝶", "绿翼鸟"])
        before = battle.player_b.active.energy
        if enemy_switched:
            battle._resolve_switch("B", Action("switch", switch_index=1))
        use(battle, "A", 0, is_first=True, opponent_switched=enemy_switched)
        if enemy_switched:
            assert battle.player_b.active.energy == before - 4
        else:
            assert battle.player_b.active.energy == before


# ═════════════════ 3 个特性 ═════════════════

def run_trait_two_turn(trait, ability_id, prep_element, skill_of_interest, observer):
    prep = skill_of_element(prep_element)
    got = []
    for prepping in (False, True):
        battle = make_battle([prep, skill_of_interest, "猛烈撞击"],
                             trait_a=trait, ability_id=ability_id)
        if prepping:
            use(battle, "A", 0, is_first=True)
        else:
            use(battle, "A", 2, is_first=True)
        use(battle, "B", 0, is_first=False)
        battle._write_turn_match_counters()
        battle.turn += 1
        battle._ctx_team_cache.clear()
        TurnPipeline.execute_turn_start(battle)
        got.append(observer(battle))
    return got, prep


def test_lengguangyuan_wing_last_turn_boosts_ice_skills():
    ice = skill_of_element("冰")
    (off, on), _ = run_trait_two_turn(
        "冷光源", 200316, "翼", ice,
        lambda b: [(bs.name, round(float(bs._modifiers.get("power_mult", 1.0)), 3))
                   for bs in b.player_a.active.skills])
    assert all(pm == 1.0 for _, pm in off)
    assert dict(on)[ice] == 2.0
    assert dict(on)["猛烈撞击"] == 1.0      # 只影响冰系


def test_rechengxiang_fire_last_turn_boosts_bug_skills():
    bug = skill_of_element("虫")
    (off, on), _ = run_trait_two_turn(
        "热成像", 200315, "火", bug,
        lambda b: [(bs.name, round(float(bs._modifiers.get("power_mult", 1.0)), 3))
                   for bs in b.player_a.active.skills])
    assert all(pm == 1.0 for _, pm in off)
    assert dict(on)[bug] == 2.0
    assert dict(on)["猛烈撞击"] == 1.0


def test_jiyinbianji_sets_base_cost_to_last_turn_energy_sum():
    """基础能耗 = 上回合双方使用技能的能耗之和（绝对值，不是增量）。"""
    water = skill_of_element("水")
    battle = make_battle([water, "猛烈撞击", "水弹"], trait_a="基因编辑", ability_id=200318)
    # 第一回合：没有上一回合 → 不生效（实现口径，见 data/traits/基因编辑.json）
    battle.turn += 1
    battle._ctx_team_cache.clear()
    TurnPipeline.execute_turn_start(battle)
    base_costs = [bs.energy_cost for bs in battle.player_a.active.skills]
    assert base_costs == [bs.base.energy_cost for bs in battle.player_a.active.skills]

    # 第一回合双方各用一只技能 → 和 = 该技能能耗 + 1（猛烈撞击）
    use(battle, "A", 0, is_first=True)
    use(battle, "B", 0, is_first=False)
    battle._write_turn_match_counters()
    total = _SKILL_DB[water]["energy_cost"] + 1
    assert battle._last_turn_energy["A"] == _SKILL_DB[water]["energy_cost"]
    assert battle._last_turn_energy["B"] == 1

    battle.turn += 1
    battle._ctx_team_cache.clear()
    TurnPipeline.execute_turn_start(battle)
    assert [bs.energy_cost for bs in battle.player_a.active.skills] == [total] * len(battle.player_a.active.skills)
