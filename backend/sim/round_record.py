"""backend/sim/round_record.py — 结构化回合记录

RoundRecord 替代 TurnRecord，提供：
- to_message()   → 人类可读 + 机器可解析的文本格式（用于 .log 文件）
- from_message() → 从文本解析回 RoundRecord（用于 bug 排查）
- to_frontend_events() → 兼容现有前端的 list[str] 格式
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ActionRecord:
    """单个行动记录（技能/聚能/换宠/道具）。"""
    team: str           # 'A' | 'B'
    actor: str          # 精灵名
    kind: str           # 'skill' | 'gather' | 'switch' | 'item'
    skill_name: str = ''  # 技能名（kind != skill 时可为空）
    events: list[str] = field(default_factory=list)


@dataclass
class RoundRecord:
    """一回合的完整结构化记录。"""
    turn: int
    weather: str = ''
    sprite_a: str = ''  # "大头骨龙"
    sprite_b: str = ''  # "蹦蹦草"
    first_team: str = ''  # "A" or "B"
    turn_start_events: list[str] = field(default_factory=list)
    action_a: Optional[ActionRecord] = None
    action_b: Optional[ActionRecord] = None
    faint_check_events: list[str] = field(default_factory=list)
    turn_end_events: list[str] = field(default_factory=list)

    # transient: 由 execute_turn 设置，不参与序列化
    _header: str = ''

    # ═══════════════════════════════════════════════════════════════
    # to_message() — 人类可读 + 可解析格式
    # ═══════════════════════════════════════════════════════════════

    def to_message(self) -> str:
        """转为带标记的文本格式，可直接写入 .log 文件。"""
        lines: list[str] = []
        lines.append(f'══════ 第{self.turn}回合 ══════')

        # sprites
        lines.append(f'>>>SPRITES:A:{self.sprite_a}|B:{self.sprite_b}')

        # weather
        if self.weather:
            lines.append(f'>>>WEATHER:{self.weather}')

        # turn_start
        lines.append('>>>TURN_START')
        for e in self.turn_start_events:
            lines.append(f'  {e}')
        lines.append('<<<TURN_START')

        # actions
        for ar in (self.action_a, self.action_b):
            if ar is None:
                continue
            lines.append(f'>>>ACTION:{ar.team}:{ar.kind}:{ar.actor}:{ar.skill_name}')
            for e in ar.events:
                lines.append(f'  {e}')
            lines.append('<<<ACTION')

        # faint_check
        lines.append('>>>FAINT_CHECK')
        for e in self.faint_check_events:
            lines.append(f'  {e}')
        lines.append('<<<FAINT_CHECK')

        # turn_end
        lines.append('>>>TURN_END')
        for e in self.turn_end_events:
            lines.append(f'  {e}')
        lines.append('<<<TURN_END')

        return '\n'.join(lines)

    # ═══════════════════════════════════════════════════════════════
    # from_message() — 从文本解析
    # ═══════════════════════════════════════════════════════════════

    _ROUND_RE = re.compile(r'══════ 第(\d+)回合 ══════')
    _ACTION_RE = re.compile(r'>>>ACTION:([AB]):(\w+):([^:]+):(.*)')
    _SPRITES_RE = re.compile(r'>>>SPRITES:A:([^|]+)\|B:(.+)')
    _WEATHER_RE = re.compile(r'>>>WEATHER:(.+)')

    @classmethod
    def from_message(cls, text: str) -> 'RoundRecord':
        """从 to_message() 输出的文本解析回 RoundRecord。

        解析失败抛 ValueError 带行号，方便定位损坏的日志文件。
        """
        lines = text.split('\n')
        if not lines:
            raise ValueError('空文本')

        # parse header
        m = cls._ROUND_RE.match(lines[0])
        if not m:
            raise ValueError(f'第1行: 期望回合头 "══════ 第N回合 ══════", got {lines[0]!r}')
        rec = cls(turn=int(m.group(1)))

        i = 1
        n = len(lines)
        while i < n:
            line = lines[i]

            if line.startswith('>>>SPRITES:'):
                sm = cls._SPRITES_RE.match(line)
                if sm:
                    rec.sprite_a = sm.group(1).strip()
                    rec.sprite_b = sm.group(2).strip()
                i += 1

            elif line.startswith('>>>WEATHER:'):
                wm = cls._WEATHER_RE.match(line)
                if wm:
                    rec.weather = wm.group(1).strip()
                i += 1

            elif line == '>>>TURN_START':
                i += 1
                while i < n and lines[i] != '<<<TURN_START':
                    rec.turn_start_events.append(_unindent(lines[i]))
                    i += 1
                if i < n and lines[i] == '<<<TURN_START':
                    i += 1

            elif line.startswith('>>>ACTION:'):
                am = cls._ACTION_RE.match(line)
                if am:
                    ar = ActionRecord(
                        team=am.group(1),
                        kind=am.group(2),
                        actor=am.group(3),
                        skill_name=am.group(4),
                    )
                    i += 1
                    while i < n and lines[i] != '<<<ACTION':
                        ar.events.append(_unindent(lines[i]))
                        i += 1
                    if i < n and lines[i] == '<<<ACTION':
                        i += 1
                    if ar.team == 'A':
                        rec.action_a = ar
                    else:
                        rec.action_b = ar
                else:
                    raise ValueError(f'第{i + 1}行: 无法解析 ACTION 头: {line!r}')

            elif line == '>>>FAINT_CHECK':
                i += 1
                while i < n and lines[i] != '<<<FAINT_CHECK':
                    rec.faint_check_events.append(_unindent(lines[i]))
                    i += 1
                if i < n and lines[i] == '<<<FAINT_CHECK':
                    i += 1

            elif line == '>>>TURN_END':
                i += 1
                while i < n and lines[i] != '<<<TURN_END':
                    rec.turn_end_events.append(_unindent(lines[i]))
                    i += 1
                if i < n and lines[i] == '<<<TURN_END':
                    i += 1

            else:
                # skip empty / unknown lines
                i += 1

        return rec

    # ═══════════════════════════════════════════════════════════════
    # to_frontend_events() — 兼容现有前端格式
    # ═══════════════════════════════════════════════════════════════

    def to_frontend_events(self, header: str = '') -> list[str]:
        """产出兼容 BattleLog.vue 的 list[str] 格式。

        与旧 TurnRecord.events 格式一致，但修复了 >>>ACTION 包裹 gate 结果。
        同时添加 >>>PHASE 标记，前端可渐进适配。
        """
        events: list[str] = []

        if header:
            events.append(header)
        else:
            a_short = _action_short(self.action_a)
            b_short = _action_short(self.action_b)
            events.append(f'[回合{self.turn}] {self.sprite_a}：{a_short} | {self.sprite_b}：{b_short}')

        events.append(f'>>>SPRITES:{self.sprite_a}|{self.sprite_b}')

        # turn_start events 作为 preEffects（前端目前渲染在 action 之前）
        events.append('>>>PHASE:TURN_START')
        events.extend(self.turn_start_events)
        events.append('<<<PHASE')

        # actions — 按结算顺序排列（换宠 > 技能/聚能）
        ordered = list(self._ordered_actions())
        for ar in ordered:
            events.append(f'>>>ACTION:{ar.actor}:{ar.skill_name}')
            events.extend(ar.events)
            events.append('<<<ACTION')

        # faint_check
        events.append('>>>PHASE:FAINT_CHECK')
        events.extend(self.faint_check_events)
        events.append('<<<PHASE')

        # turn_end
        events.append('>>>PHASE:TURN_END')
        events.extend(self.turn_end_events)
        events.append('<<<PHASE')

        return events

    def _ordered_actions(self):
        """按结算顺序 yield ActionRecord：first_team 优先，换宠优先于技能。"""
        if not self.first_team:
            # 未记录 first_team（如双方换宠随机先后），按 kind 排序
            items = [ar for ar in (self.action_a, self.action_b) if ar is not None]
            items.sort(key=lambda ar: 0 if ar.kind == 'switch' else 1)
            yield from items
            return
        first = self.action_a if self.first_team == 'A' else self.action_b
        second = self.action_b if self.first_team == 'A' else self.action_a
        if first is not None:
            yield first
        if second is not None:
            yield second

    def summary(self) -> str:
        """兼容旧 TurnRecord.summary()。"""
        a = _action_short(self.action_a)
        b = _action_short(self.action_b)
        return (
            f'T{self.turn} '
            f'A: {a} | B: {b}'
        )


# ═══════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════

def _unindent(line: str) -> str:
    """去掉前导缩进（2空格）。"""
    if line.startswith('  '):
        return line[2:]
    return line


def _action_short(ar: Optional[ActionRecord]) -> str:
    """行动简短描述，用于回合标题。"""
    if ar is None:
        return '—'
    if ar.kind == 'switch':
        return f'换宠→{ar.skill_name}'
    if ar.kind == 'gather':
        return '聚能'
    if ar.kind == 'item':
        return f'道具({ar.skill_name})'
    return ar.skill_name
