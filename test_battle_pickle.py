"""测试 Battle 对象的 Pickle 序列化

验证完整的 Battle 对象可以被序列化和反序列化。
"""

import pickle
import sys
from pathlib import Path

print("="*80)
print("Battle Pickle 兼容性测试")
print("="*80)
print()

# 创建一个完整的 Battle 对象
try:
    from backend.sim.battle import Battle
    from backend.sim.player import Player
    from backend.sim.sprite import Sprite
    from backend.common.models import SpeciesStats
    from backend.sim.skill import Skill
    from backend.sim.battleskill import BattleSkill

    print("✅ 导入成功")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

print()

# 创建测试数据
print("【步骤 1: 创建测试数据】")
print()

species = SpeciesStats(
    name="测试精灵",
    hp=200,
    atk=120,
    def_=80,
    sp_atk=100,
    sp_def=90,
    speed=110
)

# 创建技能
skill_data = {
    "name": "测试技能",
    "element": "火",
    "skill_type": "物攻",
    "power": 75,
    "energy_cost": 20,
    "combo": 1,
}
skill = Skill.load(skill_data)

# 创建精灵（带技能）
def create_sprite():
    sprite = Sprite(
        species=species,
        current_hp=170,
        max_hp=200,
        energy=50,
        initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110}
    )
    # 添加技能
    sprite.skills = [BattleSkill(base=skill) for _ in range(4)]
    return sprite

team_a = [create_sprite() for _ in range(3)]
team_b = [create_sprite() for _ in range(3)]

print(f"  队伍 A: {len(team_a)} 个精灵")
print(f"  队伍 B: {len(team_b)} 个精灵")
print(f"  每个精灵: {len(team_a[0].skills)} 个技能")

# 创建玩家
player_a = Player(team=team_a, name="玩家A")
player_b = Player(team=team_b, name="玩家B")

print(f"  玩家 A: {player_a.name}, 当前精灵 HP={player_a.active.current_hp}")
print(f"  玩家 B: {player_b.name}, 当前精灵 HP={player_b.active.current_hp}")

# 创建 Battle
battle = Battle(player_a, player_b)

print(f"  Battle: turn={battle.turn}, finished={battle.is_finished}")
print()

# 测试 pickle
print("【步骤 2: 测试 Pickle 序列化】")
print()

try:
    # 序列化
    print("  序列化中...")
    pickled = pickle.dumps(battle)
    size_kb = len(pickled) / 1024
    print(f"  ✅ 序列化成功，大小: {size_kb:.2f} KB")

    # 反序列化
    print("  反序列化中...")
    restored = pickle.loads(pickled)
    print(f"  ✅ 反序列化成功")

    # 验证状态
    print()
    print("【步骤 3: 验证恢复的状态】")
    print()

    checks = [
        ("turn", battle.turn, restored.turn),
        ("is_finished", battle.is_finished, restored.is_finished),
        ("player_a.name", battle.player_a.name, restored.player_a.name),
        ("player_b.name", battle.player_b.name, restored.player_b.name),
        ("player_a.active.current_hp", battle.player_a.active.current_hp, restored.player_a.active.current_hp),
        ("player_b.active.current_hp", battle.player_b.active.current_hp, restored.player_b.active.current_hp),
        ("player_a.active.energy", battle.player_a.active.energy, restored.player_a.active.energy),
        ("len(player_a.team)", len(battle.player_a.team), len(restored.player_a.team)),
        ("len(player_a.active.skills)", len(battle.player_a.active.skills), len(restored.player_a.active.skills)),
    ]

    all_pass = True
    for name, original, restored_val in checks:
        match = original == restored_val
        status = "✅" if match else "❌"
        print(f"  {status} {name}: {original} == {restored_val}")
        if not match:
            all_pass = False

    print()
    if all_pass:
        print("🎉 所有检查通过！Battle pickle 兼容性良好。")
    else:
        print("⚠️  部分检查失败，需要修复。")

except Exception as e:
    print(f"❌ Pickle 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("【步骤 4: 测试修改后的状态】")
print()

# 修改状态并测试
try:
    # 修改原始 battle
    battle.turn = 5
    battle.player_a.active.current_hp = 100
    battle.player_a.active.energy = 30

    print(f"  修改后 - turn: {battle.turn}, HP: {battle.player_a.active.current_hp}, energy: {battle.player_a.active.energy}")

    # 再次序列化
    pickled2 = pickle.dumps(battle)
    restored2 = pickle.loads(pickled2)

    # 验证
    checks2 = [
        ("turn", 5, restored2.turn),
        ("HP", 100, restored2.player_a.active.current_hp),
        ("energy", 30, restored2.player_a.active.energy),
    ]

    all_pass2 = True
    for name, expected, actual in checks2:
        match = expected == actual
        status = "✅" if match else "❌"
        print(f"  {status} {name}: {expected} == {actual}")
        if not match:
            all_pass2 = False

    if all_pass2:
        print("  ✅ 修改后的状态也能正确序列化")
    else:
        print("  ❌ 修改后的状态序列化失败")

except Exception as e:
    print(f"❌ 修改状态测试失败: {e}")
    import traceback
    traceback.print_exc()

print()
print("="*80)
print("【总结】")
print("="*80)
print()

if all_pass and all_pass2:
    print("✅ Battle 对象完全支持 Pickle 序列化")
    print("✅ 可以用于多进程并行化")
    print()
    print("下一步：实现完整的并行 MCTS 测试")
else:
    print("⚠️  Battle pickle 存在问题，需要修复")
    print()
    print("可能的解决方案：")
    print("  1. 使用 save_mutable_state() 代替完整 pickle")
    print("  2. 修复不可序列化的对象")
    print("  3. 使用自定义 __getstate__/__setstate__")

print()
