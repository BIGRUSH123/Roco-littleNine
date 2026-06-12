"""测试 build_ctx_cy 正确性和性能

验证 Cython 版本与 Python 版本输出一致，并测量加速比。
"""

import time
from backend.engine.snapshot import build_ctx
from backend.engine.snapshot_cy import build_ctx_cy
from backend.sim.sprite import Sprite
from backend.sim.skill import Skill
from backend.sim.battleskill import BattleSkill
from backend.sim.globals import GlobalEffects
from backend.common.models import SpeciesStats

print("="*80)
print("build_ctx_cy 正确性和性能测试")
print("="*80)
print()

# 创建测试 Sprite
species_a = SpeciesStats(
    name="测试精灵A",
    hp=200,
    atk=120,
    def_=80,
    sp_atk=100,
    sp_def=90,
    speed=110
)

species_b = SpeciesStats(
    name="测试精灵B",
    hp=220,
    atk=100,
    def_=90,
    sp_atk=110,
    sp_def=100,
    speed=95
)

sprite_a = Sprite(
    species=species_a,
    current_hp=170,
    max_hp=200,
    energy=50,
    initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110}
)

sprite_b = Sprite(
    species=species_b,
    current_hp=180,
    max_hp=220,
    energy=40,
    initial_stats={"atk": 100, "def": 90, "sp_atk": 110, "sp_def": 100, "speed": 95}
)

# 创建测试技能
skill_data_a = {
    "name": "火焰拳",
    "element": "火",
    "skill_type": "物攻",
    "power": 75,
    "energy_cost": 20,
    "combo": 1,
}

skill_data_b = {
    "name": "水炮",
    "element": "水",
    "skill_type": "特攻",
    "power": 80,
    "energy_cost": 25,
    "combo": 1,
}

skill_a = Skill.load(skill_data_a)
skill_b = Skill.load(skill_data_b)

# 包装成 BattleSkill
bs_a = BattleSkill(base=skill_a)
bs_b = BattleSkill(base=skill_b)

# GlobalEffects
globals_ = GlobalEffects()

print("【测试 1: 正确性验证】")
print()

# Python 版本
ctx_py = build_ctx(
    self_sprite=sprite_a,
    opp_sprite=sprite_b,
    self_skill=bs_a,
    opp_skill=bs_b,
    globals_=globals_,
    battle_skill=bs_a,
    team="A",
    turn=5,
    is_first=True,
)

# Cython 版本
ctx_cy = build_ctx_cy(
    self_sprite=sprite_a,
    opp_sprite=sprite_b,
    self_skill=bs_a,
    opp_skill=bs_b,
    globals_=globals_,
    battle_skill=bs_a,
    team="A",
    turn=5,
    is_first=True,
)

# 比较关键字段
print("比较关键字段：")
key_fields = [
    "hp_self", "hp_self_ratio", "energy_self", "atk_self", "def_self",
    "speed_self", "power_self", "combo_self", "element_advantage",
    "hp_opp", "energy_opp", "atk_opp", "speed_opp",
    "turn", "is_first",
]

all_match = True
for field in key_fields:
    py_val = getattr(ctx_py, field)
    cy_val = getattr(ctx_cy, field)
    match = py_val == cy_val
    if not match:
        all_match = False
        print(f"  ❌ {field}: Python={py_val}, Cython={cy_val}")
    else:
        print(f"  ✅ {field}: {py_val}")

print()
if all_match:
    print("✅ 所有字段一致！")
else:
    print("❌ 存在不一致字段！")

print()
print("="*80)
print("【测试 2: 性能测试】")
print("="*80)
print()

# Python 版本性能
iterations = 10000
start = time.perf_counter()
for _ in range(iterations):
    ctx = build_ctx(
        self_sprite=sprite_a,
        opp_sprite=sprite_b,
        self_skill=bs_a,
        opp_skill=bs_b,
        globals_=globals_,
        battle_skill=bs_a,
        team="A",
        turn=5,
        is_first=True,
    )
elapsed_py = time.perf_counter() - start

print(f"Python 版本 ({iterations} 次): {elapsed_py:.3f}s ({elapsed_py/iterations*1e6:.2f}μs/call)")

# Cython 版本性能
start = time.perf_counter()
for _ in range(iterations):
    ctx = build_ctx_cy(
        self_sprite=sprite_a,
        opp_sprite=sprite_b,
        self_skill=bs_a,
        opp_skill=bs_b,
        globals_=globals_,
        battle_skill=bs_a,
        team="A",
        turn=5,
        is_first=True,
    )
elapsed_cy = time.perf_counter() - start

print(f"Cython 版本 ({iterations} 次): {elapsed_cy:.3f}s ({elapsed_cy/iterations*1e6:.2f}μs/call)")
print()
print(f"🚀 加速比: {elapsed_py/elapsed_cy:.2f}x")
print()

# 评估
speedup = elapsed_py / elapsed_cy
if speedup >= 3.0:
    print("🎉 优秀！加速比 ≥3x，强烈推荐全面部署！")
elif speedup >= 2.0:
    print("✅ 良好！加速比 ≥2x，值得部署。")
elif speedup >= 1.5:
    print("⚠️  一般。加速比 ≥1.5x，有一定效果。")
else:
    print("❌ 效果不明显。加速比 <1.5x，需要进一步优化。")

print()
print("="*80)
print("测试完成！")
print("="*80)
