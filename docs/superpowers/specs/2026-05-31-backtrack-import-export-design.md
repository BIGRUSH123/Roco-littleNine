# 回溯 & 导入导出 设计说明

2026-05-31

## 概述

在 V1.0 对战引擎基础上，实现两个功能：

1. **回溯** — 对局中恢复到指定回合的状态，从那里继续对战（内存级，单次会话内）
2. **导入导出** — 队伍/对局的 JSON + 文本双格式导入导出（CLI + 文件系统）

两者共享同一套序列化机制：所有有状态对象实现 `to_dict()` / `from_dict()`。

## 目标用户场景

- **AI 训练**：从同一个回合状态尝试不同行动分支，比较结果差异
- **数据增强**：一场对局产生多组训练样本
- **调试**：导出对局日志，手动检查回合细节
- **队伍复用**：导出队伍配置供其他对局使用

## 架构

```
backend/engine/serializer.py   ← 核心序列化（纯逻辑，无 I/O）
  ├─→ roco/serializer.py       ← CLI + Python API（训练脚本用）
  └─→ backend/api/main.py      ← Web 端点（前端用，按需添加）
```

## 回溯

### API

```python
battle.save_snapshot()            # 拍快照（当前回合状态）
battle.restore_snapshot(turn)     # 恢复到指定回合
battle.snapshots                  # dict[int, dict] 内存快照
battle.clear_snapshots()          # 释放内存
```

### 行为

- `execute_turn()` 在回合开始时自动调用 `save_snapshot()`
- 恢复时完整重建：精灵状态、天气、印记、VM 计数器、技能历史、观测器
- 仅限内存，不持久化到磁盘

### 训练用法

```python
battle = Battle(player_a, player_b)
for _ in range(10):
    battle.execute_turn(agent_a, agent_b)

battle.restore_snapshot(5)   # 回到第 5 回合
for _ in range(5):            # 走不同分支
    battle.execute_turn(agent_a, agent_b)
```

## 导入导出

### CLI 命令

| 命令 | 功能 |
|------|------|
| `python -m roco.serializer export team --team A -o name` | 导出队伍 A |
| `python -m roco.serializer import team name` | 导入队伍 |
| `python -m roco.serializer export match -o name` | 导出当前对局 |
| `python -m roco.serializer import match name` | 导入对局 |

输出目录默认为 `./exports/`，每次导出产生 `.roco-team.json` / `.roco-match.json` 和 `.roco-team.txt` / `.roco-match.txt` 两个文件。

### JSON 格式

Match JSON:
```json
{
  "version": "1.0",
  "type": "match",
  "turn": 5,
  "winner": null,
  "weather": "晴天",
  "player_a": { "name": "...", "lives": 3, "active_index": 0,
                "devotion": {}, "team": [...] },
  "player_b": { ... },
  "globals": { "weather": "...", "weather_turns": 3,
               "marks_a": [...], "marks_b": [...] },
  "log": [ ... ],
  "vm_state": { "counter_values": {...}, "burst_effects": {...},
                "burst_names": [...], "skill_history": {...} }
}
```

Team JSON:
```json
{
  "version": "1.0",
  "type": "team",
  "name": "火系队",
  "sprites": [
    { "species": "大头骨龙", "number": "001", "form": "",
      "skills": ["火焰拳", "龙之怒", "火花", "蓄力"],
      "nature": "固执", "ability": "威压" }
  ]
}
```

### 文本格式

- 对局日志沿用现有 `RoundRecord.to_message()` 格式
- 队伍用标记格式，如 `>>>SPRITE:大头骨龙:001:火焰拳,龙之怒,火花,蓄力`

## 序列化策略

### 需要序列化的对象

| 对象 | 序列化内容 |
|------|-----------|
| Player | name, lives, active_index, item, devotion, team |
| Sprite | species 标识(name/number/form)、hp、energy、active_effects、_modifiers、counters、skills |
| BattleSkill | base skill name、_modifiers、_burst_effects、sealed、_transmission |
| GlobalEffects | weather、weather_turns、marks |
| RoundRecord | 已有 to_message()，加 to_dict() |
| BattleVMEngine | _counter_values、_burst_effects、_burst_names、_skill_history |
| Effect 类 | StatBuffEffect、AbnormalEffect、StateEffect、ModifierEffect 各自字段 |

### 引用数据处理

- Species：存 name/number/form 标识符，恢复时从数据库查找
- Skill：存 base skill name，恢复时从 skill loader 重建
- 可变状态（hp、energy、effects、_modifiers）完整序列化

## 文件清单

### 新增

| 文件 | 职责 |
|------|------|
| `backend/engine/serializer.py` | BattleSerializer：核心序列化逻辑，所有 to_dict()/from_dict() |
| `roco/serializer.py` | CLI 入口 + 公开 API（export_match、import_match、export_team、import_team） |
| `backend/engine/test_serializer.py` | 往返测试：serialize → deserialize → 状态一致 |

### 修改

| 文件 | 改动 |
|------|------|
| `backend/sim/battle.py` | 添加 save_snapshot() / restore_snapshot() / _snapshots |
| `backend/sim/sprite.py` | 添加 to_dict() / from_dict()（或由 Serializer 统一处理） |

## 不在范围内

- 前端 UI 改动
- 跨会话持久化（磁盘保存快照）
- 分支时间线（多条并行分支管理）
- 对战录像回放（已有 TimelineReplay 足够）
