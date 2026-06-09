"""backend/sim/battle.py — 对局引擎

回合 = 开始阶段 → 选择阶段 → 结算阶段 → 结束阶段
"""

from __future__ import annotations

from copy import copy
import contextlib
import itertools
import random
from typing import TYPE_CHECKING

from backend.common.skill_trait_ids import TRAIT_星地善良
from backend.vm.ir_skill import ChargeOp, CompiledSkill, WhenBlock, WhenBranch
from backend.vm.effect import AbnormalEffect, StateEffect, StatBuffEffect

from .action import Action
from .battle_mechanics import BattleMechanicsMixin
from .battleskill import BattleSkill
from .globals import GlobalEffects
from .resolver import SkillResolver
from .round_record import ActionRecord, RoundRecord
from .round_record import _action_short as _rr_action_short
from .traits import dispatch_entry, dispatch_leave
from .traits.trait_engine import fire_hook_first

if TYPE_CHECKING:
    from .agent import Agent
    from .player import Player
    from .skill import Skill
    from .sprite import Sprite


def _has_charge_op(obj) -> bool:
    """Recursively search for ChargeOp in IR tree (handles WhenBlock nesting)."""
    if isinstance(obj, ChargeOp):
        return True
    if isinstance(obj, WhenBlock):
        return any(_has_charge_op(e) for e in (obj.then + obj.else_ + obj.elif_))
    if isinstance(obj, WhenBranch):
        return any(_has_charge_op(e) for e in obj.then)
    if isinstance(obj, (tuple, list)):
        return any(_has_charge_op(e) for e in obj)
    return False


class _HeadlessActionRecord:
    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events = _NO_EVENTS


class _NoEventList(list):
    __slots__ = ()

    def append(self, item) -> None:
        return None

    def extend(self, iterable) -> None:
        return None

    def __iadd__(self, iterable):
        return self

    def insert(self, index, item) -> None:
        return None


_NO_EVENTS = _NoEventList()


class _HeadlessRoundRecord:
    __slots__ = ("turn", "first_team", "action_a", "action_b")

    def __init__(self, turn: int) -> None:
        self.turn = turn
        self.first_team = ""
        self.action_a = _HeadlessActionRecord()
        self.action_b = _HeadlessActionRecord()


_PER_TURN_KEYS = frozenset({
    "power", "power_mult", "damage_mult", "damage_reduction",
    "energy_cost", "energy_cost_mult", "priority", "combo_set",
})
_SKILL_PER_TURN_KEYS = _PER_TURN_KEYS | {"combo", "combo_mult"}


class Battle(BattleMechanicsMixin):
    """对局引擎。回合调度 + 动作执行。场地变动由 BattleMechanicsMixin 提供。"""

    MAX_TURNS = 150

    # ── 模块级技能 JSON 缓存（跨 Battle 实例共享，消除重复磁盘 I/O） ──
    _global_skill_cache: dict[str, "CompiledSkill"] = {}

    def save_mutable_state(self) -> dict:
        """保存对战可变状态，用于 MCTS 仿真回滚。只用浅拷贝，不用 deepcopy。"""
        # ── 精灵状态 ──
        sprites: list[dict] = []
        for player in (self.player_a, self.player_b):
            for sprite in player.team:
                sprites.append({
                    "sprite": sprite,
                    "hp": sprite.current_hp,
                    "energy": sprite.energy,
                    "modifiers": dict(sprite._modifiers),
                    "charging": getattr(sprite, '_charging', False),
                    "charged_idx": getattr(sprite, '_charged_skill_index', -1),
                    "counters": dict(getattr(sprite, 'counters', {})),
                    "first_action": sprite.first_action,
                    "first_action_battle": sprite.first_action_battle,
                    "locked_turns": getattr(sprite, 'locked_turns', 0),
                    "interrupted": getattr(sprite, 'interrupted', False),
                    "pending_return": getattr(sprite, 'pending_return', False),
                    "extra_skill_use": getattr(sprite, 'extra_skill_use', False),
                })
                # 效果快照：按位置保存，避免 id() 复用导致的错乱
                eff_snap = []
                for e in sprite.active_effects:
                    snap = (e, e.ttl, getattr(e, 'stacks', 0), getattr(e, 'scope', ''))
                    if isinstance(e, StatBuffEffect):
                        snap += (e.steps,)
                    eff_snap.append(snap)
                sprites[-1]["effects"] = eff_snap
                # 技能级可变状态：MCTS 回滚会高频调用，顺序列表比多个索引 dict 更少分配。
                skills = list(sprite.skills or [])
                sprites[-1]["skill_refs"] = skills
                sprites[-1]["skill_states"] = [
                    (
                        dict(sk._modifiers),
                        sk.cooldown,
                        sk.sealed,
                        sk.replaced_by,
                        sk.next_attack_mult,
                        sk.nullified,
                        sk.is_temporary,
                        sk._transmission,
                        sk._element_override,
                        sk._mech_energy_reduction,
                        list(sk._burst_effects),
                    )
                    for sk in skills
                ]
                # 精灵级可变状态（此前遗漏，MCTS 仿真残留会泄漏到真实对局）
                sprites[-1]["mod_scopes"] = dict(getattr(sprite, '_mod_scopes', {}))
                sprites[-1]["pending_mods"] = [
                    copy(m) for m in getattr(sprite, '_pending_modifiers', [])
                ]
                sprites[-1]["pending_effs"] = [
                    (copy(e), d) for e, d in getattr(sprite, '_pending_effects', [])
                ]
                sprites[-1]["trait_suppressed"] = getattr(sprite, '_trait_suppressed', False)
        # ── 印记：用 copy.copy 替代 deepcopy（MarkEffect 全字段为 primitive，
        # 浅拷贝已足够隔离 MCTS 仿真中的 stacks 修改 / list append-remove） ──
        mark_snap = {team: [copy(me) for me in lst]
                     for team, lst in self.globals.mark_effects.items()}
        # ── VM 引擎：浅拷贝字典/集合 ──
        vm = self._vm_engine
        vm_state = {
            "burst_effects": {t: list(v) for t, v in vm._burst_effects.items()},
            "burst_names": {t: set(v) for t, v in vm._burst_names.items()},
            "counter_values": dict(vm._counter_values),
            "skill_history": {k: list(v) for k, v in vm._skill_history.items()},
            "skill_tags": {k: dict(v) for k, v in vm._skill_tags.items()},
        }
        # ── 日志截断：只保留当前回合之前的记录（MCTS 仿真会追加额外回合） ──
        log_len = len(self.log)
        return {
            "sprites": sprites,
            "turn": self.turn,
            "winner": self.winner,
            "log_len": log_len,
            "weather": self.globals.weather,
            "weather_turns": self.globals.weather_turns,
            "marks": mark_snap,
            "team_counters": {t: dict(c) for t, c in self.team_counters.items()},
            # pending/scheduled effects 的元素均为 primitive-field dataclass/dict，
            # copy.copy 已足够隔离 MCTS 仿真中的增删（无需深拷贝嵌套结构）
            "pending_effects": {team: [copy(e) for e in lst]
                                for team, lst in self.pending_effects.items()},
            "scheduled_effects": [copy(s) for s in self.scheduled_effects],
            "pending_escape": self.pending_escape,
            "borrowed_restore": dict(self._borrowed_restore),
            "wish_restore": dict(self._wish_restore),
            "active_a": self.player_a.active_index,
            "active_b": self.player_b.active_index,
            "lives_a": self.player_a.lives,
            "lives_b": self.player_b.lives,
            "devotion_a": dict(getattr(self.player_a, 'devotion', {})),
            "devotion_b": dict(getattr(self.player_b, 'devotion', {})),
            "vm": vm_state,
        }

    def restore_mutable_state(self, saved: dict) -> None:
        """从 save_mutable_state 恢复可变状态（MCTS 仿真回滚）。"""
        # ── 精灵状态 ──
        idx = 0
        for player in (self.player_a, self.player_b):
            for sprite in player.team:
                s = saved["sprites"][idx]; idx += 1
                sprite.current_hp = s["hp"]
                sprite.energy = s["energy"]
                sprite._modifiers = dict(s["modifiers"])
                sprite._charging = s["charging"]
                sprite._charged_skill_index = s["charged_idx"]
                sprite.counters = dict(s["counters"])
                sprite.first_action = s["first_action"]
                sprite.first_action_battle = s["first_action_battle"]
                sprite.locked_turns = s["locked_turns"]
                sprite.interrupted = s["interrupted"]
                sprite.pending_return = s["pending_return"]
                sprite.extra_skill_use = s["extra_skill_use"]
                # 效果：按位置恢复。先清空再按保存顺序重建，消除 id() 复用风险
                saved_effects = s["effects"]  # list of (e, ttl, stacks, scope, steps?)
                saved_refs = {id(e) for e, *_ in saved_effects}
                # 移除仿真中新增的效果
                for e in list(sprite.active_effects):
                    if id(e) not in saved_refs:
                        sprite.active_effects.remove(e)
                # 恢复保存的效果状态（TTL/stacks/scope/steps）
                for snap in saved_effects:
                    e = snap[0]
                    e.ttl = snap[1]
                    if hasattr(e, 'stacks'):
                        e.stacks = snap[2]
                    if hasattr(e, 'scope'):
                        e.scope = snap[3]
                    if len(snap) > 4:
                        # StatBuffEffect: restore steps
                        if isinstance(e, StatBuffEffect):
                            e.steps = snap[4]
                    if e not in sprite.active_effects:
                        sprite.active_effects.append(e)
                # 技能状态
                if "skill_refs" in s:
                    sprite.skills = list(s["skill_refs"])
                if "skill_states" in s:
                    for sk, state in zip(sprite.skills or [], s["skill_states"]):
                        (
                            modifiers,
                            cooldown,
                            sealed,
                            replaced_by,
                            next_attack_mult,
                            nullified,
                            is_temporary,
                            transmission,
                            element_override,
                            mech_energy_reduction,
                            burst_effects,
                        ) = state
                        sk._modifiers = dict(modifiers)
                        sk.cooldown = cooldown
                        sk.sealed = sealed
                        sk.replaced_by = replaced_by
                        sk.next_attack_mult = next_attack_mult
                        sk.nullified = nullified
                        sk.is_temporary = is_temporary
                        sk._transmission = transmission
                        sk._element_override = element_override
                        sk._mech_energy_reduction = mech_energy_reduction
                        sk._burst_effects = list(burst_effects)
                else:
                    for si, sk in enumerate(sprite.skills or []):
                        if si in s.get("skill_mods", {}):
                            sk._modifiers = dict(s["skill_mods"][si])
                        if si in s.get("skill_cd", {}):
                            sk.cooldown = s["skill_cd"][si]
                        if si in s.get("skill_sealed", {}):
                            sk.sealed = s["skill_sealed"][si]
                # 精灵级可变状态回滚（此前遗漏，MCTS 仿真残留会泄漏到真实对局）
                if "mod_scopes" in s:
                    sprite._mod_scopes = dict(s["mod_scopes"])
                if "pending_mods" in s:
                    sprite._pending_modifiers = list(s["pending_mods"])
                if "pending_effs" in s:
                    sprite._pending_effects = [
                        (copy(e), d) for e, d in s["pending_effs"]
                    ]
                if "trait_suppressed" in s:
                    sprite._trait_suppressed = s["trait_suppressed"]
                # 效果缓存失效：active_effects 已恢复为新列表，缓存必须重建
                if hasattr(sprite, '_invalidate_effects_cache'):
                    sprite._invalidate_effects_cache()
        # ── 印记：完整恢复（MCTS 仿真可能新增/移除 MarkEffect 对象） ──
        self.globals.mark_effects = saved["marks"]
        # ── 全局状态 ──
        self.turn = saved["turn"]
        self.winner = saved["winner"]
        # 截断 MCTS 仿真期间追加的日志
        if "log_len" in saved:
            del self.log[saved["log_len"]:]
        self.globals.weather = saved["weather"]
        self.globals.weather_turns = saved["weather_turns"]
        self.team_counters = saved["team_counters"]
        self.pending_effects = saved["pending_effects"]
        self.scheduled_effects = saved["scheduled_effects"]
        self.pending_escape = saved["pending_escape"]
        self._borrowed_restore = saved["borrowed_restore"]
        self._wish_restore = saved["wish_restore"]
        self.player_a.active_index = saved["active_a"]
        self.player_b.active_index = saved["active_b"]
        self.player_a.lives = saved["lives_a"]
        self.player_b.lives = saved["lives_b"]
        if hasattr(self.player_a, 'devotion'):
            self.player_a.devotion = saved["devotion_a"]
        if hasattr(self.player_b, 'devotion'):
            self.player_b.devotion = saved["devotion_b"]
        # ── VM 引擎状态 ──
        vs = saved["vm"]
        self._vm_engine._burst_effects = vs["burst_effects"]
        self._vm_engine._burst_names = vs["burst_names"]
        self._vm_engine._counter_values = vs["counter_values"]
        self._vm_engine._skill_history = vs["skill_history"]
        self._vm_engine._skill_tags = vs["skill_tags"]
        self._ctx_team_cache.clear()

    def __init__(
        self, player_a: Player, player_b: Player,
        weather: str = '', verbose: bool = True,
    ):
        self.player_a = player_a
        self.player_b = player_b
        self.globals = GlobalEffects()
        if weather:
            self.globals.set_weather(weather)
        self.turn: int = 0
        self.log: list[RoundRecord] = []
        self.winner: str | None = None
        self._resolver = SkillResolver()
        self._agent_a: Agent | None = None
        self._agent_b: Agent | None = None
        self.verbose = verbose
        self._borrowed_restore: dict[tuple[str, int], Skill] = {}
        self._wish_restore: dict[tuple[str, int], BattleSkill] = {}   # 愿力一回合后还原
        # VM engine + skill cache
        from backend.engine.battle import BattleVMEngine
        from backend.engine.snapshot import build_ctx as _build_ctx
        from backend.vm.compiler.skill_compiler import SkillCompiler
        self._vm_engine = BattleVMEngine()
        self._build_ctx = _build_ctx  # stored for use in execute_turn etc.
        self._skill_compiler = SkillCompiler()
        self._skill_cache: dict[str, CompiledSkill] = {}
        self.team_counters: dict[str, dict[str, int]] = {'A': {}, 'B': {}}  # pre-entry accumulators
        self.pending_effects: dict[str, list] = {'A': [], 'B': []}  # leave-buff → next entry
        self.scheduled_effects: list[dict] = []  # 延时效果队列 [{turn, phase, effects, ...}]
        self.pending_escape: dict | None = None  # {team, inherit, urgent} 等待脱离处理
        self.species_db = None  # 由 SimFactory 注入，供形态变换查询
        self.skill_loader = None  # 由 SimFactory 注入，供形态变换加载技能
        self._snapshots: dict[int, dict] = {}
        # ── 回合内 Ctx 构建缓存 ──
        # 每个回合 team 级聚合值（fainted 计数、队伍元素、萌化层数）稳定不变，
        # 缓存它们避免 _make_ctx 每次调用都遍历 12 只精灵。
        self._ctx_team_cache: dict[str, dict] = {}

        # ── 回合 0: 首发精灵 entry 特性 ──
        from backend.sim.traits import dispatch_entry
        for team, player in (('A', player_a), ('B', player_b)):
            try:
                sprite = player.active
            except (IndexError, AttributeError):
                continue
            if sprite is None:
                continue
            dispatch_entry(sprite, self, team)
            opp_team = 'B' if team == 'A' else 'A'
            opp = None
            with contextlib.suppress(IndexError, AttributeError):
                opp = self.get_player(opp_team).active
            ctx_entry = self._make_ctx(
                sprite, opp, None, None, self.globals,
                team=team, turn=self.turn,
            )
            self._vm_engine.fire_trigger(
                "post_entry", ctx_entry, sprite, opp, self.globals,
                team=team, battle=self,
            )

    def lookup_species(self, name: str, form: str = ''):
        """形态变换时查询目标物种。由 SimFactory 注入数据库后可用。"""
        if self.species_db is None:
            return None
        return self.species_db.get(name, form)

    def lookup_species_by_number(self, number: str, form: str = ''):
        """按精灵编号查找形态（萌化退化用）。"""
        if self.species_db is None:
            return None
        return self.species_db.lookup_by_number(number, form)

    def build_skills(self, skill_names: list[str]) -> list:
        """形态变换时构建技能列表。由 SimFactory 注入后可用。"""
        if self.skill_loader is None:
            return []
        return self.skill_loader(skill_names)

    @property
    def is_finished(self) -> bool:
        return self.winner is not None or self.turn >= self.MAX_TURNS

    def get_player(self, team: str) -> Player:
        return self.player_a if team == 'A' else self.player_b

    def get_opponent(self, team: str) -> Player:
        return self.player_b if team == 'A' else self.player_a

    def inc_team_counter(self, team: str, key: str, amount: int = 1) -> None:
        """增量队伍级事件计数器（供 pre-entry accumulator 特性使用）。"""
        d = self.team_counters[team]
        d[key] = d.get(key, 0) + amount

    def get_team_counter(self, team: str, key: str) -> int:
        """读取队伍级事件计数器。"""
        return self.team_counters.get(team, {}).get(key, 0)

    def _make_ctx(self, self_sprite, opp_sprite, self_skill=None, opp_skill=None,
                  globals_=None, *, team: str = "A", **kwargs):
        """Build a Ctx with team_counters, devotion, fainted pre-filled from battle state.

        Per-turn caching: team-level aggregates (fainted count, team elements, moe stacks)
        are stable within a turn and computed once per team. This avoids iterating all
        12 sprites on every _make_ctx call (~4-6 times per skill execution).
        """
        if globals_ is None:
            globals_ = self.globals
        opp_team = 'B' if team == 'A' else 'A'
        own_player = self.get_player(team)
        opp_player = self.get_player(opp_team)

        # ── Per-turn team cache ──
        if team not in self._ctx_team_cache:
            fainted_own = sum(1 for s in own_player.team if s.is_fainted)
            fainted_opp = sum(1 for s in opp_player.team if s.is_fainted)
            team_elements_own = frozenset(
                e for s in own_player.team for e in s.species.elements)
            team_elements_opp = frozenset(
                e for s in opp_player.team for e in s.species.elements)
            self._ctx_team_cache[team] = {
                "fainted_own": fainted_own,
                "fainted_opp": fainted_opp,
                "team_elements_own": team_elements_own,
                "team_elements_opp": team_elements_opp,
            }
        cached = self._ctx_team_cache[team]

        # Count 萌化 stacks on own team sprites (excluding self) — per-call still,
        # since self_sprite changes between calls within a turn.
        moe_team_stacks = 0
        for s in own_player.team:
            if s is not self_sprite and not s.is_fainted:
                for e in getattr(s, 'active_effects', []):
                    if getattr(e, 'name', '') == '萌化':
                        moe_team_stacks += getattr(e, 'stacks', 0)

        if getattr(self, '_mcts_sim', False):
            team_counters_own = self.team_counters.get(team, {})
            team_counters_opp = self.team_counters.get(opp_team, {})
            devotion_own = getattr(own_player, 'devotion', {})
            devotion_opp = getattr(opp_player, 'devotion', {})
        else:
            team_counters_own = dict(self.team_counters.get(team, {}))
            team_counters_opp = dict(self.team_counters.get(opp_team, {}))
            devotion_own = dict(getattr(own_player, 'devotion', {}))
            devotion_opp = dict(getattr(opp_player, 'devotion', {}))

        return self._build_ctx(
            self_sprite, opp_sprite, self_skill, opp_skill, globals_,
            team=team,
            team_counters_own=team_counters_own,
            team_counters_opp=team_counters_opp,
            devotion_own=devotion_own,
            devotion_opp=devotion_opp,
            fainted_own=cached["fainted_own"],
            fainted_opp=cached["fainted_opp"],
            lives_own=getattr(own_player, 'lives', 5),
            lives_opp=getattr(opp_player, 'lives', 5),
            team_elements_own=cached["team_elements_own"],
            team_elements_opp=cached["team_elements_opp"],
            moe_team_stacks=moe_team_stacks,
            **kwargs,
        )

    def _get_agent(self, team: str) -> Agent:
        return self._agent_a if team == 'A' else self._agent_b  # type: ignore

    # ═══════════════════════════════════════════════════════════════
    # 回合主入口
    # ═══════════════════════════════════════════════════════════════

    def execute_turn(
        self,
        agent_a: Agent,
        agent_b: Agent,
        *,
        fixed_action_a: Action | None = None,
        fixed_action_b: Action | None = None,
    ) -> RoundRecord:
        self.turn += 1
        self.save_snapshot()  # key = turn number AFTER increment, matches frontend
        self._agent_a = agent_a
        self._agent_b = agent_b
        # 新回合一并清空 Ctx 缓存（key 含 turn 号自动失效，但清掉优化内存）
        self._ctx_team_cache.clear()

        # 每回合开始时，清空双方精灵的"每回合临时" VM modifier
        # （power_mult/damage_mult 等由 VM 每回合重新注入）
        # combo/combo_mult 等跨回合持久键不清空 sprite 级别（特性加成需保留），
        # 但技能级 _modifiers 的 combo/priority 每次使用由 effects 重新产生，必须清空。
        for team in (self.player_a.team, self.player_b.team):
            for sprite in team:
                sprite.interrupted = False
                for key in _PER_TURN_KEYS:
                    sprite._modifiers.pop(key, None)
                for skill in (sprite.skills or []):
                    for key in _SKILL_PER_TURN_KEYS:
                        skill._modifiers.pop(key, None)
        # Re-apply trait direct modifiers cleared by _PER_TURN_KEYS
        mark_mod_a = self.globals.mark_energy_mod("A")
        mark_mod_b = self.globals.mark_energy_mod("B")
        mark_mods: dict[int, int] | None = None
        if mark_mod_a or mark_mod_b:
            mark_mods = {}
            if mark_mod_a:
                for s in self.player_a.team:
                    mark_mods[id(s)] = mark_mod_a
            if mark_mod_b:
                for s in self.player_b.team:
                    mark_mods[id(s)] = mark_mod_b
        self._vm_engine.trait_loader.reapply_all_direct_mods(
            itertools.chain(self.player_a.team, self.player_b.team), mark_mods)
        # Restore permanent skill-scoped modifiers (observer-triggered
        # power_mod with scope=permanent, e.g. 洄游 energy_cost -1).
        for team in (self.player_a.team, self.player_b.team):
            for sprite in team:
                for skill in (sprite.skills or []):
                    skill.load_permanent_mods(sprite._modifiers)

        s_a = self.player_a.active
        s_b = self.player_b.active
        mcts_sim = getattr(self, '_mcts_sim', False)

        if mcts_sim:
            rec = _HeadlessRoundRecord(self.turn)
        else:
            rec = RoundRecord(
                turn=self.turn,
                weather=self.globals.weather,
                sprite_a=s_a.name,
                sprite_b=s_b.name,
            )

        # 1. 回合开始阶段（已内含 >>>PHASE:TURN_START 标记）
        ts_events = self._phase_turn_start()
        if not mcts_sim:
            rec.turn_start_events += ts_events

        # 2. 行动选择阶段（道具不互见）
        if fixed_action_a is None:
            action_a, item_a = self._select_action(agent_a, 'A')
        else:
            action_a, item_a = fixed_action_a, ''
        if fixed_action_b is None:
            action_b, item_b = self._select_action(agent_b, 'B')
        else:
            action_b, item_b = fixed_action_b, ''

        # 构建 ActionRecord
        if not mcts_sim:
            rec.action_a = self._build_action_record('A', action_a, item_a)
            rec.action_b = self._build_action_record('B', action_b, item_b)

        # 3. 行动结算阶段
        self._phase_resolve(action_a, action_b, rec)

        # 4. 回合结束阶段（已内含 >>>PHASE:TURN_END 标记）
        te_events = self._phase_turn_end()
        if not mcts_sim:
            rec.turn_end_events = te_events

        if mcts_sim:
            return rec

        # ── 组装 frontend events ──
        a_short = _rr_action_short(rec.action_a)
        b_short = _rr_action_short(rec.action_b)
        header = f'[回合{self.turn}] {s_a.name}：{a_short} | {s_b.name}：{b_short}'
        rec._header = header

        self.log.append(rec)
        return rec

    # ═══════════════════════════════════════════════════════════════
    # 回溯 (Snapshot / Restore)
    # ═══════════════════════════════════════════════════════════════

    def save_snapshot(self) -> None:
        """保存当前回合的快照（用于回溯）。

        MCTS 仿真期间跳过 — 仿真不需要回溯功能，
        且 battle_to_dict 序列化开销占 MCTS 总耗时 ~17%。
        """
        if getattr(self, '_mcts_sim', False):
            return
        from backend.engine.serializer import battle_to_dict
        self._snapshots[self.turn] = battle_to_dict(self)

    def restore_snapshot(self, turn: int) -> None:
        """恢复到指定回合开始前的状态。"""
        if turn not in self._snapshots:
            raise ValueError(
                f"快照不存在: 回合{turn}。"
                f"可用回合: {sorted(self._snapshots.keys())}"
            )
        from backend.engine.serializer import battle_from_dict

        snapshot = self._snapshots[turn]
        # Save snapshots before __dict__ update overwrites them
        old_snapshots = self._snapshots
        restored = battle_from_dict(
            snapshot, self.species_db, self.skill_loader,
        )
        self.__dict__.update(restored.__dict__)
        self._snapshots = {
            t: s for t, s in old_snapshots.items() if t <= turn
        }

    def clear_snapshots(self) -> None:
        """清除所有快照释放内存。"""
        self._snapshots.clear()

    @property
    def snapshots(self) -> dict[int, dict]:
        """返回所有快照（只读视图）。"""
        return dict(self._snapshots)

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: 回合开始
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_start(self) -> list[str]:
        """回合开始效果（委托给 TurnPipeline）。"""
        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = _NO_EVENTS if mcts_sim else []

        # 延迟效果结算：双方精灵 process_pending_effects
        for team in ('A', 'B'):
            sprite = self.get_player(team).active
            if not sprite.is_fainted:
                activated = sprite.process_pending_effects()
                if not mcts_sim:
                    for eff in activated:
                        events.append(f'{sprite.name} 延迟效果生效: {eff.name}')

        from .pipeline import TurnPipeline
        events += TurnPipeline.execute_turn_start(self)
        return events

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: 行动选择（含私有道具循环）
    # ═══════════════════════════════════════════════════════════════

    def _select_action(self, agent: Agent, team: str) -> tuple[Action, str]:
        """道具循环：使用道具后重新选择。返回 (最终行动, 道具名)。"""
        item_used = ''
        for _ in range(8):  # 安全上限：防止道具无限循环卡死
            action = agent.choose_action(self)
            if action.kind == 'item':
                item_used = self._resolve_item(team)
                continue
            return action, item_used
        # 道具使用超限：退回聚能兜底
        return Action('gather'), item_used

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: 行动结算（优先级排序）
    # ═══════════════════════════════════════════════════════════════

    def _phase_resolve(self, action_a: Action, action_b: Action, record: RoundRecord) -> list[str]:
        """执行结算，直接填充 record.action_a / action_b / faint_check_events。"""
        a_kind = action_a.kind
        b_kind = action_b.kind

        # 双方换宠 → 随机先后
        if a_kind == 'switch' and b_kind == 'switch':
            if random.random() < 0.5:
                record.action_a.events = self._resolve_switch('A', action_a)
                if not self.is_finished:
                    record.action_b.events = self._resolve_switch('B', action_b)
            else:
                record.action_b.events = self._resolve_switch('B', action_b)
                if not self.is_finished:
                    record.action_a.events = self._resolve_switch('A', action_a)
            return []

        # 单方换宠 + 单方技能/聚能 → 先换宠，后技能（含迅捷自动出招）
        if a_kind == 'switch':
            record.action_a.events = self._resolve_switch('A', action_a)
            record.first_team = 'A'
            if not self.is_finished and not self.player_b.active.is_fainted:
                self._resolve_after_switch('A', 'B', action_b, record)
            return []

        if b_kind == 'switch':
            record.action_b.events = self._resolve_switch('B', action_b)
            record.first_team = 'B'
            if not self.is_finished and not self.player_a.active.is_fainted:
                self._resolve_after_switch('B', 'A', action_a, record)
            return []

        # 双方技能/聚能 → 优先级判定
        return self._resolve_both_skills(action_a, action_b, record)

    # ── 双方技能/聚能 ──

    def _resolve_both_skills(self, action_a: Action, action_b: Action, record: RoundRecord) -> list[str]:
        """执行双方技能，直接填充 record.action_a / action_b / faint_check_events。"""
        ar = {'A': record.action_a, 'B': record.action_b}
        s_a = self.player_a.active
        s_b = self.player_b.active

        skill_a = self._get_skill('A', action_a)
        skill_b = self._get_skill('B', action_b)

        # 应对判定（双向检查，冷却中的技能视为未使用，不能应对）
        counter_a = False
        counter_b = False
        if skill_a and skill_b:
            if skill_a.cooldown <= 0:
                counter_a = SkillResolver.resolve_counter(skill_b, skill_a)
            if skill_b.cooldown <= 0:
                counter_b = SkillResolver.resolve_counter(skill_a, skill_b)
        countered = counter_a or counter_b

        # countered_skill:  被我方反击的对方技能（用于 reflect_damage / interrupt）
        # countering_skill: 反击了我方的对方技能（用于 damage_reduction 注入）
        countered_skill_a = skill_b if counter_a else None
        countered_skill_b = skill_a if counter_b else None
        countering_skill_a = skill_b if counter_b else None
        countering_skill_b = skill_a if counter_a else None

        if countered:
            # 应对成功 → A 先执行，力竭中断 B
            ar['A'].events = self._execute_single_action(
                'A', action_a, is_countered=counter_b,
                countered_skill=countered_skill_a,
                countering_skill=countering_skill_a, is_first=True,
            )
            self._check_faint_interrupt('A', ar['A'].events)
            self._check_faint_interrupt('B', ar['A'].events)
            if not self.is_finished:
                b_sprite_now = self.get_player('B').active
                if not b_sprite_now.is_fainted and b_sprite_now is s_b:
                    ar['B'].events = self._execute_single_action(
                        'B', action_b, is_countered=counter_a,
                        countered_skill=countered_skill_b,
                        countering_skill=countering_skill_b, is_first=True,
                    )
                    self._check_faint_interrupt('A', ar['B'].events)
                    self._check_faint_interrupt('B', ar['B'].events)
            # trait: counter success hooks → 附加到对应 action
            if counter_a:
                self.inc_team_counter('A', 'counter_success')
                # Observer: post_counter
                ctx_ca = self._make_ctx(s_a, s_b, skill_a, None, self.globals, team='A', turn=self.turn, counter_succeeded=True)
                ar['A'].events += self._vm_engine.fire_trigger("post_counter", ctx_ca, s_a, s_b, self.globals, team='A', battle=self)
            if counter_b:
                self.inc_team_counter('B', 'counter_success')
                # Observer: post_counter
                ctx_cb = self._make_ctx(s_b, s_a, skill_b, None, self.globals, team='B', turn=self.turn, counter_succeeded=True)
                ar['B'].events += self._vm_engine.fire_trigger("post_counter", ctx_cb, s_b, s_a, self.globals, team='B', battle=self)
            return []

        # 无应对 → 按优先级先后执行
        priority_a = self._effective_priority('A', action_a)
        priority_b = self._effective_priority('B', action_b)

        if priority_a > priority_b:
            first_team, first_action = 'A', action_a
            second_team, second_action = 'B', action_b
        elif priority_b > priority_a:
            first_team, first_action = 'B', action_b
            second_team, second_action = 'A', action_a
        else:
            # 优先级相等 → 比速度
            speed_a = s_a.effective_stat('speed') - self.globals.mark_speed_penalty('A')
            speed_b = s_b.effective_stat('speed') - self.globals.mark_speed_penalty('B')
            if speed_a > speed_b:
                first_team, first_action = 'A', action_a
                second_team, second_action = 'B', action_b
            elif speed_b > speed_a:
                first_team, first_action = 'B', action_b
                second_team, second_action = 'A', action_a
            else:
                if random.random() < 0.5:
                    first_team, first_action = 'A', action_a
                    second_team, second_action = 'B', action_b
                else:
                    first_team, first_action = 'B', action_b
                    second_team, second_action = 'A', action_a

        # 记录先手方
        record.first_team = first_team

        # Opponent skills for ctx (non-countered path)
        opp_skill_for_first = skill_b if first_team == 'A' else skill_a
        opp_skill_for_second = skill_b if second_team == 'A' else skill_a

        # 先手执行 (is_first=True)
        second_sprite_before = self.get_player(second_team).active
        ar[first_team].events = self._execute_single_action(
            first_team, first_action, is_first=True,
            opp_skill=opp_skill_for_first,
        )
        self._check_faint_interrupt(first_team, ar[first_team].events)
        self._check_faint_interrupt(second_team, ar[first_team].events)

        # 后手执行 (is_first=False)，力竭中断或已换宠则跳过
        if not self.is_finished:
            second_sprite_now = self.get_player(second_team).active
            if not second_sprite_now.is_fainted and second_sprite_now is second_sprite_before:
                ar[second_team].events = self._execute_single_action(
                    second_team, second_action, is_first=False,
                    opp_skill=opp_skill_for_second,
                )
                self._check_faint_interrupt(second_team, ar[second_team].events)

        return []

    # ── 单方行动执行 ──

    def _resolve_single_action(self, team: str, action: Action,
                               opponent_switched: bool = False) -> list[str]:
        """执行单方行动（换宠已处理，此处仅技能/聚能）。
        此路径下本侧是唯一技能方 → is_first=True。"""
        if action.kind in ('skill', 'gather'):
            return self._execute_single_action(team, action, is_first=True,
                                               opponent_switched=opponent_switched)
        return []

    def _execute_single_action(
        self, team: str, action: Action,
        is_countered: bool = False,
        countered_skill: BattleSkill | None = None,
        countering_skill: BattleSkill | None = None,
        is_first: bool = False,
        opponent_switched: bool = False,
        opp_skill: BattleSkill | None = None,
    ) -> list[str]:
        """执行单个玩家的技能/聚能行动。"""
        return self._execute_skill_vm(
            team, action,
            is_countered=is_countered,
            countered_skill=countered_skill,
            countering_skill=countering_skill,
            is_first=is_first,
            opponent_switched=opponent_switched,
            opp_skill=opp_skill,
        )

    # ── VM 技能执行 ──

    def _get_skill_record(self, skill_name: str) -> CompiledSkill:
        """Load and cache a CompiledSkill from RISC IR JSON.

        Uses two-level cache:
        1. Module-level global cache (shared across all Battle instances)
        2. Instance-level cache (for skills compiled before the global cache existed)
        """
        import json
        import os
        # ── 全局缓存优先（跨 Battle 共享，消除重复磁盘 I/O） ──
        if skill_name in Battle._global_skill_cache:
            record = Battle._global_skill_cache[skill_name]
            # Mirror to instance cache for consistency
            self._skill_cache[skill_name] = record
            return record
        if skill_name in self._skill_cache:
            return self._skill_cache[skill_name]
        path = os.path.join('data', 'skills', f'{skill_name}.json')
        if not os.path.exists(path):
            raise FileNotFoundError(f'Skill JSON not found: {path}')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        record = self._skill_compiler.compile(data)
        Battle._global_skill_cache[skill_name] = record
        self._skill_cache[skill_name] = record
        return record

    def _execute_skill_vm(
        self, team: str, action: Action,
        is_countered: bool = False,
        countered_skill: BattleSkill | None = None,
        countering_skill: BattleSkill | None = None,
        is_first: bool = False,
        opponent_switched: bool = False,
        opp_skill: BattleSkill | None = None,
    ) -> list[str]:
        """VM-based skill execution — replaces SkillPipeline L0-L5."""
        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = _NO_EVENTS if mcts_sim else []
        player = self.get_player(team)
        opponent = self.get_player('B' if team == 'A' else 'A')
        user = player.active
        target = opponent.active

        if user.is_fainted:
            return events

        # ── 蓄力中禁止聚能 ──
        if getattr(user, '_charging', False):
            if not mcts_sim:
                events.append(f'{user.name} 蓄力中无法聚能')
            return events

        # ── 聚能 ──
        if action.kind == 'gather':
            gained = user.gain_energy(5)
            user.first_action = False
            user.first_action_battle = False
            user.inc_counter('times_gathered')
            if not mcts_sim:
                events.append(f'{user.name} 聚能+{gained}E(→{user.energy})')
            opp_team = 'B' if team == 'A' else 'A'
            self.inc_team_counter(opp_team, 'enemy_gather')
            # Fire post_energy_change for traits like 囤积
            if gained > 0 and self._vm_engine.registry.has_candidates("post_energy_change"):
                gather_ctx = self._make_ctx(
                    user, target, None, None, self.globals,
                    team=team, turn=self.turn,
                    energy_changed_of="sprite_self",
                )
                gather_ctx.energy_delta_self = gained
                events += self._vm_engine.fire_trigger(
                    "post_energy_change", gather_ctx, user, target, self.globals,
                    team=team, battle=self,
                )
            return events

        if action.kind != 'skill' or action.skill_index is None:
            return events

        bs = self._get_skill(team, action)
        if bs is None:
            if not mcts_sim:
                events.append(f'[错误] {user.name} 无技能[{action.skill_index}]')
            return events

        # 所有 gate 检查结果都归属到此 action 内（由 to_frontend_events 统一包裹）


        # ═══ Gate: 冷却 ═══
        if bs.cooldown > 0:
            if not mcts_sim:
                events.append(f'[冷却中] {user.name} {bs.name} 还需{bs.cooldown}回合冷却')
            return events

        # ═══ Gate: 蓄力 ═══
        charge_result = self._gate_charge_vm(user, bs, action)
        if charge_result is True:
            if not mcts_sim:
                events.append(f'{user.name} 开始蓄力')
            charge_ctx = self._make_ctx(
                user, target, None, None, self.globals,
                team=team, turn=self.turn,
            )
            events += self._vm_engine.fire_trigger(
                "post_charge", charge_ctx, user, target, self.globals,
                team=team, battle=self,
            )
            return events  # entering charge
        if charge_result is False:
            return events  # blocked

        # ═══ 应对日志 ═══
        if is_countered and countering_skill and not mcts_sim:
            opp_sprite = opponent.active
            events.append(
                f'{opp_sprite.name}应对{user.name}：{user.name}使用了'
                f'{bs.name}，但被{opp_sprite.name}（{countering_skill.name}）应对了！'
            )

        # ═══ Consume on_next pending modifiers ═══
        # Must happen BEFORE energy gate so on_next energy_cost modifiers
        # are consumed and visible to the energy payment calculation.
        if user._pending_modifiers:
            skill_type = getattr(bs.base, 'skill_type', '')
            consumed = user.consume_pending_modifiers(skill_type)
            for m in consumed:
                if mcts_sim:
                    continue
                if m.stat == 'energy_cost':
                    events.append(f'{user.name} 触发待机效果: 能耗{m.value:+}')
                    continue
                label = {'power': '威力', 'combo': '连击'}.get(m.stat, m.stat)
                events.append(f'{user.name} 触发待机效果: {label}{m.value:+}')

        # ═══ Devotion consumption ═══
        devotion_triggered = False
        # Load skill record early so devotion can affect energy cost
        try:
            record = self._get_skill_record(bs.base.name)
        except FileNotFoundError:
            if not mcts_sim:
                events.append(f'[错误] 技能JSON未找到: {bs.base.name}')
            return events

        if getattr(record, 'use_devotion', False):
            devotion_stacks = dict(player.devotion)
            if devotion_stacks:
                from backend.engine.devotion_config import DEVOTION_TYPES
                from backend.vm.journal import AbnormalChange

                # Save pre-devotion modifier values so we can restore them
                # after the skill (devotion effects are per-skill-use only).
                _devotion_saved = {}
                for _key in ("combo", "power", "life_drain", "energy_cost"):
                    if _key in user._modifiers:
                        _devotion_saved[_key] = user._modifiers[_key]

                abnormal_mods: list = []
                for dname, dcount in devotion_stacks.items():
                    if dcount <= 0:
                        continue
                    dtype = DEVOTION_TYPES.get(dname)
                    if not dtype:
                        continue
                    if "combo" in dtype:
                        user._modifiers["combo"] = user._modifiers.get("combo", 0) + dtype["combo"] * dcount
                    if "energy_cost" in dtype:
                        user._modifiers["energy_cost"] = user._modifiers.get("energy_cost", 0) + dtype["energy_cost"] * dcount
                    if "power" in dtype:
                        user._modifiers["power"] = user._modifiers.get("power", 0) + dtype["power"] * dcount
                    if "life_drain" in dtype:
                        user._modifiers["life_drain"] = user._modifiers.get("life_drain", 0) + dtype["life_drain"] * dcount
                    if "abnormal" in dtype:
                        ab = dtype["abnormal"]
                        abnormal_mods.append(AbnormalChange(
                            target="sprite_opp", name=ab["name"],
                            delta=ab["stacks"] * dcount,
                        ))

                if abnormal_mods:
                    from backend.engine.replayer import JournalReplayer as _DevR
                    _dev_r = _DevR(user, target, self.globals,
                                   self._vm_engine.registry, team=team, battle=self)
                    devotion_events = _dev_r.replay(abnormal_mods)
                    events.extend(devotion_events)

                player.devotion.clear()
                devotion_triggered = True

        # ═══ Burst trigger: first_action + skill has burst effects ═══
        if user.first_action and bs._burst_effects:
            burst_ctx = self._make_ctx(
                user, target, None, None, self.globals,
                team=team, turn=self.turn,
            )
            burst_journal = self._vm_engine.execute_effects(burst_ctx, bs._burst_effects)
            from backend.engine.replayer import JournalReplayer as _BurstReplayer
            _burst_r = _BurstReplayer(user, target, self.globals, self._vm_engine.registry, team=team, battle=self)
            burst_events = _burst_r.replay(burst_journal)
            events.extend(burst_events)
            if not mcts_sim:
                events.append(f'💥 {user.name} {bs.name} 迸发!')

        # ═══ Gate: 能量支付 ═══
        cost = bs.energy_cost
        # Energy cost modifier from VM pipeline (accumulated via ModifierInjection)
        ec_mod = user._modifiers.get("energy_cost", 0)
        if ec_mod:
            cost += round(ec_mod)
        # Energy cost multiplier from VM pipeline
        ec_mult = user._modifiers.get("energy_cost_mult", 0)
        if ec_mult:
            cost = round(cost * (1.0 + ec_mult))
        # 轴承支撑被动
        si = action.skill_index
        if si is not None:
            for offset in (-1, 1):
                ni = si + offset
                if 0 <= ni < len(user.skills) and user.skills[ni].name == '轴承支撑':
                    cost -= 1
                    break
        # Weather energy cost modifier
        if hasattr(bs.base, 'element') and bs.base.element and self.globals.weather:
            cost = round(cost * self.globals.weather_energy_mod(bs.base.element))
        # 印记能耗减免
        cost -= self.globals.mark_energy_mod(team)
        cost = max(0, cost)
        if cost > 0:
            if user.energy >= cost:
                user.lose_energy(cost)
            else:
                # 石头大餐：能量不足时消耗HP代替能量
                blood_price = user._modifiers.get("blood_price", 0)
                if blood_price > 0:
                    deficit = cost - user.energy
                    hp_cost = round(user.max_hp * blood_price * deficit)
                    if user.current_hp > hp_cost:
                        user.lose_energy(user.energy)
                        user.take_damage(hp_cost)
                        if not mcts_sim:
                            events.append(f'{user.name} 消耗{hp_cost}HP代替{deficit}E')
                    else:
                        if not mcts_sim:
                            events.append(f'{user.name} HP不足无法代替能量')
                        return events
                else:
                    if not mcts_sim:
                        events.append(f'{user.name} E不足{user.energy}<{cost}')
                    return events

        # Clear one-shot on_next energy_cost modifier after consumption.
        # Only on_next writes to sprite._modifiers["energy_cost"];
        # non-on_next modifiers write to BattleSkill._modifiers (per-skill).
        user._modifiers.pop("energy_cost", None)

        # Fire post_energy_change for traits like 囤积
        if cost > 0 and self._vm_engine.registry.has_candidates("post_energy_change"):
            energy_ctx = self._make_ctx(
                user, target, None, None, self.globals,
                team=team, turn=self.turn,
                energy_changed_of="sprite_self",
            )
            self._vm_engine.fire_trigger(
                "post_energy_change", energy_ctx, user, target, self.globals,
                team=team, battle=self,
            )

        # ═══ Gate: 打断 ═══
        if user.interrupted:
            bs.nullified = True
            if not mcts_sim:
                events.append(f'{user.name} 被打断，技能无效')
            return events

        user.inc_counter(f'skill_used:{bs.name}')
        user.inc_counter('skills_used')

        # ═══ 应对效果注入：应对方直接效果在伤害计算前生效 ═══
        if is_countered and countering_skill:
            opp_team = 'B' if team == 'A' else 'A'
            try:
                counter_record = self._get_skill_record(countering_skill.base.name)
                counter_effects = self._vm_engine._get_effects(counter_record)
                direct_effects = [e for e in counter_effects
                                  if not (hasattr(e, 'when') and e.when) and 'when' not in (e if isinstance(e, dict) else {})]
                if direct_effects:
                    opp_ctx = self._build_ctx(
                        target, user, counter_record, None, self.globals,
                        team=opp_team, turn=self.turn,
                    )
                    counter_journal = self._vm_engine.execute_effects(opp_ctx, direct_effects)
                    from backend.engine.replayer import JournalReplayer as _CR
                    _cr = _CR(target, user, self.globals, self._vm_engine.registry, team=opp_team, battle=self)
                    events += _cr.replay(counter_journal)
            except Exception:
                pass

        # ═══ Load CompiledSkill + execute VM ═══
        # (record already loaded above for devotion check)

        opp_skill = countered_skill or countering_skill or opp_skill
        opp_team = 'B' if team == 'A' else 'A'
        if getattr(self, '_mcts_sim', False):
            team_counters_own = self.team_counters.get(team, {})
            team_counters_opp = self.team_counters.get(opp_team, {})
        else:
            team_counters_own = dict(self.team_counters.get(team, {}))
            team_counters_opp = dict(self.team_counters.get(opp_team, {}))

        result = self._vm_engine.execute_skill(
            user, target,
            record, opp_skill, self.globals,
            turn=self.turn, is_first=is_first,
            team=team,
            opp_switched=opponent_switched,
            was_countered=is_countered,
            counter_succeeded=countered_skill is not None,
            skill_index=action.skill_index or 0,
            species_lookup=self.lookup_species_by_number,
            battle_skill=bs,
            team_counters_own=team_counters_own,
            team_counters_opp=team_counters_opp,
            battle=self,
            devotion_triggered=devotion_triggered,
        )
        events.extend(result.events)

        # ═══ Clean up devotion modifiers after skill ═══
        # Restore pre-devotion modifier values — devotion effects are
        # per-skill-use only and must not persist.
        if devotion_triggered:
            for _key in ("combo", "life_drain", "power", "energy_cost"):
                if _key in _devotion_saved:
                    user._modifiers[_key] = _devotion_saved[_key]
                else:
                    user._modifiers.pop(_key, None)

        # ═══ Escape / Return handling ═══
        from backend.vm.journal import Escape
        for mutation in result.journal:
            if isinstance(mutation, Escape):
                if mutation.urgent:
                    # 紧急脱离：随机自动选择替补
                    if mutation.inherit:
                        self._handle_escape_inherit(team, user, events, urgent=True)
                    else:
                        self._handle_escape(team, user, events, urgent=True)
                else:
                    # 普通脱离：等待玩家选择替补 (API 检测 pending_escape)
                    self.pending_escape = {
                        "team": team,
                        "inherit": mutation.inherit,
                        "urgent": False,
                        "user_name": user.name,
                    }
                    if not mcts_sim:
                        events.append(f'{user.name} 准备脱离(请选择替补)')
                break  # Only first escape matters

        # ═══ Post-execution ═══
        user.first_action = False
        user.first_action_battle = False
        # Burst extension (连续负荷 etc.): extend first_action for one more turn
        remaining = user._modifiers.get("_burst_extended", 0)
        if remaining > 0:
            user.first_action = True
            user._modifiers["_burst_extended"] = remaining - 1
            if not mcts_sim:
                events.append(f'{user.name} 迸发延长({remaining - 1}回剩余)')
        # Clear "charged" state — consumed after the sprite acts
        user.remove_effect("charged", "state")

        # Defense skill cooldown
        if bs.base.is_defense:
            bs.cooldown = 2

        # Counters
        if record.element:
            self.inc_team_counter(team, f'element:{record.element}')
        if record.skill_type == '防御':
            self.inc_team_counter(team, 'defense_skill')
        elif record.skill_type not in ('物攻', '魔攻', '动态攻击'):
            self.inc_team_counter(team, 'status_skill')

        return events

    def _gate_charge_vm(self, user: Sprite, bs: BattleSkill, action: Action) -> bool | None:
        """VM-compatible charge gate. Reads from RISC IR effects.
        True=entering charge, False=blocked, None=pass through.
        """
        # Load record to check for charge opcode
        try:
            record = self._get_skill_record(bs.base.name)
        except FileNotFoundError:
            return None
        has_charge = any(_has_charge_op(e) for e in record.effects)

        is_charging = getattr(user, '_charging', False)
        charged_idx = getattr(user, '_charged_skill_index', -1)

        if is_charging and has_charge and action.skill_index == charged_idx:
            user._charging = False
            user._charged_skill_index = -1
            # Remove "charging" effect, add "charged" for condition checks
            user.remove_effect("charging", "state")
            user.add_effect(StateEffect(
                name="charged", state_type="charged", scope="battlefield", source="charge",
            ))
            return None  # charge released

        if is_charging:
            if bs.base.usable_while_charging or user._modifiers.get("charge_any_skill", 0) > 0:
                # Cancel charging — the sprite used a different skill instead of releasing the charged one
                user._charging = False
                user._charged_skill_index = -1
                user.remove_effect("charging", "state")
                return None  # pass through: skill can be used while charging
            return False  # blocked: must use charge skill

        if has_charge:
            # pre_charged flag (架势等): bypass first charge
            pre_charged = user._modifiers.get("pre_charged", 0)
            if pre_charged > 0:
                user._modifiers["pre_charged"] = pre_charged - 1
                if user._modifiers["pre_charged"] <= 0:
                    user._modifiers.pop("pre_charged", None)
                user.add_effect(StateEffect(
                    name="charged", state_type="charged", scope="battlefield", source="charge",
                ))
                return None  # charge bypassed, execute immediately as charged
            user._charging = True
            user._charged_skill_index = action.skill_index
            user.add_effect(StateEffect(
                name="charging", state_type="charging", scope="persistent", source="charge",
            ))
            return True  # entering charge

        return None

    # ── 辅助 ──

    def _get_skill(self, team: str, action: Action) -> Skill | None:
        if action.kind != 'skill' or action.skill_index is None:
            return None
        sprite = self.get_player(team).active
        if action.skill_index < len(sprite.skills):
            return sprite.skills[action.skill_index]
        return None

    def _find_first_swift_skill(self, sprite) -> tuple[int, BattleSkill | None]:
        """Find the first usable swift-tagged skill on a sprite.

        Returns (skill_index, BattleSkill) or (0, None) if none found.
        """
        for i, bs in enumerate(sprite.skills):
            if (bs._modifiers.get("swift")
                and not bs.sealed
                and bs.cooldown <= 0):
                return i, bs
        return 0, None

    def _resolve_after_switch(self, switch_team: str, opp_team: str,
                              opp_action: Action, record: RoundRecord) -> None:
        """Handle swift auto-fire and opponent's action after an active switch."""
        ar = {'A': record.action_a, 'B': record.action_b}
        new_sprite = self.get_player(switch_team).active

        if new_sprite.is_fainted:
            return

        swift_idx, swift_bs = self._find_first_swift_skill(new_sprite)
        if swift_bs is None:
            ar[opp_team].events = self._resolve_single_action(opp_team, opp_action, opponent_switched=True)
            return

        # Detect if swift skill counters opponent's skill
        opp_bs = None
        swift_countered_opp = False
        if opp_action.kind == 'skill' and opp_action.skill_index is not None:
            opp_bs = self._get_skill(opp_team, opp_action)
            if opp_bs and opp_bs.cooldown <= 0:
                swift_countered_opp = SkillResolver.resolve_counter(opp_bs, swift_bs)

        # Execute swift skill first
        swift_action = Action(kind='skill', skill_index=swift_idx)
        swift_countered_skill = opp_bs if swift_countered_opp else None
        ar[switch_team].events.append(f'{new_sprite.name} 迅捷：{swift_bs.name}')
        ar[switch_team].events += self._execute_skill_vm(
            switch_team, swift_action,
            is_countered=False,
            countered_skill=swift_countered_skill,
            is_first=True,
        )
        self._check_faint_interrupt(switch_team, ar[switch_team].events)
        self._check_faint_interrupt(opp_team, ar[switch_team].events)

        # Execute opponent's skill (possibly countered by swift)
        if not self.is_finished and not self.get_player(opp_team).active.is_fainted:
            if swift_countered_opp:
                ar[opp_team].events = self._execute_single_action(
                    opp_team, opp_action,
                    is_countered=True,
                    countering_skill=swift_bs,
                    is_first=False,
                    opponent_switched=True,
                )
            else:
                ar[opp_team].events = self._resolve_single_action(opp_team, opp_action, opponent_switched=True)

    def _effective_priority(self, team: str, action: Action) -> int:
        """计算有效先手等级（聚能=0，技能=基础+修正）。"""
        if action.kind == 'gather':
            return 0
        skill = self._get_skill(team, action)
        base = skill.priority if skill else 0
        return base + self.get_player(team).active.priority_mod

    # ── 延时效果结算 ──

    def _execute_scheduled_effects(self, phase: str) -> list[str]:
        """执行到期延时效果。返回事件列表。

        新格式（Skill IR Schedule opcode）: effects 列表走 VM → Replay。
        旧格式（trait_name/hook）: 已废弃，跳过。
        """
        from backend.engine.replayer import JournalReplayer
        from backend.engine.snapshot import build_ctx
        from backend.vm.executor import process_effects

        events: list[str] = _NO_EVENTS if getattr(self, '_mcts_sim', False) else []
        due = [s for s in self.scheduled_effects
               if s['turn'] <= self.turn and s['phase'] == phase]
        for sched in due:
            self.scheduled_effects.remove(sched)
            snap = sched.get('ctx_snapshot', {})
            team = snap.get('team', 'A')
            sprite = snap.get('self') or self.get_player(team).active
            if sprite is None:
                continue
            # 新格式: effects 列表 (Skill IR opcodes) → VM → Replay
            effects = sched.get('effects', [])
            if effects:
                opp_player = self.get_opponent(team)
                opp = opp_player.active if opp_player else None
                ctx = build_ctx(sprite, opp, None, None, None, team=team, turn=self.turn)
                journal = process_effects(ctx, effects)
                replayer = JournalReplayer(
                    sprite, opp, None, self._vm_engine.registry,
                    team=team, battle=self,
                )
                events.extend(replayer.replay(journal))
        return events

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: 回合结束
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_end(self) -> list[str]:
        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = _NO_EVENTS if mcts_sim else []

        # 延时效果结算（phase=end）
        events += self._execute_scheduled_effects('end')

        # scope="turn" 效果清除 + TTL 衰减合并为单次遍历
        for team in ('A', 'B'):
            player = self.get_player(team)
            for sprite in player.team:
                if sprite.is_fainted:
                    continue
                sprite.clear_effects('turn')
                expired = sprite.decrement_ttl()
                if not mcts_sim:
                    for eff in expired:
                        events.append(f'{sprite.name} {eff.name} 到期消失')

        # 借用还原
        for (team, si), original in self._borrowed_restore.items():
            sprite = self.get_player(team).active
            if si < len(sprite.skills):
                bs = sprite.skills[si]
                borrowed_name = bs.name
                bs.replaced_by = None
                if not mcts_sim:
                    events.append(f'{sprite.name} 归还 {borrowed_name}')
        self._borrowed_restore.clear()

        # 愿力还原（一回合后换回原技能）
        for (team, si), original in self._wish_restore.items():
            sprite = self.get_player(team).active
            if not sprite.is_fainted and si < len(sprite.skills):
                current_name = sprite.skills[si].name
                sprite.skills[si] = original
                if not mcts_sim:
                    events.append(f'{sprite.name} 愿力结束({current_name}→{original.name})')
        self._wish_restore.clear()

        # 返场结算（过载回路）：清 battlefield 效果 + 下回合双倍
        for team in ('A', 'B'):
            player = self.get_player(team)
            sprite = player.active
            if not sprite.is_fainted and sprite.pending_return:
                sprite.pending_return = False
                sprite.extra_skill_use = True
                events += self._resolve_return(team)

        sprites: dict[str, Sprite] = {}
        if not self.player_a.active.is_fainted:
            sprites['A'] = self.player_a.active
        if not self.player_b.active.is_fainted:
            sprites['B'] = self.player_b.active

        events += SkillResolver.turn_end(sprites, self.globals)

        # 双向光速：extra_turn_end flag 让回合末效果额外触发一次
        extra_turn = any(
            sprite._modifiers.get("extra_turn_end", 0) > 0
            for sprite in sprites.values()
        )
        if extra_turn:
            if not mcts_sim:
                events.append('⏳ 回合末效果额外触发 +1')
            events += SkillResolver.turn_end(sprites, self.globals)

        # ── 异常 tick trait 通知（只读，不修改层数/HP）──
        if self._vm_engine.registry.has_candidates("post_abnormal_tick"):
            for team, sprite in list(sprites.items()):
                opp_team = 'B' if team == 'A' else 'A'
                opp = self.get_opponent(team).active
                for e in sprite.active_effects:
                    if not isinstance(e, AbnormalEffect):
                        continue
                    if e.name in ('灼烧', '中毒'):
                        dmg = sprite._last_abnormal_dmg.get(e.name, 0)  # actual damage (with element multiplier)
                        # Observer: post_abnormal_tick — fire from both team perspectives
                        # so observers with of:sprite_opp can match
                        opp_team = 'B' if team == 'A' else 'A'
                        ctx_tick_self = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn, last_tick_abnormal=e.name, last_tick_target="sprite_self", last_tick_damage_self=dmg)
                        ctx_tick_opp = self._make_ctx(opp, sprite, None, None, self.globals, team=opp_team, turn=self.turn, last_tick_abnormal=e.name, last_tick_target="sprite_opp", last_tick_damage_opp=dmg)
                        events += self._vm_engine.fire_trigger("post_abnormal_tick", ctx_tick_self, sprite, opp, self.globals, team=team, battle=self)
                        events += self._vm_engine.fire_trigger("post_abnormal_tick", ctx_tick_opp, opp, sprite, self.globals, team=opp_team, battle=self)

        # ── Observer: turn_end ──
        for team, sprite in sprites.items():
            # Observer: turn_end
            if not self._vm_engine.registry.has_candidates("turn_end", id(sprite)):
                continue
            opp_team = 'B' if team == 'A' else 'A'
            opp = self.get_opponent(team).active
            ctx_te = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn, turn_end=True)
            events += self._vm_engine.fire_trigger("turn_end", ctx_te, sprite, opp, self.globals, team=team, battle=self)
            # 紧急脱离：observer 可能触发随机自动换宠
            if self._resolve_pending_escape_if_urgent(events):
                # 替补已上场，重建 sprites dict（后续迭代使用新 active）
                sprites = {}
                if not self.player_a.active.is_fainted:
                    sprites['A'] = self.player_a.active
                if not self.player_b.active.is_fainted:
                    sprites['B'] = self.player_b.active

        # 星地善良：回合末若己方能量=0，板凳星地善良替换上场
        for team in ('A', 'B'):
            player = self.get_player(team)
            active = player.active
            if active.is_fainted:
                continue
            # Hook: turn_end_bench_check — bench 精灵回合末主动替换检查
            bench_result = fire_hook_first('turn_end_bench_check', self, team, active, player)
            swap_index = bench_result[0] if isinstance(bench_result, tuple) else None
            swap_reason = bench_result[1] if isinstance(bench_result, tuple) and len(bench_result) > 1 else ''
            if swap_index is not None:
                pass  # hook already determined the swap
            elif active.energy > 0:
                continue
            if swap_index is None:
                # 星地善良：auto_substitute (TODO: migrate to ObserverEffect once bench support lands)
                for i, bench_sprite in enumerate(player.team):
                    if i == player.active_index or bench_sprite.is_fainted:
                        continue
                    from .traits import get_trait
                    h = get_trait(bench_sprite)
                    if h and h.trait_id == TRAIT_星地善良:
                        swap_index = i
                        swap_reason = '星地善良'
                        break
            if swap_index is not None and swap_index != player.active_index:
                old = active
                player.active_index = swap_index
                new = player.active
                new.clear_effects('battlefield')
                new.entry_turn = self.turn
                new.first_action = True
                new.inc_counter('times_entered')
                if not mcts_sim:
                    events.append(f'{old.name} 能量0↓ {new.name}↑({swap_reason})' if swap_reason else f'{old.name}↓ {new.name}↑')
                events += dispatch_leave(old, self, team)
                events += dispatch_entry(new, self, team)
                # Observer: post_leave + post_entry
                opp_team = 'B' if team == 'A' else 'A'
                opp = self.get_opponent(team).active
                ctx_leave = self._make_ctx(old, opp, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
                ctx_entry = self._make_ctx(new, opp, None, None, self.globals, team=team, turn=self.turn)
                events += self._vm_engine.fire_trigger("post_leave", ctx_leave, old, opp, self.globals, team=team, battle=self)
                # 洁癖等 post_leave observer 可能写入新的 pending_effects
                pending = self.pending_effects.get(team, [])
                for e in pending:
                    new.add_effect(e)
                if pending:
                    self.pending_effects[team] = []
                events += self._vm_engine.fire_trigger("post_entry", ctx_entry, new, opp, self.globals, team=team, battle=self)

        # 冻结斩杀检查
        for team in ('A', 'B'):
            sprite = self.get_player(team).active
            if not sprite.is_fainted and sprite.check_freeze_death():
                if not mcts_sim:
                    events.append(f'{sprite.name} 冻结斩杀(冻结{sprite.frozen_hp}HP)')

        # 回合结束力竭检查
        for team in ('A', 'B'):
            self._check_faint_interrupt(team, events)

        return events

    # ═══════════════════════════════════════════════════════════════
    # 批量运行
    # ═══════════════════════════════════════════════════════════════

    def run(self, agent_a: Agent, agent_b: Agent) -> str | None:
        """完整对局流程：出场选择 → 回合循环 → 终局。"""
        self._agent_a = agent_a
        self._agent_b = agent_b

        # 出场选择（双方轮流选首发精灵）
        lead_a = agent_a.choose_lead(self)
        lead_b = agent_b.choose_lead(self)
        self.player_a.active_index = lead_a
        self.player_b.active_index = lead_b

        if self.verbose:
            print(f'\n{"═" * 50}')
            print(f'  {self.player_a.name} ({self.player_a.active.name})'
                  f'  vs  {self.player_b.name} ({self.player_b.active.name})')
            print(f'{"═" * 50}')

        # 回合循环
        while not self.is_finished:
            record = self.execute_turn(agent_a, agent_b)
            if self.verbose:
                self._print_turn(record)

        # 终局
        result = self.winner or 'draw'
        if self.verbose:
            self._print_result(result)
        agent_a.on_game_end(result)
        agent_b.on_game_end(result)
        return result

    # ── 日志输出 ──

    def _build_action_record(self, team: str, action: Action, item_used: str = '') -> ActionRecord:
        """从 Action + 道具构建 ActionRecord。"""
        actor = self.get_player(team).active.name
        if item_used:
            return ActionRecord(team=team, actor=actor, kind='item', skill_name=item_used)
        if action.kind == 'switch' and action.switch_index is not None:
            player = self.get_player(team)
            target_name = player.team[action.switch_index].name if action.switch_index < len(player.team) else '?'
            return ActionRecord(team=team, actor=actor, kind='switch', skill_name=target_name)
        if action.kind == 'gather':
            return ActionRecord(team=team, actor=actor, kind='gather', skill_name='聚能')
        if action.kind == 'skill' and action.skill_index is not None:
            sprite = self.get_player(team).active
            skill_name = sprite.skills[action.skill_index].name if action.skill_index < len(sprite.skills) else '?'
            return ActionRecord(team=team, actor=actor, kind='skill', skill_name=skill_name)
        return ActionRecord(team=team, actor=actor, kind=action.kind)

    def _print_turn(self, r: RoundRecord) -> None:
        """单回合紧凑日志。"""
        a_short = _rr_action_short(r.action_a)
        b_short = _rr_action_short(r.action_b)
        first = r.first_team or '?'

        parts = [f'T{r.turn:03d} [{first}先]']
        parts.append(f'A:{a_short}  B:{b_short}')

        all_events = (
            r.turn_start_events
            + (r.action_a.events if r.action_a else [])
            + (r.action_b.events if r.action_b else [])
            + r.turn_end_events
        )
        if all_events:
            key_events = [e for e in all_events if 'HP' in e or '力竭' in e or '脱离' in e]
            shown = key_events if key_events else all_events[:2]
            parts.append('| ' + ' '.join(shown))

        parts.append(f'(weather={r.weather})' if r.weather else '')
        print('  '.join(parts))

    def _print_result(self, result: str) -> None:
        """终局总结。"""
        print(f'\n{"═" * 50}')
        winner_name = (
            self.player_a.name if result == 'A'
            else self.player_b.name if result == 'B'
            else '平局'
        )
        print(f'  对局结束: {winner_name} 胜 ({self.turn}回合)')
        print(f'{"═" * 50}')

        # 回合详情表
        print(f'\n  {"回合":<5} {"先":<3} {"A行动":<12} {"B行动":<12}')
        print(f'  {"─" * 40}')
        for r in self.log:
            a_short = _rr_action_short(r.action_a)
            b_short = _rr_action_short(r.action_b)
            print(f'  T{r.turn:<4d} {r.first_team:<3}'
                  f' {a_short:<12} {b_short:<12}')

    def save_log(self, path: str) -> None:
        """将对局日志保存到文件（使用 RoundRecord.to_message() 格式）。"""
        from datetime import datetime
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'# 对局记录 — {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
            f.write(f'# {self.player_a.name} vs {self.player_b.name}\n')
            f.write(f'# 结果: {self.winner or "draw"} ({self.turn}回合)\n\n')
            for r in self.log:
                f.write(r.to_message())
                f.write('\n\n')
