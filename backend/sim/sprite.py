"""backend/sim/sprite.py — 战斗精灵实例 + 效果追踪"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.common import STAT_KEYS
from backend.common.models import SpeciesStats, StatsResult
from backend.common.skill_trait_ids import TRAIT_无忧无虑

if TYPE_CHECKING:
    from .battleskill import BattleSkill

# 步数换算
_STEP_PCT = 10       # 非速度六维：1步=10%
_SPEED_STEP = 10     # 速度：1步=10点
_POWER_STEP = 10     # 威力：1步=10威力
_PRIORITY_STEP = 1   # 先手：1步=1
_ENERGY_STEP = 1     # 能耗：1步=1

# 非百分比型 stat_key（直接累加步数×单位，不做乘法）
_NON_PCT_KEYS: frozenset[str] = frozenset({'power', 'priority', 'energy_cost', 'combo', 'life_drain', 'combo_mult'})


@dataclass
class Sprite:
    """对局中的精灵实例。持有种族值引用 + 实时战斗状态。"""

    # ── 静态 ──
    species: SpeciesStats
    bloodline: str = ""
    bloodline_skills: dict[str, int] = field(default_factory=dict)
    skills: list[BattleSkill] = field(default_factory=list)

    # ── 初始六维（nature + IV 后的最终值，mods 前） ──
    initial_stats: dict[str, int] = field(default_factory=dict)
    nature: str | None = None
    iv: dict[str, int] = field(default_factory=dict)

    # ── 实时状态 ──
    current_hp: int = 0
    max_hp: int = 0
    energy: int = 10            # 0-10

    # 全部效果（EffectObject 子类：AbnormalEffect / StatBuffEffect / StateEffect / etc.）
    # 统一查询入口，由 replayer + trait loader 写入
    active_effects: list = field(default_factory=list)

    # ── 效果统计缓存（增量维护，O(1) 读取，避免 snapshot 时 O(N) 遍历） ──
    _effects_dirty: bool = field(default=True, repr=False)
    _cached_stages: dict[str, int] = field(default_factory=dict)        # stat_key → total steps
    _cached_abnormals: dict[str, int] = field(default_factory=dict)     # name → total stacks
    _cached_charging: bool = False
    _cached_charged: bool = False
    _cached_positive: int = 0

    # 进场回合
    entry_turn: int = 0

    # 通用计数器（"每使用N次技能" / "每受到N次伤害" / "每入场N次" 等）
    counters: dict[str, int] = field(default_factory=dict)

    # 迸发判定：进场后第一次行动
    first_action: bool = True
    # 全场首次行动（不随入场重置）
    first_action_battle: bool = True

    # 返场标记：回合结束时清 battlefield 效果 + 下回合技能双倍
    pending_return: bool = False
    interrupted: bool = False      # interrupted this turn (by interrupt opcode)
    locked_turns: int = 0          # turns remaining before switch allowed
    extra_skill_use: bool = False

    # 运行时 modifier 累积 (damage_reduction, power_mult, etc.)
    # 由 JournalReplayer._apply_modifier 写入，snapshot 读取
    _modifiers: dict[str, float] = field(default_factory=dict)
    # 不可见 modifier 的 scope 追踪 {stat_key: scope}
    # scope="turn" → 回合末清除；scope="battlefield" → 离场时清除
    _mod_scopes: dict[str, str] = field(default_factory=dict)
    # 最近一次异常 tick 的实际伤害（含属性克制），供仁心等 trait observer 查询
    _last_abnormal_dmg: dict[str, int] = field(default_factory=dict)

    # 延迟生效的效果队列：[(effect, delay_remaining), ...]
    _pending_effects: list = field(default_factory=list)

    # on_next 延迟 modifier 队列：引擎下次匹配技能时注入
    _pending_modifiers: list = field(default_factory=list)

    # 特性交互（禁用/复制/移除）
    _trait_suppressed: bool = False     # 特性被压制时跳过所有 trait dispatch

    # 萌化状态（形态退化）
    _moe_chain: list = field(default_factory=list)       # 进化链快照 [highest, ..., lowest]
    _moe_position: int = 0              # 当前在链中的位置（0=原始，≥1=已退化n阶）
    _moe_origin: SpeciesStats | None = None   # 萌化前的原始物种
    _moe_origin_skills: list = field(default_factory=list)  # 萌化前的原始技能列表

    # ── 有效属性 ──

    @property
    def is_fainted(self) -> bool:
        return self.current_hp <= 0

    @property
    def hp_pct(self) -> float:
        return self.current_hp / self.max_hp if self.max_hp > 0 else 0.0

    @property
    def name(self) -> str:
        return self.species.name

    # ── 效果查询辅助 ──

    def _find_abnormal(self, name: str):
        """Find an AbnormalEffect by name in active_effects."""
        from backend.vm.effect import AbnormalEffect
        for e in self.active_effects:
            if isinstance(e, AbnormalEffect) and e.name == name:
                return e
        return None

    # ── stat 步数 ──

    def _sum_steps(self, stat_key: str, ignore_negative: bool = False, ignore_positive: bool = False) -> int:
        if not ignore_negative and not ignore_positive:
            if self._effects_dirty:
                self._rebuild_effects_cache()
            return self._cached_stages.get(stat_key, 0)

        from backend.vm.effect import StatBuffEffect
        total = 0
        for e in self.active_effects:
            if not isinstance(e, StatBuffEffect) or e.stat_key != stat_key:
                continue
            if ignore_negative and e.steps < 0:
                continue
            if ignore_positive and e.steps > 0:
                continue
            total += e.steps
        return total

    def effective_stat(self, stat_key: str, ignore_negative: bool = False, ignore_positive: bool = False) -> int:
        """返回六维属性经效果修正后的有效值。"""
        if stat_key in _NON_PCT_KEYS:
            return self._sum_steps(stat_key, ignore_negative, ignore_positive)
        base = self.initial_stats.get(stat_key, 0)
        total_steps = self._sum_steps(stat_key, ignore_negative, ignore_positive)
        if stat_key == 'speed':
            return max(0, base + total_steps * _SPEED_STEP)
        return max(0, round(base * (1.0 + total_steps / _STEP_PCT)))

    @property
    def effective_stats(self) -> dict[str, int]:
        return {k: self.effective_stat(k) for k in STAT_KEYS}

    # ── 非六维修正（威力/先手/能耗） ──

    @property
    def power_mod(self) -> int:
        """威力修正步数。1步 = 10威力。"""
        return self._sum_steps('power')

    @property
    def priority_mod(self) -> int:
        """先手修正步数。1步 = 1先手值。"""
        return self._sum_steps('priority')

    @property
    def energy_cost_mod(self) -> int:
        """能耗修正步数。1步 = 1能耗。"""
        return self._sum_steps('energy_cost')

    # ── 效果管理 ──

    def add_effect(self, effect) -> None:
        """添加效果。StatBuffEffect 按 stat_key 合并步数；StateEffect 追加；AbnormalEffect 合并层数。"""
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect

        if isinstance(effect, StatBuffEffect):
            for existing in self.active_effects:
                if isinstance(existing, StatBuffEffect) and existing.stat_key == effect.stat_key and existing.scope == effect.scope:
                    existing.steps += effect.steps
                    # 增量更新缓存：steps 变化
                    if not self._effects_dirty:
                        old_steps = existing.steps - effect.steps
                        self._cached_stages[effect.stat_key] = self._cached_stages.get(effect.stat_key, 0) + effect.steps
                        # positive 计数调整：old 为正且 new 仍为正 → 不变
                        if old_steps <= 0 and existing.steps > 0:
                            self._cached_positive += 1
                        elif old_steps > 0 and existing.steps <= 0:
                            self._cached_positive -= 1
                    return
            self.active_effects.append(effect)
            # 增量更新缓存：新增 StatBuffEffect
            if not self._effects_dirty:
                self._cached_stages[effect.stat_key] = self._cached_stages.get(effect.stat_key, 0) + effect.steps
                if effect.steps > 0:
                    self._cached_positive += 1
            return
        if isinstance(effect, StateEffect):
            self.active_effects.append(effect)
            # 增量更新缓存
            if not self._effects_dirty:
                if effect.state_type == "charging" or effect.name == "charging":
                    self._cached_charging = True
                elif effect.state_type == "charged" or effect.name == "charged":
                    self._cached_charged = True
            return
        if isinstance(effect, AbnormalEffect):
            for existing in self.active_effects:
                if isinstance(existing, AbnormalEffect) and existing.name == effect.name:
                    existing.stacks += effect.stacks
                    # 增量更新缓存
                    if not self._effects_dirty:
                        self._cached_abnormals[effect.name] = self._cached_abnormals.get(effect.name, 0) + effect.stacks
                    self.check_freeze_death()
                    return
            self.active_effects.append(effect)
            # 增量更新缓存
            if not self._effects_dirty:
                self._cached_abnormals[effect.name] = self._cached_abnormals.get(effect.name, 0) + effect.stacks
            if effect.name == '冻结':
                self.check_freeze_death()
            return
        # Generic EffectObject — just append
        self.active_effects.append(effect)
        self._invalidate_effects_cache()

    def remove_effect(self, name: str, category: str = '') -> None:
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
        type_map = {'stat': StatBuffEffect, 'abnormal': AbnormalEffect, 'state': StateEffect}
        target_type = type_map.get(category)
        self.active_effects = [
            e for e in self.active_effects
            if e.name != name or (target_type and not isinstance(e, target_type))
        ]
        self._invalidate_effects_cache()

    def get_effects(self, category: str = '') -> list:
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
        type_map = {'stat': StatBuffEffect, 'abnormal': AbnormalEffect, 'state': StateEffect}
        if category in type_map:
            target = type_map[category]
            return [e for e in self.active_effects if isinstance(e, target)]
        return list(self.active_effects)

    def get_stacks(self, name: str) -> int:
        ae = self._find_abnormal(name)
        return ae.stacks if ae else 0

    @property
    def frozen_hp(self) -> int:
        """冻结锁定的生命值：每层 5% 最大HP。"""
        stacks = self.get_stacks('冻结')
        return round(self.max_hp * 0.05 * stacks) if stacks > 0 else 0

    def check_freeze_death(self) -> bool:
        """冻结斩杀：当前HP ≤ 冻结生命值 → 死亡。返回是否触发了斩杀。"""
        if self.is_fainted:
            return False
        fhp = self.frozen_hp
        if fhp > 0 and self.current_hp <= fhp:
            self.current_hp = 0
            return True
        return False

    def update_stacks(self, name: str, stacks: int) -> None:
        """直接设置异常状态层数（如灼烧衰减）。"""
        ae = self._find_abnormal(name)
        if ae is not None:
            if stacks > 0:
                old = ae.stacks
                ae.stacks = stacks
                # 增量更新缓存
                if not self._effects_dirty:
                    self._cached_abnormals[name] = self._cached_abnormals.get(name, 0) + (stacks - old)
            else:
                self.active_effects.remove(ae)
                self._invalidate_effects_cache()
        elif stacks > 0:
            # Create new AbnormalEffect from template if available
            from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
            template = ABNORMAL_TEMPLATES.get(name)
            if template is not None:
                from copy import copy
                new_ae = copy(template)
                new_ae.stacks = stacks
                self.active_effects.append(new_ae)
                # 增量更新缓存
                if not self._effects_dirty:
                    self._cached_abnormals[name] = self._cached_abnormals.get(name, 0) + stacks

    def clear_effects(self, scope: str) -> None:
        """清除指定 scope 的全部效果。同步清理 _modifiers 中的不可见 key。"""
        scopes = {scope}
        if scope in ('battlefield', 'turn'):
            scopes.add('aura')
        # 清除 active_effects 中匹配 scope 的 EffectObject
        self.active_effects = [
            e for e in self.active_effects
            if getattr(e, 'scope', '') not in scopes
        ]
        # 清除不可见 modifier（_mod_scopes 中记录的 key）
        for mod_key, mod_scope in list(self._mod_scopes.items()):
            if mod_scope == scope or (scope in ('battlefield', 'turn') and mod_scope == 'aura'):
                self._modifiers.pop(mod_key, None)
                del self._mod_scopes[mod_key]
        self._invalidate_effects_cache()

    # ── 驱散 / 翻倍 ──

    def dispel_positive(self, count: int = -1) -> int:
        """移除正面的 stat 效果。count=-1 移除全部。permanent 效果不可驱散。"""
        from backend.vm.effect import StatBuffEffect
        targets = [e for e in self.active_effects
                   if isinstance(e, StatBuffEffect) and e.steps > 0 and e.scope not in ('permanent', 'aura')]
        if count >= 0:
            targets = targets[:count]
        if targets:
            for e in targets:
                self.active_effects.remove(e)
            self._invalidate_effects_cache()
        return len(targets)

    def dispel_negative(self, count: int = -1) -> int:
        """移除负面的 stat 效果。permanent 效果不可驱散。"""
        from backend.vm.effect import StatBuffEffect
        targets = [e for e in self.active_effects
                   if isinstance(e, StatBuffEffect) and e.steps < 0 and e.scope not in ('permanent', 'aura')]
        if count >= 0:
            targets = targets[:count]
        if targets:
            for e in targets:
                self.active_effects.remove(e)
            self._invalidate_effects_cache()
        return len(targets)

    def double_positive(self) -> int:
        """加倍全部正面 stat 效果的步数。返回影响数量。"""
        from backend.vm.effect import StatBuffEffect
        n = 0
        for e in self.active_effects:
            if isinstance(e, StatBuffEffect) and e.steps > 0:
                e.steps *= 2
                n += 1
        if n:
            self._invalidate_effects_cache()
        return n

    def double_negative(self) -> int:
        """加倍全部负面 stat 效果的步数。返回影响数量。"""
        from backend.vm.effect import StatBuffEffect
        n = 0
        for e in self.active_effects:
            if isinstance(e, StatBuffEffect) and e.steps < 0:
                e.steps *= 2
                n += 1
        if n:
            self._invalidate_effects_cache()
        return n

    def clear_all_effects(self) -> None:
        self.active_effects.clear()
        self._invalidate_effects_cache()

    # ── 效果统计缓存（增量维护） ──

    def _invalidate_effects_cache(self) -> None:
        """标记缓存失效，下次读取时重建。"""
        self._effects_dirty = True

    def _rebuild_effects_cache(self) -> None:
        """O(N) 全量重建效果统计缓存（仅在 dirty 且被读取时触发）。"""
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect

        self._cached_stages.clear()
        self._cached_abnormals.clear()
        self._cached_charging = False
        self._cached_charged = False
        self._cached_positive = 0

        for e in self.active_effects:
            if isinstance(e, StatBuffEffect):
                self._cached_stages[e.stat_key] = self._cached_stages.get(e.stat_key, 0) + e.steps
                if e.steps > 0:
                    self._cached_positive += 1
            elif isinstance(e, AbnormalEffect):
                self._cached_abnormals[e.name] = self._cached_abnormals.get(e.name, 0) + e.stacks
            elif isinstance(e, StateEffect):
                if e.state_type == "charging" or e.name == "charging":
                    self._cached_charging = True
                elif e.state_type == "charged" or e.name == "charged":
                    self._cached_charged = True
        self._effects_dirty = False

    def get_effects_snapshot(self) -> dict:
        """返回效果统计快照 {stages, abnormals, charging, charged, positive}，O(1) 读取。

        内部自动处理缓存脏重建。供 snapshot.py 的 _extract_sprite_effects 使用。
        """
        if self._effects_dirty:
            self._rebuild_effects_cache()
        return {
            "stages": self._cached_stages,
            "abnormals": self._cached_abnormals,
            "charging": self._cached_charging,
            "charged": self._cached_charged,
            "positive": self._cached_positive,
        }

    # ── 效果生命周期：TTL / Delay / Cooldown ──

    def decrement_ttl(self) -> list:
        """回合末：所有 ttl>0 的效果 -1，ttl 归零的移除。返回被移除的效果列表。"""
        removed = []
        surviving = []
        for e in self.active_effects:
            ttl = getattr(e, 'ttl', 0)
            if ttl > 0:
                e.ttl -= 1
                if e.ttl <= 0:
                    removed.append(e)
                    continue
            surviving.append(e)
        if removed:
            self.active_effects = surviving
            self._invalidate_effects_cache()
        return removed

    def add_pending_effect(self, effect, delay: int) -> None:
        """添加延迟生效的效果。delay 回合后再生效。"""
        self._pending_effects.append((effect, delay))

    def process_pending_effects(self) -> list:
        """回合初：所有延迟效果 delay-1，delay=0 的生效。返回本次生效的效果列表。"""
        activated = []
        remaining = []
        for eff, delay in self._pending_effects:
            delay -= 1
            if delay <= 0:
                self.add_effect(eff)
                activated.append(eff)
            else:
                remaining.append((eff, delay))
        self._pending_effects = remaining
        return activated

    def use_cooldown(self, name: str) -> int:
        """触发指定名称效果的冷却：cooldown-1。cooldown 归零时移除效果。返回剩余冷却。"""
        for e in self.active_effects:
            cd = getattr(e, 'cooldown', 0)
            if e.name == name and cd > 0:
                e.cooldown -= 1
                if e.cooldown <= 0:
                    self.active_effects.remove(e)
                    return 0
                return e.cooldown
        return 0

    def consume_pending_modifiers(self, skill_type: str):
        """消耗 on_next pending modifiers：匹配 if_type 的注入并清除。返回匹配的 modifier 列表。"""
        consumed = []
        remaining = []
        for m in self._pending_modifiers:
            if_type = getattr(m, 'if_type', None)
            # 匹配规则：if_type 为空（所有技能）或匹配当前技能类型
            if if_type is None or if_type == '' or self._skill_type_matches(skill_type, if_type):
                consumed.append(m)
                # Apply the modifier to _modifiers
                cur = self._modifiers.get(m.stat)
                if m.mode == "set":
                    self._modifiers[m.stat] = m.value
                elif m.mode == "add":
                    self._modifiers[m.stat] = (cur or 0.0) + m.value
                elif m.mode == "multiply":
                    self._modifiers[m.stat] = (cur or 1.0) * m.value if cur is not None else m.value
                else:
                    self._modifiers[m.stat] = m.value
            else:
                remaining.append(m)
        self._pending_modifiers = remaining
        return consumed

    @staticmethod
    def _skill_type_matches(skill_type: str, if_type: str) -> bool:
        """Check if a skill type matches an if_type filter."""
        if if_type == "attack":
            return skill_type in ("物攻", "魔攻", "动态攻击")
        elif if_type == "defense":
            return skill_type == "防御"
        elif if_type == "status":
            return skill_type == "状态"
        return False

    # ── 计数器 ──

    def inc_counter(self, key: str, delta: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + delta
        return self.counters[key]

    def get_counter(self, key: str) -> int:
        return self.counters.get(key, 0)

    # ── HP / 能量 ──

    def take_damage(self, amount: int) -> int:
        actual = min(self.current_hp, amount)
        self.current_hp -= actual
        if self.current_hp > 0:
            self.check_freeze_death()
        return actual

    def heal(self, amount: int) -> int:
        actual = min(self.max_hp - self.current_hp, amount)
        self.current_hp += actual
        return actual

    @property
    def max_energy(self) -> int:
        from backend.vm.effect import ModifierEffect
        for e in self.active_effects:
            if isinstance(e, ModifierEffect) and e.attr == "max_energy":
                return int(e.value)
        return 10

    def gain_energy(self, amount: int) -> int:
        room = max(0, self.max_energy - self.energy)
        actual = min(room, amount)
        self.energy += actual
        return actual

    def lose_energy(self, amount: int) -> int:
        actual = min(self.energy, amount)
        self.energy -= actual
        return actual

    # ── 构造 ──

    @classmethod
    def from_result(cls, result: StatsResult, energy: int = 10) -> Sprite:
        return cls(
            species=result.species,
            bloodline=result.species.bloodline,
            bloodline_skills=dict(result.species.bloodline_skills),
            initial_stats=dict(result.final_stats),
            current_hp=result.final_stats['hp'],
            max_hp=result.final_stats['hp'],
            energy=energy,
            nature=result.nature,
            iv=dict(result.iv),
        )

    def transform(self, new_species, new_skills: list) -> list[str]:
        """形态变换：替换 species + skills，保留 HP 比例/能量/效果/计数器。"""
        hp_ratio = self.current_hp / max(1, self.max_hp)
        old_name = self.name
        self.species = new_species
        # 用 StatsCalc 重新计算六维（保留 IV/性格修正）
        from backend.common.formulas import StatsCalc
        calc = StatsCalc()
        result = calc.compute(new_species, nature=self.nature, iv=self.iv)
        self.initial_stats = dict(result.final_stats)
        self.max_hp = result.final_stats['hp']
        self.current_hp = max(1, round(result.final_stats['hp'] * hp_ratio))
        if new_skills:
            self.skills = new_skills
        self.first_action = True  # 形态变换后首次行动触发迸发
        return [f'{old_name} 形态变换 → {self.name}']

    # ── 萌化（形态退化）──

    def _reset_moe_state(self) -> None:
        """重置萌化状态（驱散/形态变化后清理）。"""
        self._moe_chain.clear()
        self._moe_position = 0
        self._moe_origin = None
        self._moe_origin_skills = []

    def apply_moe(self, stacks: int, battle) -> list[str]:
        """施加萌化：沿进化链向下退化。返回事件列表。"""
        if stacks <= 0:
            return []

        # 验证现有链条是否仍匹配当前物种（进化之力等可能已改变形态）
        if self._moe_chain and self._moe_position < len(self._moe_chain):
            expected = self._moe_chain[self._moe_position]
            if self.species.number != expected.number:
                self._reset_moe_state()

        # 首次萌化：构建进化链快照 + 保存原始形态
        if not self._moe_chain:
            self._moe_origin = self.species
            self._moe_origin_skills = list(self.skills)
            self._build_moe_chain(battle)
            self._moe_position = 0

        max_pos = len(self._moe_chain) - 1
        new_pos = self._moe_position + stacks

        # 已到最低形态 → 免疫
        if self._moe_position >= max_pos:
            # 无忧无虑：允许额外层数（仅计数，不变换）
            from .traits import get_trait
            h = get_trait(self)
            if h and h.trait_id == TRAIT_无忧无虑:
                self._moe_position = new_pos
                self._sync_moe_status_effect()
                return [f'{self.name} 萌化层数+{stacks}(共{self._moe_position}层，无忧无虑)']
            return [f'{self.name} 已是最低形态，免疫萌化']

        # 限制到最大退化深度
        actual_new = min(new_pos, max_pos)
        target_species = self._moe_chain[actual_new]

        old_name = self.name
        self.transform(target_species, [])
        self._moe_position = actual_new
        self._sync_moe_status_effect()
        # 萌化后清除进化之力的首领化增益（已非首领形态）
        self.remove_effect('首领化')

        events = [f'{old_name} 萌化 → 变为{self.name}({self._moe_position}层)']

        events += self._seal_exclusive_skills()

        # 多余层数（无忧无虑溢出到最低形态以下）
        if new_pos > max_pos:
            from .traits import get_trait
            h = get_trait(self)
            if h and h.trait_id == TRAIT_无忧无虑:
                self._moe_position = new_pos
                self._sync_moe_status_effect()
                events.append(f'{self.name} 萌化层数+{new_pos - max_pos}(共{self._moe_position}层，无忧无虑)')

        return events

    def remove_moe(self, stacks: int, battle) -> int:
        """移除萌化层数：沿进化链向上恢复。返回实际移除层数。"""
        if stacks <= 0 or self._moe_position <= 0:
            return 0

        removed = min(stacks, self._moe_position)
        new_pos = self._moe_position - removed

        if new_pos == 0:
            # 完全恢复原始形态
            target_species = self._moe_origin
            target_skills = self._moe_origin_skills
            self._moe_chain.clear()
            self._moe_origin = None
            self._moe_origin_skills = []
        else:
            target_species = self._moe_chain[new_pos]
            target_skills = []

        self.transform(target_species, target_skills)
        self._moe_position = new_pos
        self._sync_moe_status_effect()
        self._unseal_exclusive_skills()

        return removed

    def _build_moe_chain(self, battle) -> None:
        """从当前物种沿 pre_species 向下走到最低形态。"""
        chain = [self.species]
        current = self.species
        while current.pre_species:
            pre = battle.lookup_species_by_number(current.pre_species)
            if pre is None:
                break
            chain.append(pre)
            current = pre
        self._moe_chain = chain

    def _sync_moe_status_effect(self) -> None:
        """同步 AbnormalEffect 层数与 _moe_position。"""
        from copy import copy

        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES

        self.remove_effect('萌化', 'abnormal')
        if self._moe_position > 0:
            template = ABNORMAL_TEMPLATES.get('萌化')
            if template is not None:
                ae = copy(template)
                ae.stacks = self._moe_position
                self.active_effects.append(ae)

    def _seal_exclusive_skills(self) -> list[str]:
        """封印不匹配当前形态的专属技能。"""
        events = []
        for bs in self.skills:
            ex = bs.base.exclusive_to
            if ex and ex != self.species.name and not bs.sealed:
                bs.sealed = True
                events.append(f'{bs.name} 专属技能锁定(需{ex})')
        return events

    def _unseal_exclusive_skills(self) -> list[str]:
        """解除萌化造成的专属技能封印。"""
        events = []
        for bs in self.skills:
            ex = bs.base.exclusive_to
            if ex and bs.sealed:
                bs.sealed = False
                events.append(f'{bs.name} 专属技能解锁')
        return events
