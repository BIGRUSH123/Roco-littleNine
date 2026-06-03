# Debug Journal

> 历次 debug 经验积累。SessionStart 自动加载到上下文，`/debug-save` 追加新条目。
> 条目按时间倒序排列，最新的在最上面。

---

## 2026-05-30 - compare 条件 q="counters[times_entered]" 路径语法不被 _resolve_dict_query 支持导致静默失败

- **现象**: 鳗尾兽的铃兰晚钟特性（首次入场失去一半当前生命）完全不触发，战斗日志无任何痕迹
- **根因**: `compare` 条件的 `q: "counters[times_entered]"` 走 `_resolve_dict_query` → ADDRESS_MAP 中不存在 `("sprite_self", "counters[times_entered]")` → `KeyError` 被 `observer.py:171` 的 `except Exception: continue` 静默吞掉。公式串 `=@self.counters[times_entered]` 走 `_resolve_trait_ref`（有独立正则处理），所以 `then` 块能用但 `cond` 块不行
- **修复**: `铃兰晚钟.json`/`蓄电池.json`/`超级电池.json` 的 `q: "counters[times_entered]"` → `q: "times_entered"`（ADDRESS_MAP 已注册）
- **涉及文件**: `data/traits/铃兰晚钟.json:25`, `data/traits/蓄电池.json:36`, `data/traits/超级电池.json:36`
- **教训**: `compare` 条件的 `q` 只支持 ADDRESS_MAP 简单键名，不支持 `counters[key]` 语法——和公式串 `=@self.counters[key]` 解析路径不同。特性完全不触发时先搜 `"q": "counters\[`

## 2026-05-29 - 应对穿透修复（8abc1ad）误将应对当打断导致被应对技能完全无效

- **现象**: 被防御等技能应对后，攻击技能完全不造成伤害，而非只减伤70%
- **根因**: 8abc1ad 将应对块从"仅记日志"改成"注入应对方效果 + 扣能 + return events"，把应对和打断搞混——应对是正常行为，对方技能应继续执行（伤害被减伤削弱）；打断才应阻断技能
- **修复**: 应对块恢复为仅记日志；应对方效果（damage_reduction）移到打断 gate 之后、VM 执行之前注入，确保减伤在伤害计算前生效但技能仍正常执行
- **涉及文件**: `backend/sim/battle.py:538-543, 667-685`
- **教训**: 排查"被应对技能完全不生效"时，先确认应对块是否有 return——应对≠打断，应对不阻断技能执行

## 2026-05-27 - 圣火骑士 post_counter observer 产生的 power_mult 跨回合不生效 + on_next 全链路补齐

- **现象**: 炽心勇狮（圣火骑士："应对成功后，下次攻击威力翻倍"）应对成功后，下回合攻击威力与普通攻击相同，完全不触发。首次修复（persistent scope 跳过 `_PER_TURN_KEYS`）后 modifier 永不被消耗，后续攻击全部翻倍
- **根因**: 两阶段。① observer `scope: "persistent"` 未传播到子效果 `mult_mod`（默认 scope "battlefield"）；② `_PER_TURN_KEYS` 每回合开始无条件 pop `power_mult`，Round 1 应对后写入的 modifier 在 Round 2 攻击前被清除。最终方案用 `on_next`：modifier 入 `_pending_modifiers`，下次攻击 `consume_pending_modifiers` 消耗后 `_PER_TURN_KEYS` 清残值。修复中 `_inject_default_scope` 漏 `self.` 导致 NameError 被 `except Exception: continue` 静默吞掉
- **修复**: `battle.py` 新增 `_inject_default_scope()` 将 observer scope 注入子效果；`ir_skill.py` `MultModOp`、`skill_parse.py` parser、`mod.py` `op_mult_mod` 全链路补齐 `on_next`/`if_type` 字段；`圣火骑士.json` mult_mod 添加 `on_next: true, if_type: attack`
- **涉及文件**: backend/engine/battle.py:248-266, backend/vm/ir_skill.py:98-99, backend/vm/compiler/passes/skill_parse.py:534-535, backend/vm/ops/mod.py:169-170, data/traits/圣火骑士.json:14-15
- **教训**: "下次攻击生效"用 `on_next` 机制；新增字段须补全 JSON→parser→IR op→handler→Mutation 全链路；漏 `self.` 导致 NameError 被 `except Exception: continue` 静默吞掉

## 2026-05-27 - 图书守卫者 q="energy" 查精灵技能能量而非玩家魔力值导致条件永远不满足

- **现象**: 古卷执政官（图书守卫者特性）在玩家魔力值为1时换上场，双攻+50%效果不触发
- **根因**: trait JSON 使用 `q: "energy"` 查询 → ADDRESS_MAP 映射到 `energy_self`（精灵技能能量，默认10），而非 `lives_own`（玩家魔力值）。同时 `lives_own`/`lives_opp` 不在 ADDRESS_MAP 中，`build_ctx` 从未从 Player 对象填充这两个字段。`10 eq 1` 永远为 False。同问题导致构装契约者的 `opponent.lives` 因不在 ADDRESS_MAP 而 KeyError 被静默吞掉
- **修复**: ctx.py ADDRESS_MAP 新增 `(sprite_self, lives)`/`(sprite_opp, lives)` 等 4 条映射；snapshot.py build_ctx 新增 lives_own/lives_opp 参数；battle.py _make_ctx 从 own_player.lives 填入；图书守卫者.json q: "energy"→"lives"；构装契约者.json q: "opponent.lives"→"lives"+of 改为 sprite_opp
- **涉及文件**: backend/vm/ctx.py:289-296, backend/engine/snapshot.py:80-81/293-294, backend/sim/battle.py:138-139, data/traits/图书守卫者.json:17, data/traits/构装契约者.json:35-36
- **教训**: trait 条件不触发时，先 grep ADDRESS_MAP 确认查询键是否已注册——未注册的 KeyError 被 `ObserverRegistry.fire()` 中 `except Exception: continue` 静默吞掉

## 2026-05-27 - StatusEffect 删除后残留 getattr(e, 'category') 类型判断失效

- **现象**: 审查 StatusEffect 移除代码时发现 3 处静默 bug——replayer 的 convert/steal 路径会 AttributeError（.effects 字段已删除），API 效果分类序列化返回空字符串
- **根因**: 旧代码用 getattr(e, 'category', '') == 'abnormal' 做效果类型判断，但 EffectObject 子类（AbnormalEffect/StatBuffEffect/StateEffect）没有 category 属性——getattr 永远返回默认值。同时 replayer 两处仍引用已删除的 sprite.effects
- **修复**: 全部改为 isinstance(e, AbnormalEffect) / isinstance(e, StatBuffEffect) + sprite.active_effects；API 新增 _effect_category() helper 用 isinstance 返回正确分类
- **涉及文件**: backend/engine/replayer.py:549,759-762, backend/api/main.py:367,429
- **教训**: 大规模迁移（类型替换）完成后，必须 grep 所有 getattr(*, 'old_field') 引用——动态属性访问绕过类型检查，删除字段后不会有 import 错误，只在运行时静默失效

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

## 2026-05-26 - 星陨印记非幻系攻击不触发消耗和伤害

- **现象**: 敌方有星陨印记时，用非幻系技能攻击不会消耗印记，也没有额外幻系伤害
- **根因**: 三个断点：① `consume_starfall_stacks()` 在代码库中无任何调用者，星陨印记只增不减；② `_MARK_EFFECTS星陨印记` 空配，缺少伤害公式配置（每层30威力）；③ 整个引擎没有 `skill.element != "幻"` 的触发门检查
- **修复**: globals.py 新增 `trigger_starfall(team, attacker, defender)` 方法（计算伤害 → 消耗层数 → take_damage）；`_MARK_EFFECTS` 添加 `starfall_damage: 30`；battle.py execute_skill() step 6.7 新增非幻系攻击触发检查；清理 consume_starfall_stacks 中已删除的 before_consume_starfall hook 调用
- **涉及文件**: backend/sim/globals.py:87-89/318-357, backend/engine/battle.py:189-193
- **教训**: 印记类机制排查先从三个点入手——配置是否有伤害/效果字段、消耗/触发方法是否有调用者、触发条件门（元素/技能类型）是否在引擎中存在

## 2026-05-27 - passive→effects 键名不匹配导致四个 DataDrivenTrait 从未加载

- **现象**: 咔咔鸟（咔咔冲刺）特性"先手时连击+1"完全不触发；囤积、冰钻、图书守卫者同样静默失效
- **根因**: trait JSON 使用旧键名 "passive" 存放效果列表，但 load_data_trait() 和 TraitLoader 只读 "effects"——data.get("effects", []) 返回空列表，特性从未加载、Observer 从未注册。咔咔冲刺和图书守卫者的 and 条件额外使用 "conds" 子键，cond.py 求值器用 cond["conditions"] 直接访问→KeyError 被静默吞掉
- **修复**: 四个 JSON passive→effects；咔咔冲刺和图书守卫者 conds→conditions
- **涉及文件**: data/traits/咔咔冲刺.json, data/traits/囤积.json, data/traits/冰钻.json, data/traits/图书守卫者.json
- **教训**: 特性完全不触发时，先查 trait JSON 键名是否匹配加载器——grep 确认加载器读的键（effects）和文件实际用的键（passive）是否一致

## 2026-05-27 - BattleSkill._modifiers combo 未逐回合清理导致日志连击数累加

- **现象**: 疾风刺等含 combo 加成的技能，第二次使用时日志显示"连击+6"而非"+3"——数值逐回合累加（+3 → +6 → +9...）
- **根因**: `_PER_TURN_KEYS` 不含 "combo" 和 "combo_mult"，设计注释误认为"跨回合持久键不清空"。技能效果 `power_mod(target="skill_off_0", combo+3)` 写入 BattleSkill._modifiers，但该 dict 未在回合初清理，旧值残留导致累加。精灵级 `sprite._modifiers["combo"]` 则需要保留（特性加成跨回合有效），两者不能共用同一清空集合
- **修复**: battle.py 新增 `_SKILL_PER_TURN_KEYS = _PER_TURN_KEYS | {"combo", "combo_mult"}`，技能级用扩展集合清理，精灵级保持原集合
- **涉及文件**: backend/sim/battle.py:156-168
- **教训**: 技能日志数值呈等差数列累加（+3,+6,+9...）时，直接查该 stat 是否在回合初清理集合中——且确认清理的是 sprite._modifiers 还是 skill._modifiers，两者生命周期不同

## 2026-05-27 - 特性效果 tooltip 不显示 —— observer 从未触发

- **现象**: 鼠标悬停特性名称时 tooltip 显示"暂无效果数据"，即使精灵拥有囤积等特性也不展示具体数值
- **根因**: 囤积特性的 observer 监听 `post_energy_change` 触发器，但该触发器仅在 `_fire_mutation_events` 扫描到 journal 中有 `EnergyChange` 时才会触发。技能能耗（`user.lose_energy`）、聚能回能（`user.gain_energy`）均在 VM 外部直接修改 sprite 属性，不经过 journal，因此 observer 从未被触发，`_sync_mult_display_effect` 从未被调用
- **修复**: 在 3 个能量变化点手动构建 ctx 并调用 `fire_trigger("post_energy_change")`：① `dispatch_entry` 特性加载后（初始能量）② `_execute_skill_vm` 技能能耗支付后 ③ `_execute_skill_vm` 聚能回能后。同时将 `post_energy_change` 加入 `_fire_post_event` 的 owner 过滤列表
- **涉及文件**: `backend/sim/battle.py:464,548`, `backend/sim/traits/__init__.py:88`, `backend/engine/battle.py:263`
- **教训**: VM 外部直接修改状态的操作不会产生 journal mutation，依赖 mutation 触发的 observer 不会感知到此类变更。排查 observer 不触发时，先检查触发源是否在 journal 中

## 2026-05-27 - 特性元数据效果错误显示在前端 buff 栏

- **现象**: 精灵 buff 栏中显示了"囤积、物防、魔防"三个效果——它们属于特性元数据或仅供 tooltip 使用的展示效果，不应出现在 buff 栏
- **根因**: ① `TraitLoader.load_for_sprite()` 将 trait JSON 的 observer op 通过 `effect_from_dict()` 转为 `ObserverEffect` 插入 `active_effects`；② `_sync_mult_display_effect` 创建的展示用 `StatBuffEffect`（steps=0, display_mult≠null）也写入 `active_effects`。API 将所有这些效果序列化到 `effects[]`，前端 `BattleArena` 无差别遍历渲染 `EffectCard`
- **修复**: API 新增 `_is_trait_metadata_effect()`（过滤 ObserverEffect/ModifierEffect）和 `_is_display_stat_effect()`（过滤 steps=0 的展示 StatBuffEffect）；展示效果改为写入 `TraitInfo.display_effects` 新字段；`TraitTooltip` 改为从 `trait.display_effects` 读取
- **涉及文件**: `backend/api/main.py:316-340`, `backend/api/schemas.py:75`, `frontend/src/components/TraitTooltip.vue:19-25`
- **教训**: buff 栏出现不该有的条目时，先查 `active_effects` 中是否有 `ObserverEffect`/`ModifierEffect` 等非可见类型——它们通过 `effect_from_dict` 或 replayer 的 `_sync_*` 方法写入，需要在 API 序列化层过滤

## 2026-05-27 - 首发精灵 entry 特性在回合0未触发导致初始状态显示错误

- **现象**: 首发「布克棱岩」（地脉）初始能量显示为10而非0；「囤积」特性加成在首次行动后才显示
- **根因**: `_init_battle_impl` 只 `load_for_sprite` 注册 observer，不触发 entry 效果。`post_entry` 在 `execute_turn` turn 1 才触发，但 `serialize_battle_state` 在此之前已调用。`snapshot.py:247` 的 `turn > 0` 守卫阻止回合 0 时 `sprite_entered` 生效
- **修复**: 增加回合 0——`_init_battle_impl` 序列化前调用 `dispatch_entry` + `fire_trigger("post_entry")` 静默触发 entry 效果；移除 `execute_turn` turn-1 冗余分支；`turn > 0` → `turn >= 0`
- **涉及文件**: `api/main.py:706-714`, `battle.py:184-192`, `snapshot.py:247`
- **教训**: 初始状态显示异常→检查 entry/init 阶段的效果触发时序，observer 注册 ≠ 触发

## 2026-05-27 - 坠星特性三个断点：ADDRESS_MAP 缺 key + power_mult mode:add 被忽略 + Replayer 倍率 stat 用错默认值

- **现象**: 祭礼巨像「坠星」特性（敌方每有1层星陨印记→技能威力+15%）完全不触发，逐层修复后威力显示 30% 而非 130%
- **根因**: 三个独立断点。① ADDRESS_MAP 缺少 `(team_opp, mark_stacks)` → KeyError 被静默吞掉 + `_NAMED_DICT_QUERIES` 不含 `"mark_stacks"` → name 子键不查；② `modifiers.py` 对 `power_mult` 忽略 `mode` 永远 `*=` → `1.0 * 0.3 = 0.3`；③ `replayer.py` `(cur or 0.0) + delta` 对倍率 stat 用 0.0 而非 1.0 做默认值 → 写入 0.3，`build_ctx` 算 `1.0 + (0.3-1.0) = 0.3`
- **修复**: ① ctx.py ADDRESS_MAP 新增 mark_stacks；resolve.py NAMED_DICT_QUERIES 添加 mark_stacks；② modifiers.py power_mult 分支支持 mode add/set/multiply；③ replayer.py add 模式 cur 为 None 时倍率 stat 用 1.0 做默认值
- **涉及文件**: backend/vm/ctx.py:285,295, backend/vm/resolve.py:17, backend/engine/modifiers.py:50-53, backend/engine/replayer.py:432-435
- **教训**: 数值加成从"完全不生效"到"数值不对"的逐层排查链——① 查 ADDRESS_MAP+_NAMED_DICT_QUERIES（静默吞掉）→ ② 查 modifier 收集逻辑的 mode 分发 → ③ 查 replayer 默认值（倍率 stat 基值 1.0 不是 0.0）

## 2026-05-27 - stat_stage 特性未创建展示效果导致 trait tooltip 显示"暂无效果数据"

- **现象**: 壮胆特性修复后 buff 栏正确显示双攻加成，但特性 tooltip 悬停仍显示"暂无效果数据"
- **根因**: `_apply_stat_change` 只创建真实 `StatBuffEffect`（steps≠0，显示在 buff 栏），未创建展示用 `StatBuffEffect`（steps=0 + display_mult，用于 tooltip）。对比 `_apply_modifier` 对 stage stats 会同时调用 `_sync_mult_display_effect` 创建 tooltip 展示效果
- **修复**: `_apply_stat_change` 中对百分比类 stage stats（atk/def/sp_atk/sp_def）调用 `_sync_mult_display_effect`，display_mult = steps × 0.1
- **涉及文件**: backend/engine/replayer.py:329-336
- **教训**: tooltip 不显示但 buff 栏正常时，查对应 mutation handler 是否同时创建了展示效果（`_sync_mult_display_effect`）——`_apply_stat_change` 和 `_apply_modifier` 需保持一致

## 2026-05-27 - team_has_element 条件未在 COND_EVAL 注册导致壮胆特性完全不触发

- **现象**: 伏地兽（壮胆特性：队伍存在虫系精灵时双攻+50%）在队伍中有虫系精灵时完全不触发
- **根因**: trait JSON 使用 `cond: "team_has_element"` 但该条件键未在 `COND_EVAL` 调度表中注册，`eval_one()` 抛出 KeyError 被 `except Exception: continue` 静默吞掉。同时 `team_elements` 路径解析器错误返回 `skill_elements_self`（技能元素）而非队伍成员元素
- **修复**: cond.py 新增 `team_has_element` handler；ctx.py 新增 `team_elements_own/opp` 字段 + ADDRESS_MAP；snapshot.py/battle.py 从 `own_player.team` 收集 `species.elements` 传入 build_ctx；修复 `team_elements` 路径指向 `ctx.team_elements_own`
- **涉及文件**: backend/vm/cond.py:412-418, backend/vm/ctx.py:119-120/212-213/292/302, backend/engine/snapshot.py:82-83/290-291, backend/sim/battle.py:129-143
- **教训**: 特性完全不触发且 observer 已注册时，直接 grep COND_EVAL 确认条件键是否已注册——缺少就是 KeyError 被静默吞掉

## 2026-05-27 - display-only StatBuffEffect scope="turn" 被回合结束清理导致特性 tooltip 不显示

- **现象**: 厉毒修萝「侵蚀」特性连击+N 在战斗日志中正常显示，但特性 tooltip 始终不显示效果数值
- **根因**: replayer `_apply_modifier` 创建的 display-only `StatBuffEffect`（steps=0, display_value=N）继承了 mutation 的 `scope="turn"`。`_phase_turn_end()` (battle.py:757) 在 API 序列化前调用 `sprite.clear_effects('turn')` 将其清除，`_extract_display_effects` 读不到任何展示效果。同时 `_is_display_stat_effect` 只检查 `display_mult` 导致 combo 的 display_value 效果泄露到 buff 栏
- **修复**: `replayer.py:793` `_sync_mult_display_effect` — display-only 效果改用 `scope="battlefield"`（回合结束不清除，离场时清除），更新现有效果时同步修改 scope；`main.py:327` `_is_display_stat_effect` — 同时检查 `display_value` 防止泄露到 buff 栏
- **涉及文件**: backend/engine/replayer.py:784-796, backend/api/main.py:327-332
- **教训**: 效果不显示但日志有 → 先查效果生命周期。效果被创建但 API 看不到，大概率是被回合结束/回合开始的清理逻辑在序列化之前清掉了；直接搜 `clear_effects` 调用点确认时序

## 2026-05-27 - 多人宿舍（九幽菇）max_energy 不生效导致聚能不能超过10E

- **现象**: 九幽菇特性"多人宿舍"（能量上限+5→15）不生效，聚能始终+0E(→10)，能量无法超过默认上限
- **根因**: 三个独立断点。① `_parse_power_mod` / `PowerModOp` / `op_power_mod` 全链路只支持 `delta`（add 模式），trait JSON 使用 `value: 15, mode: "set"` → delta 未解析，value=0；② observer 触发的 `power_mod` 经 replayer 写入 `sprite._modifiers`，但 `Sprite.max_energy` property 从 `sprite.active_effects` → `ModifierEffect` 读取——两个不同数据结构；③ `ModifierEffect` 构造漏了必需参数 `name` → `TypeError` 被 `_fire_post_event` 的 `except Exception: continue` 静默吞掉
- **修复**: `ir_skill.py` PowerModOp 新增 value/mode 字段；`skill_parse.py` _parse_power_mod 读取 value/mode；`mod.py` op_power_mod 优先用 value/mode，回退 delta 保持向后兼容；`replayer.py` _apply_modifier 将 _SPRITE_LEVEL_ATTRS（max_energy、starfall_consume_ratio）同步为 ModifierEffect 写入 active_effects
- **涉及文件**: backend/vm/ir_skill.py:73-74, backend/vm/compiler/passes/skill_parse.py:498-501, backend/vm/ops/mod.py:137-143, backend/engine/replayer.py:450-471
- **教训**: trait 数值效果不生效时，排查三连环——① 检查 parser/op 是否读取了 JSON 中使用的字段名（delta vs value），② 检查数据写入目标（_modifiers vs active_effects）是否与 consumer（property 方法）一致，③ 任何 `except Exception: continue` 都是静默失败的黑洞——加新代码后务必确认无异常被吞

## 2026-05-27 - defer opcode turns=0 被 falsy 短路吞掉 + at="turn_end" 未规范化为 "end"

- **现象**: 奔波鼠（奔波命特性）使用防御技能后，回合结束时没有触发脱离；日志显示"延时效果(1回合后)"而非当前回合
- **根因**: op_schedule() 两个独立 bug：① `turns = _get(e, "turns") or _get(e, "delay_turns", 1)` — 0 是 falsy 短路变成 1；② `at: "turn_end"` 直接透传，引擎 `_execute_scheduled_effects` 只匹配 `"end"/"start"`，phase 永不相配
- **修复**: turns 改用 `is None` 判断；at 增加 `"turn_end"→"end"` / `"turn_start"→"start"` 规范化
- **涉及文件**: backend/vm/ops/schedule.py:16-36
- **教训**: defer 延时效果不触发时，先查两个点——turns 值是否被 Python falsy 逻辑篡改（0、空字符串），以及 phase 值是否匹配引擎检查的 key

## 2026-05-27 - post_counter 缺少 owner 过滤导致观察者从错误精灵视角执行

- **现象**: 对手（黑羽夫人）使用偷袭成功应对后，错误触发了好象坏象特性的形态变换，对手变成了棋绮后
- **根因**: 两个独立 bug 组合：① `engine/battle.py:303` owner 过滤列表漏掉了 `"post_counter"`，所有监听 post_counter 的 observer 无论属于哪个精灵都会触发；② `sim/battle.py:343,348` ctx 的 `self_skill` 传了被应对技能而非应对技能，`skill_type` 条件检查了错误的技能类型
- **修复**: `engine/battle.py:303` 加入 `"post_counter"`；`sim/battle.py:343,348` 将 `countered_skill_a`/`countered_skill_b` 改为 `skill_a`/`skill_b`
- **涉及文件**: backend/engine/battle.py:303, backend/sim/battle.py:343,348
- **教训**: 特性效果出现在错误目标（对手）身上时，先查 `_fire_post_event` 的 owner 过滤列表是否包含对应 trigger——漏掉的 trigger 导致所有 observer 无差别触发，`sprite_self` 指向 replayer.self 而非 trait holder

## 2026-05-28 - display-only effect 创建在 skill_scoped 守卫内导致 skill_off_0 目标的 trait tooltip 不显示

- **现象**: 嫁祸特性的连击加成在战斗日志中正常显示，但特性 tooltip 悬停时看不到连击数值
- **根因**: display-only StatBuffEffect 创建在 `if not skill_scoped` 块内，observer 的 power_mod 以 skill_off_0 为目标时 skill_scoped=True，整个 tooltip 效果创建被跳过
- **修复**: 将 display-only 效果创建逻辑移出 skill_scoped 守卫，使其对 skill_scoped 目标同样生效
- **涉及文件**: backend/engine/replayer.py:498-513
- **教训**: tooltip 不显示但日志有数值 → 查效果是否因 target 类型（skill_scoped vs sprite）被跳过创建

## 2026-05-28 - life_drain 不在 _VISIBLE_MOD_STATS 导致吸血 buff 不显示在精灵 buff 栏

- **现象**: 「贪婪」技能设置吸血100%，伤害中吸血效果生效但精灵 buff 栏无吸血图标
- **根因**: _VISIBLE_MOD_STATS 仅含 combo/priority，replayer 不为 life_drain 创建可见 StatBuffEffect。同时 display-only 效果创建时 source 为空回退到 species.ability，skill 的 life_drain 被误归入 trait tooltip
- **修复**: life_drain 加入 _VISIBLE_MOD_STATS；ratio 类 stat 的 step 用 `value * _STEP_UNIT` 转换；display_name 加 % 后缀；移除 display-only 效果的 species.ability 回退
- **涉及文件**: backend/engine/replayer.py:67/485-489/498-513, backend/vm/effect.py:180-183
- **教训**: buff 栏有数值生效但无图标 → 先查 _VISIBLE_MOD_STATS 是否包含该 stat；效果出现在错误归属（skill→trait）→ 查 source 回退逻辑

## 2026-05-28 - hp_missing_ratio 派生查询 per 量化在 (1-x) 变换之前导致连击值为负

- **现象**: 朔夜伊芙「嫁祸」特性（每失去25%生命连击+2）满血时连击显示-6，血量越低连击负值越小，完全不正确
- **根因**: 编译器路径 hp_missing_ratio 派生查询将 (1-hp_ratio) 变换混入 scale/offset，但 resolve() 先执行 per 的 int() 量化再执行 scale/offset → `int(hp_ratio/0.25)*-2+2`（满血=-6）而非 `int((1-hp_ratio)/0.25)*2`（满血=0）
- **修复**: Query 新增 pre_scale/pre_offset 字段（在 per 前应用）；hp_missing_ratio 改用 pre 变换；嫁祸 power_mod 加 mode="set" 防止跨回合累加
- **涉及文件**: backend/vm/ir_values.py:22-23, backend/vm/resolve.py:70-71, backend/vm/compiler/passes/skill_parse.py:164-182, data/traits/嫁祸.json:22
- **教训**: 派生查询使用 per 量化时，检查变换顺序——若 (1-x) 这类派生变换在 per 之后才执行，int() 会量化错误的值

## 2026-05-28 - 守护者特性 @player_moe_stacks 公式变量未注册 + 解析器 int() 静默失败 + display-only 效果缺失

- **现象**: 卡洛儿「守护者」特性不触发——己方有萌化 buff 时入场，技能能耗未减少。第一轮修复（添加变量）后仍不触发；第二轮修复后能耗减少生效，但特性 tooltip 不显示效果数值。
- **根因**: 四个独立断点：(1) `@player_moe_stacks` 不在 `_FORMULA_PATH_MAP` 中，`_resolve_trait_ref` fallback 返回 0；(2) `_parse_stat_stage` 对公式字符串 `"=@player_moe_stacks * -1"` 直接 `int()` → ValueError，被 `ObserverRegistry.fire()` 的 `except Exception: continue` 静默吞掉；(3) `resolve()` 对 `Literal(value="=@...")` 直接返回原始字符串而非解析公式，`int()` 再次失败；(4) `_apply_stat_change` 只为 `_STAGE_STATS` 创建 tooltip 展示效果，漏掉 `energy_cost`。
- **修复**: (1) ctx.py + resolve.py + snapshot.py + battle.py：新增 `moe_team_stacks` 字段+FORMULA_PATH_MAP+ADDRESS_MAP+计算逻辑；(2) skill_parse.py：`_parse_stat_stage` 识别 `=` 前缀，转为 `Literal` 存储；(3) resolve.py：`Literal` 分支检测 `=` 前缀调用 `_resolve_formula_string`；(4) replayer.py：`_apply_stat_change` 对 `energy_cost`/`priority`/`combo` 创建 display-only 效果。
- **涉及文件**: backend/vm/ctx.py:128,296, backend/vm/resolve.py:55-61,250, backend/engine/snapshot.py:85,300, backend/sim/battle.py:133-140,153, backend/vm/compiler/passes/skill_parse.py:489-492, backend/engine/replayer.py:341-345
- **教训**: trait 完全不触发时按三阶段排查——① 查公式变量是否在 `_FORMULA_PATH_MAP`/ADDRESS_MAP 中注册；② 查 trait 加载路径是 `TraitToObserver`（保持 dict）还是 `DataDrivenTrait`（编译为 IR），dict 路径的 parser 对公式字符串可能 `int()` 失败；③ 任意 `_parse_*` 方法的字符串处理出错都被 `except Exception: continue` 静默吞掉——加 print 或日志确认效果是否被执行到。

## 2026-05-28 - 守护者特性第二次入场不触发

- **现象**: 守护者特性第一次入场正常减能耗，第二次入场完全不触发
- **根因**: 三个问题叠加：(1) `_resolve_return` 缺少 `entry_turn` 赋值，返场时 `just_entered` 为 False；(2) trait 从 `stat_stage` 改成 `power_mod` 后写入 `sprite._modifiers`，被 `_PER_TURN_KEYS` 每回合清理；(3) 萌化 scope 为 `battlefield`，离场时被清除，`moe_team_stacks` 变为 0
- **修复**: (1) `battle_mechanics.py:92` 补上 `entry_turn = self.turn`；(2) 守护者.json `power_mod` → `stat_stage`；(3) `abnormal_config.py:46` 萌化 scope → `persistent`
- **涉及文件**: `backend/sim/battle_mechanics.py:92`, `data/traits/守护者.json`, `backend/engine/abnormal_config.py:46`
- **教训**: 入场触发类 observer 不生效时，先检查三个入场路径（switch/return/faint）是否都设置了 `entry_turn`；能量修正优先用 `stat_stage` 而非 `power_mod`，后者会被 `_PER_TURN_KEYS` 误伤

## 2026-05-28 - 暮星辰特性触发对面星陨印记时消耗全部层数

- **现象**: 暮星辰触发对面星陨印记时消耗了全部层数，而非一半
- **根因**: trigger_starfall 调用 consume_starfall_stacks 时传了 defender 而非 attacker，starfall_consume_ratio 设在 attacker（暮星辰）身上，检查了错误的精灵
- **修复**: globals.py:255 将 defender 改为 attacker
- **涉及文件**: backend/sim/globals.py:255
- **教训**: consume/trigger 类方法传入 sprite 时，确认是 attacker 还是 defender——ratio 类 buff 通常设在特性持有者身上

## 2026-05-28 - 钻石蜗完全偏振特性无法免疫对应系别伤害

- **现象**: 钻石蜗携带水炮盾，对面水系技能仍造成伤害（最终-1HP），减伤一度显示200%
- **根因**: 四重问题 — ① owner filter 阻止 defender 的 pre_calc observer 在 attacker 回合触发；② damage_reduction 在 _RATIO_STATS 中默认值 1.0，add 模式 1.0+1.0=2.0 显示 200%；③ calc_damage 的 max(1, round(core)) 将 100%减伤后的 0 伤害兜底为 1；④ adjust_damage 的 max(1, amount) 再次将 0 兜底为 1
- **修复**: ① battle.py 新增第二次 _fire_pre_calc(..., id(opp_sprite))；② replayer.py 对 damage_reduction 特殊处理 add 模式默认值 0.0；③ damage.py 和 resolver.py 在 damage_reduction>=1.0 时提前返回 0；④ modifiers.py adjust_damage 在原始 damage<=0 时提前返回 0
- **涉及文件**: backend/engine/battle.py:130, backend/engine/replayer.py:455, backend/vm/damage.py:68, backend/sim/resolver.py:116, backend/engine/modifiers.py:132
- **教训**: 防御方 observer 需在 attacker ctx 中额外触发（owner filter 过滤掉非 owner 的 observer）；max(1, ...) 兜底有三处（damage.py、resolver.py、modifiers.py adjust_damage），缺一不可

## 2026-05-28 - power_mod 缺 _STAT_LABELS + skill_where 路径绕开显示格式化导致日志/tooltip 不显示

- **现象**: 波多西「定向精炼」特性入场时日志显示 power_mod+1（原始 key 名、整数格式），特性 tooltip 不显示威力加成
- **根因**: 四重缺失。① power_mod 不在 replayer.py 和 effect.py 的 _STAT_LABELS 中 → 日志和 tooltip 均显示原始 key；② mult_mod 带 skill_where（元素过滤）走 _apply_to_matching_skills 函数，该路径无 display-only 效果创建逻辑 → tooltip 无数据；③ _apply_to_matching_skills 格式固定为 {delta:+.0f} → 步数值显示为整数而非百分比；④ 日志不含元素名，两个系别效果相同无法区分
- **修复**: effect.py _STAT_LABELS 加 power_mod；replayer.py _STAT_LABELS + _VISIBLE_MOD_STATS 加 power_mod；_apply_to_matching_skills 新增 power_mod 分支：用 _STEP_PCT 换算百分比显示 + skill_where.element 前缀 + 创建 display-only StatBuffEffect；_apply_modifier 主路径同样加 power_mod 格式分支
- **涉及文件**: backend/engine/replayer.py:67,99,206-230,525-528,535-536, backend/vm/effect.py:208
- **教训**: 带 skill_where/skill_filter 的 modifier 走 _apply_to_matching_skills（replayer.py:393-395），完全绕开 _apply_modifier 的格式化和 display-only 逻辑——排查「改了 _apply_modifier 但不生效」时直接检查是否被 skill_where 分支提前 return

## 2026-05-28 - 宝剑王牌传动后封印位置不跟随

- **现象**: 传动后，被封印的仍是传动前在2/4号位的技能，传动到2/4号位的技能可正常使用
- **根因**: TraitLoader._trait_cache 是模块级 dict，修改 data/traits/*.json 后 uvicorn --reload 不会重启（只监控 .py 文件），导致旧版 trait（无 turn_start 监听）仍在缓存中
- **修复**: 重启后端服务器；后端逻辑经验证正确——turn_start 观察者会在传动后重新封印当前2/4号位
- **涉及文件**: `data/traits/宝剑王牌.json`, `backend/engine/trait_loader.py:22`
- **教训**: 修改 JSON 数据文件后必须手动重启服务器；排查"数据不刷新"问题时优先检查模块级缓存是否过期

## 2026-05-28 - 御驾亲征 on_self_ko observer 不触发：sim 层 _check_faint_interrupt 未触发 post_ko

- **现象**: 棋契陛下（御驾亲征特性：力竭时额外扣魔力）力竭时只损失1点魔力，特性完全不触发。修复后魔力扣到0但未判负
- **根因**: `battle_mechanics.py:_check_faint_interrupt` 处理力竭流程时只触发 `post_leave`/`post_entry`/`post_enemy_leave`，完全没触发 `post_ko`。`on_self_ko` 条件对应的 observer 在 sim 层永不激活。连带问题：`player.lives <= 0` 战败判定在 `post_ko` 之前执行，特性扣完魔力到0后不判负
- **修复**: ① `battle_mechanics.py` 在力竭切换流程中新增 `fire_trigger("post_ko", ...)` 并在其后追加战败检查；② `battle.py` 将 `post_ko` 加入 owner filter 列表 + 视角翻转逻辑，防止对手 on_self_ko 误触发；③ `replayer.py` lives 事件格式改为 `魔力Δ→最终值` 便于确认
- **涉及文件**: backend/sim/battle_mechanics.py:145-150, backend/engine/battle.py:327/337, backend/engine/replayer.py:1080-1081
- **教训**: 特性完全不触发且 observer 条件在 COND_EVAL 已注册时，grep 对应 trigger 是否在 sim 层所有力竭/切换/入场路径中都有 `fire_trigger`——缺少就是静默不触发

## 2026-05-28 - 思维之盾 on_next 能耗减免"不触发"——全链路正确但日志和前端双静默

- **现象**: 游蛇魔使应对成功后，下次行动能耗未减5，日志无任何反馈
- **根因**: Observer → pending → consume 全链路实际正常工作（debug 日志确认）。三处静默：① `replayer.py` on_next 加入 pending 返回 `""`；② `battle.py` energy_cost 消费被 `continue` 跳过；③ `main.py` 前端只读 `sk._modifiers`（BattleSkill 级），on_next 写入 `s._modifiers`（Sprite 级），显示不匹配；④ 消费后 `_modifiers` 残留到回合末才清，API 调用无法区分"已消费"和"未消费"
- **修复**: ① replayer on_next 输出"获得待机效果: 能耗-N"事件；② battle.py energy_cost 消费输出"触发待机效果: 能耗-N"事件；③ main.py 前端公式加上 `_pending_modifiers` + `s._modifiers` 两处 energy_cost；④ battle.py 能耗支付后立即 `pop("energy_cost")` 清除一次性 modifier
- **涉及文件**: backend/engine/replayer.py:408-412, backend/sim/battle.py:549-553/583-586, backend/api/main.py:426-427
- **教训**: `on_next` 特性"不触发"时，加 debug 日志确认 observer→pending→consume 三阶段链路——大概率触发了但日志和前端都静默

## 2026-05-28 - 湿润印记不减费——MARK_TEMPLATES 名称不匹配（湿润 vs 润泽）

- **现象**: 使用"打湿"技能获得湿润印记后，技能能耗没有减少
- **根因**: `mark_config.py` 的 `MARK_TEMPLATES` 只有 "润泽印记"（`润`），技能 JSON 用的是 "湿润印记"（`湿`），字形相近但不匹配。未命中 template 时 `energy_mod` 默认为 0
- **修复**: 在 `MARK_TEMPLATES` 新增 "湿润印记" 条目，`energy_mod=1`
- **涉及文件**: backend/engine/mark_config.py:26-29
- **教训**: 印记效果不生效 → grep `MARK_TEMPLATES` 确认印记名是否**逐字**匹配（润≠湿）

## 2026-05-28 - 奉献修饰器（combo/life_drain）残留到 buff 栏

- **现象**: 女王蜂使用虫群后，特性栏显示连击+1/吸血+10% 的永久 buff
- **根因**: 奉献消耗走 replayer 写 `sprite._modifiers`，同时 combo/life_drain 在 `_VISIBLE_MOD_STATS` 中，replayer 自动创建 `StatBuffEffect` 显示在 buff 栏。pop `_modifiers` 清不掉 `active_effects` 里的 `StatBuffEffect`
- **修复**: 修饰器直接写入 `user._modifiers`（绕过 replayer，不创建 StatBuffEffect），技能结束后恢复奉献前的值（而非粗暴 pop，避免误清其他特性给的永久 buff）
- **涉及文件**: backend/sim/battle.py:564-616, 716-724
- **教训**: 临时效果不要走 replayer 的 `_apply_modifier`——它会创建可见的 `StatBuffEffect`；直接写 `_modifiers` + 事后恢复原值即可

## 2026-05-28 - Trait 回合末奉献 +1 变成 -1（覆盖已有层数）

- **现象**: 扫拖一体/花精灵回合末日志显示"奉献XX -1层"而非"+1层"
- **根因**: trait JSON 中 devotion mod op 未指定 mode，`op_mod` 默认 `mode="set"`，将已有层数设回 1（2→1 = -1）
- **修复**: 四个 trait JSON 的 devotion mod 添加 `"mode": "add"`
- **涉及文件**: data/traits/扫拖一体.json, 花精灵.json, 特殊清洁场景.json, 振奋虫心.json
- **教训**: 数值叠加效果缺少 mode 字段时，先查 op handler 的默认值——默认 set 而非 add

## 2026-05-28 - 随机奉献 value>1 时 N 层叠加到同一类型，而非 N 次独立随机

- **现象**: 虫群智慧（value:2, name:"random"）获得 2 层同一奉献类型，而非两次独立随机选取
- **根因**: replayer.py `_apply_modifier` 奉献分支先 `random.choice` 选一个类型，再把全部 value 层数加到该类型上
- **修复**: `mode="add"` + `name="random"` 时循环 `value` 次，每次独立随机选取一个类型 +1
- **涉及文件**: backend/engine/replayer.py:398-404
- **教训**: 随机效果叠加时，先确认语义是"对同一目标叠加 N 次"还是"N 次独立随机"

## 2026-05-28 - 拨浪鼓特性完全不生效 + power_mod 日志/tooltip 显示为百分比

- **现象**: 古钟蛇（拨浪鼓特性）换上场后，特性完全不触发——战斗日志和特性 tooltip 均无任何效果显示。修复键名后日志显示"毒威力+30%"，tooltip 仍显示百分比
- **根因**: 三个独立断点。① trait JSON 使用 "triggers" 键名，load_data_trait() 只读 "effects" → 返回 None → 特性从未注册（与 passive→effects 同模式）；② power_mod 在战斗日志和 tooltip 中均被当作百分比（display_mult）显示，但实际是步数制固定威力（1步=10威力），显示格式与语义不一致；③ effect.py is_positive/is_negative 只检查 display_mult，漏掉 display_value
- **修复**: 拨浪鼓.json triggers→effects；replayer.py 两处日志显示去 % 改为 flat 值 + 两处 tooltip 效果改用 display_value；effect.py is_positive/is_negative 补 display_value 检查；resolve.py @ref 正则补 (?:\[[^\]]*\])? 支持 @key[sub] 无点号方括号（附带修复）
- **涉及文件**: data/traits/拨浪鼓.json, backend/engine/replayer.py:206-228/570-573/231/581, backend/vm/effect.py:161-176, backend/vm/resolve.py:325
- **教训**: ① 特性完全不触发→先 grep 确认 JSON 键名是否匹配 loader（effects vs triggers/passive）；② power_mod 是步数制（1步=10威力），不是倍率 stat——日志和 tooltip 都应显示为固定值，排查"加成数值看起来不对"时先确认 stat 类型是 ratio 还是 step-based

## 2026-05-28 - 指挥家特性退场后再入场永久 buff 丢失

- **现象**: 绅士鸡（指挥家特性）应对成功后获得双攻+20%，切换下场再上场后，特性 tooltip 和 buff 栏均不显示已获得的加成
- **根因**: 双重断点。① `trait_loader.py:72` — re-entry 时 `load_for_sprite` 用 `e.source != trait_source` 清除所有同源 EffectObject（含 permanent StatBuffEffect），仅应清除 ObserverEffect/ModifierEffect；② `replayer.py:936/942` — `_sync_mult_display_effect` 将 display-only StatBuffEffect scope 硬编码为 `"battlefield"`，离场时被 `clear_effects('battlefield')` 清除，tooltip 无数据
- **修复**: trait_loader.py 改为 `isinstance(e, (ObserverEffect, ModifierEffect)) and e.source == trait_source` 精准过滤；replayer.py display effect scope 改用调用者传入的 `scope` 参数（permanent → permanent）
- **涉及文件**: backend/engine/trait_loader.py:70-80, backend/engine/replayer.py:932-942
- **教训**: 永久效果退场再入场后丢失，排查两个点——① load_for_sprite reload 时是否精准清理（只删元数据不删战斗效果）② display-only 效果的 scope 是否与真实效果一致（不一致则 tooltip 静默消失）

## 2026-05-28 - 无差别过滤 combo_set 固定值被 combo_mod 叠加

- **现象**: 特性"在场时连击数固定为2"触发后，实际连击为3而非2
- **根因**: snapshot.py:178 `combo_set + combo_mod` 公式——combo_set=2 后，奉献系统等来源的 combo_mod=1 仍会叠加，2+1=3
- **修复**: snapshot.py 改为 `combo_self = max(1, combo_set)` 忽略 combo_mod；replayer.py 加 combo_set 中文标签 + 日志显示"固定为2"而非"+2"
- **涉及文件**: `backend/engine/snapshot.py:177-178`, `backend/engine/replayer.py:88,595-596`
- **教训**: combo_set 是"固定覆盖"语义，不应叠加任何其他连击修改；modifiers.py 中同名的 combo_set 是技能日志路径（set/add 叠加），两条路径语义不同

## 2026-05-28 - 斗技 power 加成显示为百分比而非绝对值

- **现象**: 武者鸡斗技触发后，战斗日志显示"威力+20%"（应为"+20"），特性tooltip无加成显示，前端技能栏威力不变
- **根因**: replayer.py `_apply_stat_change` 中 power 未列入绝对显示分支（走入 else 显示%），也未列入 display effect 分支（不写 tooltip）；api/main.py effective_power 只读 sk._modifiers["power"] 忽略 sprite.power_mod
- **修复**: replayer.py power 加入绝对显示分支 + display effect 分支；api 两处加 `+ sprite.power_mod * 10`；effect.py display_name 加 power display_value 判断
- **涉及文件**: `backend/engine/replayer.py:355-373`, `backend/api/main.py:417,539`, `backend/vm/effect.py:186-190`
- **教训**: 非六维非百分比的新 stat（power/priority/combo）要同时加到 replayer 的绝对显示分支、display effect 分支、前端 API 的 effective 计算三处

## 2026-05-28 - damage_restraint 条件缺失导致"最好的伙伴"特性完全不触发

- **现象**: 最好的伙伴特性（造成克制伤害后获得增益）配置后游戏中从未触发
- **根因**: JSON 中原用 `compare` + `q: "type_mult"` 查询，但 ADDRESS_MAP 中不存在该地址，导致条件静默失败。引擎缺少 `damage_restraint` 条件的基础设施（`element_advantage` 字段 + VM cond 判断 + snapshot 计算）
- **修复**: 在 `ctx.py` 新增 `element_advantage` 字段及 ADDRESS_MAP；`cond.py` 新增 `damage_restraint` 条件（`element_advantage >= 2.0`）；`snapshot.py` 新增 `_get_element_advantage()` 计算属性克制乘积
- **涉及文件**: `backend/vm/ctx.py`, `backend/vm/cond.py`, `backend/engine/snapshot.py`
- **教训**: 新条件类型需同时检查 ADDRESS_MAP 注册、cond 触发器和 eval、snapshot 计算三项，缺一不可

## 2026-05-28 - 特性 tooltip 数值始终显示 20% 不叠加

- **现象**: 最好的伙伴触发多次后，特性 tooltip 始终显示"+20%"而非叠加后的"+40%"
- **根因**: `replayer.py:_sync_mult_display_effect` 默认 `additive=False`，每次触发时用新值覆盖旧 display_mult 而非累加
- **修复**: 新增 `additive` 参数，`_apply_stat_change` 调用处传入 `additive=True`，实现 `display_mult += mult_value` 累加
- **涉及文件**: `backend/engine/replayer.py:_sync_mult_display_effect`, `:_apply_stat_change`
- **教训**: 显示层和逻辑层叠加行为不一致时，先查显示层是覆盖还是累加

## 2026-05-28 - 速度加成不显示在特性 tooltip + 百分比速度特例

- **现象**: 最好的伙伴的速度加成不显示在特性 tooltip 中；且该特性需要百分比速度（+20%）而全局速度公式是绝对数值（每步+10点）
- **根因**: ① `_apply_stat_change` 中 speed 被跳过不创建 display effect；② 全局 `effective_stat` 速度公式为 `base + steps * 10`，无法表达百分比加成
- **修复**: ① 移除 speed 排除并新增 display_value 路径；② 将 JSON 中速度改为 `mult_mod { attr:"speed", value:0.2, mode:"add" }`，写入 `_modifiers["speed"]` 经 `_compute_speed_self` 做 `base * (1 + speed_mod)` 乘算；③ `_apply_modifier` 中 `_STAGE_STATS` 的 display effect 传入 `additive=(m.mode == "add")` 确保 tooltip 叠加
- **涉及文件**: `data/traits/最好的伙伴.json`, `backend/engine/replayer.py`
- **教训**: `stat_stage` 适合绝对数值增益，百分比增益用 `mult_mod`，两者走不同的 sprite 存储路径（`active_effects` vs `_modifiers`），显示路径也不同

## 2026-05-29 - 月牙雪糕特性星陨伤害不触发

- **现象**: 月牙雪熊攻击有冻结的敌方时，没有额外星陨伤害
- **根因**: trait JSON 用 compare + q:"skill.is_attack" 查 ADDRESS_MAP，该 computed path 不在映射中。skill.is_attack 只能通过 trait_path 条件类型访问，compare 条件走的是 ADDRESS_MAP 查找路径
- **修复**: 月牙雪糕.json 将 skill.is_attack 子条件从 cond:"compare" + q → cond:"trait_path" + path
- **涉及文件**: data/traits/月牙雪糕.json:20-22
- **教训**: trait 条件中 computed path（skill.is_attack/skill.is_defense/target.is_fainted 等）只能用 trait_path 条件类型，compare 类型只支持 ADDRESS_MAP 中的直接字段

## 2026-05-29 - 抓到你了特性入场冰冻不触发

- **现象**: 雪影娃娃进化之力首领化为雪影冰灵后，对方没有获得2层冻结
- **根因**: trait JSON 用 compare + q:"is_fainted" 查 ADDRESS_MAP，("sprite_self", "is_fainted") 不存在 → KeyError 被 _fire_post_event 的 except Exception: continue 静默吞掉
- **修复**: resolve.py 新增 is_fainted 派生查询，sprite_self→ctx.event.self_koed，sprite_opp→ctx.event.target_fainted
- **涉及文件**: backend/vm/resolve.py:110-112
- **教训**: trait 条件用 compare + q 字段时，grep ADDRESS_MAP 确认 (of, q) 键是否存在——不存在就是 KeyError 被静默吞掉

## 2026-05-29 - 水翼飞升：印记能耗减免未在支付中生效 + skill_where 识别不到

- **现象**: 水翼飞升特性减费显示为0，但实际支付仍消耗能量；2层印记+2层特性时威力加成不生效，3层才生效
- **根因**: 
  1. `battle.py` 支付阶段未扣除 `globals.mark_energy_mod(team)`
  2. `_apply_to_matching_skills` / `_apply_direct_mods` 中 `skill_where={"energy_cost":0}` 检查使用 `bs.energy_cost`，该属性只含 `_modifiers["energy_cost"]`（特性减费），不含印记减费
- **修复**: 
  1. `battle.py` 应对路径和正常路径均加 `cost -= self.globals.mark_energy_mod(team)`
  2. `replayer.py` — `_apply_to_matching_skills` 接受 `mark_energy_mod` 参数，`skill_info["energy_cost"]` 计算时减去
  3. `trait_loader.py` — `_apply_direct_mods` 同样接受并使用 `mark_energy_mod`；`battle.py:211` 按队伍计算传入
- **涉及文件**: `battle.py:564-576`, `replayer.py:160-183`, `replayer.py:495-500`, `trait_loader.py:138-143`, `trait_loader.py:150-196`
- **教训**: 支付时扣减但不写入 `_modifiers` 的数值，对依赖 `bs.energy_cost` 属性的 `skill_where` 过滤是透明的——要么写入 `_modifiers`，要么传递到过滤逻辑

## 2026-05-29 - 水翼飞升：特性 tooltip 显示威力倍率+130%

- **现象**: 特性 tooltip 显示"威力倍率+130%"而非"+30%"
- **根因**: `power_mult`/`damage_mult` 的 `ModifierInjection.value` 存的是总值(1.3)，但 `display_mult` 直接透传，前端 `formatMult(val)` 做 `Math.round(val*100)` 得出130%
- **修复**: `replayer.py:64-66` 新增 `_TOTAL_BASED_RATIO_STATS`，对 `power_mult`/`damage_mult` 在创建 `StatBuffEffect` 时 `display_mult = delta - 1.0`
- **涉及文件**: `replayer.py:54-66`, `replayer.py:255-266`
- **教训**: 比率型 stat 要区分"存总值"还是"存增量"——`_RATIO_STATS` 中 `power_mult`/`damage_mult` 是总值，`damage_reduction` 等是增量，显示转换需分别处理

## 2026-05-29 - 水翼飞升：turn_start 观察者每回合触发两次

- **现象**: 战斗日志中每回合显示两次"威力倍率=130%"
- **根因**: `pipeline.py` Step 5 (`DataDrivenTrait.on_turn_start`) 和 Step 5b (直接 `fire_trigger("turn_start")`) 都调用了同一个 `vm_engine.fire_trigger("turn_start")`，导致所有 `listen:["turn_start"]` 的 observer 被执行两次
- **修复**: 删除 `pipeline.py:60-74` 的 Step 5b（Step 5 已覆盖）
- **涉及文件**: `pipeline.py:60-74`
- **教训**: 管线重复触发点排查：搜索 `fire_trigger("trigger_name")` 出现位置，确认是否被不同入口重复调用

## 2026-05-29 - 泛音列特性能耗方向在回合间翻转

- **现象**: 泛音列给对方攻击技能+3能耗，第一回合反而降能耗，第二回合能耗暴涨耗光所有能量
- **根因**: trait_loader.py `_apply_direct_mods` 重新应用修改器时未乘以 sprite 的 `energy_cost_delta_mult`（由对方「对流」特性设为 -1），导致首次应用 delta=-3（降能耗），回合重新应用 delta=+3（升能耗），方向翻转
- **修复**: `trait_loader.py:191` 增加 `if attr == "energy_cost": delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)`，与 replayer.py `_apply_to_matching_skills` 行为一致
- **涉及文件**: `backend/engine/trait_loader.py:191`
- **教训**: 特性修改器在 reapply 路径和首次 apply 路径必须用同一套乘数逻辑；`energy_cost_delta_mult` 是 sprite 级持久键，排查能耗异常时优先检查它

## 2026-05-29 - 洁癖特性继承效果在换宠后 buff 栏不显示

- **现象**: 翠顶夫人（洁癖特性：离场后增益/减益被换上精灵继承）换宠后，新上场精灵 buff 栏无任何继承效果。日志显示"继承1个效果"和"威力倍率=130%"，但 buff 栏为空
- **根因**: dispatch_entry（消费 battle.pending_effects）在 fire_trigger("post_leave")（洁癖 observer 通过 via_pending 写入继承效果）之前调用。5 处离场/换宠流程均有此时序：① _resolve_switch ② _check_faint_interrupt ③ _handle_escape ④ _handle_escape_inherit ⑤ battle.py 能量归零强制换宠。dispatch_entry 读 pending_effects 时为空，post_leave 写后无人再消费
- **修复**: 5 处 fire_trigger("post_leave") 之后立即消费 battle.pending_effects[team]，将继承的 StatBuffEffect 写入新上场精灵
- **涉及文件**: backend/sim/battle_mechanics.py:75,161,287,322, backend/sim/battle.py:1064
- **教训**: via_pending 继承效果不显示时，直接检查 dispatch_entry 和 fire_trigger("post_leave") 的调用顺序——dispatch_entry 消费 pending_effects 必须在所有能写入 pending_effects 的 trigger 之后

## 2026-05-29 - 龙鱼洄游特性不触发 + 架势 pre_charged 不生效

- **现象**: 龙鱼的洄游特性（每次进入蓄力全技能能耗永久-1）完全不触发，日志无能耗变化；架势技能虽设置 pre_charged 标志但蓄力门控不读取，下一发蓄力技能仍需等一回合
- **根因**: ① `eval_one` 不处理裸字符串条件（如 `"always"`），observer cond 为 plain string 时直接走 `return False` 分支——洄游、悼亡、悲悯、暴食、水翼飞升等特性均受此影响；② `_gate_charge_vm` 只写不读 pre_charged——架势设置后无人消费，蓄力门控照常进入 charging；③ observer 触发的 `scope=permanent` power_mod 在每回合 `_SKILL_PER_TURN_KEYS` 清理后未被恢复，能耗减益一回合就丢失
- **修复**: ① `cond.py:457-461` — eval_one 增加 `isinstance(cond, str)` 分支，调用 COND_EVAL 字典；② `battle.py:800-811` — _gate_charge_vm 在 has_charge 分支检查并消费 pre_charged 标志；③ `battle.py:219-222` — execute_turn 在 reapply 后调用 skill.load_permanent_mods；④ `replayer.py:155-162` — _apply_to_all_skills 对 permanent scope 持久化到 sprite._modifiers[skill.<name>.<stat>]
- **涉及文件**: backend/vm/cond.py:457, backend/sim/battle.py:800/219, backend/engine/replayer.py:155
- **教训**: 特性 observer 不触发 → 先查 eval_one 是否认识该 cond 类型（plain string vs dict）；permanent scope 数值跨回合丢失 → 查 per-turn cleanup 后是否有 restore 环节；flag 型 stat 日志写入但不生效 → 检查消费端是否只写不读

## 2026-05-30 - 洄游特性能耗减免在特性 tooltip 中不显示

- **现象**: 龙鱼洄游特性触发后，战斗日志显示"全技能能耗-1"且能耗确实减少，但鼠标悬停特性名称时 tooltip 仍显示"暂无效果数据"
- **根因**: ① `_apply_to_all_skills` 分发 `energy_cost` modifier 到各 BattleSkill._modifiers 后直接 return，未创建 display-only StatBuffEffect（tooltip 数据源）；② `_apply_modifier` 在此路径提前 return，跳过所有 tooltip 展示效果创建代码；③ 前端 TraitTooltip fallback 路径只过滤 `display_mult != null`，遗漏 `display_value`
- **修复**: ① `replayer.py` _apply_to_all_skills 加 `replayer` 参数，分发后调用 `_sync_mult_display_effect` 创建 display_value 效果；② TraitTooltip.vue fallback 加上 `display_value != null` 检查
- **涉及文件**: backend/engine/replayer.py:138/522, frontend/src/components/TraitTooltip.vue:28
- **教训**: skill_filter="all" 路径的 tooltip 不显示 → 直接查 `_apply_to_all_skills` 是否创建了 display effect——它绕开了 `_apply_modifier` 的所有展示逻辑

## 2026-05-30 - 渗透 1使用显示0%、2使用显示10% —— stat_stage int()截断 + 模块级缓存

- **现象**: 渗透特性（每使用1次武/地技能→入场攻防+5%）用1次技能后换上场无加成，用2次才显示10%
- **根因**: ① `stat_stage` 的 `steps` 在 `mod.py:119-121` 被 `int()` 截断（0.5→0，1.0→1），最小粒度为10%；② 改为 `mult_mod` 后仍不生效，因为 `trait_loader.py:22` 的 `_trait_cache` 是模块级 dict，修改 JSON 不重启服务器不会重读文件
- **修复**: `渗透.json` — `stat_stage`→`mult_mod`，`steps: "* 0.5"`→`value: "* 0.05"`，绕过 int() 截断；重启后端清除 _trait_cache
- **涉及文件**: data/traits/渗透.json, backend/vm/ops/mod.py:119-121, backend/engine/trait_loader.py:22/270
- **教训**: 百分比数值 <10% 时优先用 `mult_mod` 而非 `stat_stage`（后者 steps=int() 仅支持 10% 粒度）；修改 JSON 后必须重启服务器清除 `_trait_cache`

## 2026-05-30 - 深层氧循环回复10%而非15%

- **现象**: 深层氧循环描述为"回复15%生命"，实际只回复10%
- **根因**: JSON 中 heal value 写的是 `max_hp * 0.1`（10%），应为 `0.15`
- **修复**: `深层氧循环.json` — `0.1` → `0.15`
- **涉及文件**: data/traits/深层氧循环.json
- **教训**: 描述和数值不一致时，先 grep 同类特性确认正确系数再改

## 2026-05-30 - 伊兰龙 ability_id 写错导致特性始终为嫉妒

- **现象**: 修改游弋.json 并重启后端前端后，伊兰龙特性仍显示为嫉妒
- **根因**: sprite JSON 中 `ability_id: 20151`（嫉妒）而非 `20088`（游弋），后端按 ability_id 加载特性，ability 字段仅用于展示
- **修复**: 204_伊兰龙.json — ability_id 20151→20088，ability 嫉妒→游弋
- **涉及文件**: data/sprites/204_伊兰龙.json:14-15
- **教训**: 特性名和效果对不上时，先查 sprite JSON 的 ability_id 是否正确——ability 字段只是展示名，ability_id 才是实际加载的 key

## 2026-05-30 - 游弋 charge_any_skill flag 只写不读 + 前端硬编码嫉妒

- **现象**: 伊兰龙蓄力期间其他技能仍为灰色不可选
- **根因**: 三重断点。① 后端 `_gate_charge_vm` 只检查 `usable_while_charging`（技能级），不读 sprite 级 `charge_any_skill` flag；② 前端 `canUseSkill` 硬编码 `trait.name === '嫉妒'`；③ API 不返回 `charge_any_skill` 字段
- **修复**: battle.py gate 加 `charge_any_skill` 检查；schemas.py/main.py 新增字段；BattleArena.vue 用 `charge_any_skill` 替代硬编码嫉妒
- **涉及文件**: backend/sim/battle.py:792, backend/api/schemas.py:90, backend/api/main.py:450, frontend/src/components/BattleArena.vue:32-80
- **教训**: flag 型 trait 效果不生效时，直接搜 flag 名在所有层的引用——后端 gate、API 序列化、前端 UI 各有一层过滤，缺一不可

## 2026-05-30 - 游弋 permanent scope mult_mod 重复入场叠加到 +200%

- **现象**: 伊兰龙换下再换上场后，双防显示 +200% 而非 +100%
- **根因**: mult_mod 用 `scope: "permanent"` + `mode: "add"`，退场不清除 modifier，再入场时 post_entry 再次触发叠加
- **修复**: scope 改为 "battlefield"，退场自动清除，再入场重新加到 100%
- **涉及文件**: data/traits/游弋.json:28,36
- **教训**: 入场触发类永久 buff 用 `scope: "battlefield"` 而非 `"permanent"`——后者退场不清理，搭配 mode:"add" 会导致跨入场累加

## 2026-05-30 - 溶解扩散/溶解腐蚀 _parse_abnormal 对公式字符串 int() 失败

- **现象**: 千棘盔携带5个毒系技能，使用水系技能后对方完全没中毒
- **根因**: trait JSON 的 `stacks: "=@self.skills[element=毒].count"` 是公式字符串，`_parse_abnormal` 用 `_int_or` 直接 `int()` → ValueError → `except Exception: continue` 静默吞掉。与 2026-05-28 守护者 `_parse_stat_stage` 同模式
- **修复**: `_parse_abnormal` 仿照 `_parse_stat_stage` 识别 `=` 前缀公式字符串，路由到 `_parse_value` 转为 Literal 走运行时 resolve
- **涉及文件**: backend/vm/compiler/passes/skill_parse.py:328-345
- **教训**: trait JSON 中任何带 `=` 前缀的公式字符串，在 parser 层都要确认对应的 `_parse_*` 方法是否识别——直接 `int()` 就会 ValueError 被静默吞掉

## 2026-05-30 - skills[element=X].count 返回布尔值而非实际数量

- **现象**: 修复解析后，5个毒系技能只能上1层中毒（预期5层）
- **根因**: `_resolve_trait_ref` 中 `skills[element=X].count` 读的是 `skill_elements_self`（frozenset，去重集合），返回 `1 if element in elements else 0`——布尔值而非实际技能数量
- **修复**: Ctx 新增 `skill_element_counts_self/opp: dict[str, int]`；snapshot 遍历技能累加计数；resolve 公式改为 `counts.get(element, 0)`
- **涉及文件**: backend/vm/ctx.py:126-127/214-215, backend/engine/snapshot.py:136-139/163-166, backend/vm/resolve.py:300
- **教训**: frozenset 只能做包含判断，聚合计数必须用 dict——公式解析路径中用了什么数据结构直接决定返回值语义

## 2026-05-30 - 灰色肖像 observer 不触发：compare+q 查 computed path + post_skill 当条件用

- **现象**: 画间法师手（灰色肖像）攻击后敌方中毒层数未+3，observer 完全不触发
- **根因**: trait JSON 两个条件写法错误。① `compare`+`q:"skill.is_attack"` 走 ADDRESS_MAP 查找，computed path 不在映射中→KeyError；② `{"cond":"post_skill"}` 是 trigger point 名，不在 COND_EVAL 中→KeyError。两处均被 `_fire_post_event` 的 `except Exception: continue` 静默吞掉
- **修复**: ① compare→trait_path + path；② 移除 and+post_skill 子条件，改用显式 `listen:["post_skill"]`；③ 新增 `effect_delta` opcode 实现遍历所有减益+层数
- **涉及文件**: data/traits/灰色肖像.json:14-21
- **教训**: observer 不触发时两个排查点——compare+q 字段是否在 ADDRESS_MAP（computed path 必须用 trait_path），and 块每个子条件是否在 COND_EVAL（trigger point 名不是条件）

## 2026-05-30 - 石天平特性不触发：schedule op 未在 parser 注册被静默吞掉（第7层）

- **现象**: 巨灵石使用高能耗技能后，回合末敌方未损失能量
- **根因**: `skill_parse.py` 只认 `op="defer"`（`_parse_defer`），JSON 用 `op="schedule"`，`_parse_effect` dispatch 找不到 `_parse_schedule` → `ValueError` 被 `_fire_post_event` 的 `except Exception: continue` 静默吞掉。此前已修复 6 层问题（ADDRESS_MAP 缺 energy_cost、compare handler 未 resolve 公式值、FORMULA_PATH_MAP 缺 target.energy_cost、opp_skill 未传递、opp_skill_for_second 逻辑反了、JSON 公式语义错误），全部被同一静默 catch 掩盖
- **修复**: `skill_parse.py:690` 添加 `_parse_schedule = _parse_defer` 别名
- **涉及文件**: `backend/vm/compiler/passes/skill_parse.py:690`, `backend/vm/ops/schedule.py`, `backend/vm/ctx.py`, `backend/vm/cond.py`, `backend/vm/resolve.py`, `backend/sim/battle.py`, `data/traits/石天平.json`
- **教训**: `except Exception: continue` 是静默杀手——observer/trait 不触发时，优先去 `_fire_post_event` 和 `ObserverRegistry.fire()` 里临时移除 try/except 或加 print，而不是逐层猜。多轮修复后仍不生效，直接怀疑还有一层被静默吞掉

## 2026-05-30 - 腐植循环聚能不触发 + heal 用名义能量而非实际能量

- **现象**: 伊贝粉粉使用聚能回复能量后，腐植循环特性（每回复1能量回复5%生命）不触发；使用藤绞回复4点能量，却按技能描述中的5点能量计算治疗量
- **根因**: 三个断点。① sim/battle.py 聚能路径 fire_trigger("post_energy_change") 未设置 ctx.energy_delta_self（默认0）→ observer 的 when:compare energy_delta>0 过滤掉；② fire_trigger 返回的日志事件被丢弃（未 append 到 events）；③ EnergyChange.delta 存的是 JSON 名义值(5)，gain_energy(5) 被 max_energy cap 到 4，但 _fire_mutation_events 仍读原始 m.delta(5) → heal 多算
- **修复**: ① sim/battle.py 设置 gather_ctx.energy_delta_self = gained + 捕获 fire_trigger 返回事件 append 到 events；② replayer.py _apply_energy_change 将实际 capped 值存入 _energy_deltas dict；③ battle.py _fire_mutation_events 从 _energy_deltas 读取实际值而非 m.delta
- **涉及文件**: backend/sim/battle.py:526-530, backend/engine/replayer.py:737-743, backend/engine/battle.py:417
- **教训**: VM 外部操作（聚能、技能能耗）不经过 journal → observer 依赖的 ctx 字段可能未设置 + 返回值被丢弃 → 双重静默。排查时先 grep fire_trigger 调用点确认 ctx 字段是否填充 + 返回值是否捕获

## 2026-05-30 - 营养液泡特性不触发：post_positive_change 只覆盖 StatChange 不含 ModifierInjection

- **现象**: 白发路路使用热身运动（combo+3）后，营养液泡特性（获得增益时额外层数+2）完全不触发，日志无任何额外效果
- **根因**: 三重断点。① 热身运动 combo+3 走 power_mod → ModifierInjection，而 post_positive_change 仅在 _fire_mutation_events 扫描到 StatChange(is_positive=True) 时发射——ModifierInjection 不在扫描范围；② ctx.event.positive_changed_of 从未被设置（连 StatChange 分支都没设），on_positive_changed 条件永远为 False——即使 StatChange 触发了 post_positive_change，observer 条件也通不过；③ effect_delta 只处理 active_effects 中的 StatBuffEffect/AbnormalEffect，不处理 sprite._modifiers 中的 combo/power/priority
- **修复**: ① battle.py _fire_mutation_events 新增 ModifierInjection 分支 + _is_positive_modifier() 辅助函数（mode=add 时 value>0 为正向，energy_cost 反向）；② StatChange 和 ModifierInjection 分支均设置 positive_changed_of；③ replayer.py _apply_effect_delta 新增 _modifiers 处理（combo/power/priority 增量 + energy_cost 减量）
- **涉及文件**: backend/engine/battle.py:48-61/396-402/455-461, backend/engine/replayer.py:1225-1234
- **教训**: observer 不触发时三连查——① trigger 发射是否覆盖所有 mutation 类型（grep 对应 isinstance 检查）② ctx.event 字段是否被设置（grep 字段名确认赋值点）③ effect op 是否处理目标数据结构（active_effects vs _modifiers）。post_positive_change 之前只覆盖 StatChange 是设计盲区——power_mod/mult_mod 的增益同样应视为 positive change

## 2026-05-30 - 蚀刻 observer 缺少 cond 字段导致永不触发

- **现象**: 裘卡（蚀刻特性）在场时，回合结束敌方中毒层数未转化为中毒印记
- **根因**: 蚀刻.json observer 缺少 cond 字段 → op_count 中 cond=None → eval_one(ctx, None) 在 cond.py:474 走到 return False，observer 注册了但条件永远不满足
- **修复**: 添加 "cond": {"cond": "always"} 到 observer JSON
- **涉及文件**: data/traits/蚀刻.json:10
- **教训**: observer 完全不触发且 JSON 中无明显错误时，先检查是否有 cond 字段——缺失不报错，eval_one 对 None 静默返回 False

## 2026-05-30 - pre_modifier observer 缺少 sprite_id 导致特性效果施加到对手

- **现象**: 窃光蚊（血型吸引）攻击时对手双灯鱼也获得全技能威力+30
- **根因**: battle.py:159 `_fire_pre_event("pre_modifier", ctx)` 未传 sprite_id（默认0），owner 过滤条件 `sprite_id != 0` 为 False 导致跳过过滤，窃光蚊的 observer 在双灯鱼攻击时也被触发，sprite_self 被解析为双灯鱼
- **修复**: 补上 `id(self_sprite)` → `_fire_pre_event("pre_modifier", ctx, id(self_sprite))`，与同文件 pre_calc/pre_defend 调用保持一致
- **涉及文件**: backend/engine/battle.py:159
- **教训**: 特性效果施加到对手时，直接查 `_fire_pre_event` / `_fire_post_event` 调用处是否漏传 sprite_id/owner_id——pre_modifier 是唯一漏掉的 pre_event

## 2026-05-30 - Steal action=copy 不生效：IR 类型和 Parser 未传递 action 字段

- **现象**: 衡量特性入场时 `steal` 操作设置 `action=copy`，但实际仍表现为偷取（移除了对手的增益）
- **根因**: `ir_skill.py:StealOp` 缺少 `action` 属性，`skill_parse.py:_parse_steal` 未从 JSON 提取 `action` 字段。`op_steal` 中的 `getattr(effect, "action", "steal")` 因属性不存在而静默回退到默认值 `"steal"`
- **修复**: `StealOp` 新增 `action: str = "steal"`；`_parse_steal` 新增 `action=e.get("action", "steal")`
- **涉及文件**: `backend/vm/ir_skill.py:246`, `backend/vm/compiler/passes/skill_parse.py:379`
- **教训**: JSON 字段值未生效时，先检查 op_steal 的 `_get` 辅助函数——它用 `getattr` 取对象属性，属性不存在时静默回退默认值，不会报错。追溯到 IR 类型定义和 parser 是否传递了该字段

## 2026-05-30 - replayer._apply_escape 用错属性名导致 escape 测试崩溃

- **现象**: test_e2e_escape_skill 报 AttributeError: 'JournalReplayer' object has no attribute 'battle'
- **根因**: _apply_escape 实现时用了 `self.battle`，JournalReplayer 的属性名是私有形式 `self._battle`（带下划线），同时 engine/battle.py 引用 replayer 时也写了 `replayer.battle`
- **修复**: replayer.py:1272 → `self._battle`，engine/battle.py:392 → `replayer._battle`
- **涉及文件**: backend/engine/replayer.py:1272, backend/engine/battle.py:392
- **教训**: 给对象加新属性前先 grep 确认已有属性的命名约定（self.x vs self._x）

## 2026-05-30 - 脱离后前端不弹出换宠选择界面

- **现象**: 警惕特性触发脱离，战斗日志显示"遁地鼠 脱离"但前端没弹出替补选择 UI
- **根因**: 前端 store 从未读取 API 返回的 `pending_escape` 字段——后端设置状态并正确序列化，但前端 battle.js updateFromResponse 不包含该字段，数据断链
- **修复**: battle.js 新增 pendingEscape ref 并在 updateFromResponse 中读取；BattleArena.vue 新增 forced escape menu（无取消按钮，显示脱离提示）；App.vue 新增 handleResolveEscape 调用 POST /resolve-escape
- **涉及文件**: frontend/src/stores/battle.js, frontend/src/components/BattleArena.vue, frontend/src/App.vue
- **教训**: 后端新增 API 字段后必须同步检查前端 store 的 updateFromResponse 是否读取——grep 字段名前后端都要出现

<!-- 新条目追加在此行上方，格式如下：

## YYYY-MM-DD - 简短标题

- **现象**: 用户观察到什么异常
- **根因**: 实际原因是什么
- **修复**: 做了什么修改
- **涉及文件**: file:line, file:line
- **教训**: 一句话，下次遇到类似症状怎么快速定位

## 2026-06-02 - _random_teams 两队选到同一批精灵

- **现象**: AI 随机对战中双方的前 X 只精灵一模一样，不符合随机
- **根因**: train.py:91 — `used` 集合是 `build_team` 内部局部变量，AB 两队各自独立从同一个 shuffled names 列表头部取精灵，used 形同虚设
- **修复**: `used` 提升为闭包共享变量（train.py:88），A 队取过的精灵 B 队不再选
- **涉及文件**: backend/engine/ai/train.py:88-91
- **教训**: 两队阵容重叠 → 先查排重逻辑的作用域是否跨队伍共享

## 2026-06-02 - choose_replacement 返回 0 导致死宠被换上场

- **现象**: 引擎将已力竭的精灵换上场，对局状态损坏、MCTS 训练数据污染
- **根因**: MCTSAgent / NetworkPolicyAgent / _PlayerSwappedAgent 的 choose_replacement 在无存活替补时返回 0 而非 -1。_check_faint_interrupt 用 replacement < 0 判断"扣魔力"路径，0 被误认为有效替补索引
- **修复**: 3 处 choose_replacement 返回值 0→-1；引擎层新增替补 is_fainted 二次校验和 switch_index 死宠拦截
- **涉及文件**: backend/engine/ai/train.py:182, backend/engine/ai/mcts.py:173/384, backend/sim/battle_mechanics.py:131-142/33-35
- **教训**: 死宠异常上场 → 先查 choose_replacement 的兜底返回值是否用了 -1（引擎约定），再查引擎层是否做了二次校验

## 2026-06-02 - MCTS 仿真中 save_snapshot 和 deepcopy 占 46% 耗时 + 错误优化方向教训

- **现象**: 性能优化 commit 后模拟速度从 16 样本/s 下降到 6 样本/s，越优化越慢
- **根因**: ① save_mutable_state 对 marks/effects/VM 状态使用 copy.deepcopy（占 29%）；② MCTS 仿真中 execute_turn 每步触发 save_snapshot → battle_to_dict 全量序列化（占 17%）
- **错误路径**: clone_for_mcts 的 micro-benchmark 显示 0.30ms vs battle_from_dict 1.57ms 更快，但真实 self-play 中 object graph 随回合增长，deepcopy 遍历 1000+ 对象，开销被放大。micro-benchmark 不可信
- **修复**: ① save_mutable_state 用浅拷贝 + 只保存数值替代 deepcopy；② _mcts_sim 标志跳过 save_snapshot → battle_to_dict；③ 之后再改为 save/restore 原位仿真（零 clone）
- **涉及文件**: backend/sim/battle.py:53-140, backend/engine/ai/mcts.py:274-350
- **教训**: **profile 是唯一真相**——用 cProfile 跑实际 self-play 流程（不要信 micro-benchmark）；性能优化前先 profile 定位真实瓶颈；MCTS 仿真中 **永远不要 clone**，用 save/restore 原位操作

## 2026-06-03 - save/restore 未保存 battle.log 导致 MCTS 仿真回合泄漏到日志

- **现象**: --max-turns 150 但日志显示对战回合数 177/295/531/944/1003，远超限制
- **根因**: save_mutable_state 未保存 battle.log。MCTS 仿真中 _step_battle→execute_turn 每步追加 RoundRecord 到 battle.log，restore 后未截断。实际主对局正确停在 max_turns，仅日志数字被污染
- **修复**: save 记录 log_len，restore 时 del self.log[log_len:]
- **涉及文件**: backend/sim/battle.py:53-140
- **教训**: 日志计数异常偏大 → 查 save/restore 是否覆盖了该数据结构；原地仿真的副作用不限于"可变状态"

## 2026-06-03 - save/restore 用 id() 追踪 effect + StatBuffEffect.steps 遗漏导致 OverflowError

- **现象**: self-play worker 崩溃：OverflowError: int too large to convert to float at encode.py:520
- **根因**: ① effect 快照用 Python id() 做 key — 对象被 GC 后 id 可能被新对象复用，导致 TTL/stacks 错误赋值给不相关的 effect；② StatBuffEffect.steps 不在保存字段中，仿真中若被修改则 restore 不还原，异常值跨回合累积
- **修复**: effect 快照改为位置列表 [(e, ttl, stacks, steps)]；StatBuffEffect.steps 纳入快照并在 restore 还原
- **涉及文件**: backend/sim/battle.py:53-140
- **教训**: save/restore 中**绝不用 id()**追踪可变对象——GC 后的地址复用是 heisenbug；保存时确认每个 effect 子类的特有字段都在快照中

## 2026-06-03 - 评估最后一局卡死 — Windows multiprocessing.Queue 超时过短

- **现象**: 评估环节最后 1 局总是卡在 31/32 完成、7/8 workers 退出，不再有进展
- **根因**: BatchedInferenceServer._collect_batch 用 0.0001s 超时轮询。Windows multiprocessing.Queue 超时<1ms 时因底层管道实现可能漏收消息。只剩 1 个 worker 低频发送推理请求时，漏收导致 worker 在 reply_queue 永久阻塞
- **修复**: _collect_batch 最短超时 0.0001s→0.005s；eval worker 推理回复超时 300s→60s
- **涉及文件**: backend/engine/ai/evaluator.py, backend/engine/ai/selfplay_worker.py
- **教训**: 卡死先查多线程/多进程队列超时——Windows 上 Queue 不可设 <1ms 超时

## 2026-06-03 - MCTS 全局状态编码缓存错误 — 仿真中全局状态会变化

- **现象**: 提交了"MCTS 预编码全局 56 维复用"优化，但逻辑有误需要回退
- **根因**: 假设 MCTS 仿真期间天气/印记/道具/魔力不变。实际上 _step_battle 执行完整回合，技能可改变天气、消耗印记、使用道具。leaf eval 在 save/restore 之间，必须编码仿真路径修改后的状态
- **修复**: 回退 global_cache 参数和 MCTS 中预编码逻辑
- **涉及文件**: backend/engine/ai/encode.py:77-98, backend/engine/ai/mcts.py:299-306
- **教训**: MCTS 仿真≠只读——_step_battle 执行真实回合，所有状态都可能被修改；缓存前先确认数据是否真的 immutable

-->
