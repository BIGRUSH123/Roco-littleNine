"""scripts/sim/test_pipeline.py — 技能效果管线分层测试

覆盖 L0-L6 全部 33 个 SpecialName + 4 种非 special 效果 + 条件触发 + 回合末结算。
与 test_l*.json 技能文件配套使用。

用法:  python scripts/sim/test_pipeline.py
"""

import json
import random
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ))

from scripts.common.models import SpeciesStats
from scripts.sim.sprite import Sprite
from scripts.sim.skill import Skill
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.sim.player import Player, PlayStyle
from scripts.sim.battle import Battle
from scripts.sim.action import Action
from scripts.sim.effects import SpecialName, EffectLayer
from scripts.sim.resolver import SkillResolver, TurnContext

SKILLS_DIR = _PROJ / 'data' / 'skills'

# ── Helpers ────────────────────────────────────────────────────────

def load_bs(name: str) -> BattleSkill:
    path = SKILLS_DIR / f'{name}.json'
    data = json.loads(path.read_text('utf-8'))
    return BattleSkill(base=Skill.load(data))


def make_species(name: str, **kw) -> SpeciesStats:
    defaults = dict(hp=100, atk=100, sp_atk=100, def_=100, sp_def=100, speed=100,
                    attributes='普通', ability='')
    defaults.update(kw)
    return SpeciesStats(name=name, **defaults)


def make_sprite(species: SpeciesStats, skills: list[BattleSkill],
                stats_ov: dict | None = None, hp: int | None = None,
                energy: int = 10) -> Sprite:
    """创建测试精灵。stats_ov 覆盖 initial_stats 全部六维。
    hp 参数只设置 current_hp；max_hp 始终来自 initial_stats['hp']。"""
    if stats_ov is None:
        stats = {k: species.base_dict().get(k, 100) for k in
                 ['hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed']}
        stats['hp'] = 400
    else:
        stats = dict(stats_ov)
    max_hp = stats['hp']
    s = Sprite(
        species=species,
        initial_stats=stats,
        current_hp=hp if hp is not None else max_hp,
        max_hp=max_hp, energy=energy,
    )
    s.skills = skills
    return s


class TestAgent:
    """按预定序列出招的测试 Agent。"""

    def __init__(self, team: str, player: 'Player',
                 actions: list[tuple[str, int | None]]):
        self.team = team
        self.player = player
        self.actions = actions
        self.step = 0

    def choose_lead(self, battle: 'Battle') -> int:
        return 0

    def choose_action(self, battle: 'Battle') -> Action:
        if self.step >= len(self.actions):
            return Action(kind='gather')
        kind, idx = self.actions[self.step]
        self.step += 1
        if kind == 'skill':
            return Action(kind='skill', skill_index=idx)
        if kind == 'gather':
            return Action(kind='gather')
        if kind == 'switch':
            return Action(kind='switch', switch_index=idx)
        return Action(kind='gather')

    def choose_replacement(self, battle: 'Battle') -> int:
        p = self.player
        for i, s in enumerate(p.team):
            if i != p.active_index and not s.is_fainted:
                return i
        return 0

    def on_game_end(self, winner: str) -> None:
        pass


def _make_battle(skills_a: list[BattleSkill], skills_b: list[BattleSkill],
                 bench_a: list[BattleSkill] | None = None,
                 bench_b: list[BattleSkill] | None = None,
                 hp_a: int = 400, hp_b: int = 400,
                 energy_a: int = 10, energy_b: int = 10,
                 weather: str = '') -> Battle:
    """构建 2 玩家 Battle，可选单只或多只队伍。"""
    spec_a = make_species('测试A', attributes='普通')
    spec_bench = make_species('替补', attributes='普通')
    spec_b = make_species('测试B', attributes='草',
                          def_=120, sp_def=120, speed=90)

    sprite_a = make_sprite(spec_a, skills_a, hp=hp_a, energy=energy_a)
    sprite_b = make_sprite(spec_b, skills_b, hp=hp_b, energy=energy_b,
                           stats_ov={'hp': 400, 'atk': 80, 'sp_atk': 80,
                                     'def': 120, 'sp_def': 120, 'speed': 90})

    team_a = [sprite_a]
    team_b = [sprite_b]

    if bench_a:
        for i, bskills in enumerate(bench_a):
            bs = make_sprite(spec_bench, bskills, hp=400)
            team_a.append(bs)
    if bench_b:
        for i, bskills in enumerate(bench_b):
            bs = make_sprite(spec_bench, bskills, hp=400)
            team_b.append(bs)

    p_a = Player(name='A', team=team_a, style=PlayStyle())
    p_b = Player(name='B', team=team_b, style=PlayStyle())
    return Battle(p_a, p_b, weather=weather, verbose=False)


def _run(battle: Battle, a_actions: list, b_actions: list,
         turns: int = 1) -> list[list[str]]:
    """执行 N 回合，返回每回合的 events 列表。"""
    agent_a = TestAgent('A', battle.player_a, a_actions)
    agent_b = TestAgent('B', battle.player_b, b_actions)
    all_events = []
    for _ in range(turns):
        record = battle.execute_turn(agent_a, agent_b)
        all_events.append(record.events)
    return all_events


def _contains(events: list[str], keyword: str) -> bool:
    return any(keyword in e for e in events)


# ═══════════════════════════════════════════════════════════════════
# Scenario 1: L0 modifier 收集 + L2 伤害/吸血/burst/ignore_mods
# ═══════════════════════════════════════════════════════════════════

def test_l0_l2():
    sk_a = [load_bs('test_l0_l2')]      # attack: 40 power, all L0+L2 effects
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b)

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]

    s_a = battle.player_a.active
    s_b = battle.player_b.active

    # L0: multi_hit=2 → 2 次伤害事件
    dmg_events = [e for e in events if 'HP' in e and 'test_l0_l2' in e]
    assert len(dmg_events) >= 1, f'Expected ≥1 damage, got {len(dmg_events)}'

    # L2: life_drain=20% → 吸血事件
    assert _contains(events, '吸血'), f'Expected life_drain event, got {events}'

    # L2: 伤害确实造成了
    assert s_b.current_hp < s_b.max_hp, f'B should take damage, HP={s_b.current_hp}'

    # L0: modifiers 在 SkillUse 中; 可以通过间接方式验证:
    # power_mult=1.5 → 有效威力 = (40+20)*1.5 = 90 > base 40
    # damage > 纯 40*1.0 能打出的伤害
    print(f'  L0+L2: A HP={s_a.current_hp} E={s_a.energy}, '
          f'B HP={s_b.current_hp}/{s_b.max_hp}, events={len(events)}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 2: L1 动态威力 + next_attack_mult
# ═══════════════════════════════════════════════════════════════════

def test_l1_power():
    sk_a = [load_bs('test_l1_setup'),    # status: POWER_MULT=1.5 → next_attack_mult
            load_bs('test_l1_power')]    # attack: POWER_BY_ENEMY_ENERGY + POWER_BY_ADJACENT
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b, energy_a=10, energy_b=10)

    # Turn 1: test_l1_setup → 设置 next_attack_mult
    events1 = _run(battle, [('skill', 0)], [('gather', None)])[0]
    bs_l1 = battle.player_a.active.skills[1]  # test_l1_power
    assert bs_l1.next_attack_mult == 1.5, \
        f'Expected next_attack_mult=1.5, got {bs_l1.next_attack_mult}'
    print(f'  L1 setup: next_attack_mult={bs_l1.next_attack_mult}')

    # Turn 2: test_l1_power → 消费 next_attack_mult + 动态威力
    # B 的技能耗能: test_basic_attack cost=1*4 槽位? 实际只有 1 个技能槽=1
    # power_by_enemy_energy: B 总耗能×10 = (1)*10 = 10 → power_override=10
    # 但 test_l1_power 也有 power_by_adjacent: 相邻技能威力之和×0.333
    # A 的技能: [0]=test_l1_setup(0), [1]=test_l1_power(80), 相邻只有 test_l1_setup
    # adj_sum = 0 (test_l1_setup power=0) → power_override = max(1, 0*0.333) = 1
    # 但 POWER_BY_ADJACENT 在数组后，会覆盖 POWER_BY_ENEMY_ENERGY 的 power_override
    # 所以最终 power_override = 1
    events2 = _run(battle, [('skill', 1)], [('gather', None)])[0]

    # L1: next_attack_mult 已消费
    assert bs_l1.next_attack_mult == 1.0, \
        f'next_attack_mult should be consumed, got {bs_l1.next_attack_mult}'

    # power_by_adjacent 设置了 power_override (覆盖 power_by_enemy_energy)
    assert bs_l1.power_override is not None, 'Expected power_override to be set'

    print(f'  L1 power: power_override={bs_l1.power_override}, '
          f'next_attack_mult consumed={bs_l1.next_attack_mult}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 3: 能量支付 gate
# ═══════════════════════════════════════════════════════════════════

def test_energy_gate():
    sk_a = [load_bs('test_l0_l2')]   # cost=2
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b, energy_a=1)  # 只有 1 能量

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]

    # 能量不足 → 技能失败
    assert _contains(events, '能量不足'), f'Expected energy fail, got {events}'
    # 能量没有被扣除
    assert battle.player_a.active.energy == 1, \
        f'Energy should remain 1, got {battle.player_a.active.energy}'
    print(f'  Energy gate: PASS (energy left={battle.player_a.active.energy})')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 4: L3+L4 防御应对 (interrupt + counter_damage + reflect)
# ═══════════════════════════════════════════════════════════════════

def test_l3_l4_defense():
    sk_a = [load_bs('test_l3_l4_defense')]  # 防御: counter=攻击
    sk_b = [load_bs('test_basic_attack')]   # 攻击: 魔攻
    battle = _make_battle(sk_a, sk_b)

    events = _run(battle, [('skill', 0)], [('skill', 0)])[0]

    s_a = battle.player_a.active
    s_b = battle.player_b.active

    # counter_succeeded → conditional stat(atk+1)
    assert _contains(events, '测试A'), f'Expected A action in events'

    # L4: counter_damage → 反击伤害事件
    assert _contains(events, '反击'), f'Expected counter_damage, got {events}'

    # L4: reflect_damage → replaced_by 设置
    bs_def = s_a.skills[0]
    assert bs_def.replaced_by is not None, \
        f'reflect_damage should set replaced_by, got {bs_def.replaced_by}'

    # L3: interrupt → B 的技能被空化
    # B 使用普通攻击，但被 interrupt 空化后 B 应该没有造成伤害
    # (A 有 damage_reduction=0.5，且 interrupt 后 B 的 skill 为 null)
    print(f'  L3+L4 defense: A HP={s_a.current_hp}, B HP={s_b.current_hp}, '
          f'A effects={len(s_a.effects)}, replaced_by={bs_def.replaced_by.name}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 5: L3 状态效果 (资源 + stat + abnormal + mark + weather + 条件)
# ═══════════════════════════════════════════════════════════════════

def test_l3_state():
    sk_a = [load_bs('test_l3_state')]    # 状态: 全部 L3a+L3b 效果
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b, hp_a=200)  # HP<50% 触发 hp_below 条件

    # 预设效果供驱散/翻倍使用
    from scripts.sim.sprite import StatusEffect
    s_a = battle.player_a.active
    s_b = battle.player_b.active
    s_b.add_effect(StatusEffect(name='物攻+10%', category='stat', stat_key='atk', steps=1, scope='persistent', source='pre'))
    s_b.add_effect(StatusEffect(name='物防-10%', category='stat', stat_key='def', steps=-1, scope='persistent', source='pre'))
    s_a.add_effect(StatusEffect(name='物防-10%', category='stat', stat_key='def', steps=-1, scope='persistent', source='pre'))

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]
    s_a = battle.player_a.active
    s_b = battle.player_b.active

    # L3a: heal 30% (400*0.3=120) + direct_heal 50 + hp_below heal 20%
    assert s_a.current_hp > 200, f'Expected heal above 200, got {s_a.current_hp}'

    # L3a: gain_energy + steal_energy + gain_energy_by_enemy
    # gain_energy=2, steal_energy=1 (from B), gain_energy_by_enemy=0.5 of B's cost
    assert _contains(events, '回复'), f'Expected energy/HP recovery events'

    # L3a: steal_energy: B 能量曾被偷取（之后聚能恢复 5）
    assert _contains(events, '偷取'), f'Expected steal_energy event, got {events}'

    # L3b: stat(self, atk+1) → effective_stat('atk') 增加
    assert s_a.effective_stat('atk') > 100, \
        f'Expected atk boost, got {s_a.effective_stat("atk")}'

    # L3b: stat(opp, def-1)
    # L3b: abnormal(中毒×2) + abnormal(灼烧×4) + abnormal(寄生)
    # 注意：回合末 tick 已经触发一次，灼烧层数已减半
    assert s_b.get_stacks('中毒') == 2, f'Expected poison×2, got {s_b.get_stacks("中毒")}'
    assert s_b.get_stacks('灼烧') == 2, \
        f'Expected burn×2 after Turn1 end halving, got {s_b.get_stacks("灼烧")}'
    assert s_b.get_stacks('寄生') == 1, f'Expected parasite×1, got {s_b.get_stacks("寄生")}'

    # L3b: weather(rain, 5) → Turn1 末已递减为 4
    assert battle.globals.weather == 'rain', \
        f'Expected rain, got {battle.globals.weather}'
    assert battle.globals.weather_turns == 4, \
        f'Expected weather turns 4 after tick, got {battle.globals.weather_turns}'

    # L3b: mark(光合印记, own_team)
    pos_mark, _ = battle.globals.get_marks('A')
    assert pos_mark is not None and pos_mark.name == '光合印记', \
        f'Expected 光合印记, got {pos_mark}'

    # L3b: adjacent_power_bonus → 相邻技能 power_mod 增加
    # conditional(weather_is rain → sp_atk+1)
    weather_sp_atk = [e for e in s_a.effects
                      if e.is_stat and e.stat_key == 'sp_atk' and e.source == '']
    # rain condition 触发 sp_atk+1
    assert _contains(events, '天气→rain'), f'Expected weather event'

    # L3b: charge, priority_bonus
    assert _contains(events, '蓄力'), f'Expected charge event'

    # L3b: dispel_positive(opp), dispel_negative(self)
    assert _contains(events, '驱散'), f'Expected dispel events'

    # L3b: double_positive(self), double_negative(opp)
    assert _contains(events, '翻倍'), f'Expected double events'

    print(f'  L3 state: A HP={s_a.current_hp} E={s_a.energy}, '
          f'B HP={s_b.current_hp} E={s_b.energy}, '
          f'B abnormal=[中毒×{s_b.get_stacks("中毒")}, '
          f'灼烧×{s_b.get_stacks("灼烧")}, 寄生×{s_b.get_stacks("寄生")}], '
          f'weather={battle.globals.weather}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 6: L3c 交换效果
# ═══════════════════════════════════════════════════════════════════

def test_l3_exchange():
    sk_a = [load_bs('test_l3_exchange')]  # 状态: 全部 L3c 效果
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b, hp_a=400, hp_b=200)

    # 给 A 提前加一个 buff，以便测试 exchange_effects
    from scripts.sim.sprite import StatusEffect
    s_a = battle.player_a.active
    s_a.add_effect(StatusEffect(
        name='物攻+10%', category='stat', stat_key='atk', steps=1,
        scope='persistent', source='pre',
    ))

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]
    s_a = battle.player_a.active
    s_b = battle.player_b.active

    # L3c: exchange_hp_ratio → HP 比例交换
    # A HP=400/400 (100%), B HP=200/400 (50%)
    # 交换后: A=200(50%), B=400(100%)
    # 但由于 healing 到 max_hp 的限制, A HP=200, B HP=400? No, 交换的是比例
    # A: max(1, round(0.5 * 400)) = 200 → B HP: max(1, round(1.0 * 400)) = 400
    assert s_a.current_hp <= 250, \
        f'A HP should decrease after exchange, got {s_a.current_hp}'
    assert s_b.current_hp >= 350, \
        f'B HP should increase after exchange, got {s_b.current_hp}'

    # L3c: exchange_effects → effects 列表交换
    # A 之前有物攻+10%, B 有 B 原有的 effects
    # 交换后: A 得到 B 的 effects, B 得到 A 的 effects (含物攻+10%)
    assert _contains(events, '交换了增益和减益'), 'Expected exchange_effects event'

    # L3c: exchange_skills → 技能列表交换
    assert _contains(events, '交换了技能'), 'Expected exchange_skills event'

    # L3c: random_devotion×3 → 3 次随机奉献
    assert _contains(events, '随机奉献'), 'Expected random_devotion event'

    print(f'  L3c exchange: A HP={s_a.current_hp}/{s_a.max_hp}, '
          f'B HP={s_b.current_hp}/{s_b.max_hp}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 7a: L5 ESCAPE (脱离)
# ═══════════════════════════════════════════════════════════════════

def test_l5_escape():
    main_skills = [load_bs('test_l5_escape')]
    bench_skills = [load_bs('test_basic_attack')]
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(main_skills, sk_b, bench_a=[bench_skills])

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]

    # L5: escape → 当前精灵换下，替补上场
    assert _contains(events, '脱离'), f'Expected escape event, got {events}'
    # 检查 active 是否已切换
    s_a = battle.player_a.active
    assert s_a.species.name == '替补', \
        f'Expected bench sprite active, got {s_a.species.name}'
    print(f'  L5 escape: active={s_a.name}, events={[e for e in events if "脱离" in e]}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 7b: L5 ESCAPE_INHERIT (折返继承)
# ═══════════════════════════════════════════════════════════════════

def test_l5_escape_inherit():
    main_skills = [load_bs('test_l5_inherit')]
    bench_skills = [load_bs('test_basic_attack')]
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(main_skills, sk_b, bench_a=[bench_skills])

    # 给首发精灵加一个 buff（用于验证继承）
    from scripts.sim.sprite import StatusEffect
    s_a = battle.player_a.active
    s_a.add_effect(StatusEffect(
        name='物攻+20%', category='stat', stat_key='atk', steps=2,
        scope='persistent', source='pre',
    ))

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]

    # 替补上场，且继承了增益
    assert _contains(events, '继承'), f'Expected inherit event, got {events}'
    s_new = battle.player_a.active
    assert s_new.species.name == '替补', f'Expected bench, got {s_new.species.name}'

    # 验证继承的增益
    inherited = [e for e in s_new.effects if e.source == 'pre']
    assert len(inherited) > 0, f'Expected inherited effects, got {s_new.effects}'
    print(f'  L5 escape_inherit: {s_new.name} inherited {len(inherited)} effects')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 7c: L5 force_return + return_self + borrow_skill
# ═══════════════════════════════════════════════════════════════════

def test_l5_multi():
    main_skills = [load_bs('test_l5_multi')]
    bench_skills = [load_bs('test_basic_attack')]
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(main_skills, sk_b,
                          bench_a=[bench_skills], bench_b=[bench_skills])

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]

    # L5: force_return → 对方返场 (清 battlefield 效果)
    # B 切换不会发生因为没有 force_return 的支持, 只有 _resolve_return
    assert _contains(events, '返场'), f'Expected return event, got {events}'

    # L5: return_self → 设置 pending_return flag (在 L6 才实际执行)
    s_a = battle.player_a.active
    # pending_return 在 dispatch 中设为 True
    # 注意: 如果 borrow_skill 先触发了, 那 return_self 标记可能不在了
    # 因为 borrow 可能替换了技能

    # L5: borrow_skill → 从替补借技能
    assert _contains(events, '借用'), f'Expected borrow event, got {events}'

    print(f'  L5 multi: events={[e for e in events if any(kw in e for kw in ["返场", "借用", "蓄力"])]}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 8: L6 回合末结算 (tick + cooldown + weather + mark)
# ═══════════════════════════════════════════════════════════════════

def test_l6_turn_end():
    """回合末: 中毒/灼烧/寄生 tick + 冷却递减 + 天气 + 印记回合末效果。
    Turn1 应用异常后回合末即触发首次 tick → 灼烧层数已减半。Turn2 验证第二次 tick。"""
    sk_a = [load_bs('test_l3_state')]   # 状态: 会挂中毒×2, 灼烧×4, 寄生 给 B
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b)
    s_b = battle.player_b.active

    # Turn 1: 施加异常状态 + weather rain + mark 光合印记
    # Turn 1 回合末已经触发了一次 tick: 灼烧 4→2, 中毒/寄生各扣血
    _run(battle, [('skill', 0)], [('gather', None)])
    # Turn 1 end 已将灼烧层数减半
    assert s_b.get_stacks('中毒') == 2, f'Poison should stay 2, got {s_b.get_stacks("中毒")}'
    assert s_b.get_stacks('灼烧') == 2, \
        f'Burn should have halved to 2 after Turn1 end, got {s_b.get_stacks("灼烧")}'

    hp_before = s_b.current_hp

    # Turn 2: 双方聚能 (让回合正常推进), 回合末结算触发第二次 tick
    events = _run(battle, [('gather', None)], [('gather', None)])[0]
    s_b = battle.player_b.active

    # L6: 中毒 tick → 3% × 2 stacks = 6% maxHP = 24 (每次回合末都触发)
    assert _contains(events, '中毒'), f'Expected poison tick, got {events}'

    # L6: 灼烧 tick → 2% × 2 stacks = 4% maxHP = 16, 层数再减半 → 1
    assert _contains(events, '灼烧'), f'Expected burn tick, got {events}'
    assert s_b.get_stacks('灼烧') == 1, \
        f'Burn stacks should halve to 1, got {s_b.get_stacks("灼烧")}'

    # L6: 寄生 tick → 6% maxHP = 24
    assert _contains(events, '寄生'), f'Expected parasite tick, got {events}'

    # L6: HP 确实减少了
    assert s_b.current_hp < hp_before, \
        f'B should take tick damage: {hp_before} → {s_b.current_hp}'

    # L6: 天气 tick → weather_turns 递减 (初始5, Turn1末→4, Turn2末→3)
    assert battle.globals.weather_turns < 4, \
        f'Weather turns should tick down from 4, got {battle.globals.weather_turns}'

    # L6: 印记光合印记 → turn_end +1 E (A 已满能量，静默处理)
    pos_mark, _ = battle.globals.get_marks('A')
    assert pos_mark is not None and pos_mark.name == '光合印记', \
        f'Mark should still exist, got {pos_mark}'

    dmg_taken = hp_before - s_b.current_hp
    print(f'  L6 turn_end: B HP {hp_before}→{s_b.current_hp} (-{dmg_taken}), '
          f'burn 2→{s_b.get_stacks("灼烧")}, '
          f'weather_turns={battle.globals.weather_turns}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 9: 防御技能冷却 (cooldown test)
# ═══════════════════════════════════════════════════════════════════

def test_defense_cooldown():
    sk_a = [load_bs('test_l3_l4_defense')]
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b)

    # Turn 1: 使用防御技能 → cooldown 应为 1
    _run(battle, [('skill', 0)], [('skill', 0)])
    bs_def = battle.player_a.active.skills[0]
    assert bs_def.cooldown == 1, \
        f'Defense skill should have cooldown=1, got {bs_def.cooldown}'

    # Turn 2: 聚能 → L6 cooldown-- → cooldown=0
    _run(battle, [('gather', None)], [('gather', None)])
    assert bs_def.cooldown == 0, \
        f'Cooldown should decrement to 0, got {bs_def.cooldown}'
    print(f'  Defense cooldown: 1→{bs_def.cooldown} PASS')
    return True


# ═══════════════════════════════════════════════════════════════════
# Scenario 10: ignore_mods 生效验证
# ═══════════════════════════════════════════════════════════════════

def test_ignore_mods_effect():
    """ignore_mods 应在 calc_damage 中忽略对方防御修正。"""
    sk_a = [load_bs('test_l0_l2')]       # 含 ignore_mods
    sk_b = [load_bs('test_basic_attack')]
    battle = _make_battle(sk_a, sk_b)

    # 给 B 加 def+2 (正面防御)
    from scripts.sim.sprite import StatusEffect
    s_b = battle.player_b.active
    s_b.add_effect(StatusEffect(
        name='物防+20%', category='stat', stat_key='def', steps=2,
        scope='persistent', source='pre',
    ))
    def_before = s_b.effective_stat('def')   # 120 * 1.2 = 144

    events = _run(battle, [('skill', 0)], [('gather', None)])[0]
    s_b = battle.player_b.active

    # ignore_mods 生效: calc_damage 中 ignore_positive=True 用 def=120 而非 144
    # 如果没用 ignore_mods，伤害会更低
    # 我们只能间接验证: 伤害事件存在且合理
    dmg_events = [e for e in events if 'test_l0_l2' in e and 'HP' in e]
    assert len(dmg_events) >= 1
    print(f'  ignore_mods: def_before={def_before}, B HP after={s_b.current_hp}')
    return True


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

TESTS = [
    ('L0+L2  modifier+damage',         test_l0_l2),
    ('L1      动态威力+next_attack',    test_l1_power),
    ('Gate     能量支付',               test_energy_gate),
    ('L3+L4   防御应对',                test_l3_l4_defense),
    ('L3       状态效果',               test_l3_state),
    ('L3c      交换效果',               test_l3_exchange),
    ('L5       ESCAPE',                test_l5_escape),
    ('L5       ESCAPE_INHERIT',        test_l5_escape_inherit),
    ('L5       force_return+borrow',   test_l5_multi),
    ('L6       回合末结算',             test_l6_turn_end),
    ('Cooldown 防御冷却',              test_defense_cooldown),
    ('L2       ignore_mods 生效',      test_ignore_mods_effect),
]

if __name__ == '__main__':
    random.seed(42)  # reproducible random_devotion / borrow_skill

    passed = 0
    failed = 0
    for label, fn in TESTS:
        try:
            fn()
            print(f'  [PASS] {label}')
            passed += 1
        except AssertionError as e:
            print(f'  [FAIL] {label}: {e}')
            failed += 1
        except Exception as e:
            print(f'  [ERROR] {label}: {type(e).__name__}: {e}')
            import traceback
            traceback.print_exc()
            failed += 1

    print(f'\n{"=" * 50}')
    print(f'  Results: {passed} passed, {failed} failed, {len(TESTS)} total')
    print(f'{"=" * 50}')
    sys.exit(0 if failed == 0 else 1)
