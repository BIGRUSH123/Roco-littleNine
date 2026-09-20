"""recorder — 固定种子对局录制器。

驱动一场确定性对局（RuleAgent vs RuleAgent），逐回合记录
完整事件流 + 状态摘要。任何引擎行为变化都会在对拍文件中体现。

确定性保障：
- random.seed(seed) 统一驱动阵容生成与对局内随机数（伤害浮动等）；
- 生成与测试进程须以 PYTHONHASHSEED=0 运行（代码中不迭代影响行为的集合，
  但显式固定以绝后患）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from backend.common.constants import STAT_KEYS
from backend.common.nature import NATURE_TABLE
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.engine.serializer import round_record_to_dict
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory

MAX_TURNS_SAFETY = 300


# ── 种子化阵容生成（复刻 train.py 逻辑，避免引入 torch 依赖）──

def _seeded_item():
    from backend.sim.player import Item
    return Item.leader() if random.random() < 0.5 else Item.wish()


def _seeded_teams(max_team_size: int = 3, max_skills: int = 4) -> tuple[list[dict], list[dict]]:
    names = list(SPRITE_RANDOM_POOL.keys())
    random.shuffle(names)
    used: set[str] = set()

    max_possible = min(max_team_size, len(names) // 2)
    team_size = random.randint(1, max_possible) if max_possible >= 1 else 1

    def build_team(size: int) -> list[dict]:
        specs: list[dict] = []
        for name in names:
            if name in used:
                continue
            if len(specs) >= size:
                break
            available = SPRITE_RANDOM_POOL[name]
            n_skills = min(max_skills, len(available))
            chosen = random.sample(available, max(1, n_skills))
            nature = random.choice(list(NATURE_TABLE.keys()))
            iv_keys = random.sample(list(STAT_KEYS), 3)
            iv = {k: 10 if k in iv_keys else 0 for k in STAT_KEYS}
            specs.append({'name': name, 'skills': chosen, 'nature': nature, 'iv': iv})
            used.add(name)
        return specs

    return build_team(team_size), build_team(team_size)


def build_seeded_battle(seed: int):
    """按种子构建 (battle, agent_a, agent_b, meta)。"""
    random.seed(seed)
    factory = SimFactory()
    team_a, team_b = _seeded_teams()
    item_a, item_b = _seeded_item(), _seeded_item()
    p1 = factory.build_player('A', team_a, item=item_a)
    p2 = factory.build_player('B', team_b, item=item_b)
    battle = factory.build_battle(p1, p2)
    meta = {
        'seed': seed,
        'team_a': team_a,
        'team_b': team_b,
        'item_a': item_a.name,
        'item_b': item_b.name,
    }
    return battle, RuleAgent('A', p1), RuleAgent('B', p2), meta


# ── 状态摘要 ──

def _jsonable(v):
    """把任意引擎内部值转成 JSON 稳定表示。"""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, set):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, dict):
        return {str(k): _jsonable(v[k]) for k in sorted(v.keys(), key=str)}
    name = getattr(v, 'name', None)
    if isinstance(name, str):
        return name
    return str(v)


def _modifiers_digest(mods: dict) -> dict:
    return {str(k): json.dumps(_jsonable(v), sort_keys=True, ensure_ascii=False)
            for k, v in mods.items()}


def _effect_digest(e) -> dict:
    return {
        'name': getattr(e, 'name', str(e)),
        'ttl': getattr(e, 'ttl', None),
        'stacks': getattr(e, 'stacks', 0),
        'scope': getattr(e, 'scope', ''),
        'cooldown': getattr(e, 'cooldown', 0),
        'is_inherent': getattr(e, 'is_inherent', False),
        'value': _jsonable(getattr(e, 'value', None)),
    }


def _skill_digest(sk) -> dict:
    return {
        'name': sk.base.name,
        'cooldown': sk.cooldown,
        'sealed': sk.sealed,
        'replaced_by': _jsonable(sk.replaced_by),
        'next_attack_mult': _jsonable(sk.next_attack_mult),
        'nullified': sk.nullified,
        'is_temporary': sk.is_temporary,
        'transmission': getattr(sk, '_transmission', 0),
        'element_override': getattr(sk, '_element_override', ''),
        'morph_temp': bool(getattr(sk, '_morph_temp', False)),
        'modifiers': _modifiers_digest(getattr(sk, '_modifiers', {})),
        'burst_effects': [json.dumps(_jsonable(x), sort_keys=True, ensure_ascii=False)
                          for x in getattr(sk, '_burst_effects', [])],
    }


def _sprite_digest(s) -> dict:
    return {
        'name': s.name,
        'species_form': getattr(s.species, 'form', ''),
        'species_appearance': getattr(s.species, 'appearance', ''),
        'hp': s.current_hp,
        'max_hp': s.max_hp,
        'energy': s.energy,
        'entry_turn': s.entry_turn,
        'charging': getattr(s, '_charging', False),
        'charged_skill': getattr(getattr(s, '_charged_skill_ref', None), 'base', None) is not None
        and s._charged_skill_ref.base.name or '',
        'effects': [_effect_digest(e) for e in (s.active_effects or [])],
        'modifiers': _modifiers_digest(s._modifiers),
        'pending_mods': [json.dumps(_jsonable(m), sort_keys=True, ensure_ascii=False)
                         for m in getattr(s, '_pending_modifiers', [])],
        'skills': [_skill_digest(sk) for sk in (s.skills or [])],
    }


def _player_digest(p) -> dict:
    return {
        'name': p.name,
        'lives': p.lives,
        'active_index': p.active_index,
        'item': None if p.item is None else {
            'name': p.item.name, 'uses': getattr(p.item, 'uses', 0),
        },
        'sprites': [_sprite_digest(s) for s in p.team],
    }


def state_digest(battle) -> dict:
    """对局当前状态的完整可比较摘要。"""
    g = battle.globals
    vm = battle._vm_engine
    # id(sprite) → 稳定键（跨进程内存地址不同，必须换成名字）
    id_map: dict[int, str] = {}
    for p in (battle.player_a, battle.player_b):
        for s in p.team:
            id_map[id(s)] = f'{p.name}:{s.name}'

    def stable_key(k) -> str:
        return id_map.get(k, f'unknown:{k!s}')

    def stable_dict(d: dict) -> dict:
        return {stable_key(k): v for k, v in d.items()}

    return {
        'turn': battle.turn,
        'winner': battle.winner,
        'weather': g.weather,
        'weather_turns': g.weather_turns,
        'players': [_player_digest(battle.player_a), _player_digest(battle.player_b)],
        'marks': {
            team: [
                {'name': m.name, 'stacks': m.stacks, 'positive': m.is_positive}
                for m in lst
            ]
            for team, lst in g.mark_effects.items()
        },
        'team_counters': _jsonable(battle.team_counters),
        'vm_counters': {stable_key(k): _jsonable(v)
                        for k, v in getattr(vm, '_counter_values', {}).items()},
        'skill_history': {
            stable_key(k): [_jsonable(x) for x in v]
            for k, v in getattr(vm, '_skill_history', {}).items()
        },
        'skill_tags': {
            stable_key(k): {str(kk): _jsonable(vv) for kk, vv in sorted(v.items(), key=lambda x: str(x[0]))}
            for k, v in getattr(vm, '_skill_tags', {}).items()
        },
        'burst_names': {
            stable_key(k): sorted(str(x) for x in v)
            for k, v in getattr(vm, '_burst_names', {}).items()
        },
        'pending_effects': {
            team: [
                {'name': getattr(e, 'name', str(e)), 'payload': json.dumps(
                    _jsonable(e), sort_keys=True, ensure_ascii=False, default=str)}
                for e in lst
            ]
            for team, lst in battle.pending_effects.items()
        },
        'scheduled_effects': [
            {'turn': se['turn'], 'phase': se['phase'],
             'effects': [getattr(e, 'name', str(e)) for e in se['effects']]}
            for se in battle.scheduled_effects
        ],
    }


# ── 录制 ──

def run_recorded(seed: int) -> dict:
    """跑一场种子对局，返回可 JSON 序列化的完整录制（含逐回合事件+状态摘要）。"""
    battle, agent_a, agent_b, meta = build_seeded_battle(seed)

    # 复刻 Battle.run 的出场选择，但逐回合录制
    lead_a = agent_a.choose_lead(battle)
    lead_b = agent_b.choose_lead(battle)
    battle.player_a.active_index = lead_a
    battle.player_b.active_index = lead_b
    battle._invalidate_ctx_team_cache()

    turns: list[dict] = []
    while not battle.is_finished and battle.turn < MAX_TURNS_SAFETY:
        rec = battle.execute_turn(agent_a, agent_b)
        turns.append({
            'record': round_record_to_dict(rec),
            'state': state_digest(battle),
        })

    return {
        **meta,
        'lead_a': lead_a,
        'lead_b': lead_b,
        'winner': battle.winner,
        'turns_count': battle.turn,
        'turns': turns,
    }


def load_fixture(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))
