"""scripts/tools/test_skills.py — 全技能冒烟测试 + 效果验证

创建 1,000,000 HP 的测试精灵，加载所有技能逐一执行，
验证无崩溃且效果正确触发。

用法:
  python scripts/tools/test_skills.py              # 全部冒烟 + 验证
  python scripts/tools/test_skills.py --verbose    # 详细输出
  python scripts/tools/test_skills.py --skill 闪击 # 单技能调试
"""

import json
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from scripts.sim.sprite import Sprite, StatusEffect
from scripts.sim.skill import Skill
from scripts.sim.battleskill import BattleSkill
from scripts.sim.player import Player, PlayStyle
from scripts.sim.battle import Battle
from scripts.sim.action import Action
from scripts.sim.agent import RuleAgent
from scripts.common.models import SpeciesStats

# ── 需要跳过测试的技能 ──
SKIP_SKILLS: set[str] = set()

TEST_SPECIES = SpeciesStats(
    name="测试精灵",
    hp=100, atk=100, sp_atk=100,
    def_=100, sp_def=100, speed=100,
    attributes="普通",
)

TEST_HP = 1_000_000
TEST_ENERGY = 99  # 足够高以支持所有技能（最高耗能约30）


def _make_sprite(skills: list[BattleSkill]) -> Sprite:
    return Sprite(
        species=TEST_SPECIES,
        initial_stats={'hp': TEST_HP, 'atk': 100, 'sp_atk': 100,
                       'def': 100, 'sp_def': 100, 'speed': 100},
        current_hp=TEST_HP, max_hp=TEST_HP,
        energy=TEST_ENERGY,
        skills=skills,
    )


def _make_battle(attacker_skills: list[BattleSkill]) -> Battle:
    atk_sprite = _make_sprite(attacker_skills)
    def_sprite = _make_sprite([])

    p1 = Player(name='测试方', team=[atk_sprite], style=PlayStyle(), lives=1)
    p2 = Player(name='沙包', team=[def_sprite], style=PlayStyle(), lives=1)

    battle = Battle(p1, p2, verbose=False)
    battle._agent_a = RuleAgent('A', p1)
    battle._agent_b = RuleAgent('B', p2)
    return battle


def _load_skills(skills_dir: Path) -> list[Skill]:
    skills: list[Skill] = []
    for path in sorted(skills_dir.glob('*.json')):
        if path.stem.startswith('_'):
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            skills.append(Skill.load(data))
        except Exception as exc:
            print(f'  [加载失败] {path.stem}: {exc}')
    return skills


def _skill_tags(skill: Skill) -> str:
    tags = [skill.skill_type]
    if skill.element:
        tags.append(skill.element)
    if skill.power:
        tags.append(f'威力{skill.power}')
    if skill.counter != '无':
        tags.append(f'应对{skill.counter}')
    if skill.priority:
        tags.append(f'先手+{skill.priority}')
    if skill.energy_cost:
        tags.append(f'耗{skill.energy_cost}')
    return ', '.join(tags)


def _effect_summary(skill: Skill) -> str:
    parts: list[str] = []
    for e in skill.effects:
        k = e.kind
        if k == 'stat':
            parts.append(f'{e.stat}{"+" if e.steps > 0 else ""}{e.steps}步→{e.target}')
        elif k == 'abnormal':
            parts.append(f'{e.name}×{e.stacks}→{e.target}')
        elif k == 'mark':
            parts.append(f'印记:{e.name}→{e.target}')
        elif k == 'weather':
            parts.append(f'天气:{e.weather}')
        elif k == 'special':
            parts.append(e.name)
        elif k == 'conditional':
            when_str = str(e.when.get('kind', '?')) if e.when else '?'
            parts.append(f'条件:{when_str}')
    return '; '.join(parts) if parts else '(无效果)'


# ═══════════════════════════════════════════════════════════════
# 条件求值（独立于 resolver，用于验证时判断 conditional 是否应触发）
# ═══════════════════════════════════════════════════════════════

def _eval_cond(cond: dict | None, attacker: Sprite, defender: Sprite,
               battle: Battle, is_first: bool) -> bool:
    """判断条件是否满足（模拟 _check_condition）。"""
    if not cond:
        return True
    kind = cond.get('kind', '')
    if kind == 'is_first':
        return is_first
    if kind == 'counter_succeeded':
        return False  # 孤立测试无 counter
    if kind == 'opp_switched':
        return False
    if kind == 'hp_below':
        ratio = cond.get('ratio', 0.5)
        return attacker.current_hp / attacker.max_hp < ratio
    if kind == 'has_abnormal':
        return attacker.get_stacks(cond.get('name', '')) > 0
    if kind == 'weather_is':
        return battle.globals.weather == cond.get('weather', '')
    if kind == 'counter_ge':
        return attacker.get_counter(cond.get('key', '')) >= cond.get('value', 0)
    if kind == 'and':
        return all(_eval_cond(c, attacker, defender, battle, is_first)
                   for c in cond.get('conditions', []))
    if kind == 'or':
        return any(_eval_cond(c, attacker, defender, battle, is_first)
                   for c in cond.get('conditions', []))
    return True


# ═══════════════════════════════════════════════════════════════
# 效果验证
# ═══════════════════════════════════════════════════════════════

def _verify_effects(skill: Skill, battle: Battle,
                    hp_before: dict[str, int],
                    energy_before: dict[str, int],
                    is_first: bool) -> list[str]:
    """验证 skill 声明的效果是否实际生效。返回未生效的效果描述列表。"""
    mismatches: list[str] = []
    attacker = battle.player_a.active
    defender = battle.player_b.active
    bs = attacker.skills[0] if attacker.skills else None

    for effect in skill.effects:
        ms = _verify_one(effect, skill, battle, attacker, defender, bs,
                         hp_before, energy_before, is_first)
        mismatches.extend(ms)
    return mismatches


def _verify_one(effect, skill: Skill, battle: Battle,
                attacker: Sprite, defender: Sprite, bs: BattleSkill,
                hp_before: dict[str, int], energy_before: dict[str, int],
                is_first: bool) -> list[str]:
    """验证单个效果。"""
    k = effect.kind

    if k == 'stat':
        return _verify_stat(effect, attacker, defender)
    elif k == 'abnormal':
        return _verify_abnormal(effect, attacker, defender)
    elif k == 'mark':
        return _verify_mark(effect, battle)
    elif k == 'weather':
        return _verify_weather(effect, battle)
    elif k == 'special':
        return _verify_special(effect, skill, attacker, defender, bs,
                               hp_before, energy_before)
    elif k == 'conditional':
        return _verify_conditional(effect, skill, battle, attacker, defender, bs,
                                   hp_before, energy_before, is_first)
    return []


def _verify_stat(effect, attacker: Sprite, defender: Sprite) -> list[str]:
    target = attacker if effect.target == 'self' else defender
    found = [e for e in target.effects
             if e.is_stat and e.stat_key == effect.stat and e.steps == effect.steps]
    if not found:
        return [f'stat未生效: {effect.stat} {effect.steps:+d}步 → {effect.target}']
    return []


def _verify_abnormal(effect, attacker: Sprite, defender: Sprite) -> list[str]:
    target = attacker if effect.target == 'self' else defender
    stacks = target.get_stacks(effect.name)
    if stacks < effect.stacks:
        return [f'abnormal未生效: {effect.name} 期望>={effect.stacks} 实际{stacks} → {effect.target}']
    return []


def _verify_mark(effect, battle: Battle) -> list[str]:
    team = 'A' if effect.target == 'own_team' else 'B'
    pos, neg = battle.globals.get_marks(team)
    mark = pos if battle.globals.classify_mark(effect.name) == 'positive' else neg
    if mark is None or mark.name != effect.name:
        return [f'mark未生效: {effect.name} → {team}方']
    return []


def _verify_weather(effect, battle: Battle) -> list[str]:
    if battle.globals.weather != effect.weather:
        return [f'weather未生效: {effect.weather} 实际={battle.globals.weather}']
    return []


def _verify_special(effect, skill: Skill, attacker: Sprite, defender: Sprite,
                    bs: BattleSkill, hp_before: dict[str, int],
                    energy_before: dict[str, int]) -> list[str]:
    name = effect.name

    if name == 'power_mult' and not skill.is_attack:
        val = effect.value or 1.0
        if bs.next_attack_mult != val:
            return [f'power_mult未生效: next_attack_mult={bs.next_attack_mult} 期望={val}']
        return []

    if name in ('power_bonus', 'adjacent_power_bonus') and not skill.is_attack:
        # 孤立测试：只有1个技能，无相邻技能可加
        return []

    if name == 'heal':
        pct = effect.value
        expected = round(attacker.max_hp * pct)
        actual_heal = attacker.current_hp - hp_before['attacker']
        if actual_heal < min(1, expected):
            return [f'heal未生效: HP变化={actual_heal} 期望≈{expected}']
        return []

    if name == 'direct_heal':
        amount = effect.amount or 0
        actual_heal = attacker.current_hp - hp_before['attacker']
        if amount > 0 and actual_heal < amount:
            return [f'direct_heal未生效: HP变化={actual_heal} 期望={amount}']
        return []

    if name == 'gain_energy':
        amount = effect.amount or 0
        gained = attacker.energy - energy_before['attacker']
        if amount > 0 and gained < min(amount, 10 - energy_before['attacker']):
            return [f'gain_energy未生效: +{gained}E 期望{amount}']
        return []

    if name == 'steal_energy':
        amount = effect.amount or 1
        def_lost = energy_before['defender'] - defender.energy
        if def_lost < amount:
            return [f'steal_energy未生效: def-{def_lost}E 期望{amount}']
        return []

    if name == 'life_drain':
        if skill.is_attack and skill.power > 0:
            pct = effect.value / 100.0 if effect.value > 1 else effect.value
            dmg_dealt = hp_before['defender'] - defender.current_hp
            expected_heal = round(dmg_dealt * pct)
            actual_heal = attacker.current_hp - hp_before['attacker']
            if actual_heal < expected_heal:
                return [f'life_drain未生效: heal={actual_heal} 期望≈{expected_heal}']
        return []

    # 以下 special 效果在孤立测试中属于正常 no-op：
    # damage_reduction → 仅在 counter 时生效（由 _collect_modifiers 处理）
    # reflect_damage → 需要 countered_skill
    # burst → 迸发效果，battle.py L0段处理（首次行动威力/能耗/收集）
    # charge → 仅记录事件
    # escape → 需要 bench sprite
    # multi_hit → 仅影响 calc_damage
    # priority_bonus → 仅在 battle._effective_priority 中生效
    return []


def _verify_conditional(effect, skill: Skill, battle: Battle,
                        attacker: Sprite, defender: Sprite, bs: BattleSkill,
                        hp_before: dict[str, int], energy_before: dict[str, int],
                        is_first: bool) -> list[str]:
    cond_met = _eval_cond(effect.when, attacker, defender, battle, is_first)
    if not cond_met:
        # 条件不满足 → then 中的效果不应触发
        return []
    # 条件满足 → 验证 then 中的每个效果
    mismatches: list[str] = []
    for sub in (effect.then or []):
        ms = _verify_one(sub, skill, battle, attacker, defender, bs,
                         hp_before, energy_before, is_first)
        mismatches.extend(ms)
    return mismatches


# ═══════════════════════════════════════════════════════════════
# 主测试逻辑
# ═══════════════════════════════════════════════════════════════

def test_one(skill: Skill, verbose: bool = False) -> dict:
    """测试单个技能。返回结果 dict 含 verification 字段。"""
    bs = BattleSkill(base=skill)
    battle = _make_battle([bs])

    attacker = battle.player_a.active
    defender = battle.player_b.active

    # 预扣血到半血：让 heal/life_drain 效果有验证空间
    has_heal = any(
        (e.kind == 'special' and e.name in ('heal', 'direct_heal', 'life_drain'))
        or (e.kind == 'conditional' and any(
            s.kind == 'special' and s.name in ('heal', 'direct_heal', 'life_drain')
            for s in (e.then or [])))
        for e in skill.effects
    )
    if has_heal:
        attacker.current_hp = TEST_HP // 2

    hp_before = {'attacker': attacker.current_hp, 'defender': defender.current_hp}
    energy_before = {'attacker': attacker.energy, 'defender': defender.energy}

    try:
        action = Action(kind='skill', skill_index=0)
        events = battle._execute_single_action('A', action, is_first=True)

        # 重新获取引用（_execute_single_action 可能修改了精灵状态）
        attacker = battle.player_a.active
        defender = battle.player_b.active

        dmg_taken = hp_before['defender'] - defender.current_hp

        # 验证效果
        mismatches = _verify_effects(skill, battle, hp_before, energy_before, is_first=True)

        result = {
            'skill': skill.name,
            'type': skill.skill_type,
            'power': skill.power,
            'element': skill.element,
            'energy_cost': skill.energy_cost,
            'effect_count': len(skill.effects),
            'status': 'PASS' if not mismatches else 'MISMATCH',
            'damage': dmg_taken,
            'hp_remaining': defender.current_hp,
            'events': events,
            'atk_energy': attacker.energy,
            'mismatches': mismatches,
        }

        if verbose:
            tag_str = _skill_tags(skill)
            eff_str = _effect_summary(skill)
            evt_str = ' | '.join(events) if events else '(无事件)'
            print(f'  [{skill.name}] {tag_str}')
            print(f'    声明效果: {eff_str}')
            print(f'    实际事件: {evt_str}')
            if skill.is_attack:
                print(f'    伤害: {dmg_taken} (剩余HP: {defender.current_hp})')
            print(f'    剩余能量: {attacker.energy}')
            if mismatches:
                for m in mismatches:
                    print(f'    [验证失败] {m}')
            else:
                print(f'    [验证通过]')

        return result

    except Exception as exc:
        if verbose:
            traceback.print_exc()
        return {
            'skill': skill.name,
            'type': skill.skill_type,
            'power': skill.power,
            'element': skill.element,
            'energy_cost': skill.energy_cost,
            'effect_count': len(skill.effects),
            'status': 'FAIL',
            'error': str(exc),
            'traceback': traceback.format_exc(),
        }


def main() -> None:
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    single_skill = None
    for i, arg in enumerate(sys.argv):
        if arg == '--skill' and i + 1 < len(sys.argv):
            single_skill = sys.argv[i + 1]
            break

    skills_dir = BASE / 'data' / 'skills'
    if not skills_dir.is_dir():
        print(f'错误: 技能目录不存在: {skills_dir}')
        sys.exit(1)

    all_skills = _load_skills(skills_dir)
    if not all_skills:
        print('错误: 未加载到任何技能')
        sys.exit(1)

    if single_skill:
        test_skills = [s for s in all_skills if s.name == single_skill]
        if not test_skills:
            print(f'未找到技能: {single_skill!r}')
            sys.exit(1)
    else:
        test_skills = [s for s in all_skills if s.name not in SKIP_SKILLS]

    n_total = len(test_skills)
    n_skipped = len(all_skills) - n_total

    print(f'全技能冒烟测试 + 效果验证')
    print(f'  测试精灵 HP={TEST_HP} 能量={TEST_ENERGY}')
    print(f'  总技能: {len(all_skills)}  测试: {n_total}  跳过: {n_skipped}')
    print(f'{"─" * 60}')

    results: list[dict] = []
    passed = 0
    mismatched = 0
    failed = 0

    for i, skill in enumerate(test_skills):
        r = test_one(skill, verbose=verbose)
        results.append(r)

        status = r['status']
        if status == 'PASS':
            passed += 1
            if not verbose:
                dmg_str = f' → {r["damage"]}伤害' if r.get('damage') else ''
                n_eff = r.get('effect_count', 0)
                print(f'  [{i+1:3d}/{n_total}] PASS {skill.name}'
                      f' ({skill.skill_type}{dmg_str}) [{n_eff}效果]')
        elif status == 'MISMATCH':
            mismatched += 1
            print(f'  [{i+1:3d}/{n_total}] MISMATCH {skill.name}:')
            for m in r.get('mismatches', []):
                print(f'    ! {m}')
        else:
            failed += 1
            print(f'  [{i+1:3d}/{n_total}] FAIL {skill.name}: {r.get("error", "?")}')
            if verbose:
                print(r.get('traceback', ''))

    # ── 总结 ──
    print(f'{"─" * 60}')
    print(f'  结果: {passed} PASS, {mismatched} MISMATCH, {failed} FAIL, {n_skipped} SKIP')

    # ── 按技能类型统计 ──
    by_type: dict[str, tuple[int, int, int]] = {}
    for r in results:
        t = r['type']
        prev = by_type.get(t, (0, 0, 0))
        if r['status'] == 'PASS':
            by_type[t] = (prev[0] + 1, prev[1], prev[2])
        elif r['status'] == 'MISMATCH':
            by_type[t] = (prev[0], prev[1] + 1, prev[2])
        else:
            by_type[t] = (prev[0], prev[1], prev[2] + 1)

    print(f'\n按类型统计:')
    for t, (ok, mm, ng) in sorted(by_type.items()):
        total_t = ok + mm + ng
        parts = [f'{ok}/{total_t} pass']
        if mm:
            parts.append(f'{mm} mismatch')
        if ng:
            parts.append(f'{ng} FAIL')
        print(f'  {t}: {", ".join(parts)}')

    # ── 详细 mismatch 列表 ──
    if mismatched:
        print(f'\n效果未触发详情 ({mismatched}个):')
        for r in results:
            if r['status'] == 'MISMATCH':
                print(f'  [{r["skill"]}] ({r["type"]})')
                for m in r.get('mismatches', []):
                    print(f'    ! {m}')

    # ── 有声明效果且全部验证通过的比例 ──
    has_effects = [r for r in results if r.get('effect_count', 0) > 0]
    has_effects_pass = [r for r in has_effects if r['status'] == 'PASS']
    if has_effects:
        print(f'\n有声明效果的技能: {len(has_effects)}个, '
              f'全部验证通过: {len(has_effects_pass)}个 '
              f'({len(has_effects_pass)*100//len(has_effects)}%)')

    # ── 零效果技能（JSON 效果数组为空，需人工补全） ──
    zero_effect = [r for r in results
                   if r.get('effect_count', 0) == 0
                   and not r.get('damage')]
    if zero_effect:
        print(f'\n空效果技能 ({len(zero_effect)}个, JSON effects=[] 且 power=0):')
        for r in zero_effect:
            print(f'  - {r["skill"]} ({r["type"]})')


if __name__ == '__main__':
    main()
