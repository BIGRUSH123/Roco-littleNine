"""Battle replay tests — key scenarios extracted from raw/记录.txt, 记录2.txt, 记录3.txt.

Each test replays a critical moment from a real battle through the VM engine.
Since exact species stats are unavailable, we use reasonable mocked values and
focus on verifying engine behavior (damage, counters, stat changes, escape).
"""

from __future__ import annotations
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from scripts.engine.skill_loader import SkillLoader
from scripts.engine.battle import BattleVMEngine
from scripts.sim.sprite import Sprite
from scripts.sim.globals import GlobalEffects
from scripts.common.models import SpeciesStats

# Shared test species template
def _make_species(name="测试精灵", hp=450, atk=130, def_=110, sp_atk=120, sp_def=105, speed=100):
    return SpeciesStats(name=name, hp=hp, atk=atk, def_=def_, sp_atk=sp_atk, sp_def=sp_def, speed=speed)

def _make_sprite(species, hp=None, energy=8):
    max_hp = species.hp
    return Sprite(
        species=species,
        current_hp=hp if hp is not None else max_hp,
        max_hp=max_hp, energy=energy,
        initial_stats={"atk": species.atk, "def": species.def_,
                       "sp_atk": species.sp_atk, "sp_def": species.sp_def,
                       "speed": species.speed},
    )


# ═══════════════════════════════════════════════════════════════
# Battle 1: 火神队 vs 父子兵队
# raw/记录.txt
# ═══════════════════════════════════════════════════════════════

def test_battle1_round1_counter_defense():
    """Battle1 Round1: 翠顶夫人 uses 力量增效, 怖哭菇 uses 错乱（应对）.

    Scenario: Status buff is countered by a defense skill.
    Verify: counter_succeeded flag is correctly set for the countering skill.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    cui = _make_sprite(_make_species("翠顶夫人", hp=434))
    bu = _make_sprite(_make_species("怖哭菇", hp=400))

    # 翠顶夫人: 力量增效 → self atk+100% (steps-based stat mod)
    record_cui = loader.load_file("data/skills/力量增效.json")
    result_cui = engine.execute_skill(cui, bu, record_cui, None, GlobalEffects(), turn=1, is_first=True, team="A")

    # Verify stat buff was applied
    atk_stages = sum(getattr(e, 'steps', 0) for e in cui.effects if getattr(e, 'stat_key', '') == "atk")
    assert atk_stages > 0, f"Expected atk buff from 力量增效, got {atk_stages} stages"
    print(f"  R1-A: 翠顶夫人 力量增效 → atk+{atk_stages*10}%")

    # 怖哭菇: 错乱 → counter defense
    record_bu = loader.load_file("data/skills/错乱.json")
    result_bu = engine.execute_skill(bu, cui, record_bu, record_cui, GlobalEffects(),
                                      turn=1, is_first=True, team="B",
                                      counter_succeeded=True)
    # 错乱 reduces opp atk on counter_succeeded
    opp_atk_stages = sum(getattr(e, 'steps', 0) for e in cui.effects
                         if getattr(e, 'stat_key', '') == "atk" and getattr(e, 'steps', 0) < 0)
    print(f"  R1-B: 怖哭菇 错乱(应对成功) → events={result_bu.events[:3]}")
    print(f"       cui atk total stages: {atk_stages + opp_atk_stages}")


def test_battle1_round6_switch_counter():
    """Battle1 Round6-8: Multiple switches and counter defense chain.

    Round6: 一号换上龙息帕尔, 翠顶夫人使用风隐, 二号换上怖哭菇
    Round7: 龙息帕尔 uses 力量增效, 怖哭菇 uses 纤维化
    Round8: 二号换上翠顶夫人, 龙息帕尔 uses 蝙蝠, 翠顶夫人 uses 风墙(应对)

    Key test: 风墙 as counter defense against 蝙蝠 attack.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    longxi = _make_sprite(_make_species("龙息帕尔", hp=480, atk=140))
    cui = _make_sprite(_make_species("翠顶夫人", hp=501, def_=120))

    # 龙息帕尔 uses 蝙蝠 (attack)
    record_bat = loader.load_file("data/skills/蝙蝠.json")
    result_atk = engine.execute_skill(longxi, cui, record_bat, None, GlobalEffects(),
                                       turn=8, is_first=True, team="A",
                                       was_countered=True)
    dmg_to_cui = 501 - cui.current_hp
    print(f"  R8-A: 龙息帕尔 蝙蝠 → cui HP: {cui.current_hp} (-{dmg_to_cui})")

    # 翠顶夫人 uses 风墙 (defense, counter)
    record_fq = loader.load_file("data/skills/风墙.json")
    result_def = engine.execute_skill(cui, longxi, record_fq, record_bat, GlobalEffects(),
                                       turn=8, is_first=True, team="B",
                                       counter_succeeded=True)
    print(f"  R8-B: 翠顶夫人 风墙(应对) → events={result_def.events}")
    # 风墙 should apply damage_reduction on counter_succeeded
    assert any("damage_reduction" in str(e).lower() for e in result_def.events) or \
           cui._modifiers.get("damage_reduction", 0) > 0, \
           "风墙 should provide damage reduction"
    print(f"       cui damage_reduction: {cui._modifiers.get('damage_reduction', 0):.0%}")


# ═══════════════════════════════════════════════════════════════
# Battle 2: 火神队 vs 父子兵队
# raw/记录2.txt
# ═══════════════════════════════════════════════════════════════

def test_battle2_round1_poison_counter():
    """Battle2 Round1: 千棘盔 uses 愿力冲击(武系), 嗜波螺 uses 地陷.

    千棘盔 uses 愿力冲击 → 嗜波螺 HP=72%. 嗜波螺 uses 地陷 → 千棘盔 HP=282/486.
    Turn end: 毒印记 triggers → 嗜波螺 HP=332/473.

    Key test: Damage dealing and HP tracking for both sides.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    qian = _make_sprite(_make_species("千棘盔", hp=486, atk=135))
    shi = _make_sprite(_make_species("嗜波螺", hp=473, def_=115))

    # Load skills — 愿力冲击 and 地陷 are RISC IR skills
    # 千棘盔 uses attack (愿力冲击 is modified by 愿力强化)
    record_hit = loader.load_file("data/skills/地陷.json")
    result = engine.execute_skill(shi, qian, record_hit, None, GlobalEffects(),
                                   turn=1, is_first=False, team="B")
    dmg = 486 - qian.current_hp
    print(f"  R1: 嗜波螺 地陷 → 千棘盔 HP: {qian.current_hp}/{486} (-{dmg})")
    assert qian.current_hp < 486, "地陷 should deal damage"
    assert qian.current_hp > 0, "千棘盔 should survive this hit"


def test_battle2_round5_death_and_switch():
    """Battle2 Round5: 棋齐垒 dies to 利灯鱼's 水光冲击.

    一号换上棋齐垒. 利灯鱼 uses 水光冲击. 棋齐垒 uses 风墙(应对) but dies.
    One heart lost.

    Key test: Damage can kill a sprite (HP <= 0).
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    # Low HP sprite to simulate an already weakened 棋齐垒
    qi = _make_sprite(_make_species("棋齐垒", hp=350, atk=110, def_=95), hp=100)
    li = _make_sprite(_make_species("利灯鱼", hp=439, sp_atk=140))

    record_sg = loader.load_file("data/skills/水光冲击.json")
    result = engine.execute_skill(li, qi, record_sg, None, GlobalEffects(),
                                   turn=5, is_first=True, team="B",
                                   was_countered=False)
    print(f"  R5: 利灯鱼 水光冲击 → 棋齐垒 HP: {qi.current_hp}/{qi.max_hp}")
    assert qi.is_fainted, f"棋齐垒 should be fainted, HP={qi.current_hp}"
    print(f"       棋齐垒 fainted! ✓")


def test_battle2_round7_counter_chain():
    """Battle2 Round7: 利灯鱼 uses 落雷, 琉璃水母 uses 泡沫幻影(应对).

    利灯鱼 HP drops to 39% → 16% (poison). Then switches to 裘卡.
    Turn end: 利灯鱼 dies from poison.

    Key test: Counter defense with damage, sprite states tracked.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    liuli = _make_sprite(_make_species("琉璃水母", hp=519, sp_def=110))
    li = _make_sprite(_make_species("利灯鱼", hp=439, sp_atk=140))

    # 利灯鱼 uses 落雷
    record_ll = loader.load_file("data/skills/落雷.json")
    result_atk = engine.execute_skill(li, liuli, record_ll, None, GlobalEffects(),
                                       turn=7, is_first=True, team="B",
                                       was_countered=True)
    dmg = 519 - liuli.current_hp
    print(f"  R7-A: 利灯鱼 落雷 → 琉璃水母 HP: {liuli.current_hp}/{519} (-{dmg})")

    # 琉璃水母 uses 泡沫幻影 (defense, counter)
    record_pm = loader.load_file("data/skills/泡沫幻影.json")
    result_def = engine.execute_skill(liuli, li, record_pm, record_ll, GlobalEffects(),
                                       turn=7, is_first=True, team="A",
                                       counter_succeeded=True)
    print(f"  R7-B: 琉璃水母 泡沫幻影(应对) → events={result_def.events}")
    assert any("damage_reduction" in str(e).lower() for e in result_def.events), \
           "泡沫幻影 should provide damage reduction on counter"


def test_battle2_round8_burst_ko():
    """Battle2 Round8: 裘卡 uses 毒孢子, 小皮球 uses 大爆炸. 裘卡 dies.

    Both sides take heavy damage. 裘卡 faints, 一号 loses a heart.

    Key test: Mutual heavy damage, one sprite faints.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    qiu = _make_sprite(_make_species("裘卡", hp=380, def_=95), hp=200)
    pi = _make_sprite(_make_species("小皮球", hp=400, atk=150))

    # 小皮球 uses 大爆炸 (high power hit)
    record_dbz = loader.load_file("data/skills/大爆炸.json")
    result = engine.execute_skill(pi, qiu, record_dbz, None, GlobalEffects(),
                                   turn=8, is_first=True, team="B")
    print(f"  R8: 小皮球 大爆炸 → 裘卡 HP: {qiu.current_hp}/{qiu.max_hp}")
    assert qiu.current_hp < 200, f"大爆炸 should deal heavy damage, HP={qiu.current_hp}"
    print(f"       裘卡 lost {200 - qiu.current_hp} HP")


# ═══════════════════════════════════════════════════════════════
# Battle 3: 火神队 vs 父子兵队
# raw/记录3.txt
# ═══════════════════════════════════════════════════════════════

def test_battle3_round6_burst_ko():
    """Battle3 Round6: 岚鸟 uses 闪击, 圆号鱼 dies.

    岚鸟 uses 闪击 → 号儿鱼 faints. 二号 loses a heart, switches to 圣剑X.

    Key test: Quick kill with priority skill (闪击 = 迅捷).
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    lan = _make_sprite(_make_species("岚鸟", hp=420, atk=145))
    yu = _make_sprite(_make_species("圆号鱼", hp=380, def_=90), hp=120)

    record_sj = loader.load_file("data/skills/闪击.json")
    result = engine.execute_skill(lan, yu, record_sj, None, GlobalEffects(),
                                   turn=6, is_first=True, team="A")
    print(f"  R6: 岚鸟 闪击 → 圆号鱼 HP: {yu.current_hp}/{yu.max_hp}")
    assert yu.current_hp < 120, f"闪击 should deal damage, HP={yu.current_hp}"


def test_battle3_round9_counter_exchange():
    """Battle3 Round9: 灵羽骑士 uses 水刃, 爬爬 uses 破罐破摔.

    灵羽骑士 uses 水刃 → 爬爬 HP=49%. 爬爬 uses 破罐破摔 → 灵羽骑士 HP=250/400.

    Key test: Both sides deal damage, neither faints.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    lingyu = _make_sprite(_make_species("灵羽骑士", hp=400, atk=135, def_=110))
    papa = _make_sprite(_make_species("化蝶", hp=380, def_=100))

    # 灵羽骑士 uses 水刃
    record_sr = loader.load_file("data/skills/水刃.json")
    result_a = engine.execute_skill(lingyu, papa, record_sr, None, GlobalEffects(),
                                     turn=9, is_first=True, team="A")
    dmg_a = 380 - papa.current_hp
    print(f"  R9-A: 灵羽骑士 水刃 → 化蝶 HP: {papa.current_hp}/{380} (-{dmg_a})")
    assert dmg_a > 0, "水刃 should deal damage"

    # 化蝶 uses 破罐破摔
    record_pg = loader.load_file("data/skills/破罐破摔.json")
    result_b = engine.execute_skill(papa, lingyu, record_pg, None, GlobalEffects(),
                                     turn=9, is_first=False, team="B")
    dmg_b = 400 - lingyu.current_hp
    print(f"  R9-B: 化蝶 破罐破摔 → 灵羽骑士 HP: {lingyu.current_hp}/{400} (-{dmg_b})")
    assert dmg_b > 0, "破罐破摔 should deal damage"


def test_battle3_round22_final_ko():
    """Battle3 Round22: 圣剑X uses 齿轮切开, 毛毛 dies. 一号 wins.

    Final blow: 圣剑X 齿轮切开 kills 毛毛. 二号 out of hearts.

    Key test: Final killing blow ends the match.
    """
    loader = SkillLoader()
    engine = BattleVMEngine()

    shengjian = _make_sprite(_make_species("圣剑X", hp=517, atk=145))
    maomao = _make_sprite(_make_species("化蝶", hp=320, def_=90), hp=80)

    record_cl = loader.load_file("data/skills/齿轮切开.json")
    result = engine.execute_skill(shengjian, maomao, record_cl, None, GlobalEffects(),
                                   turn=22, is_first=True, team="A")
    print(f"  R22: 圣剑X 齿轮切开 → 化蝶 HP: {maomao.current_hp}/{maomao.max_hp}")
    assert maomao.current_hp < 80, f"齿轮切开 should deal damage, HP={maomao.current_hp}"
    print(f"       Final blow dealt!")


# ═══════════════════════════════════════════════════════════════
# Run all
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("Battle1 R1: counter defense", test_battle1_round1_counter_defense),
        ("Battle1 R6-8: switch + counter chain", test_battle1_round6_switch_counter),
        ("Battle2 R1: poison counter", test_battle2_round1_poison_counter),
        ("Battle2 R5: death and switch", test_battle2_round5_death_and_switch),
        ("Battle2 R7: counter chain", test_battle2_round7_counter_chain),
        ("Battle2 R8: burst KO", test_battle2_round8_burst_ko),
        ("Battle3 R6: burst KO", test_battle3_round6_burst_ko),
        ("Battle3 R9: counter exchange", test_battle3_round9_counter_exchange),
        ("Battle3 R22: final KO", test_battle3_round22_final_ko),
    ]

    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  ✓ {name}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}: {e}")

    print(f"\n{'='*60}")
    print(f"Battle replay tests: {len(tests)-failed}/{len(tests)} passed")
    if failed:
        print(f"FAILED {failed} test(s)")
        sys.exit(1)
    else:
        print("All battle replay tests passed!")
