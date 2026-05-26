# Debug Journal

> 历次 debug 经验积累。SessionStart 自动加载到上下文，`/debug-save` 追加新条目。
> 条目按时间倒序排列，最新的在最上面。

---

## 2026-05-26 - extra_turn_end flag 只写不读导致双向光速特性不生效

- **现象**: 粉耳星兔（双向光速特性）在场时，回合末异常效果（灼烧）仍只触发一次，而非预期的两次
- **根因**: `extra_turn_end` flag 被正确写入 `sprite._modifiers`，但战斗引擎 `_phase_turn_end` 中无任何代码读取此 flag——`backend/sim/` 完全不存在对 `extra_turn_end` 的引用
- **修复**: `_phase_turn_end` 中 `SkillResolver.turn_end()` 首次调用后，检查场上精灵是否有 `extra_turn_end > 0`，有则再调用一次，使异常tick/天气/印记等回合末效果额外触发一次
- **涉及文件**: backend/sim/battle.py:804-810
- **教训**: flag 型 stat（extra_turn_end/tick_reduce/extra_action 等）若日志显示已设置但效果不生效，直接搜 `backend/sim/` 中是否有对该 flag 的消费代码——大概率是"只写不读"

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
- **根因**: ① `on_damage_taken` 条件不检查 "of"（谁受伤），任意伤害都触发 observer；② `post_damage` 不在 owner-filter 列表中，对手攻击时 replayer 视角颠倒，`hit(sprite_opp)` 错误命中 owner 自己；③ 视角翻转在 `eval_one(cond)` 之后执行，导致条件仍从攻击方视角求值，`of: "sprite_self"` 永远不匹配→反伤完全不触发
- **修复**: EventContext 新增 `damage_taken_of` 字段追踪受伤方；`on_damage_taken` 支持 `of` 参数（缺省向后兼容）；`_fire_post_event` 对 post_damage 做 owner 视角翻转；刺肤/坚韧铠甲/最好的伙伴三个 trait JSON 补上正确的 `of` 值
- **涉及文件**: backend/vm/ctx.py:38, backend/engine/snapshot.py:64/197, backend/engine/battle.py:289-290/260-277, backend/vm/cond.py:307-310, data/traits/刺肤.json:10, data/traits/坚韧铠甲.json:19, data/traits/最好的伙伴.json:45
- **教训**: 反伤/受击触发类特性出现"双方都受伤"、"反伤打自己"或"反伤完全不触发"症状时，直接查三个点——条件是否含 `of` 过滤 + trigger 是否在视角翻转列表中 + 视角翻转是否在条件求值**之前**执行

## 2026-05-26 - 反伤视角交换后 ctx 未重建导致反击伤害计算用错双方数值

- **现象**: 石冠王蜥反伤触发但只造成 1 点伤害（预期为 50 威力物攻的正常伤害）
- **根因**: 交换 replayer.self/opp 后 ctx 未重建——`op_hit` 读取 `ctx.atk_self` 仍是原攻击方的攻击力，`ctx.def_opp` 仍是防御方的防御力，攻防比极端不利 → `calc_damage` 返回 `max(1, ...)`
- **修复**: Ctx 新增 `swapped_view()` 方法交换所有 self/opp 和 own/opp 字段；`_fire_post_event` 视角翻转时同步替换 ctx
- **涉及文件**: backend/vm/ctx.py:162-216, backend/engine/battle.py:271-277
- **教训**: 反伤伤害异常低（仅1点）时，查 ctx 的 atk_self/def_opp 是否对应反击方和受击方——视角交换了 replayer 但没交换 ctx

## 2026-05-26 - post_damage 视角翻转在条件判断之后导致反伤完全不触发

- **现象**: 石冠王蜥（刺肤特性）挨打后不再触发反伤，日志中无反伤伤害数字
- **根因**: `_fire_post_event` 对 post_damage 的视角翻转（swap replayer.self/opp）放在 `eval_one(cond)` 内部，条件仍从攻击方视角求值——`damage_taken_of` 为 `"sprite_opp"`（对手受伤），`of: "sprite_self"` 永远不匹配
- **修复**: 将 replayer.self/opp 交换移至 `eval_one` 之前，用 try/finally 包裹确保条件求值后恢复原视角
- **涉及文件**: backend/engine/battle.py:259-286
- **教训**: 反伤触发条件包含 `of` 过滤时，若完全不触发，查视角翻转是否在条件求值**之前**执行

## 2026-05-26 - mult_mod 对 atk/def/sp_atk 等倍率值日志显示为 +0

- **现象**: 火神（助燃：使用火系技能后双攻+20%）日志显示 物攻+0 / 魔攻+0，看起来特性未生效
- **根因**: replayer._apply_modifier 对 _STAGE_STATS 的 ModifierInjection 用 :+.0f 格式化，倍率值 0.2 被四舍五入为 +0。实际数值（_modifiers["atk"]=0.2）和伤害计算（build_ctx 用 1.0+0.2 倍率）均正确，纯显示 bug
- **修复**: replayer.py — _STAGE_STATS 的 ModifierInjection 显示改用 :+.0% 格式化为百分比
- **涉及文件**: backend/engine/replayer.py:454
- **教训**: 日志显示 +0 但实际效果可能已生效——先验证数值是否存入 sprite._modifiers，再看 build_ctx 是否正确读取，最后查显示格式化

## 2026-05-26 - 刺肤 on_damage_taken 缺 of 过滤导致主动攻击时也触发反伤

- **现象**: 石冠王蜥（刺肤）主动攻击时，对手受到两次伤害数字（技能伤害 + 误触发的反伤）
- **根因**: 刺肤 trait JSON 的 on_damage_taken 条件缺少 of: sprite_self 过滤，任意精灵受伤都触发 observer
- **修复**: data/traits/刺肤.json — 条件加 of: sprite_self，限制仅在自身受伤时触发
- **涉及文件**: data/traits/刺肤.json:9-10
- **教训**: 反伤类特性出现"攻击时也造成额外伤害"，直接查 on_damage_taken 条件是否含 of 过滤

## 2026-05-26 - resolve.py scale/offset 后 int() 截断导致小数值特性完全不生效

- **现象**: 变形活画（敌方每有1层增益→技能威力+10%）完全不生效，无论敌方多少层增益威力都不变。囤积、冰钻等使用 scale: 0.1 的特性同样不生效
- **根因**: `_resolve_dict_query` 和 `_apply_transforms` 在 scale/offset 变换后对结果调用了 `int()`——`int(3 * 0.1) = 0`，`int(0 + 1) = 1`——小数值被截断为 0，power_mult 永远为 1.0
- **修复**: 移除 scale/offset 后的 int() 转换（per 的 int() 保留，用于除法取整是正确的）。涉及 `backend/vm/resolve.py:140-144` 和 `:158-162`
- **涉及文件**: backend/vm/resolve.py:140-144, 158-162
- **教训**: 特性数值完全不生效时，先查 resolve 中 dict query 的数值变换是否被 int() 截断——对比 typed Query 路径（正确保留 float）和 dict query 路径（之前有 int()）

## 2026-05-26 - DataDrivenTrait 路径 observer listen 字段丢失导致触发所有事件

- **现象**: 通过 DataDrivenTrait 路径加载的 observer 未按 listen 限制触发——例如 listen: "pre_calc" 的 observer 在 post_damage、turn_end 等所有事件上都触发
- **根因**: `op_count` 未提取 `listen` 字段，CounterRegister 无此字段，_effects_to_observers 和 register_from_counter 只用 infer_triggers() 推断——"compare" 条件推断结果为空集合（等同于 fire on all events）
- **修复**: CounterRegister 加 listen 字段；op_count 提取 listen；两处 Observer 构造使用显式 listen（回退到 infer_triggers）。主路径 TraitToObserver 不受影响（已正确处理 listen）
- **涉及文件**: backend/vm/journal.py:265, backend/vm/ops/count.py:29-35, backend/sim/traits/trait_engine.py:129, backend/engine/observer.py:125-130
- **教训**: observer 触发频率异常（不该触发时也在触发）时，检查 listen 字段是否在 trait→observer 转换链路中全程保留——每个中间数据结构都要有对应字段

## 2026-05-26 - _target_sprite 不识别 ally_new/enemy_new 导致离场 buff 错误施加给对手

- **现象**: 吉利丁片 def/sp_def 加成（修复条目1后）理论上会生效，但会错误施加到对手而非换入队友
- **根因**: `_target_sprite` 只识别 sprite_self/self/team_own/skill_off_0，其余全部 fallback 到 self.opp
- **修复**: _target_sprite 新增 ally_new → battle.get_player(team).active, enemy_new → battle.get_opponent(team).active
- **涉及文件**: backend/engine/replayer.py:896-899
- **教训**: 加成效果出现在错误目标（对手）身上时，查 _target_sprite 是否识别该 target 值

## 2026-05-26 - sprite_left 条件未在 COND_EVAL 注册导致 post_leave observer 永不触发

- **现象**: 吉利丁片（离场后换入精灵获得双防+20%）完全不触发，无任何加防提示
- **根因**: `COND_EVAL` 调度表中缺少 `sprite_left` 条件——`eval_one()` 抛出 KeyError，被 `_fire_post_event` 的 `except Exception: continue` 静默吞掉。同时所有 `post_leave` 的 ctx 构造未传 `self_switched=True`
- **修复**: cond.py 新增 `sprite_left` 条件（of=sprite_self→self_switched, of=sprite_opp→opp_switched）；battle_mechanics.py/battle.py 全部 post_leave ctx 补传 self_switched=True
- **涉及文件**: backend/vm/cond.py:318-321, backend/sim/battle_mechanics.py:72/150/270/298, backend/sim/battle.py:886
- **教训**: 特性完全不触发且 observer 已注册时，直接检查 COND_EVAL 是否包含 observer 的条件键——缺少就是静默失败

## 2026-05-26 - 向心力 skill_at_N 目标未被识别导致 buff 加到对手 + 只触发一次不跟随传动换位

- **现象**: 声波缇塔（向心力：1/2号位技能传动1+威力30）入场后，威力+30和传动+1全显示在对手双灯鱼身上，且效果永久附着入场时技能而非跟随传动换位
- **根因**: ① `_target_sprite("skill_at_1")` 不匹配已知 target，fallback 到 `self.opp` → buff 全写给对手；② 无路由逻辑指到 `sprite.skills[N]._modifiers`；③ drive flag 写入后无人翻译为 `_transmission`；④ 只监听 `post_entry`，无 `turn_start` 每回合重评估
- **修复**: replayer 新增 `skill_at_N` 识别（target 解析 + 位置路由 + drive→_transmission + 同 stat 批次清理）；cond/battle 加 `turn_start` trigger/`always` 条件；DataDrivenTrait 加 `on_turn_start`；向心力.json 改 `or(sprite_entered, always)` 监听 `[post_entry, turn_start]`
- **涉及文件**: backend/engine/replayer.py:94/235/240/387-400/908, backend/vm/cond.py:198-199/397-398, backend/engine/battle.py:254, backend/sim/traits/trait_engine.py:133-148, data/traits/向心力.json
- **教训**: 效果出现在错误目标时查 `_target_sprite` 是否识别该 target；位置型效果不跟随回合刷新时检查是否只有 `post_entry` 无 `turn_start`


## 2026-05-26 - 吟游之弦 mark_coexist flag 只写不读 + Hook 路径 user 参数从未传入

- **现象**: 吟游之弦（id=20146）印记共存特性在生产环境中完全不生效——印记仍然互相替换而非共存
- **根因**: 两条执行路径均失效：① Hook 路径 `_bard_before_apply_mark` 检查 `user` 参数，但 replayer 全部 4 处 `apply_mark` 调用点均未传 `user`（永远为 None），hook 返回 None；② DataDrivenTrait observer 通过 `flag_set` 写入 `sprite._modifiers["mark_coexist"] = True`，但 `apply_mark` 从未读取此 flag——与 `extra_turn_end` 同模式的"只写不读"
- **修复**: 删除全部 hook 代码（`hooks/__init__.py` 清空，18 个注册全部移除）；`apply_mark` 签名 `user` → `coexist: bool`；replayer 4 处调用点改为读 `self.self._modifiers.get("mark_coexist")` 后传 `coexist=`；`吟游之弦.json` listen 改为 `["pre_calc", "post_entry"]` 确保入场即设 flag
- **涉及文件**: backend/engine/hooks/__init__.py, backend/sim/globals.py:273-286, backend/engine/replayer.py:523/551/576/705, data/traits/吟游之弦.json:15
- **教训**: 特性通过 hook 实现时，先查 hook 的触发条件在生产环境中是否满足——特别是参数是否被实际传入（grep 所有调用点确认）。特性同时有 JSON observer 和 hook 两套路径时，大概率 observer 路径的 flag 只写不读
<!-- 新条目追加在此行上方，格式如下：

## YYYY-MM-DD - 简短标题

- **现象**: 用户观察到什么异常
- **根因**: 实际原因是什么
- **修复**: 做了什么修改
- **涉及文件**: file:line, file:line
- **教训**: 一句话，下次遇到类似症状怎么快速定位

-->
