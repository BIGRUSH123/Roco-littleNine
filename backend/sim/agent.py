"""backend/sim/agent.py — 决策代理

TODO: 接入大模型 API（如 Claude/DeepSeek），将 battle 状态序列化为 prompt，
由 LLM 推理选择动作，替代当前规则评分。需解决：
  - 状态序列化格式（JSON / 自然语言）
  - 输出解析（JSON schema / function calling）
  - 延迟与成本控制（缓存、fallback 到 RuleAgent）
"""

from typing import Protocol, TYPE_CHECKING

from .action import Action
from .battleskill import SkillUse

if TYPE_CHECKING:
    from .battle import Battle
    from .player import Player


class Agent(Protocol):
    """决策代理协议。"""

    team: str

    def choose_lead(self, battle: 'Battle') -> int: ...
    def choose_action(self, battle: 'Battle') -> Action: ...
    def choose_replacement(self, battle: 'Battle') -> int: ...
    def on_game_end(self, winner: str) -> None: ...


class RuleAgent:
    """基于 PlayStyle 的规则 AI。"""

    def __init__(self, team: str, player: 'Player'):
        self.team = team
        self.player = player

    def choose_lead(self, battle: 'Battle') -> int:
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

    def choose_action(self, battle: 'Battle') -> Action:
        p = self.player
        s = p.active
        style = p.style

        # 已力竭 → 强制换宠
        if s.is_fainted:
            replacement = p.find_replacement()
            if replacement is not None:
                return Action(kind='switch', switch_index=replacement)
            return Action(kind='gather')

        # 道具使用（进化之力早期用，愿力强化低HP用）
        item = p.item
        if item and item.can_use(battle.turn):
            if item.name == '进化之力' and battle.turn <= 2:
                return Action(kind='item')
            if item.name == '愿力':
                hp_ratio = s.current_hp / s.max_hp if s.max_hp > 0 else 0
                if hp_ratio < 0.5 and style.aggression > 0.4:
                    return Action(kind='item')

        # 低 HP → 可能换宠
        hp_ratio = s.current_hp / s.max_hp if s.max_hp > 0 else 0
        if hp_ratio < style.switch_hp_threshold:
            replacement = p.find_replacement()
            if replacement is not None:
                return Action(kind='switch', switch_index=replacement)

        # 低能量 → 聚能（蓄力中除外，蓄力技能必须释放）
        has_charging = getattr(s, '_charging', False)
        if s.energy <= style.gather_energy_threshold and not has_charging:
            return Action(kind='gather')

        # 蓄力中：强制释放蓄力技能（游弋/嫉妒 可任选）
        if has_charging:
            charged_idx = getattr(s, '_charged_skill_index', -1)
            # 游弋/嫉妒 trait：蓄力期间可选任一技能
            from .traits import get_trait
            h = get_trait(s)
            free_charge = h and h.name in ('游弋', '嫉妒')
            if free_charge and charged_idx < 0:
                pass  # fall through to normal skill selection
            elif 0 <= charged_idx < len(s.skills) and not s.skills[charged_idx].sealed:
                return Action(kind='skill', skill_index=charged_idx)

        opponent = battle.get_opponent(self.team).active

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
                # 用伤害公式估算（保守：假设后手）
                dmg, _ = battle._resolver.calc_damage(
                    s, opponent, SkillUse(battle_skill=skill), battle.globals,
                    attacker_team=self.team,
                )
                score = dmg * style.aggression
            elif skill.is_defense:
                # 防御技能：按效果数量 + 应对能力给分
                n_effects = sum(1 for e in skill.effects if e.kind in ('stat', 'abnormal', 'mark'))
                score = n_effects * 20 * (1.0 - style.aggression)
            else:
                # 状态技能：按效果数量给分
                n_effects = sum(1 for e in skill.effects if e.kind in ('stat', 'abnormal', 'mark'))
                score = n_effects * 15 * (1.0 - style.aggression)

            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0:
            return Action(kind='skill', skill_index=best_idx)

        # 无可用技能 → 聚能或换宠
        if s.energy < 10:
            return Action(kind='gather')
        replacement = p.find_replacement()
        if replacement is not None:
            return Action(kind='switch', switch_index=replacement)
        return Action(kind='gather')

    def choose_replacement(self, battle: 'Battle') -> int:
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

    def __init__(self, team: str, player: 'Player', name: str = ''):
        self.team = team
        self.player = player
        self.name = name or player.name

    # ── 显示 ──

    def _render(self, battle: 'Battle') -> None:
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
    def _print_effects(sprite: 'Sprite') -> None:
        from .sprite import Sprite
        s: 'Sprite' = sprite  # type: ignore
        if not s.effects:
            return

        # 同名去重：stat/state 合并显示，abnormal 显示层数
        seen: dict[str, str] = {}  # name -> 显示文本
        for e in s.effects:
            if e.category == 'abnormal':
                seen[e.name] = f'{e.name}×{e.stacks}'
            elif e.name not in seen:
                seen[e.name] = e.name

        if seen:
            print(f'  效果: {", ".join(seen.values())}')

    @staticmethod
    def _print_marks(battle: 'Battle', team: str) -> None:
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
            except EOFError:
                raise SystemExit()
            try:
                v = int(raw.strip())
                if min_val <= v <= max_val:
                    return v
                print(f'  超出范围 {min_val}-{max_val}', end=' ')
            except ValueError:
                print('  请输入数字', end=' ')

    def _choose_switch_action(self, battle: 'Battle') -> Action:
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

    def choose_lead(self, battle: 'Battle') -> int:
        print(f'\n{self.name} 选择首发精灵:')
        for i, sprite in enumerate(self.player.team):
            skills_str = ' '.join(s.name for s in sprite.skills)
            print(f'  [{i}] {sprite.name}  HP {sprite.current_hp}/{sprite.max_hp}  技能: {skills_str}')
        return self._input_int('选择 > ', 0, len(self.player.team) - 1)

    def choose_action(self, battle: 'Battle') -> Action:
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

    def choose_replacement(self, battle: 'Battle') -> int:
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
