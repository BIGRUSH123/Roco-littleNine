"""backend/sim/agent.py — 决策代理

TODO: 接入大模型 API（如 Claude/DeepSeek），将 battle 状态序列化为 prompt，
由 LLM 推理选择动作，替代当前规则评分。需解决：
  - 状态序列化格式（JSON / 自然语言）
  - 输出解析（JSON schema / function calling）
  - 延迟与成本控制（缓存、fallback 到 RuleAgent）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .item_policy import should_evolve, should_use_wish

from .action import Action
from .battleskill import SkillUse

if TYPE_CHECKING:
    from .battle import Battle
    from .player import Player
    from .sprite import Sprite

_GATHER_ACTION = Action(kind='gather')
_ITEM_ACTION = Action(kind='item')
_SKILL_ACTIONS = tuple(Action(kind='skill', skill_index=i) for i in range(10))
_SWITCH_ACTIONS = tuple(Action(kind='switch', switch_index=i) for i in range(6))


def _skill_action(index: int) -> Action:
    return _SKILL_ACTIONS[index] if 0 <= index < len(_SKILL_ACTIONS) else Action(kind='skill', skill_index=index)


def _switch_action(index: int) -> Action:
    return _SWITCH_ACTIONS[index] if 0 <= index < len(_SWITCH_ACTIONS) else Action(kind='switch', switch_index=index)


def gather_is_noop(sprite) -> bool:
    """满能量聚能 = **严格劣着**（+0 能量、白丢一回合）。

    引擎侧"聚能"始终合法（它是游戏里可以随时做的行动），所以这条只约束**决策层**：
    AI 不该把空过当候选，更不该在能出招时用它兜底。判定用能量上限而非固定常量，
    因为上限会随血脉/首领化等变化。见 docs/引擎机制对账-游戏描述图鉴.md「僵局」。
    """
    max_e = getattr(sprite, "max_energy", 0) or 0
    return max_e > 0 and getattr(sprite, "energy", 0) >= max_e


MAX_CONSECUTIVE_SWITCHES = 5   # 连续换人上限（防"换人空转"僵局；0 = 关闭该规则）


class SwitchStreak:
    """连续换人计数 —— **决策层状态**，不进战斗快照（不被 MCTS 回滚，也不影响引擎）。

    同一回合内 agent 可能因非法动作被重问多次，用 `turn` 去重只记一次实际选择。
    """

    __slots__ = ("streak", "_turn")

    def __init__(self) -> None:
        self.streak = 0
        self._turn = -1

    def note(self, battle, action, *, forced: bool = False) -> None:
        """记一次实际选择。（力竭顶替等非主动换人）→ 计数归零而不是 +1。"""
        turn = getattr(battle, "turn", -1)
        if turn == self._turn:
            return
        self._turn = turn
        if forced:
            self.streak = 0
        elif getattr(action, "kind", "") == "switch":
            self.streak += 1
        else:
            self.streak = 0

    def blocked(self, limit) -> bool:
        """连续换人是否已达上限（`limit` 为 0/None = 不限制）。"""
        if not limit:
            return False
        return self.streak >= int(limit)


def best_self_buff_skill_index(battle, sprite, *, min_gain: float, usable=None) -> int:
    """值得花一回合叠的**自身增益**技能下标（没有返回 -1）。

    "进攻不划算时先增益自己"这条规则的判据：旧 kind 层的 `e.kind == 'stat'`
    在 IR 语料下恒不成立（2026-09-22 删除），这里改用 `sim.skill_ir.skill_profile`
    的 `self_buff_value`（步数口径）——与 V3 的 `_try_setup` 同源，单一实现。
    """
    from .skill_ir import skill_profile

    if not min_gain:
        return -1
    best_i, best_val = -1, 0.0
    for i, bs in enumerate(getattr(sprite, "skills", None) or ()):
        if usable is not None and not usable(i, bs):
            continue
        if getattr(bs, "is_attack", False) or getattr(bs, "is_defense", False):
            continue
        prof = skill_profile(battle, bs)
        val = prof.self_buff_value
        if prof.doubles_buffs and prof.counters == "防御":
            val *= 1.3                   # 应对成功会翻倍，值得赌
        if val > best_val:
            best_i, best_val = i, val
    if best_i >= 0 and best_val >= float(min_gain):
        return best_i
    return -1


def first_usable_skill_index(battle, team: str, sprite, *, attack_first: bool = True) -> int:
    """本次**真能放出去**的技能下标（走引擎唯一判据 `Battle.action_legality`）。

    用于"满能量不能空过"的兜底：攻击优先（`attack_first`），其次任意可用技能；没有返回 -1。
    """
    if sprite is None:
        return -1
    fallback = -1
    for i, skill in enumerate(getattr(sprite, "skills", None) or ()):
        try:
            if not battle.action_legality(team, Action(kind='skill', skill_index=i)).ok:
                continue
        except Exception:  # noqa: BLE001 — 判据不可用时不阻断决策
            continue
        if attack_first and getattr(skill, "is_attack", False):
            return i
        if fallback < 0:
            fallback = i
    return fallback


class Agent(Protocol):
    """决策代理协议。"""

    team: str

    def choose_lead(self, battle: Battle) -> int: ...
    def choose_action(self, battle: Battle) -> Action: ...
    def choose_replacement(self, battle: Battle) -> int: ...
    def on_game_end(self, winner: str) -> None: ...


class RuleAgent:
    """基于 PlayStyle 的规则 AI。"""

    def __init__(self, team: str, player: Player):
        self.team = team
        self.player = player
        # 连续换人计数（决策层状态，不进战斗快照）
        self._switches = SwitchStreak()

    def choose_lead(self, battle: Battle) -> int:
        """选择出场精灵：选对对手威胁最大的。"""
        opponent = battle.get_opponent(self.team).active
        best_idx = 0
        best_score = -1.0
        for i, sprite in enumerate(self.player.team):
            if sprite.is_fainted:
                continue
            max_dmg = 0
            for skill in sprite.skills:
                if skill.is_attack:
                    dmg, _ = battle._resolver.calc_damage(
                        sprite, opponent, SkillUse(battle_skill=skill), battle.globals,
                        attacker_team=self.team,
                    )
                    if dmg > max_dmg:
                        max_dmg = dmg
            score = max_dmg + sprite.current_hp * 0.05
            if score > best_score:
                best_score = score
                best_idx = i
        return best_idx

    def choose_action(self, battle: Battle) -> Action:
        forced = bool(getattr(self.player.active, "is_fainted", False))
        action = self._decide(battle)
        self._switches.note(battle, action, forced=forced)
        return action

    def _decide(self, battle: Battle) -> Action:
        p = self.player
        s = p.active
        style = p.style
        # 连续换人到上限后不再主动换人（"不能连续 5 次换人"）
        may_switch = not self._switches.blocked(MAX_CONSECUTIVE_SWITCHES)

        # 已力竭 → 强制换宠
        if s.is_fainted:
            replacement = p.find_replacement()
            if replacement is not None:
                return _switch_action(replacement)
            return _GATHER_ACTION

        # 道具·进化之力（**不消耗回合**，首领化无代价 → 能变就变，判据见 item_policy）
        item = p.item
        if (item and item.can_use(battle.turn) and item.name == '进化之力'
                and should_evolve(battle, self.team, s)):
            return _ITEM_ACTION

        opponent = battle.get_opponent(self.team).active

        # 低 HP → 可能换宠
        hp_ratio = s.current_hp / s.max_hp if s.max_hp > 0 else 0
        if may_switch and hp_ratio < style.switch_hp_threshold:
            replacement = p.find_replacement()
            if replacement is not None:
                return _switch_action(replacement)

        # 低能量 → 聚能（蓄力中除外，蓄力技能必须释放）
        has_charging = getattr(s, '_charging', False)
        if s.energy <= style.gather_energy_threshold and not has_charging:
            return _GATHER_ACTION

        # 蓄力中：强制释放蓄力技能（游弋/嫉妒 可任选）
        if has_charging:
            # 游弋/嫉妒 trait：蓄力期间可选任一技能
            from .traits import get_trait
            h = get_trait(s)
            free_charge = h and h.name in ('游弋', '嫉妒')
            charged_idx, charged_skill = battle._charged_skill(s)
            if free_charge and charged_skill is None:
                pass  # fall through to normal skill selection
            elif charged_skill is not None:
                return _skill_action(charged_idx)
            elif may_switch:
                replacement = p.find_replacement()
                if replacement is not None:
                    return _switch_action(replacement)
                return _GATHER_ACTION

        # 道具·愿力：换出的血脉技能比最强攻击更疼/能斩杀 → 先用道具再出招，
        # 同一回合完成（放在换宠/聚能之后：确定要出手才花这一次次数与冷却）
        if (item and item.can_use(battle.turn)
                and should_use_wish(battle, self.team, s, opponent)):
            return _ITEM_ACTION

        # 评分所有技能（威力通过伤害计算器估算，含克制/天气/印记）
        best_idx = -1
        best_score = -1.0
        for i, skill in enumerate(s.skills):
            # 跳过冷却中 / 被封印的技能
            if s.skills[i].cooldown > 0:
                continue
            if s.skills[i].sealed:
                continue
            if skill.energy_cost > s.energy:
                continue

            if skill.is_attack:
                # 用伤害公式估算（保守：假设后手）；带上槽位号 —— 估伤要用它求值
                # `when{skill_at}` 一类的位置条件（械斗/磁暴/六自由度…）
                dmg, _ = battle._resolver.calc_damage(
                    s, opponent, SkillUse(battle_skill=skill, skill_index=i),
                    battle.globals,
                    attacker_team=self.team,
                )
                score = dmg * style.aggression
            else:
                # 防御 / 状态技按槽位序取第一个。旧 kind 层的「效果条数」判据
                # （`e.kind in ('stat','abnormal','mark')`）随该层于 2026-09-22
                # 删除：IR 语料下它恒为 0、从未影响过选择；要复活请改用
                # `sim/skill_ir.skill_profile`（需单独一轮量测）。
                score = 0.0

            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0:
            return _skill_action(best_idx)

        # 无可用技能 → 聚能或换宠
        if s.energy < 10:
            return _GATHER_ACTION
        if may_switch:
            replacement = p.find_replacement()
            if replacement is not None:
                return _switch_action(replacement)
        return _GATHER_ACTION

    def choose_replacement(self, battle: Battle) -> int:
        """力竭换宠：选对对手威胁最大的存活精灵。"""
        p = self.player
        opponent = battle.get_opponent(self.team).active
        alive = [i for i in p.alive_sprites if i != p.active_index]

        best_idx = -1
        best_score = -1.0
        for idx in alive:
            sprite = p.team[idx]
            # 用最强攻击技能的估算伤害评分
            max_dmg = 0
            for skill in sprite.skills:
                if skill.is_attack:
                    dmg, _ = battle._resolver.calc_damage(
                        sprite, opponent, SkillUse(battle_skill=skill), battle.globals,
                        attacker_team=self.team,
                    )
                    if dmg > max_dmg:
                        max_dmg = dmg
            score = max_dmg + sprite.current_hp * 0.1
            if score > best_score:
                best_score = score
                best_idx = idx

        # fallback: first alive
        if best_idx < 0 and alive:
            best_idx = alive[0]
        return best_idx

    def on_game_end(self, winner: str) -> None:
        pass


class HumanAgent:
    """终端人机交互代理。"""

    def __init__(self, team: str, player: Player, name: str = ''):
        self.team = team
        self.player = player
        self.name = name or player.name

    # ── 显示 ──

    def _render(self, battle: Battle) -> None:
        p = self.player
        opp = battle.get_opponent(self.team)
        s = p.active
        t = opp.active
        g = battle.globals

        print()
        print('═' * 50)
        weather_str = f'天气: {g.weather} ({g.weather_turns}回合)' if g.weather else '天气: 无'
        print(f'  回合 {battle.turn}  |  {weather_str}')
        print('═' * 50)

        # 我方
        hp_pct = s.current_hp / s.max_hp * 100 if s.max_hp else 0
        hp_bar = self._bar(hp_pct)
        print(f'  [{self.name}]  {s.name}  HP: {s.current_hp}/{s.max_hp} {hp_bar}  E: {s.energy}/10')
        self._print_effects(s)

        print('─' * 50)

        # 对手
        hp_pct2 = t.current_hp / t.max_hp * 100 if t.max_hp else 0
        hp_bar2 = self._bar(hp_pct2)
        print(f'  [{opp.name}]   {t.name}  HP: {t.current_hp}/{t.max_hp} {hp_bar2}  E: {t.energy}/10')
        self._print_effects(t)
        self._print_marks(battle, self.team)

        print('═' * 50)

    @staticmethod
    def _bar(pct: float, width: int = 15) -> str:
        filled = round(pct / 100 * width)
        bar = '█' * filled + '░' * (width - filled)
        return f'[{bar}]'

    @staticmethod
    def _print_effects(sprite: Sprite) -> None:
        from backend.vm.effect import AbnormalEffect
        s = sprite
        active = getattr(s, 'active_effects', None) or []
        if not active:
            return

        # 同名去重：stat/state 合并显示，abnormal 显示层数
        seen: dict[str, str] = {}  # name -> 显示文本
        for e in active:
            if isinstance(e, AbnormalEffect):
                seen[e.name] = f'{e.name}×{e.stacks}'
            elif e.name not in seen:
                seen[e.name] = e.name

        if seen:
            print(f'  效果: {", ".join(seen.values())}')

    @staticmethod
    def _print_marks(battle: Battle, team: str) -> None:
        pos, neg = battle.globals.get_marks(team)
        if pos or neg:
            parts = []
            for m in pos:
                parts.append(f'{m.name}(+×{m.stacks})')
            for m in neg:
                parts.append(f'{m.name}(-×{m.stacks})')
            print(f'  印记: {", ".join(parts)}')

    # ── 输入工具 ──

    @staticmethod
    def _input_int(prompt: str, min_val: int, max_val: int) -> int:
        while True:
            try:
                raw = input(prompt)
            except EOFError as err:
                raise SystemExit() from err
            try:
                v = int(raw.strip())
                if min_val <= v <= max_val:
                    return v
                print(f'  超出范围 {min_val}-{max_val}', end=' ')
            except ValueError:
                print('  请输入数字', end=' ')

    def _choose_switch_action(self, battle: Battle) -> Action:
        """力竭/无可用行动时强制换宠。"""
        p = self.player
        alive = [i for i in p.alive_sprites if i != p.active_index]
        if not alive:
            print('  无存活精灵可换!')
            return Action(kind='gather')
        print(f'\n{self.name} 选择替补:')
        for j, idx in enumerate(alive):
            sprite = p.team[idx]
            print(f'  [{j}] {sprite.name}  HP {sprite.current_hp}/{sprite.max_hp}')
        choice = self._input_int('选择 > ', 0, len(alive) - 1)
        return Action(kind='switch', switch_index=alive[choice])

    # ── 决策 ──

    def choose_lead(self, battle: Battle) -> int:
        print(f'\n{self.name} 选择首发精灵:')
        for i, sprite in enumerate(self.player.team):
            skills_str = ' '.join(s.name for s in sprite.skills)
            print(f'  [{i}] {sprite.name}  HP {sprite.current_hp}/{sprite.max_hp}  技能: {skills_str}')
        return self._input_int('选择 > ', 0, len(self.player.team) - 1)

    def choose_action(self, battle: Battle) -> Action:
        p = self.player
        s = p.active

        # 已力竭 → 强制换宠
        if s.is_fainted:
            return self._choose_switch_action(battle)

        opp = battle.get_opponent(self.team).active
        self._render(battle)

        options: list[tuple[str, Action | None]] = []

        # 道具
        item = p.item
        if item and item.can_use(battle.turn):
            remaining = item.max_uses - item.uses
            options.append((f'使用道具: {item.name} ({remaining}/{item.max_uses})', Action(kind='item')))

        # 技能
        for i, skill in enumerate(s.skills):
            cooldown = s.skills[i].cooldown > 0
            enough_e = skill.energy_cost <= s.energy
            disabled = cooldown or not enough_e
            tags: list[str] = []
            if cooldown:
                tags.append('冷却中')
            if not enough_e:
                tags.append('能量不足')
            tags.append(f'耗{skill.energy_cost}')

            # 估算伤害
            dmg_preview = ''
            if skill.is_attack and enough_e:
                dmg, _ = battle._resolver.calc_damage(
                    s, opp, SkillUse(battle_skill=skill), battle.globals,
                    attacker_team=self.team,
                )
                dmg_preview = f' → ~{dmg}伤害'

            label = f'{skill.name} ({skill.skill_type} {skill.element or "无"} 威力{skill.power} {",".join(tags)}){dmg_preview}'
            if disabled:
                label = f'[X] {label}'
            options.append((label, Action(kind='skill', skill_index=i) if not disabled else None))

        # 聚能
        options.append(('聚能 (+5能量)', Action(kind='gather')))

        # 换宠
        for i, sprite in enumerate(p.team):
            if i == p.active_index or sprite.is_fainted:
                continue
            hp_pct = sprite.current_hp / sprite.max_hp * 100 if sprite.max_hp else 0
            options.append((f'↓换宠→{sprite.name} (HP {sprite.current_hp}/{sprite.max_hp} {hp_pct:.0f}%)',
                           Action(kind='switch', switch_index=i)))

        # 渲染选项
        valid_options = []
        display_idx = 1
        for label, action in options:
            prefix = f'[{display_idx}]'
            valid_options.append(action)
            print(f'  {prefix} {label}')
            display_idx += 1

        choice = self._input_int('选择 > ', 1, len(valid_options))
        action = valid_options[choice - 1]
        if action is None:
            print('  不可用，请重选')
            return self.choose_action(battle)
        return action

    def choose_replacement(self, battle: Battle) -> int:
        p = self.player
        alive = [i for i in p.alive_sprites if i != p.active_index]

        print(f'\n{self.name} 选择替补:')
        for j, idx in enumerate(alive):
            sprite = p.team[idx]
            skills_str = ' '.join(s.name for s in sprite.skills)
            print(f'  [{j}] {sprite.name}  HP {sprite.current_hp}/{sprite.max_hp}  技能: {skills_str}')

        if not alive:
            return -1
        choice = self._input_int('选择 > ', 0, len(alive) - 1)
        return alive[choice]

    def on_game_end(self, winner: str) -> None:
        print(f'\n{"═" * 30}')
        if winner == self.team:
            print(f'  {self.name} 胜利!')
        else:
            print(f'  {self.name} 败北')
        print(f'{"═" * 30}')
