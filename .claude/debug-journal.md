# Debug Journal

> 历次 debug 经验积累。SessionStart 自动加载到上下文，`/debug-save` 追加新条目。
> 条目按时间倒序排列，最新的在最上面。

---

## 2026-05-26 - 观察者系统 power_mod 未写入 BattleSkill._modifiers 导致前端技能栏威力不更新

- **现象**: 风滚暮虫（共鸣特性：虫鸣威力+20）换上场后，前端技能栏中虫鸣仍显示基础威力15，实际伤害也未享受+20加成
- **根因**: 共鸣特性用 `op: "observer"` 包裹 `power_mod`，走观察者路径。`JournalReplayer._apply_modifier()` 对带 `skill_where` 的非 skill-scoped `ModifierInjection`，直接写入 `sprite._modifiers`，而前端和伤害计算读取的是 `BattleSkill._modifiers`——两个不同 dict。同时 `_PER_TURN_KEYS` 每回合清理后也无法恢复观察者产生的 modifier
- **修复**: `replayer.py` 新增 `_apply_to_matching_skills()` 函数，遍历精灵技能用 `eval_skill_where` 过滤后写入匹配的 `BattleSkill._modifiers`；同时注册到 `sprite._trait_direct_effects` 确保跨回合持久化
- **涉及文件**: backend/engine/replayer.py:133-186 (新增), replayer.py:359-361 (分发入口)
- **教训**: 特性加成数值不生效时，先查 modifier 写入的是 `sprite._modifiers` 还是 `BattleSkill._modifiers`——观察者路径和直接路径写入目标不同

## 2026-05-26 - Observer turn_end 缺少 owner 过滤导致特性回能错误施加到对手

- **现象**: 奇丽果（养分内循环：回合末回6能）回合结束时日志出现 +3E 和 +0E 两次回能；对手双灯鱼也出现 +5E 和 +0E（不应有回能）
- **根因**: `_fire_post_event` 的 owner 过滤列表（post_entry/post_leave/post_skill）漏掉了 turn_end 和 post_abnormal_tick。回合结束时 `_phase_turn_end` 对每个在场精灵循环调用 `fire_trigger("turn_end")`，由于无 owner 过滤，奇丽果的特性 observer 在双灯鱼的循环中也触发了，`sprite_self` 被解析为双灯鱼，回能错误加给了对手
- **修复**: `battle.py:252` — 将 `"turn_end"` 和 `"post_abnormal_tick"` 加入 owner 过滤列表。owner_sprite_id 不为空的 observer 只在所属精灵的循环中触发，全局 observer（owner=None）不受影响
- **涉及文件**: backend/engine/battle.py:252-254
- **教训**: 特性效果（回能/扣血/印记）出现"对手也触发了"或"触发两次"的症状时，直接查 `_fire_post_event` 的 owner 过滤是否包含对应 trigger

<!-- 新条目追加在此行上方，格式如下：

## YYYY-MM-DD - 简短标题

- **现象**: 用户观察到什么异常
- **根因**: 实际原因是什么
- **修复**: 做了什么修改
- **涉及文件**: file:line, file:line
- **教训**: 一句话，下次遇到类似症状怎么快速定位

-->
