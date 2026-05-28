"""tests/sim/test_round_record.py — RoundRecord 测试

测试 to_message / from_message 往返、to_frontend_events 兼容性。
"""

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.round_record import (
    ActionRecord,
    RoundRecord,
    _action_short,
)

# ═══════════════════════════════════════════════════════════════
# to_message / from_message 往返
# ═══════════════════════════════════════════════════════════════

def test_roundtrip_empty():
    """空回合往返：无行动、无事件。"""
    rec = RoundRecord(
        turn=1,
        sprite_a='大头骨龙',
        sprite_b='蹦蹦草',
    )
    msg = rec.to_message()
    parsed = RoundRecord.from_message(msg)

    assert parsed.turn == 1
    assert parsed.sprite_a == '大头骨龙'
    assert parsed.sprite_b == '蹦蹦草'
    assert parsed.weather == ''
    assert parsed.turn_start_events == []
    assert parsed.action_a is None
    assert parsed.action_b is None
    assert parsed.faint_check_events == []
    assert parsed.turn_end_events == []
    print('  [OK] 空回合往返')


def test_roundtrip_full():
    """完整回合往返：双方技能 + 各阶段事件。"""
    rec = RoundRecord(
        turn=3,
        weather='晴天',
        sprite_a='大头骨龙',
        sprite_b='蹦蹦草',
        turn_start_events=[
            '大头骨龙 传动→ 龙吟/龙血/甩水',
            '蹦蹦草 不朽: 检查复活',
        ],
        action_a=ActionRecord(
            team='A', actor='大头骨龙', kind='skill', skill_name='龙吟',
            events=['大头骨龙 开始蓄力'],
        ),
        action_b=ActionRecord(
            team='B', actor='蹦蹦草', kind='skill', skill_name='毒孢子',
            events=['大头骨龙 中毒+5层'],
        ),
        faint_check_events=[],
        turn_end_events=[
            '大头骨龙 中毒-44HP',
            '蹦蹦草 寄生种子+3HP',
        ],
    )
    msg = rec.to_message()
    parsed = RoundRecord.from_message(msg)

    assert parsed.turn == 3
    assert parsed.weather == '晴天'
    assert parsed.sprite_a == '大头骨龙'
    assert parsed.sprite_b == '蹦蹦草'
    assert parsed.turn_start_events == rec.turn_start_events
    assert parsed.action_a is not None
    assert parsed.action_a.team == 'A'
    assert parsed.action_a.skill_name == '龙吟'
    assert parsed.action_a.events == ['大头骨龙 开始蓄力']
    assert parsed.action_b is not None
    assert parsed.action_b.skill_name == '毒孢子'
    assert parsed.action_b.events == ['大头骨龙 中毒+5层']
    assert parsed.faint_check_events == []
    assert parsed.turn_end_events == rec.turn_end_events
    print('  [OK] 完整回合往返')


def test_roundtrip_switch():
    """换宠行动往返：kind=switch。"""
    rec = RoundRecord(
        turn=2,
        sprite_a='水灵',
        sprite_b='水灵',
        action_a=ActionRecord(
            team='A', actor='水灵', kind='switch', skill_name='波波拉',
            events=['水灵↓ 波波拉↑', '水灵 蓄力中断（换宠）'],
        ),
        action_b=ActionRecord(
            team='B', actor='水灵', kind='skill', skill_name='猛烈撞击',
            events=['波波拉 -30HP'],
        ),
    )
    msg = rec.to_message()
    parsed = RoundRecord.from_message(msg)

    assert parsed.action_a is not None
    assert parsed.action_a.kind == 'switch'
    assert parsed.action_a.skill_name == '波波拉'
    assert parsed.action_a.events == ['水灵↓ 波波拉↑', '水灵 蓄力中断（换宠）']
    assert parsed.action_b is not None
    assert parsed.action_b.kind == 'skill'
    print('  [OK] 换宠回合往返')


def test_roundtrip_faint():
    """力竭回合往返：力竭事件在 action events 中。"""
    rec = RoundRecord(
        turn=5,
        sprite_a='大头骨龙',
        sprite_b='蹦蹦草',
        action_a=ActionRecord(
            team='A', actor='大头骨龙', kind='skill', skill_name='龙吟',
            events=[
                '蹦蹦草 -200HP',
                '蹦蹦草 力竭(B 魔力-1→3)',
                '蹦蹦草 力竭↓ 游蛇魔使↑',
            ],
        ),
    )
    msg = rec.to_message()
    parsed = RoundRecord.from_message(msg)

    assert parsed.action_a is not None
    assert parsed.action_a.events == rec.action_a.events
    assert parsed.faint_check_events == []
    print('  [OK] 力竭回合往返')


def test_roundtrip_gather():
    """聚能行动往返：kind=gather。"""
    rec = RoundRecord(
        turn=1,
        sprite_a='大头骨龙',
        sprite_b='蹦蹦草',
        action_a=ActionRecord(
            team='A', actor='大头骨龙', kind='gather', skill_name='聚能',
            events=['大头骨龙 聚能+5E(→10)'],
        ),
        action_b=ActionRecord(
            team='B', actor='蹦蹦草', kind='skill', skill_name='毒孢子',
            events=['大头骨龙 中毒+5层'],
        ),
    )
    msg = rec.to_message()
    parsed = RoundRecord.from_message(msg)

    assert parsed.action_a is not None
    assert parsed.action_a.kind == 'gather'
    assert parsed.action_a.skill_name == '聚能'
    print('  [OK] 聚能回合往返')


def test_roundtrip_no_weather():
    """无天气时 WEATHER 行不出现。"""
    rec = RoundRecord(turn=1, sprite_a='A', sprite_b='B')
    msg = rec.to_message()
    assert '>>>WEATHER' not in msg
    print('  [OK] 无天气不输出WEATHER行')


# ═══════════════════════════════════════════════════════════════
# from_message 错误处理
# ═══════════════════════════════════════════════════════════════

def test_from_message_missing_header():
    """缺少回合头应抛异常。"""
    try:
        RoundRecord.from_message('垃圾文本\n>>>TURN_START\n<<<TURN_START')
        raise AssertionError('应抛 ValueError')
    except ValueError as e:
        assert '回合头' in str(e)
    print('  [OK] 缺少回合头抛异常')


def test_from_message_bad_action_header():
    """损坏的 ACTION 头应抛异常。"""
    msg = (
        '══════ 第1回合 ══════\n'
        '>>>SPRITES:A:x|B:y\n'
        '>>>ACTION:badformat\n'
        '<<<ACTION\n'
    )
    try:
        RoundRecord.from_message(msg)
        raise AssertionError('应抛 ValueError')
    except ValueError as e:
        assert 'ACTION' in str(e)
    print('  [OK] 损坏ACTION头抛异常')


# ═══════════════════════════════════════════════════════════════
# to_frontend_events 兼容性
# ═══════════════════════════════════════════════════════════════

def test_to_frontend_events_format():
    """to_frontend_events 产出含 >>>ACTION 和 >>>PHASE 标记的格式。"""
    rec = RoundRecord(
        turn=1,
        sprite_a='大头骨龙',
        sprite_b='蹦蹦草',
        turn_start_events=['大头骨龙 传动→ 龙吟/龙血/甩水'],
        action_a=ActionRecord(
            team='A', actor='大头骨龙', kind='skill', skill_name='龙吟',
            events=['大头骨龙 开始蓄力'],
        ),
        action_b=ActionRecord(
            team='B', actor='蹦蹦草', kind='skill', skill_name='毒孢子',
            events=['大头骨龙 中毒+5层'],
        ),
        turn_end_events=['大头骨龙 中毒-44HP'],
    )
    events = rec.to_frontend_events(
        header='[回合1] 大头骨龙：龙吟 | 蹦蹦草：毒孢子',
    )

    # 回合头 + SPRITES
    assert events[0] == '[回合1] 大头骨龙：龙吟 | 蹦蹦草：毒孢子'
    assert '>>>SPRITES:大头骨龙|蹦蹦草' in events

    # TURN_START phase
    ts_idx = events.index('>>>PHASE:TURN_START')
    assert events[ts_idx + 1] == '大头骨龙 传动→ 龙吟/龙血/甩水'
    assert '<<<PHASE' in events[ts_idx:ts_idx + 5]

    # ACTION A — gate 结果在 action 内
    a_idx = events.index('>>>ACTION:大头骨龙:龙吟')
    assert events[a_idx + 1] == '大头骨龙 开始蓄力'
    assert '<<<ACTION' in events[a_idx:a_idx + 5]

    # ACTION B — 毒伤在 action 内
    b_idx = events.index('>>>ACTION:蹦蹦草:毒孢子')
    assert events[b_idx + 1] == '大头骨龙 中毒+5层'

    # TURN_END phase — 毒伤在回合末
    te_idx = events.index('>>>PHASE:TURN_END')
    assert events[te_idx + 1] == '大头骨龙 中毒-44HP'
    print('  [OK] to_frontend_events 格式正确: TURN_START → ACTION A → ACTION B → TURN_END')


def test_to_frontend_events_auto_header():
    """不传 header 时自动生成回合标题。"""
    rec = RoundRecord(
        turn=2,
        sprite_a='水灵',
        sprite_b='水灵',
        action_a=ActionRecord(
            team='A', actor='水灵', kind='skill', skill_name='猛烈撞击',
        ),
    )
    events = rec.to_frontend_events()
    assert events[0] == '[回合2] 水灵：猛烈撞击 | 水灵：—'
    print('  [OK] 自动生成回合标题')


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def test_action_short():
    """_action_short 各类型简短描述。"""
    assert _action_short(ActionRecord('A', 'x', 'skill', '龙吟')) == '龙吟'
    assert _action_short(ActionRecord('A', 'x', 'gather', '聚能')) == '聚能'
    assert _action_short(ActionRecord('A', 'x', 'switch', '波波拉')) == '换宠→波波拉'
    assert _action_short(ActionRecord('A', 'x', 'item', '进化之力')) == '道具(进化之力)'
    assert _action_short(None) == '—'
    print('  [OK] _action_short 各类型')


def test_summary():
    """summary() 兼容旧 TurnRecord。"""
    rec = RoundRecord(
        turn=3,
        sprite_a='A',
        sprite_b='B',
        action_a=ActionRecord('A', 'A', 'skill', '龙吟'),
        action_b=ActionRecord('B', 'B', 'skill', '毒孢子'),
    )
    s = rec.summary()
    assert 'T3' in s
    assert '龙吟' in s
    assert '毒孢子' in s
    print('  [OK] summary() 兼容')


# ═══════════════════════════════════════════════════════════════
# 多回合解析
# ═══════════════════════════════════════════════════════════════

def test_parse_multi_round():
    """多回合文本：每回合独立解析。"""
    r1 = RoundRecord(
        turn=1,
        sprite_a='A1', sprite_b='B1',
        action_a=ActionRecord('A', 'A1', 'skill', '撞击', ['A1 -10HP']),
    )
    r2 = RoundRecord(
        turn=2,
        sprite_a='A2', sprite_b='B2',
        action_a=ActionRecord('A', 'A2', 'skill', '水枪', ['B2 -20HP']),
    )
    combined = r1.to_message() + '\n' + r2.to_message()

    parts = combined.split('══════ 第')
    assert len(parts) == 3  # ['', '1回合 ══════\n...', '2回合 ══════\n...']

    parsed1 = RoundRecord.from_message('══════ 第' + parts[1])
    parsed2 = RoundRecord.from_message('══════ 第' + parts[2])

    assert parsed1.turn == 1
    assert parsed1.action_a.skill_name == '撞击'
    assert parsed2.turn == 2
    assert parsed2.action_a.skill_name == '水枪'
    print('  [OK] 多回合独立解析')


if __name__ == '__main__':
    test_roundtrip_empty()
    test_roundtrip_full()
    test_roundtrip_switch()
    test_roundtrip_faint()
    test_roundtrip_gather()
    test_roundtrip_no_weather()
    test_from_message_missing_header()
    test_from_message_bad_action_header()
    test_to_frontend_events_format()
    test_to_frontend_events_auto_header()
    test_action_short()
    test_summary()
    test_parse_multi_round()
    print('\n  [ALL ROUND RECORD TESTS PASSED]')
