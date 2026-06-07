"""backend/engine/ai/battle_log.py — 单局回合技能日志记录器

每局自我博弈自动生成一条 JSONL 记录，包含：
  - 双方队伍
  - 每回合双方精灵 + 技能名
  - 终局结果

便于快速定位 bug（如某技能导致异常、某精灵配置有误等）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def extract_battle_summary(battle, end_reason: str) -> dict[str, Any]:
    """从对局对象提取紧凑的回合技能摘要。

    Args:
        battle: 已结束的 Battle 对象（含 battle.log 列表）。
        end_reason: 终局原因字符串。

    Returns:
        字典：teams, rounds (每回合技能摘要), winner, end_reason。
    """
    team_a = [s.name for s in battle.player_a.team]
    team_b = [s.name for s in battle.player_b.team]
    winner = battle.winner or "draw"

    rounds: list[dict[str, Any]] = []
    for rec in battle.log:
        rnd: dict[str, Any] = {
            "turn": rec.turn,
            "weather": rec.weather or "",
            "sprite_a": rec.sprite_a,
            "sprite_b": rec.sprite_b,
        }
        # A 方行动
        if rec.action_a is not None:
            rnd["action_a"] = {
                "kind": rec.action_a.kind,
                "skill": rec.action_a.skill_name,
                "actor": rec.action_a.actor,
            }
        # B 方行动
        if rec.action_b is not None:
            rnd["action_b"] = {
                "kind": rec.action_b.kind,
                "skill": rec.action_b.skill_name,
                "actor": rec.action_b.actor,
            }
        rounds.append(rnd)

    return {
        "teams": {"A": team_a, "B": team_b},
        "rounds": rounds,
        "winner": winner,
        "end_reason": end_reason,
        "turns": len(rounds),
    }


class BattleLogWriter:
    """单次训练运行的逐局技能日志写入器。

    每局一条 JSONL，写入 <log_dir>/battles_<run_id>.jsonl。
    内置缓冲区控制，accumulate 到 buffer_size 条才 flush，
    避免多 Worker 高并发时频繁系统调用阻塞主进程。
    """

    def __init__(self, log_dir: str | Path, run_id: str = "", buffer_size: int = 100) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"battles_{run_id}" if run_id else "battles"
        self.path = self.log_dir / f"{prefix}.jsonl"
        self._fp = open(self.path, "w", encoding="utf-8")
        self._buffer_size = buffer_size
        self._write_count = 0

    def write(self, summary: dict[str, Any]) -> None:
        """写入一条对局摘要（JSONL 一行），仅达阈值时 flush。"""
        self._fp.write(json.dumps(summary, ensure_ascii=False) + "\n")
        self._write_count += 1
        if self._write_count >= self._buffer_size:
            self._fp.flush()
            self._write_count = 0

    def close(self) -> None:
        """关闭前确保落盘。"""
        self._fp.flush()
        self._fp.close()

    def __enter__(self) -> "BattleLogWriter":
        return self

    def __exit__(self, *args) -> None:
        self.close()
