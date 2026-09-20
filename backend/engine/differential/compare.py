"""compare — 两份对局录制文件的逐回合比对。

返回人类可读的分歧报告（首个不一致的回合、字段路径、双方取值）。
"""

from __future__ import annotations

import json
from typing import Any

MAX_REPORT = 20


def _fmt(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def _walk(a: Any, b: Any, path: str, diffs: list[str]) -> None:
    """深度比对，收集所有差异（有界）。"""
    if len(diffs) >= MAX_REPORT:
        return
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        diffs.append(f'{path}: 类型不一致 {type(a).__name__} vs {type(b).__name__}')
        return
    if isinstance(a, dict):
        for k in sorted(set(a.keys()) | set(b.keys()), key=str):
            if len(diffs) >= MAX_REPORT:
                return
            if k not in a:
                diffs.append(f'{path}.{k}: 仅存在于新结果: {_fmt(b[k])}')
            elif k not in b:
                diffs.append(f'{path}.{k}: 仅存在于金标准: {_fmt(a[k])}')
            else:
                _walk(a[k], b[k], f'{path}.{k}', diffs)
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f'{path}: 长度不一致 {len(a)} vs {len(b)}')
        for i, (x, y) in enumerate(zip(a, b)):
            if len(diffs) >= MAX_REPORT:
                return
            _walk(x, y, f'{path}[{i}]', diffs)
    elif a != b:
        diffs.append(f'{path}: {_fmt(a)} != {_fmt(b)}')


def compare_fixtures(golden: dict, actual: dict) -> list[str]:
    """比对金标准与实际结果，返回分歧列表（空列表 = 完全一致）。"""
    diffs: list[str] = []

    # 元信息：阵容/道具/结果必须一致
    for key in ('seed', 'item_a', 'item_b', 'lead_a', 'lead_b', 'winner'):
        if golden.get(key) != actual.get(key):
            diffs.append(f'meta.{key}: {_fmt(golden.get(key))} != {_fmt(actual.get(key))}')
    if len(golden.get('team_a', [])) != len(actual.get('team_a', [])) or \
       len(golden.get('team_b', [])) != len(actual.get('team_b', [])):
        diffs.append('meta.team: 队伍规模不一致')
    else:
        for side in ('team_a', 'team_b'):
            for i, (ga, aa) in enumerate(zip(golden.get(side, []), actual.get(side, []))):
                if ga.get('name') != aa.get('name'):
                    diffs.append(f'meta.{side}[{i}].name: {ga.get("name")} != {aa.get("name")}')
                if sorted(ga.get('skills', [])) != sorted(aa.get('skills', [])):
                    diffs.append(f'meta.{side}[{i}].skills: {ga.get("skills")} != {aa.get("skills")}')

    # 逐回合
    g_turns, a_turns = golden.get('turns', []), actual.get('turns', [])
    if len(g_turns) != len(a_turns):
        diffs.append(f'turns: 回合数不一致 {len(g_turns)} vs {len(a_turns)}')

    for i, (gt, at) in enumerate(zip(g_turns, a_turns)):
        if len(diffs) >= MAX_REPORT:
            break
        before = len(diffs)
        _walk(gt, at, f'turn[{i}]', diffs)
        if len(diffs) > before:
            # 在该回合前插入提示行
            diffs.insert(before, f'── 回合 {i + 1} 出现分歧 ──')
    return diffs


def first_divergence_turn(golden: dict, actual: dict) -> int:
    """首个分歧回合号（1-based），完全一致返回 -1。"""
    diffs: list[str] = []
    for i, (gt, at) in enumerate(zip(golden.get('turns', []), actual.get('turns', []))):
        diffs.clear()
        _walk(gt, at, f'turn[{i}]', diffs)
        if diffs:
            return i + 1
    if len(golden.get('turns', [])) != len(actual.get('turns', [])):
        return min(len(golden.get('turns', [])), len(actual.get('turns', []))) + 1
    return -1
