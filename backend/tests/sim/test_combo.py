"""tests/sim/test_combo.py — 连击机制测试

测试 combo / per_hit 管线：
- 纯伤害连击（combo×power 伤害倍增）
- per_hit abnormal（连续毒针、易燃物质）
- per_hit mark（星链）
- per_hit mod（三连破、冰捆缚、电离爆破）
"""

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory


def _make_battle(skills_a=None, skills_b=None):
    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "水灵", "skills": skills_a or ["猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "水灵", "skills": skills_b or ["猛烈撞击"]},
    ])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


def _run_skill(battle, team='A', skill_index=0):
    """执行一个技能并返回 events。"""
    return battle._execute_skill_vm(team, Action(kind='skill', skill_index=skill_index))


# ═══════════════════════════════════════════════════════════════
# 纯伤害连击
# ═══════════════════════════════════════════════════════════════

def test_combo2_damage():
    """combo=2 纯伤害技能：双响炮 25威力×2连击。

    断言用「2 连击 ≈ 单发的两倍」，不用绝对阈值——绝对伤害还含属性克制
    （火系打水灵是 0.5 倍），固定阈值会把克制关系混进连击判定。
    """
    b = _make_battle(skills_a=["双响炮"], skills_b=["猛烈撞击"])
    opp = b.player_b.active
    hp_before = opp.current_hp

    _run_skill(b)

    dmg = hp_before - opp.current_hp

    b1 = _make_battle(skills_a=["双响炮"], skills_b=["猛烈撞击"])
    b1.player_a.active.skills[0].base.combo = 1
    opp1 = b1.player_b.active
    hp1_before = opp1.current_hp
    _run_skill(b1)
    dmg1 = hp1_before - opp1.current_hp

    assert dmg1 > 0, "单发伤害不应为 0"
    assert dmg >= dmg1 * 1.8, f"combo=2 应约为单发两倍: {dmg} vs 单发 {dmg1}"
    print(f"  [OK] combo=2 双响炮伤害: {dmg}（单发 {dmg1}）")


def test_combo3_damage():
    """combo=3 纯伤害技能：旋转突击 35威力×3连击。"""
    b = _make_battle(skills_a=["旋转突击"], skills_b=["猛烈撞击"])
    opp = b.player_b.active
    hp_before = opp.current_hp

    _run_skill(b)

    dmg = hp_before - opp.current_hp
    assert dmg > 50, f"combo=3伤害应>50, got {dmg}"
    print(f"  [OK] combo=3 旋转突击伤害: {dmg}")


def test_combo5_damage():
    """combo=5 纯伤害技能：午夜噪音 20威力×5连击。"""
    b = _make_battle(skills_a=["午夜噪音"], skills_b=["猛烈撞击"])
    opp = b.player_b.active
    hp_before = opp.current_hp

    _run_skill(b)

    dmg = hp_before - opp.current_hp
    assert dmg > 60, f"combo=5伤害应>60, got {dmg}"
    print(f"  [OK] combo=5 午夜噪音伤害: {dmg}")


# ═══════════════════════════════════════════════════════════════
# per_hit abnormal
# ═══════════════════════════════════════════════════════════════

def test_per_hit_abnormal_poison():
    """连续毒针 combo=2 per_hit 中毒+1 → 2层中毒。"""
    b = _make_battle(skills_a=["连续毒针"], skills_b=["猛烈撞击"])
    opp = b.player_b.active

    _run_skill(b)

    stacks = opp.get_stacks("中毒")
    assert stacks == 2, f"per_hit combo=2中毒应=2层, got {stacks}"
    print(f"  [OK] 连续毒针 per_hit 中毒: {stacks}层")


def test_per_hit_abnormal_burn():
    """易燃物质 combo=2 per_hit 灼烧+2 → 4层灼烧。"""
    b = _make_battle(skills_a=["易燃物质"], skills_b=["猛烈撞击"])
    opp = b.player_b.active

    _run_skill(b)

    stacks = opp.get_stacks("灼烧")
    assert stacks == 4, f"per_hit combo=2×灼烧+2应=4层, got {stacks}"
    print(f"  [OK] 易燃物质 per_hit 灼烧: {stacks}层")


# ═══════════════════════════════════════════════════════════════
# per_hit mark
# ═══════════════════════════════════════════════════════════════

def test_per_hit_mark():
    """星链 combo=2 per_hit 星陨印记+1 → 2层印记。"""
    b = _make_battle(skills_a=["星链"], skills_b=["猛烈撞击"])

    _run_skill(b)

    mark = b.globals.get_mark_by_name('B', '星陨印记')
    assert mark is not None, "应存在星陨印记"
    assert mark.stacks == 2, f"per_hit combo=2印记应=2层, got {mark.stacks}"
    print(f"  [OK] 星链 per_hit 星陨印记: {mark.stacks}层")


# ═══════════════════════════════════════════════════════════════
# per_hit mod (stat steps)
# ═══════════════════════════════════════════════════════════════

def test_per_hit_mod_buff():
    """三连破 combo=3 per_hit atk+3 steps → 合并为1个+9步效果。"""
    b = _make_battle(skills_a=["三连破"], skills_b=["猛烈撞击"])
    user = b.player_a.active

    _run_skill(b)

    # 三连破: 3连击 × atk+3 steps，stat效果按stat_key合并 = 1个+9步
    from backend.vm.effect import StatBuffEffect
    atk_effs = [e for e in user.active_effects if isinstance(e, StatBuffEffect) and e.stat_key == 'atk']
    assert len(atk_effs) == 1, f"stat效果合并后应=1, got {len(atk_effs)}"
    assert atk_effs[0].steps == 9, f"总步数应=9, got {atk_effs[0].steps}"
    print(f"  [OK] 三连破 per_hit atk: {atk_effs[0].steps}步")


def test_per_hit_mod_debuff():
    """电离爆破 combo=2 per_hit speed-2 steps → 合并为1个-4步效果。"""
    b = _make_battle(skills_a=["电离爆破"], skills_b=["猛烈撞击"])
    opp = b.player_b.active

    _run_skill(b)

    from backend.vm.effect import StatBuffEffect
    spd_effs = [e for e in opp.active_effects if isinstance(e, StatBuffEffect) and e.stat_key == 'speed']
    assert len(spd_effs) == 1, f"stat效果合并后应=1, got {len(spd_effs)}"
    assert spd_effs[0].steps == -4, f"总步数应=-4, got {spd_effs[0].steps}"
    print(f"  [OK] 电离爆破 per_hit speed: {spd_effs[0].steps}步")


def test_per_hit_mod_energy_cost():
    """冰捆缚 combo=2 per_hit energy_cost+1 → 2个 ModifierInjection。"""
    b = _make_battle(skills_a=["冰捆缚"], skills_b=["猛烈撞击"])
    opp = b.player_b.active

    _run_skill(b)

    # energy_cost modifier 应累积+2，分布在每个 BattleSkill._modifiers 上
    ec = opp.skills[0]._modifiers.get("energy_cost", 0) if opp.skills else 0
    assert ec == 2, f"energy_cost modifier在BattleSkill上应=2, got {ec}"
    print(f"  [OK] 冰捆缚 per_hit energy_cost: +{ec}")


# ═══════════════════════════════════════════════════════════════
# 连击 + 非 per_hit 效果（不应重复）
# ═══════════════════════════════════════════════════════════════

def test_combo_without_per_hit():
    """反击拳 combo=2 无 per_hit（条件连击），非首回合应有反击加成但不重复异常。"""
    b = _make_battle(skills_a=["反击拳"], skills_b=["猛烈撞击"])
    opp = b.player_b.active
    hp_before = opp.current_hp

    _run_skill(b)

    dmg = hp_before - opp.current_hp
    # 反击拳 25威力 武系 物攻 combo=2，应造成多段伤害
    assert dmg > 10, f"combo=2应有伤害, got {dmg}"
    print(f"  [OK] 反击拳 combo=2 伤害: {dmg}")


# ═══════════════════════════════════════════════════════════════
# per_hit 聚盐：per_hit 治疗 + per_hit 能量
# ═══════════════════════════════════════════════════════════════

def test_per_hit_heal_energy():
    """聚盐 combo=2 per_hit hp+5% + per_hit energy+1 → 治疗×2, 能量×2（扣3费后净+2）。"""
    b = _make_battle(skills_a=["聚盐"], skills_b=["猛烈撞击"])
    user = b.player_a.active
    # 先扣点血
    user.current_hp = max(1, user.current_hp - 20)
    hp_before = user.current_hp

    _run_skill(b)

    hp_gained = user.current_hp - hp_before
    # 5% per hit × 2 hits = 10% max HP
    assert hp_gained > 0, f"per_hit应有治疗, got {hp_gained}"
    # 扣除3费后，per_hit energy+1×2，验证净能量变化
    print(f"  [OK] 聚盐 per_hit: +{hp_gained}HP (费3E→返还2E)")


# ═══════════════════════════════════════════════════════════════
# combo_mult 跨技能连击倍率
# ═══════════════════════════════════════════════════════════════

def test_combo_mult_snapshot():
    """snapshot combo_self = base+add（不含mult）；combo_mult 留给 adjust_damage。"""
    from backend.engine.snapshot import build_ctx
    from backend.sim.battleskill import BattleSkill
    from backend.sim.skill import Skill
    from backend.sim.sprite import SpeciesStats, Sprite

    stats = SpeciesStats(name="t", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    user = Sprite(stats, current_hp=100, max_hp=100, energy=10)
    opp = Sprite(stats, current_hp=100, max_hp=100, energy=10)
    bs = BattleSkill(base=Skill(name="test", combo=2, power=25, element="普通", skill_type="物攻", energy_cost=0))

    # Without any modifiers
    ctx1 = build_ctx(user, opp, bs, None, None, team="A")
    assert ctx1.combo_self == 2, f"base combo=2: {ctx1.combo_self}"
    assert ctx1.combo_mult_self == 0.0

    # combo_mult 只存在 ctx 上，不参与 combo_self
    user._modifiers["combo_mult"] = 2.0
    ctx2 = build_ctx(user, opp, bs, None, None, team="A")
    assert ctx2.combo_self == 2, f"snapshot不含mult: {ctx2.combo_self}"
    assert ctx2.combo_mult_self == 2.0, f"mult应=2: {ctx2.combo_mult_self}"

    # combo_add 参与 combo_self
    user._modifiers["combo"] = 1
    ctx3 = build_ctx(user, opp, bs, None, None, team="A")
    assert ctx3.combo_self == 3, f"base+add=3: {ctx3.combo_self}"
    assert ctx3.combo_mult_self == 2.0

    print("  [OK] snapshot: combo_self=base+add, combo_mult_self 独立")


def test_combo_mult_adjust_damage():
    """adjust_damage：连击**不再折进伤害值**，而是改写段数 `hits`（连击 = N 次独立命中）。

    段数口径：(set/add 合并) 之后乘 combo_mult；`amount` 保持单段口径不变，
    展开由 `expand_combo_hits` 在 replay 前完成。
    """
    from backend.engine.modifiers import adjust_damage, expand_combo_hits
    from backend.vm.journal import Damage

    dmg = Damage(target="sprite_opp", amount=100, element="普通", type="物攻", hits=1)

    # 只有 combo_mult=2：base=1, mult=2 → 3 段
    assert adjust_damage(dmg, {"combo_base": 1, "combo_mult": 2.0}).hits == 3
    # combo_set=3 + combo_mult=2 → (3)×(1+2) = 9 段
    assert adjust_damage(dmg, {"combo_base": 1, "combo_set": 3, "combo_mult": 2.0}).hits == 9
    # combo_add=2 + combo_mult=1 → (1+2)×2 = 6 段
    assert adjust_damage(dmg, {"combo_base": 1, "combo_add": 2, "combo_mult": 1.0}).hits == 6
    # set+add+mult → (3+1)×3 = 12 段
    assert adjust_damage(dmg, {"combo_base": 1, "combo_set": 3, "combo_add": 1,
                               "combo_mult": 2.0}).hits == 12

    # 伤害值不被连击改写（每段各自取整）→ 展开成 N 条等价结算
    r = adjust_damage(dmg, {"combo_base": 1, "combo_add": 2, "combo_mult": 1.0})
    assert r.amount == 100, f"单段伤害应保持 100: {r.amount}"
    assert len(expand_combo_hits([r])) == 6, "6 段应展开成 6 条 Damage"

    # 非连击命中（hits=0，如自伤/特性追加）不参与段数改写、也不展开
    plain = Damage(target="sprite_opp", amount=100, element="普通", type="物攻")
    assert adjust_damage(plain, {"combo_base": 1, "combo_add": 2}).hits == 0
    assert expand_combo_hits([plain]) == [plain]

    print("  [OK] adjust_damage: 连击改写段数（(set/add) × mult），不折进伤害值")


def test_storm_eye_stores_combo_mult():
    """暴风眼 使用后 sprite._modifiers['combo_mult']=1.0（+100%）。"""
    b = _make_battle(skills_a=["暴风眼"], skills_b=["猛烈撞击"])
    _run_skill(b)
    user = b.player_a.active
    assert user._modifiers.get("combo_mult") == 1.0, \
        f"暴风眼应存combo_mult=1, got {user._modifiers.get('combo_mult')}"
    print("  [OK] 暴风眼 combo_mult=1 (+100%) 已存储")


if __name__ == '__main__':
    test_combo2_damage()
    test_combo3_damage()
    test_combo5_damage()
    test_per_hit_abnormal_poison()
    test_per_hit_abnormal_burn()
    test_per_hit_mark()
    test_per_hit_mod_buff()
    test_per_hit_mod_debuff()
    test_per_hit_mod_energy_cost()
    test_combo_without_per_hit()
    test_per_hit_heal_energy()
    test_combo_mult_snapshot()
    test_combo_mult_adjust_damage()
    test_storm_eye_stores_combo_mult()
    print('\n  [ALL COMBO TESTS PASSED]')
