"""Gap9 六项：体重寄存器+砂糖弹球档位 / 六自由度 / 入梦 / 小型打劫 / 引力偏转 / 过山车。

契约（data/IR_GUIDE.md）：
- §1.2「体重寄存器与『两侧技能威力』口径」：`weight_self`/`weight_opp`（kg，Catalog 区间取中点）、
  派生查询 `weight_diff`（带符号）、`adjacent_power_sum`/`adjacent_power_diff`（不环绕、缺一侧按 0）；
- §二 变换链：`abs` → `per` → `scale` → `offset`；
- §3A `energize` 的 `team_*_all` 队伍落点、`power_mod attr:"energy_gain_delta" on_next:true` 的相位；
- §3C `starfall_trigger`（复用 `trigger_starfall`）与 `skill_rotate`（跨精灵轮转）。

每项都有「真实回合/真实技能」的实测断言，不是只测单元函数。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.engine.snapshot import adjacent_powers
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.vm.effect import MarkEffect
from backend.vm.resolve import resolve

factory = SimFactory()

SPRITE_WEIGHTS_PATH = Path('data/sprites/_weights.json')


# ═══════════════════════════════════════════════════════════════════
# 公共夹具
# ═══════════════════════════════════════════════════════════════════

def make_battle(specs_a, specs_b, *, energy: int = 10, entry: bool = False,
                active_a: int = 0, active_b: int = 0):
    p1 = factory.build_player('A', specs_a)
    p2 = factory.build_player('B', specs_b)
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = active_a
    battle.player_b.active_index = active_b
    for sprite in list(p1.team) + list(p2.team):
        sprite.energy = energy
    if entry:
        from backend.sim.traits import dispatch_entry
        dispatch_entry(p1.team[active_a], battle, 'A')
        dispatch_entry(p2.team[active_b], battle, 'B')
    return battle


def use(battle, team, idx, **kw):
    return battle._execute_skill_vm(team, Action('skill', skill_index=idx), **kw)


def skill_names(sprite) -> list[str]:
    return [bs.name for bs in sprite.skills]


def skill_power(sprite, idx: int) -> int:
    return sprite.skills[idx].power


def set_weight(sprite, kg: float) -> None:
    """直接改物种体重（只用于档位边界用例；真实取值见 sidecar 用例）。"""
    sprite.species.weight = float(kg)


# ═══════════════════════════════════════════════════════════════════
# G9-1 体重寄存器 + 砂糖弹球档位
# ═══════════════════════════════════════════════════════════════════

def test_weight_sidecar_is_complete_and_midpoint():
    """sidecar 覆盖全部 data/sprites 条目，取值 = 区间中点。"""
    assert SPRITE_WEIGHTS_PATH.is_file(), '先跑 backend/tools/gen_sprite_weights.py'
    payload = json.loads(SPRITE_WEIGHTS_PATH.read_text(encoding='utf-8'))
    weights = payload['weights']
    meta = payload['_meta']
    sprite_files = [p for p in Path('data/sprites').glob('*.json')
                    if not p.name.startswith('_')]
    assert len(weights) == len(sprite_files)
    assert meta['unmatched'] == 0
    # 水灵 "77~85.5KG" → (77 + 85.5) / 2
    assert weights['水灵'] == pytest.approx(81.25)
    # 喵喵 "3.62~4.6KG"
    assert weights['喵喵'] == pytest.approx(4.11)


def test_weight_loaded_onto_sprite_and_registers():
    """Sprite.weight / Ctx weight_self,weight_opp / 派生 weight_diff（带符号 + abs）。"""
    battle = make_battle(
        [{'name': '水灵', 'skills': ['猛烈撞击']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']}],
    )
    sa, sb = battle.player_a.active, battle.player_b.active
    assert sa.weight == pytest.approx(81.25)
    assert sb.weight == pytest.approx(4.11)

    ctx = battle._make_ctx(sa, sb, None, None, battle.globals, team='A', turn=1)
    assert ctx.weight_self == pytest.approx(81.25)
    assert ctx.weight_opp == pytest.approx(4.11)
    assert resolve(ctx, {'q': 'weight', 'of': 'sprite_self'}) == pytest.approx(81.25)

    diff = 81.25 - 4.11
    assert resolve(ctx, {'q': 'weight_diff', 'of': 'sprite_self'}) == pytest.approx(diff)
    # of=sprite_opp 反向（仍带符号）
    assert resolve(ctx, {'q': 'weight_diff', 'of': 'sprite_opp'}) == pytest.approx(-diff)
    # abs 变换链：per/scale/offset 之前取绝对值
    assert resolve(ctx, {'q': 'weight_diff', 'of': 'sprite_opp', 'abs': True}) \
        == pytest.approx(diff)
    assert resolve(ctx, {'q': 'weight_diff', 'of': 'sprite_opp', 'abs': True,
                         'per': 4}) == int(diff / 4)


def test_swapped_view_swaps_weight():
    battle = make_battle([{'name': '水灵', 'skills': ['猛烈撞击']}],
                         [{'name': '喵喵', 'skills': ['猛烈撞击']}])
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active, None, None,
                           battle.globals, team='A', turn=1)
    swapped = ctx.swapped_view()
    assert swapped.weight_self == pytest.approx(ctx.weight_opp)
    assert swapped.weight_opp == pytest.approx(ctx.weight_self)


@pytest.mark.parametrize('diff,expected', [
    (0.0, 20), (3.999, 20),
    (4.0, 40), (13.999, 40),
    (14.0, 60), (29.999, 60),
    (30.0, 80), (59.999, 80),
    (60.0, 100), (119.999, 100),
    (120.0, 120), (5000.0, 120),
])
def test_sugar_ball_tier_boundaries(diff, expected):
    """档位表（左闭右开，最后一段含等号）：`|diff|` → 威力。"""
    battle = make_battle([{'name': '水灵', 'skills': ['砂糖弹球']}],
                         [{'name': '水灵', 'skills': ['猛烈撞击']}])
    sa, sb = battle.player_a.active, battle.player_b.active
    set_weight(sa, diff)
    set_weight(sb, 0.0)
    use(battle, 'A', 0, is_first=True)
    assert skill_power(sa, 0) == expected


@pytest.mark.parametrize('diff,expected', [(-2.0, 20), (-30.0, 80), (-500.0, 120)])
def test_sugar_ball_uses_abs_of_signed_diff(diff, expected):
    """对方更重（weight_diff 为负）时按绝对值取档。"""
    battle = make_battle([{'name': '水灵', 'skills': ['砂糖弹球']}],
                         [{'name': '水灵', 'skills': ['猛烈撞击']}])
    sa, sb = battle.player_a.active, battle.player_b.active
    set_weight(sa, 0.0)
    set_weight(sb, -diff)
    use(battle, 'A', 0, is_first=True)
    assert skill_power(sa, 0) == expected


@pytest.mark.parametrize('a,b,expected', [
    ('喵喵', '迪莫', 20),        # 4.11 vs 6.25 → diff 2.14（0–4）
    ('小灵面', '影狸', 80),      # 0.325 vs 30.5 → diff 30.18（30–60）
    ('小灵面', '立方人', 120),   # 0.325 vs 122.785 → diff 122.46（≥120）
])
def test_sugar_ball_real_sprite_pairs(a, b, expected):
    """真实精灵体重（sidecar）驱动的三档实测。"""
    battle = make_battle([{'name': a, 'skills': ['砂糖弹球']}],
                         [{'name': b, 'skills': ['猛烈撞击']}])
    use(battle, 'A', 0, is_first=True)
    assert skill_power(battle.player_a.active, 0) == expected


# ═══════════════════════════════════════════════════════════════════
# G9-2 六自由度 —— 两侧技能威力差的四分之一
# ═══════════════════════════════════════════════════════════════════

def test_adjacent_powers_no_wrap_and_missing_side():
    battle = make_battle(
        [{'name': '水灵', 'skills': ['交叉闪电', '六自由度', '午夜噪音']}],  # 100 / 30 / 20
        [{'name': '喵喵', 'skills': ['猛烈撞击']}],
    )
    sa = battle.player_a.active
    assert adjacent_powers(sa, 1) == (120, 80)     # 左右 100 / 20
    assert adjacent_powers(sa, 0) == (30, 30)      # 0 号位只有右侧 30（不环绕，看不到末位 20）
    assert adjacent_powers(sa, 2) == (30, 30)      # 末位只有左侧 30
    assert adjacent_powers(sa, 9) == (0, 0)        # 越界
    # 单技能精灵：两侧皆缺
    assert adjacent_powers(battle.player_b.active, 0) == (0, 0)


def test_six_dof_adds_quarter_of_adjacent_diff():
    """两侧 100/20 → diff 80 → +20 → 威力 30+20=50。"""
    battle = make_battle(
        [{'name': '水灵', 'skills': ['交叉闪电', '六自由度', '午夜噪音']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']}],
    )
    sa = battle.player_a.active
    ctx = battle._make_ctx(sa, battle.player_b.active, sa.skills[1], None,
                           battle.globals, team='A', turn=1, skill_index=1)
    assert ctx.adjacent_power_sum == 120
    assert ctx.adjacent_power_diff == 80
    assert resolve(ctx, {'q': 'adjacent_power_diff', 'of': 'sprite_self'}) == 80

    use(battle, 'A', 1, is_first=True)
    assert skill_power(sa, 1) == 50


def test_six_dof_same_power_adds_nothing():
    battle = make_battle(
        [{'name': '水灵', 'skills': ['猛烈撞击', '六自由度', '猛烈撞击']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']}],
    )
    sa = battle.player_a.active
    use(battle, 'A', 1, is_first=True)
    assert skill_power(sa, 1) == 30


def test_adjacent_power_sum_register_is_alive():
    """`adjacent_power_sum` 此前写死 0（死寄存器），现在两侧读数都有值。"""
    battle = make_battle(
        [{'name': '水灵', 'skills': ['交叉闪电', '猛烈撞击', '午夜噪音']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']}],
    )
    sa = battle.player_a.active
    ctx = battle._make_ctx(sa, battle.player_b.active, sa.skills[1], None,
                           battle.globals, team='A', turn=1, skill_index=1)
    assert ctx.adjacent_power_sum == skill_power(sa, 0) + skill_power(sa, 2) == 120
    assert ctx.adjacent_power_diff == abs(skill_power(sa, 0) - skill_power(sa, 2))


# ═══════════════════════════════════════════════════════════════════
# G9-3 入梦 —— 敌方**下回合**回复的能量 -5
# ═══════════════════════════════════════════════════════════════════

def _dream_battle():
    return make_battle(
        [{'name': '水灵', 'skills': ['入梦', '猛烈撞击']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击', '水花四溅']}],
        energy=5,
    )


def test_rumeng_arms_next_turn_and_expires_after():
    battle = _dream_battle()
    sb = battle.player_b.active

    # T1：A 先手入梦（B 用技能，不聚能）→ 当回合不生效，只压入待生效队列
    battle.execute_turn(None, None, fixed_action_a=Action('skill', skill_index=0),
                        fixed_action_b=Action('skill', skill_index=0))
    assert sb._energy_gain_delta_turn == 0
    assert sb._pending_energy_gain_delta == [(-5, '入梦')] or sb._pending_energy_gain_delta

    # T2：武装到本回合 → 聚能 +0（5 − 5）
    before = sb.energy
    t2 = battle.execute_turn(None, None, fixed_action_a=Action('skill', skill_index=1),
                             fixed_action_b=Action('gather'))
    assert sb.energy - before == 0
    assert any('回复能量-5' in line for line in t2.to_message().splitlines())

    # T3：恢复正常 → 聚能 +5
    before = sb.energy
    battle.execute_turn(None, None, fixed_action_a=Action('skill', skill_index=1),
                        fixed_action_b=Action('gather'))
    assert sb.energy - before == 5


def test_rumeng_does_not_affect_same_turn_gather():
    """入梦当回合（B 后手聚能）不生效——『下回合』才 -5。"""
    battle = _dream_battle()
    sb = battle.player_b.active
    before = sb.energy
    battle.execute_turn(None, None, fixed_action_a=Action('skill', skill_index=0),
                        fixed_action_b=Action('gather'))
    assert sb.energy - before == 5   # A 用入梦不耗 B 能量，B 正常聚能 +5


def test_rumeng_cooldown_flag_preserved():
    """入梦 JSON 的 `flag_set cooldown`（冷却 2 回合）仍保留生效。"""
    battle = _dream_battle()
    use(battle, 'A', 0, is_first=True)
    assert battle.player_a.active.skills[0].cooldown == 2


def test_control_gather_without_rumeng():
    battle = make_battle([{'name': '水灵', 'skills': ['猛烈撞击']}],
                         [{'name': '喵喵', 'skills': ['猛烈撞击']}], energy=5)
    sb = battle.player_b.active
    before = sb.energy
    battle.execute_turn(None, None, fixed_action_a=Action('skill', skill_index=0),
                        fixed_action_b=Action('gather'))
    assert sb.energy - before == 5


def test_energy_gain_delta_queued_once_is_summed():
    """多条待生效累加；武装后清零，回合末归零。"""
    battle = _dream_battle()
    sb = battle.player_b.active
    sb.queue_energy_gain_delta(-5, '入梦')
    sb.queue_energy_gain_delta(-2, '入梦')
    assert sb.arm_pending_energy_gain() == -7
    assert sb._pending_energy_gain_delta == []
    assert sb.energy_gain_delta == -7
    sb.clear_turn_energy_gain()
    assert sb.energy_gain_delta == 0


def test_energy_gain_delta_fields_are_mcts_rollback_safe():
    """两个新字段必须进 `save_mutable_state`/`restore_mutable_state`（否则仿真泄漏到真实对局）。"""
    battle = _dream_battle()
    sb = battle.player_b.active
    saved = battle.save_mutable_state()

    sb.queue_energy_gain_delta(-5, '入梦')
    assert sb.arm_pending_energy_gain() == -5
    assert sb.energy_gain_delta == -5

    battle.restore_mutable_state(saved)
    assert sb._pending_energy_gain_delta == []
    assert sb._energy_gain_delta_turn == 0
    assert sb.energy_gain_delta == 0


# ═══════════════════════════════════════════════════════════════════
# G9-4 小型打劫 —— 敌方队伍中所有精灵失去 1 能量
# ═══════════════════════════════════════════════════════════════════

def _rob_battle(team_size: int = 6):
    return make_battle(
        [{'name': '水灵', 'skills': ['小型打劫', '猛烈撞击']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']} for _ in range(team_size)],
        energy=5,
    )


def test_small_rob_hits_all_six_including_bench():
    battle = _rob_battle(6)
    use(battle, 'A', 0, is_first=True)
    assert [s.energy for s in battle.player_b.team] == [4] * 6


def test_small_rob_floor_at_zero_and_includes_fainted():
    battle = _rob_battle(6)
    battle.player_b.team[1].energy = 0
    battle.player_b.team[2].energy = 0
    battle.player_b.team[3].energy = 3
    battle.player_b.team[4].current_hp = 0          # 力竭者照样参与
    use(battle, 'A', 0, is_first=True)
    assert [s.energy for s in battle.player_b.team] == [4, 0, 0, 2, 4, 4]


def test_small_rob_does_not_touch_own_team():
    """只作用于敌方队伍：己方替补能量不变，在场只付技能能耗（小型打劫 2 能耗）。"""
    battle = make_battle(
        [{'name': '水灵', 'skills': ['小型打劫', '猛烈撞击']},
         {'name': '火花', 'skills': ['猛烈撞击']}],
        [{'name': '喵喵', 'skills': ['猛烈撞击']} for _ in range(3)],
        energy=5,
    )
    use(battle, 'A', 0, is_first=True)
    assert [s.energy for s in battle.player_a.team] == [3, 5]
    assert [s.energy for s in battle.player_b.team] == [4, 4, 4]


def test_small_rob_three_sprites():
    battle = _rob_battle(3)
    use(battle, 'A', 0, is_first=True)
    assert [s.energy for s in battle.player_b.team] == [4, 4, 4]


# ═══════════════════════════════════════════════════════════════════
# G9-5 引力偏转 —— 手动触发星陨，与自然结算同值
# ═══════════════════════════════════════════════════════════════════

def _starfall_battle(skills_a, stacks: int = 6):
    battle = make_battle([{'name': '水灵', 'skills': list(skills_a)}],
                         [{'name': '喵喵', 'skills': ['猛烈撞击']}])
    battle.globals.apply_mark('B', '星陨印记', 'negative', stacks)
    return battle


def _starfall_stacks(battle, team: str) -> int:
    return sum(m.stacks for m in battle.globals.mark_effects.get(team, [])
               if isinstance(m, MarkEffect) and m.name == '星陨印记')


def test_gravitation_deflection_manual_trigger_equals_natural():
    """敌方 6 层星陨：引力偏转（应对成功，魔攻触发）与自然结算伤害同值。"""
    # 自然结算：水花四溅（魔攻、非幻系）命中即引爆
    natural = _starfall_battle(['水花四溅'])
    hp0 = natural.player_b.active.current_hp
    natural_events = use(natural, 'A', 0, is_first=True)
    natural_total = hp0 - natural.player_b.active.current_hp
    auto_line = next(e for e in natural_events if '星陨' in e)
    auto_dmg = int(auto_line.rsplit('-', 1)[1].rstrip('HP'))

    manual = _starfall_battle(['引力偏转'])
    hp0 = manual.player_b.active.current_hp
    manual_events = use(manual, 'A', 0, is_first=True,
                        countered_skill=manual.player_b.active.skills[0])
    manual_dmg = int(next(e for e in manual_events if '星陨' in e).rsplit('-', 1)[1].rstrip('HP'))

    assert manual_dmg == auto_dmg > 0
    assert natural_total > auto_dmg          # 自然结算还含技能自身伤害
    assert _starfall_stacks(natural, 'B') == _starfall_stacks(manual, 'B') == 0


def test_gravitation_deflection_no_marks_is_noop():
    battle = _starfall_battle(['引力偏转'], stacks=0)
    hp0 = battle.player_b.active.current_hp
    events = use(battle, 'A', 0, is_first=True,
                 countered_skill=battle.player_b.active.skills[0])
    assert hp0 - battle.player_b.active.current_hp == 0
    assert not [e for e in events if '星陨' in e]


def test_gravitation_deflection_requires_counter_success():
    """未应对成功（无 countered_skill）时不触发星陨。"""
    battle = _starfall_battle(['引力偏转'])
    hp0 = battle.player_b.active.current_hp
    events = use(battle, 'A', 0, is_first=True)
    assert hp0 - battle.player_b.active.current_hp == 0
    assert _starfall_stacks(battle, 'B') == 6
    assert not [e for e in events if '星陨' in e]


def test_gravitation_deflection_keeps_80_percent_reduction():
    battle = _starfall_battle(['引力偏转'], stacks=0)
    use(battle, 'A', 0, is_first=True)
    assert battle.player_a.active._modifiers.get('damage_reduction') == pytest.approx(0.8)


def test_starfall_trigger_op_can_target_own_team():
    """`target: "sprite_self"` = 触发自己队伍持有的星陨印记（防守方=自己）。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StarfallTrigger

    battle = _starfall_battle(['猛烈撞击'])
    battle.globals.apply_mark('A', '星陨印记', 'negative', 4)
    replayer = JournalReplayer(battle.player_a.active, battle.player_b.active,
                              battle.globals, battle._vm_engine.registry,
                              team='A', battle=battle)
    events = replayer.replay([StarfallTrigger(target='sprite_self', damage_type='魔攻')])
    assert _starfall_stacks(battle, 'A') == 0
    assert any('星陨' in e for e in events)


# ═══════════════════════════════════════════════════════════════════
# G9-6 过山车 —— 己方全队技能跨精灵向下轮转 1 位
# ═══════════════════════════════════════════════════════════════════
#
# 说明：过山车自带 `qiaobian: {element: 机械}`，**用完后本槽位会变成随机机械系技能**
# （`morph.apply_after_use`），因此结构类断言走可复用 pass `Battle.rotate_team_skills()`
# 直接驱动（不经过技能→不触发巧变），端到端那条单独容忍巧变产物。

def _rotate_team():
    return make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击']},
        {'name': '喵喵', 'skills': ['水花四溅', '灵媒']},
        {'name': '火花', 'skills': ['交叉闪电', '双星']},
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击', '灵媒']}])


def test_overcoaster_rotates_three_sprites_down_one():
    """3 只 × 2 槽：[A,B,C,D,E,F] → [F,A,B,C,D,E]。"""
    battle = _rotate_team()
    before = [skill_names(s) for s in battle.player_a.team]
    assert before == [['过山车', '猛烈撞击'], ['水花四溅', '灵媒'], ['交叉闪电', '双星']]

    battle.rotate_team_skills('A', 1)

    after = [skill_names(s) for s in battle.player_a.team]
    assert after == [['双星', '过山车'], ['猛烈撞击', '水花四溅'], ['灵媒', '交叉闪电']]
    # 逐槽位核对「向下移动 1 位、末位回到首位」
    flat_before = [n for row in before for n in row]
    flat_after = [n for row in after for n in row]
    assert flat_after == [flat_before[-1]] + flat_before[:-1]


def test_overcoaster_end_to_end_through_skill():
    """端到端：A 使用过山车（技能 JSON 的 `skill_rotate`）→ 跨精灵下移 1 位。

    用过山车自己所在槽位之外的两个标记技能定位（该槽位用完后被巧变替换）。
    """
    battle = _rotate_team()
    use(battle, 'A', 0, is_first=True)
    after = [skill_names(s) for s in battle.player_a.team]

    # 原本在 (2,1) 的双星 → (0,0)；原本在 (2,0) 的交叉闪电 → (2,1)
    assert after[0][0] == '双星'
    assert after[2][1] == '交叉闪电'
    # 过山车所在的 0 号槽位轮转后落在 (0,1)，且已被巧变替换为机械系技能
    moved = battle.player_a.team[0].skills[1]
    assert moved.element == '机械'


def test_overcoaster_variable_slot_counts():
    """槽位数不同（3/2/1/2/3/1）：按各精灵原槽位数分段写回，技能总数不变。"""
    battle = make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击', '灵媒']},
        {'name': '喵喵', 'skills': ['水花四溅', '双星']},
        {'name': '火花', 'skills': ['交叉闪电']},
        {'name': '迪莫', 'skills': ['双星', '灵媒']},
        {'name': '火神', 'skills': ['猛烈撞击', '水花四溅', '灵媒']},
        {'name': '罗隐', 'skills': ['双星']},
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    before = [skill_names(s) for s in battle.player_a.team]
    battle.rotate_team_skills('A', 1)
    after = [skill_names(s) for s in battle.player_a.team]

    assert [len(row) for row in after] == [len(row) for row in before]
    flat_before = [n for row in before for n in row]
    flat_after = [n for row in after for n in row]
    assert sorted(flat_after) == sorted(flat_before)
    assert flat_after == [flat_before[-1]] + flat_before[:-1]


def test_overcoaster_skips_main_axis_slot():
    """主轴（transmission == -1）位置锁定：原地不动，其余槽位仍整体轮转 1 位。"""
    battle = make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击']},
        {'name': '喵喵', 'skills': ['锁芯', '灵媒']},   # 锁芯 = 主轴（transmission: -1）
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    assert battle.player_a.team[1].skills[0]._transmission == -1

    battle.rotate_team_skills('A', 1)

    after = [skill_names(s) for s in battle.player_a.team]
    # 参与槽位 = [过山车, 猛烈撞击, 灵媒]（锁芯是主轴，被跳过），轮转 1 位
    # → 灵媒 / 过山车 / 猛烈撞击 依次写回参与槽位
    assert after == [['灵媒', '过山车'], ['锁芯', '猛烈撞击']]


def test_overcoaster_skips_replaced_slot():
    """被替换槽位（replaced_by：借用/愿力/巧变产物）同样位置锁定。"""
    battle = make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击']},
        {'name': '喵喵', 'skills': ['水花四溅', '灵媒']},
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    locked = battle.player_a.team[1].skills[0]
    locked.replaced_by = factory.get_skill_by_name('双星')

    battle.rotate_team_skills('A', 1)

    after = [skill_names(s) for s in battle.player_a.team]
    # 参与槽位 = [过山车, 猛烈撞击, 灵媒]（水花四溅被 replaced_by 锁定、跳过）
    # → 灵媒 / 过山车 / 猛烈撞击 依次写回参与槽位；锁定槽位原地不动
    assert after[0] == ['灵媒', '过山车']
    assert after[1] == ['双星', '猛烈撞击']


def test_overcoaster_does_not_touch_opponent():
    battle = make_battle([{'name': '水灵', 'skills': ['过山车', '猛烈撞击']}],
                         [{'name': '花衣蝶', 'skills': ['猛烈撞击', '灵媒']}])
    before = skill_names(battle.player_b.active)
    battle.rotate_team_skills('A', 1)
    assert skill_names(battle.player_b.active) == before


def test_overcoaster_offset_two_rotates_two():
    battle = make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击']},
        {'name': '喵喵', 'skills': ['水花四溅', '灵媒']},
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    battle.rotate_team_skills('A', 2)
    after = [skill_names(s) for s in battle.player_a.team]
    assert after == [['水花四溅', '灵媒'], ['过山车', '猛烈撞击']]


def test_overcoaster_fires_position_changed_notification():
    """移动到的槽位触发 `skill_position_changed` 通知（与传动 pass 一致）。"""
    battle = make_battle([
        {'name': '水灵', 'skills': ['过山车', '猛烈撞击']},
        {'name': '喵喵', 'skills': ['水花四溅', '灵媒']},
    ], [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    seen = []
    orig = battle._fire_skill_position_changed

    def spy(team, sprite, moved_skill):
        seen.append((team, sprite.name, moved_skill.name))
        return orig(team, sprite, moved_skill)

    battle._fire_skill_position_changed = spy
    battle.rotate_team_skills('A', 1)
    assert seen                                  # 至少移动了一个技能且逐条通知
    assert all(team == 'A' for team, _, _ in seen)
    assert {name for _, _, name in seen} == {'猛烈撞击', '水花四溅', '灵媒', '过山车'}


def test_overcoaster_qiaobian_key_preserved():
    data = json.loads(Path('data/skills/过山车.json').read_text(encoding='utf-8'))
    assert data['qiaobian'] == {'element': '机械'}
    assert data['effects'][0]['op'] == 'skill_rotate'
    skill = factory.get_skill_by_name('过山车')
    assert skill.qiaobian == {'element': '机械'}


def test_skill_rotate_noop_with_single_participating_slot():
    battle = make_battle([{'name': '水灵', 'skills': ['过山车']}],
                         [{'name': '花衣蝶', 'skills': ['猛烈撞击']}])
    assert battle.rotate_team_skills('A', 1) == []
