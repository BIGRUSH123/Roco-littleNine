# -*- coding: utf-8 -*-
"""阵容专家共用的小工具（多条队伍共享同一机制时只留一份实现）。"""
from __future__ import annotations


def switch_target(player, cand) -> str:
    """换人候选 → 目标精灵名（规划层用 `switch_index`、EV 层用 `index`）。"""
    idx = getattr(cand, "switch_index", None)
    if idx is None:
        idx = getattr(cand, "index", None)
    if not isinstance(idx, int):
        return ""
    try:
        return player.team[idx].name
    except (IndexError, TypeError):
        return ""


def drop_switch_to(player, cands, name_prefix: str):
    """把"换到名字以此开头的精灵"的候选去掉。"""
    out = []
    for cand in cands:
        if getattr(cand, "kind", "") == "switch" \
                and switch_target(player, cand).startswith(name_prefix):
            continue
        out.append(cand)
    return out


def heal_bot_guard(player, active, cands, *, bot_prefix: str, hp_threshold: float):
    """`正位宝剑` 类精灵（只剩 1 号位治疗技）是"治疗机器人"：
    我方血线健康时**不要换它上场**（否则一回合白丢 —— 实测铁头海豹 75% 平局就是这么来的）。"""
    hp_ok = (getattr(active, "current_hp", 0) / max(1, getattr(active, "max_hp", 1))) \
        >= hp_threshold
    if not hp_ok:
        return cands
    return drop_switch_to(player, cands, bot_prefix)
