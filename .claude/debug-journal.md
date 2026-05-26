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

## 2026-05-26 - load_for_sprite 重复调用 + should_clear reload 失效导致特性 observer 注册两份

- **现象**: 奇丽果（养分内循环）回合末回能出现两次（+6E + +1E），delta=6 两次触发但第二次能量已近满只能+1
- **根因**: `load_for_sprite()` 在 api init 和 dispatch_entry 各调用一次，去重用的 `unregister_by_owner(id, "reload")` 因 `Observer.should_clear("reload")` 对 persistent scope 返回 False（仅 faint 才清），旧 observer 未被删除导致重复注册
- **修复**: `observer.py:68` — `should_clear` 新增 `reason == "reload"` 分支始终返回 True，确保 reload 去重对所有 scope 都生效
- **涉及文件**: backend/engine/observer.py:68-79
- **教训**: 特性效果数值翻倍或触发次数异常时，先怀疑 observer 是否被重复注册——检查 `load_for_sprite` 调用次数和 `should_clear` 的去重逻辑

## 2026-05-26 - Observer 条件 on_skills_energy_changed 永远不触发

- **现象**: 冰钻（敌方技能总能耗 → 攻击威力加成）特性完全不生效，observer 注册后从未触发
- **根因**: battle.py `_fire_mutation_events` 处理 EnergyChange 时未填充 `ctx.event.skills_energy_changed_of`（默认 ""），条件求值 `"" == "sprite_opp"` 永远为 False
- **修复**: EnergyChange 分支新增 `target_of` 解析，同时设置 `ctx.event.energy_changed_of` 和 `ctx.event.skills_energy_changed_of`
- **涉及文件**: backend/engine/battle.py:291-293
- **教训**: 特性完全不生效且使用 on_* 条件时，先查 `ctx.event.*` 对应字段是否在 `_fire_mutation_events` 或 `fire_trigger` 处被正确填充

## 2026-05-26 - mult_mod 丢弃 skill_filter 导致技能过滤失效

- **现象**: 冰钻/变形活画等使用 `op: "mult_mod"` + `skill_filter: "attack"` 的特性，威力加成作用于全部技能而非仅攻击技能
- **根因**: `op_mult_mod` 创建 `ModifierInjection` 时只传 target/stat/value/mode/scope，丢弃了 skill_filter/skill_where 等元数据。编译器路径 `_parse_mult_mod` → `MultModOp` 同样缺少这些字段
- **修复**: `MultModOp` 添加 5 个字段；`_parse_mult_mod` 传递它们；`op_mult_mod` 在 ModifierInjection 中包含它们
- **涉及文件**: backend/vm/ir_skill.py:94-98, backend/vm/compiler/passes/skill_parse.py:527-531, backend/vm/ops/mod.py:164-168
- **教训**: RISC op 处理函数之间应保持元数据传递一致性——对比 op_power_mod 和 op_mult_mod 就能发现遗漏

## 2026-05-26 - Replayer 不处理 skill_filter: "attack" 导致 modifier 全局写入

- **现象**: 带 skill_filter: "attack" 的 ModifierInjection 被写入 sprite._modifiers 而非对应技能的 BattleSkill._modifiers
- **根因**: `_apply_modifier` 只对 `skill_filter == "all"` 和 `skill_where is not None` 做技能级分发，`skill_filter: "attack"` 落入全局 sprite._modifiers 分支
- **修复**: 新增 `_matches_skill_type()` 辅助函数；`_apply_modifier` 将非 "all" 的 skill_filter 路由到 `_apply_to_matching_skills`；后者新增 skill_filter 过滤逻辑
- **涉及文件**: backend/engine/replayer.py:108-121, 171-173, 378-380
- **教训**: 数值加成为何不限于指定技能类型时，先查 replayer 的 modifier 分发路由是否匹配了该 skill_filter 值

## 2026-05-26 - 刺肤 on_damage_taken 无 of 过滤 + post_damage 视角翻转缺失导致双方双重伤害

- **现象**: 石冠王蜥（刺肤特性）在场上时，双方每次攻击均出现两次伤害数字——攻击方额外受到一次反伤，受击方也额外受到一次反伤
- **根因**: ① `on_damage_taken` 条件不检查 "of"（谁受伤），任意伤害都触发 observer；② `post_damage` 不在 owner-filter 列表中，对手攻击时 replayer 视角颠倒，`hit(sprite_opp)` 错误命中 owner 自己
- **修复**: EventContext 新增 `damage_taken_of` 字段追踪受伤方；`on_damage_taken` 支持 `of` 参数（缺省向后兼容）；`_fire_post_event` 对 post_damage 做 owner 视角翻转；刺肤/坚韧铠甲/最好的伙伴三个 trait JSON 补上正确的 `of` 值
- **涉及文件**: backend/vm/ctx.py:38, backend/engine/snapshot.py:64/197, backend/engine/battle.py:289-290/260-277, backend/vm/cond.py:307-310, data/traits/刺肤.json:10, data/traits/坚韧铠甲.json:19, data/traits/最好的伙伴.json:45
- **教训**: 反伤/受击触发类特性出现"双方都受伤"或"反伤打自己"症状时，直接查两个点——条件是否含 `of` 过滤 + 对应 trigger 是否在 `_fire_post_event` 的视角翻转/owner 过滤列表中

<!-- 新条目追加在此行上方，格式如下：

## YYYY-MM-DD - 简短标题

- **现象**: 用户观察到什么异常
- **根因**: 实际原因是什么
- **修复**: 做了什么修改
- **涉及文件**: file:line, file:line
- **教训**: 一句话，下次遇到类似症状怎么快速定位

-->
