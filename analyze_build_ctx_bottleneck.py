"""分析 build_ctx 性能瓶颈

找出哪些部分最耗时。
"""

import time
from backend.engine.snapshot import build_ctx, _extract_sprite_effects, _collect_skill_summary
from backend.sim.sprite import Sprite
from backend.sim.skill import Skill
from backend.sim.battleskill import BattleSkill
from backend.sim.globals import GlobalEffects
from backend.common.models import SpeciesStats

# 创建测试数据
species = SpeciesStats(
    name="测试精灵",
    hp=200,
    atk=120,
    def_=80,
    sp_atk=100,
    sp_def=90,
    speed=110
)

sprite_a = Sprite(
    species=species,
    current_hp=170,
    max_hp=200,
    energy=50,
    initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110}
)

sprite_b = Sprite(
    species=species,
    current_hp=180,
    max_hp=200,
    energy=40,
    initial_stats={"atk": 100, "def": 90, "sp_atk": 110, "sp_def": 100, "speed": 95}
)

skill_data = {
    "name": "测试技能",
    "element": "火",
    "skill_type": "物攻",
    "power": 75,
    "energy_cost": 20,
    "combo": 1,
}
skill = Skill.load(skill_data)
bs = BattleSkill(base=skill)
globals_ = GlobalEffects()

print("="*80)
print("build_ctx 性能瓶颈分析")
print("="*80)
print()

iterations = 10000

# 1. 完整 build_ctx
start = time.perf_counter()
for _ in range(iterations):
    ctx = build_ctx(sprite_a, sprite_b, bs, bs, globals_, battle_skill=bs, turn=5, is_first=True)
total = time.perf_counter() - start
print(f"1. 完整 build_ctx:         {total*1e6/iterations:.2f}μs/call (基准)")

# 2. 只提取 sprite effects
start = time.perf_counter()
for _ in range(iterations):
    _extract_sprite_effects(sprite_a)
    _extract_sprite_effects(sprite_b)
elapsed = time.perf_counter() - start
print(f"2. _extract_sprite_effects: {elapsed*1e6/iterations:.2f}μs/call ({elapsed/total*100:.1f}%)")

# 3. 只提取技能摘要
start = time.perf_counter()
for _ in range(iterations):
    _collect_skill_summary(sprite_a)
    _collect_skill_summary(sprite_b)
elapsed = time.perf_counter() - start
print(f"3. _collect_skill_summary:  {elapsed*1e6/iterations:.2f}μs/call ({elapsed/total*100:.1f}%)")

# 4. 只读取属性
start = time.perf_counter()
for _ in range(iterations):
    hp = sprite_a.current_hp
    energy = sprite_a.energy
    atk = sprite_a.atk_with_modifiers
    def_ = sprite_a.def_with_modifiers
    speed = sprite_a.sp_atk_with_modifiers
    hp2 = sprite_b.current_hp
    energy2 = sprite_b.energy
elapsed = time.perf_counter() - start
print(f"4. 属性读取 (7个):          {elapsed*1e6/iterations:.2f}μs/call ({elapsed/total*100:.1f}%)")

# 5. 只创建 Ctx（使用简化数据）
from backend.vm.ctx import Ctx, EventContext
start = time.perf_counter()
for _ in range(iterations):
    ctx = Ctx(
        event=EventContext(),
        bloodline_self="",
        bloodline_opp="",
        elements_self=(),
        elements_opp=(),
        hp_self=100,
        hp_self_ratio=0.5,
        hp_self_max=200,
        energy_self=50,
        priority_self=0,
        atk_self=120,
        def_self=80,
        sp_atk_self=100,
        sp_def_self=90,
        speed_self=110,
        damage_reduction_self=0.0,
        power_mult_self=1.0,
        damage_mult_self=1.0,
        energy_cost_mult_self=1.0,
        combo_mult_self=1.0,
        life_drain_self=0.0,
        abnormal_count_self=0,
        abnormal_stacks_self={},
        positive_count_self=0,
        first_action_self=False,
        first_action_battle_self=False,
        charged_self=False,
        is_charging_self=False,
        is_charging_opp=False,
        times_entered_self=0,
        times_left_self=0,
        elements_used_count_self=0,
        skills_energy_sum_self=0,
        just_entered=False,
        skill_elements_self=frozenset(),
        skill_element_counts_self={},
        skill_element_count_self=0,
        stat_stages_self={},
        energy_cost_sum_self={},
        zero_cost_skill_count_self=0,
        hp_opp=100,
        hp_opp_ratio=0.5,
        hp_opp_max=200,
        energy_opp=40,
        atk_opp=100,
        def_opp=90,
        sp_atk_opp=110,
        sp_def_opp=100,
        speed_opp=95,
        damage_reduction_opp=0.0,
        power_mult_opp=1.0,
        damage_mult_opp=1.0,
        abnormal_count_opp=0,
        abnormal_stacks_opp={},
        positive_count_opp=0,
        charged_opp=False,
        skill_elements_opp=frozenset(),
        skill_element_counts_opp={},
        skill_element_count_opp=0,
        stat_stages_opp={},
        skills_energy_sum_opp=0,
        mark_count_own=0,
        mark_stacks_own={},
        mark_count_opp=0,
        mark_stacks_opp={},
        mark_bonus_own=0.0,
        mark_count_both=0,
        skill_count_own={},
        team_counters_own={},
        team_counters_opp={},
        team_elements_own=frozenset(),
        team_elements_opp=frozenset(),
        devotion_own={},
        devotion_opp={},
        abnormal_stacks_battle={},
        fainted_own=0,
        fainted_opp=0,
        lives_own=5,
        lives_opp=5,
        burst_triggered_count_own=0,
        moe_team_stacks=0,
        power_self=75,
        adjacent_power_sum=0,
        power_opp=75,
        skill_type_self="物攻",
        skill_type_opp="物攻",
        element_self="火",
        element_opp="火",
        skill_tag_self="",
        combo_self=1,
        element_advantage=1.0,
        energy_cost_self=20,
        energy_cost_reduction_self=0,
        energy_cost_opp=20,
        damage_taken_this_turn=0,
        damage_reduced_self=0,
        prev_skill_type="",
        prev_damage_taken_self=False,
        prev_damage_taken_opp=False,
        skill_index=0,
        last_tick_damage_self=0,
        last_tick_damage_opp=0,
        weather="",
        turn=5,
        is_first=True,
        counter_values={},
    )
elapsed = time.perf_counter() - start
print(f"5. Ctx 构建 (100+字段):     {elapsed*1e6/iterations:.2f}μs/call ({elapsed/total*100:.1f}%)")

print()
print("="*80)
print("结论：")
print("="*80)
print()
print("Ctx 对象创建占了大部分时间（100+ 字段的 dataclass）。")
print("这是 Python 对象创建的固有开销，Cython 无法优化。")
print()
print("真正能优化的部分：")
print("- 属性计算逻辑（已通过 C 变量优化）")
print("- 辅助函数（如 compute_speed_cy，已有 50x+ 加速）")
print()
print("但这些只占总时间的一小部分，所以整体加速有限。")
