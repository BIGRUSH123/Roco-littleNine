# Rust 移植笔记（PORTING NOTES）

> 本文件是 Rust 移植的工作记忆：关键发现、约定、接口设计、移植顺序。
> 正确性铁律：以 Python 引擎为 oracle，`backend/engine/differential/` 的
> 固定种子对拍必须逐事件、逐状态字段一致。

## 已完成

- **阶段0**：金标准 fixtures（种子1-24，`backend/engine/differential/fixtures/`）+
  守卫测试 `test_differential_fixtures.py`；整局/MCTS 基线
  （`differential/baseline_20260917.md`）；三个行为保持 Python 小修
  （observer 缓存、快照 O(T)、trait 读盘缓存）——503 测试全过。
- **阶段1**：rustc 1.98.1-msvc + maturin 1.15 + `native/` workspace
  （roco-core 纯核心 + roco-py 绑定，扩展名 `roco_engine`）。
  构建：`cd native/roco-py; $env:CONDA_PREFIX='D:\projects\Roco-LittleNine\env';
  env\python.exe -m maturin develop --release`。
- **阶段2a**：`roco-core/src/rng.rs` — CPython random.Random 位级兼容
  （random/choice/sample/randbelow/shuffle/getrandbits）。26 项对拍全过
  （`backend/engine/test_native_parity.py`）。
- **阶段2b**：`roco-core/src/damage.rs` — calc_damage 移植，含 py_round
  （银行家舍入）。
- **阶段2c（进行中）**：`roco-core/src/journal.rs` — 31 种 Mutation
  （enum 建模，嵌套 IR 暂用 serde_json::Value）；
  `roco-core/src/vm_ctx.rs` — EventContext + Ctx 全字段 + swapped_view
  （注意：is_charging 与 energy_delta 不参与交换，hasattr 跳过语义）。
  两者 cargo check 通过。下一步：读 backend/vm/resolve.py + ir_values.py，
  写 `roco-core/src/resolve.rs`（ADDRESS_MAP → match 分发）。

## 关键发现（血泪教训）

1. **CPython init_by_array 的初始种子是 19650218，不是 MT19937 官方的
   19650210**（`Modules/_randommodule.c` init_by_array 第一行）。
   一位之差导致全部种子序列不一致。已修复。
2. Python `round()` 是银行家舍入（2.5→2），必须用 `py_round`。
3. 引擎伤害公式**无随机数**；RNG 只在 battle.py（2处 random()）、
   battle_mechanics.py（choice 选替补/借用）、replayer.py（choice/sample）。
4. 引擎内 dict 键用 `id(sprite)`（skill_history/counters 等）——对拍摘要
   必须换稳定键（玩家:精灵名）。
5. 终端命令必须走显式 PowerShell（AGENTS.md 规则）；注意外层 Bash 会展开
   `$env` 和 `$()`，需 `\$` 转义或避免。

## 架构约定

- `roco-core`：纯 Rust，不依赖 pyo3。将来可脱离 Python 跑自博弈。
- `roco-py`：薄 PyO3 绑定。带 `py_` 前缀的函数用于对拍测试。
- **对局初始化规格**：Python 侧做静态数据准备（StatsCalc/SpriteDB 属性
  计算、技能 JSON 收集），产出 BattleSpec JSON；Rust 从 spec 构建
  BattleState。属性计算不在热路径，不移植。
- BattleSpec 结构（草案）：weather, seed, players[{name, lives, item,
  sprites[{name, max_hp, stats{atk,def,sp_atk,sp_def,speed}, energy,
  nature, iv, bloodline, ability, ability_id, skills[SkillJson]}]}]。
- 引擎内部状态键：用精灵索引（0..11）或 (player, slot)，不用 id()。

## 移植顺序（依赖序）

1. ✅ rng / damage
2. ✅ data/skills.rs（SkillJson serde）
3. → **vm/ctx.rs**：Ctx 快照结构（snapshot.py build_ctx 的 Rust 版，字段
   以 Python Ctx 为准——先读 backend/vm/ctx.py）
4. → **vm/journal.rs**：31 种 Mutation（读 backend/vm/journal.py）
5. → **vm/cond.rs**：cond DSL 求值器（读 backend/vm/cond.py，对齐
   compile_cond 的语义而非 lambda）
6. → **vm/executor.rs**：op 分发 + WhenBlock 递归（29 个 ops 模块）
7. → **sim/sprite.rs**：效果/modifier/缓存
8. → **sim/battle.rs**：回合四阶段管线 + resolver + battle_mechanics
9. → **engine/replayer.rs**：Mutation → 状态变更 + 事件串
10. → **engine/observer.rs + trait_loader.rs**：观察者注册表
11. → **engine/battle.rs**：execute_skill 管线
12. 阶段3对拍门：≥200 场 0 分歧

- **阶段2c**：`journal.rs`（31 Mutation，嵌套 IR 用 serde_json::Value）+
  `vm_ctx.rs`（EventContext + Ctx 全字段 + swapped_view；is_charging 与
  energy_delta 不参与交换）。
- **阶段2d-1**：`resolve.rs`（ADDRESS_MAP 全表 + dict 查询 + default/
  per/scale/offset 变换，int×int 保持 int）+ `formula.rs`（=@ 公式串，
  自写 Python 表达式求值器：真除法/floor除/%符号/**右结合/int截断/
  round银行家）——160 项对拍全过（真实数据语料 + 合成）。
  `cond.rs`（COND_EVAL 全部分发 + compare_op + have 子分发 +
  trait_path 桥 + infer_triggers；eval_one 返回 Result<bool,()>，
  Err=Python 异常=跳过观察者；裸字符串未知条件=False、dict 未知=Err）
  ——259 项三态对拍全过。
- **对拍测试文件**：test_native_parity.py / test_native_resolve.py /
  test_native_cond.py（共 445 项原生对拍）。922 全量测试通过。
- Rust 值类型 `Val`（I/F/S/B/Set/Map/L/Null）+ Python truthiness/eq/cmp
  语义在 resolve.rs；公式求值入口 formula::resolve_formula_string(ctx,
  expr_without_eq)。

## 阶段2d-2 进行中：ops 语义已读

**sort.py**：相位表 cost0/power1/mult2/result3(默认)/counter4/turn_end5；
feeds 优先于 needs；相位升序 → priority 降序 → 原序稳定。priority 缺失
按 0。feeds/needs/priority 是所有 op 的公共字段。

**skill_parse.py 已读完**（Rust 直接解释 JSON 必须镜像的默认值）：
- when 块：{"when": cond, "then": [...], "else": [...], "elif"/"else_if":
  [{cond|when, then}]}, feeds/needs/priority 公共字段。
- 条件解析：and/or → conditions[]；not → condition；其他键全部进 params
  （Rust 端直接把整个 dict 传给 cond::eval_one 即可，无需转换）。
- value 解析：dict 含 "q" → 查询；其余字面量。hp_missing_ratio/mark_count_both
  派生查询已在 resolve.rs 直接实现（无需 pre_scale 技巧）。
- 各 op 默认值（缺省填充）：mod{target:sprite_self,stat:"",mode:set,
  scope:battlefield,steps:0(value 缺省 0),on_next:F,per_hit:F,...}；
  hit{type:物攻,combo:1,power 缺省 0}；mark{stacks:1,action:apply,
  ratio:1.0,target_team:""}；abnormal{stacks:1(可为 "=..." 串→value),
  scope:battlefield,heal_pct:0.0,energy_gain:0}；weather{turns:8}；
  dispel{what:""}；steal{amount:0,action:steal}；effect_delta{target:
  sprite_opp,what:negative,delta:1}；lock{turns:1}；exchange{what:""}；
  replay{from: from 或 from_}；gain_skills{count:1,exclude_carried:T,
  source:learnset}；count{name:"",scope:persistent,threshold:1,
  reset_on_fire:T}；stat_stage{scope:battlefield,per_hit:F,steps:0 或
  "=/dict"→value}；power_mod{mode:add,ttl:0,source: source 或 name}；
  mult_mod{mode:set,value 缺省 1.0,on_next:F}；flag_set{value 缺省 True}；
  heal{ratio 缺省 0.5（无 ratio 无 value 时）}；energize{delta 缺省 0}；
  revive{hp_ratio 缺省 1.0}；observer→CountOp{counter{name,threshold,
  reset}}；defer/schedule{turns:0,at:turn_start}；inherit{source:self,
  target:enemy_new,via_pending:F,inherit_stat_effects:F}；branch→WhenBlock；
  lives{target_team:own,delta:1}；team_counter_write{target:own,delta:1}；
  transform/trait_interaction。
- 非常规兜底：无 op 无 when 但有 "effects" → 报错(解析失败=跳过)；
  有 skill_type+power 无 op → 视为 hit。未知 op → 解析失败=跳过该效果
  （_parse_effect 抛错 → process… 等等：executor._compile_effect 返回
  None 仅当 legacy when；解析抛异常会向上传播？——看 process_one:
  op = _compile_effect(op) 未捕获异常！！但 SkillParsePass.process 有
  try/except。executor 的 JIT 路径没 try —— 需再核对
  （engine/battle.py 的 execute_skill 外层有没有兜底 except）。

**mod.py 已读完**（8 个处理器，Rust ops::mod 移植要点）：
- op_stat_stage：stat 可为 "=..." 串先 resolve；value 非 None → steps=
  int(resolve(value))；per_hit 且 combo>1 → 列表重复 combo 次。
- op_power_mod：value 优先，否则 delta，否则 0；float(value)。
- op_mult_mod：value 缺省 1.0。
- op_flag_set：value 缺省 True；mode 固定 "set"。
- op_heal：ratio 非 None → amount=max(1,round(ratio*hp_max))；value float
  ∈(0,1] → 同比例式；否则 int(raw)。amount<0 → Damage(element=element_self,
  type=skill_type_self)；==0 → 空。
- op_energize：int(delta)，0 → 空。
- op_revive → ModifierInjection(stat="revive", mode="set")。
- op_mod（legacy 大码）：steps 存在 → StatChange(steps=int(raw))；stat=hp →
  Heal/Damage（float∈(0,1] 按 max 比例，负值 Damage）；stat=energy →
  EnergyChange；stat=devotion → ModifierInjection+then；其余 stat →
  ModifierInjection（含 ttl）。meta 字段（element/per_element/skill_filter/
  skill_where/on_next/if_type/source）仅在 dict 显式存在时带上。
  negative 标志 → 取反。per_hit × combo。

**29 个 op 处理器已全部读完**。核心规则：dict 一律先过 SkillParsePass
（executor._compile_effect），所以 **Rust 以 parse 默认值为准**（op_xxx
内部 dict 分支的默认值是死代码）。parse 默认（必须镜像）：
- tick/interrupt/lock/escape/mark/charge/return/redirect/transform/
  trait_interaction/stat_stage/power_mod/mult_mod/flag_set/heal/energize/
  revive/burst_grant/gain_skills → target 默认 **sprite_self**
- effect_delta → sprite_opp/what=negative/delta=1；reset → target=
  **skill_off_0**/stat=""；exchange → 无 target 字段 → 运行时恒 sprite_opp；
  weather → turns=**8**；abnormal → target=sprite_self/stacks=1（stacks
  为 "=/dict" 时进 value、stacks=0）；hit → type=物攻/combo=1/power 缺 0；
  steal → amount=**int 0**/action=steal/name=None；dispel → name/limit/
  type_limit=None（无 source 字段→None）；replay → from=(from or from_
  or "")；count → cond=cond或when/then/scope=persistent/threshold=1/
  reset_on_fire=T/listen(str→[s], list→list)；mark → stacks=1/value=
  Option/action=apply/ratio=1.0/target_team=""/source=Option/then；
  defer→schedule turns=0/at=turn_start；inherit → source=self/
  target=enemy_new/effects=[]/scope=battlefield/via_pending=F/
  inherit_stat_effects=F；observer→CountOp(counter{name,threshold,reset})；
  branch→WhenBlock（cond 必需）；lives→target_team=own/delta=1；
  team_counter_write→target=own/key=""/delta=1；trait_interaction→
  action=""/copy_from=None/new_ability=None。

**op_xxx 运行时语义要点**（其余见各源文件，均直译）：
- hit：物攻用 atk/def，否则 sp_atk/sp_def；stage=steps*0.1；calc_damage(
  power, atk, def, atk_stage, def_stage, damage_reduction=ctx.
  damage_reduction_opp, power_mult=power_mult_self, damage_mult=
  damage_mult_self, mark_bonus=mark_bonus_own, combo_count=combo_self)；
  element 缺省 ctx.element_self；固定 target=sprite_opp。
- mark：delta=stacks 或 resolve(value 缺 1)；team 归一（team_own/own_team/
  sprite_self→own 否则 opp；显式 target_team 覆盖：own/own_team/team_own→
  own 否则 opp）；per_hit×combo。
- abnormal：delta=int(stacks 或 resolve(value 缺 1))；per_hit×combo。
- op_mod legacy：见笔记前文 mod.py 段。
- schedule（defer）：turns 缺省 int(delay_turns 缺 1)；at=at or phase or
  "start"，turn_end→end/turn_start→start；then=then or effects；
  **freeze**：递归把 delta/value/ratio/power/hp_ratio/stacks 键的
  查询/公式当场 resolve 成字面量（then/effects/else_ 递归）。
- per_hit 通用语义：per_hit 且 combo=max(1,combo_self)>1 → 变异列表
  重复 combo 次（stat_stage/power_mod/mult_mod/mark/abnormal/mod）。
- stat_stage：stat 可 "=..."；value→steps=int(resolve)；per_hit×combo。
- 未知 op / 解析失败：executor._compile_effect 抛异常会向上传播
  （process_one 无 try）→ SkillParsePass.process 才有 try。**待核对
  engine/battle.py execute_skill 是否兜底**（影响 Rust Err 传播边界）。

## 下一步动作（继续 2d-2）

1. 读 backend/vm/ops/hit.py（伤害管线核心）、mark.py、abnormal.py、
   tick.py、count.py、weather.py、dispel.py、steal.py、double.py、
   effect_delta.py、charge.py、escape.py、return_.py、lock.py、
   interrupt.py、exchange.py、reset.py、redirect.py、replay.py、
   borrow.py、burst_grant.py、gain_skills.py、inherit_effects.py、
   schedule.py、team_counter_write.py、lives_change.py、transform.py、
   trait_interaction.py（一次性批量读）。
2. Rust：roco-core/src/ops.rs（每 op 一个函数，签名
   fn op_xxx(ctx: &Ctx, effect: &J) -> Result<Vec<Mutation>, ()>）+
   executor.rs（parse_effect 默认值填充可直接内联在各 handler 里——
   逐字段 get_or_default，无需独立 parse 层；when 块递归；排序函数
   sort_effects）。
3. journal 对拍：新增 py 绑定 py_execute(ctx_json, effects_json) →
   Mutation JSON 列表；Python 侧写 journal_to_json（dataclasses.asdict
   遍历 + 类型名作 tag）比对。注意 Python Mutation 的 then/skill_where
   字段可能是 dict——JSON 序列化直接对齐。
4. 全程 pytest 回归。

**executor.py 已读完**，关键语义：
- 入口 execute(ctx, effects, sort=True)：先 sort_effects（vm/sort.py，读之）
  再 process_effects 顺序执行，累计 Mutation 序列。
- 38 个 typed op（ir_skill.py）：ModOp(legacy)/StatStageOp/PowerModOp/
  MultModOp/FlagSetOp/HealOp/EnergizeOp/ReviveOp/HitOp/MarkOp/AbnormalOp/
  WeatherOp/DispelOp/StealOp/TickOp/DoubleOp/EffectDeltaOp/ChargeOp/
  EscapeOp/ReturnOp/LockOp/InterruptOp/ExchangeOp/ResetOp/RedirectOp/
  ReplayOp/BorrowOp/CountOp/TeamCounterWrite/LivesChange/Schedule/
  InheritEffects/Transform/TraitInteraction/GainSkills/BurstGrantOp/
  WhenBlock。
- WhenBlock 递归：cond 真 → then；假 → 逐个 elif_；全假 → else_。
- dict 现编译规则（_compile_effect）：effect 含 "when" 且无 "op" 时，
  若 when 是 dict 且无 "cond" 键 → 跳过（legacy 死触发）；否则走
  SkillParsePass._parse_effect。
- ModOp.on_next=True → _defer_mod 返回空（引擎侧行为，Rust 同样空）。
- 未知 op 类型 → 空 journal（case _ 返回 []）——Rust 解释器遇到未知
  "op" 值同样返回空。
- Rust 策略：**直接解释原始 JSON dict**（不建 typed op 层），但必须
  移植 SkillParsePass 的默认值填充 + sort.py 的相位排序。

剩余阅读清单（按序）：
1. backend/vm/sort.py（相位排序 + _SORTED_EFFECTS_CACHE 语义=按 tuple
   身份缓存，Rust 可按内容排序不做缓存——排序须确定性）
2. backend/vm/compiler/passes/skill_parse.py（每 op 的字段抽取+默认值）
3. backend/vm/ir_skill.py（typed op 字段全集——即每个 op 的合法字段表）
4. backend/vm/ops/*.py 逐个（op_xxx(ctx, op) → [Mutation]）
5. backend/vm/ir_values.py + resolve 的 Query pre_index（跳过——Rust
   只走原始 JSON 路径）

- **阶段2d-2 完成**：`vm_exec.rs`（排序 + 38 op 全部处理器 + WhenBlock
  递归 + schedule freeze）——534 项真实技能/特性语料 journal 对拍全过
  （test_native_exec.py）。全量 1456 测试通过。
- **对拍揭示的 Python 怪癖（已镜像，勿"修复"）**：
  1. power_mod/mult_mod parse 无 per_element（power_mod 也无 if_type）→
     ModifierInjection 恒 None；flag_set parse 不传 ttl → 恒 0；
     flag_set value 保留 bool/int/float（不 float 化）。
  2. mod：parse 无 source 字段 → meta 的 source 恒 None；_metadata 循环
     不含 on_next → 注入恒 False；per_element 经 parse 恒 int(0)；
     devotion 分支不透传 meta（只 name/then/ttl）。
  3. stat_stage 只认 "steps" 字段——"delta" 被忽略（美拉德反应）；
     steps 缺失 → 0。
  4. abnormal：stacks 为查询/公式 → parse 置 stacks=0 且 typed 处理器
     不 resolve value → delta 恒 0（溶解扩散）。
  5. 缺 op 但有 skill_type+power → hit 兜底（指指点点）；when 是 dict
     但无 cond → 死触发跳过。
  6. tick/interrupt/lock/weather 等 dict 分支默认值是死代码——一律以
     parse 默认为准。

## 下一步（阶段2e：sim 核心）

核心难点：可变状态层。设计：
- `roco-core/src/battle_state.rs`：BattleState（players/sprites/globals/
  marks/pending/scheduled/vm 状态）+ BattleSpec 输入格式（见上文架构
  约定）+ save/restore（Rust 全量结构体克隆，天然廉价）。
- 状态变更全部经 journal::Mutation 回放（replayer 移植）——这是
  replayer.py:376 的 31 种 Mutation 处理 + 事件字符串生成。
- 回合管线：_execute_turn_core 四阶段（sim/battle.py:800）。
- 对拍策略：先用「Python 真实对局 → 每回合的 Ctx 快照 + journal 序列
  化导出 → Rust 重放比对」逐层验证，再进整局对拍门。
- 依赖阅读清单：sim/sprite.py（效果/缓存）、sim/resolver.py（伤害
  管线+应对）、sim/battle_mechanics.py（换宠/道具）、sim/pipeline.py
  （turn_start）、engine/replayer.py（1702 行，最大单文件）、
  engine/battle.py（862 行桥）。

## 阶段2e 进行中

- **已完成**：`battle_state.rs`（Species/BattleSkill[skill_id 引用语义]/
  Effect{name,source,scope,ttl,cooldown}+EffectKind{Observer,Modifier,
  Abnormal,StatBuff,State}/Sprite 全字段+方法（effective_stat/take_damage/
  heal/max_energy/gain_energy/lose_energy/frozen_hp/check_freeze_death/
  get_stacks/add_effect 合并语义/update_stacks/clear_effects/dispel/
  double/decrement_ttl/process_pending_effects/consume_pending_modifiers）/
  PlayerState/Item/GlobalState{weather+marks}/PendingEffect/ScheduledEffect/
  BattleState[players/globals/turn/winner/team_counters/pending_effects/
  scheduled_effects/borrowed_restore/wish_restore/burst_effects/
  burst_names/counter_values/skill_history(BTreeMap<u64,..>)/skill_tags]/
  BattleSpec 输入格式）。`statics.rs`（include_str 内嵌
  native/static_tables.json——mark/abnormal 模板+克制表，由
  native/tools/dump_static_tables.py 从 Python 导出）。
  编译通过。cargo check 干净。
- 设计要点：Python 的效果/属性缓存不移植（按需重算，行为等价）；
  save/restore=BattleState.clone()；技能引用用 skill_id（跟随对象，
  换位安全）；charged_skill_ref=Option<u64>。

## replayer.py 阅读进度与要点

- 已读 1-1254 行。剩余 1254-1702（double abnormal 尾/effect_delta/
  charge/escape/return/lock/interrupt/exchange/reset/redirect/
  gain_skills/inherit/transform/trait_interaction/team_counter/lives/
  schedule + _target_sprite/_trait_sourcing）。
- **headless 分支**：replay() 直接 dispatch 全部 handler、返回 []——
  显示串逻辑被各 handler 内部 `if self.is_headless: return ""` 跳过，
  但状态变更照常。Rust 头版只需 headless 语义（无事件串）。
- 核心状态语义（Rust replayer 必须镜像）：
  - _apply_stat_change：免疫门（steps<0 且 stage stat → immune_stat_down
    检查）→ _sync_stat_buff_effect(stat_key,steps,scope,source 缺省
    "skill",is_inherent=trait_sourcing)。StatBuff 按 (stat_key,scope)
    合并或 set。
  - _apply_modifier：devotion→player.devotion（random 用 RNG！）；
    on_next→pending_modifiers+_mod_scopes；skill_filter=all→全技能分发；
    skill_where→匹配技能+注册 _trait_direct_effects；skill_at_N→位置
    清理(_cleared_position_stats 每批)+定位技能；skill_ 缺省→当前技能；
    add 模式 cur 缺省：damage_reduction/life_drain→0.0、其余 ratio→1.0、
    其他→0.0；multiply cur None→value。sprite 级属性（max_energy/
    immune_*/starfall_consume_ratio∈TraitLoader._SPRITE_LEVEL_ATTRS）→
    同步 ModifierEffect 到 active_effects。_mod_scopes 记录非技能级
    turn/battlefield/persistent。
  - damage：take_damage+吸血（life_drain 来自 self._modifiers 与
    _self_skill 的 max，round(actual*pct) heal）。
  - mark：apply（classify_mark+coexist=mark_coexist 修饰符+同 category
    替换）/dispel/steal/convert（random.choice 用 RNG）。
  - abnormal：萌化走 species_lookup 变形；免疫门只挡 delta>0；
    _sync_abnormal_effect（模板建效果，stacks<=0 移除）。
  - dispel：positive/negative/abnormal(source 分支/mark 分支 random)/
   萌化解除走 remove_moe。
  - steal：positive 复制/转移；energy（含 team_opp 全队）；mark。
  - tick：模板 tick 参数（dmg_pct 缺 0.03）×stacks×克制 mult。
  - RNG 调用点（replayer 内）：devotion random、mark steal/dispel
    random.choice——Rust 用 rng.rs。

## 下一步动作

1. 读 replayer.py 1254-1702 收尾（含 _target_sprite/_trait_sourcing/
   _SPRITE_LEVEL_ATTRS）。
2. 读 engine/modifiers.py（eval_skill_where）+ engine/mark_config.py
   的 POSITIVE/NEGATIVE 名单（classify_mark 需要——dump 进
   static_tables.json）+ devotion_config.py DEVOTION_TYPES（同上 dump）。
3. Rust replayer.rs：Rust 版 JournalReplayer（headless 语义先行），
   方法挂在 BattleState 上（self/opp/team 上下文 + &mut 两个精灵的
   split 处理）。
4. 阶段2e-2 行为对拍：Sprite 方法级 fuzz 对拍（effective_stat 等）。

## 阶段2e 完成 + 2f-1 完成

- **battle_state.rs**：全状态层（见前文）编译通过。关键设计：技能引用用
  skill_id；Python 缓存不移植（按需重算）；save/restore=clone()。
- **statics.rs**：include_str 内嵌 native/static_tables.json（mark/异常
  模板+克制表+positive/negative 印记名单+奉献类型，dump 脚本
  native/tools/dump_static_tables.py）。classify_mark/element_mult/
  mark_template/abnormal_template/devotion_types。
- **replayer.rs**：31 Mutation headless 回放全部实现（含 devotion random、
  mark steal/dispel 的 RNG、免疫门、skill_at_N 位置清理、sprite 级
  ModifierEffect 同步、exchange HP/effects/skills、burst_grant、
  trait_interaction、schedule freeze 结果登记）。InheritEffects/
  Transform/GainSkills/Redirect/Replay/Borrow 在 replayer 层为空——
  分别由换宠管线/物种库/引擎 journal 变换处理（延后项已标注）。
- replayer 语义要点（对拍锚点）：add 模式 cur 缺省（damage_reduction/
  life_drain=0.0、ratio=1.0、其他=0.0）；multiply cur None→value；
  skill_at_N 每批首次清同 stat 旧值（cleared_position_stats）；
  sprite 级属性（max_energy/starfall_consume_ratio/immune_*)同步
  ModifierEffect；mark_coexist 修饰符控制印记共存。

## 下一步（阶段2f-1：snapshot.rs = build_ctx）

1. 读 backend/engine/snapshot.py（build_ctx：双方精灵效果提取→
   stat_stages/abnormal_stacks/skill_elements/技能摘要/印记聚合/
   counter_values → 100+ 字段 Ctx）。已有 Cython 版 snapshot_cy.pyx
   语义相同。
2. Rust：`roco-core/src/snapshot.rs`——`fn build_ctx(state: &BattleState,
   team, self_skill_idx) -> Ctx`。字段全部来自已移植的状态层。
3. 对拍：Python 侧用 SimFactory 建真实对局 → 每回合 build_ctx 导出 →
   Rust 对同一状态快照构建 Ctx → 逐字段比较（复用差异测试基建）。
4. 然后：observer 注册表（trigger 分桶）+ execute_skill 桥 +
   回合管线 → 阶段3 整局对拍门。

## 阶段2f-1 完成：snapshot.rs

- `snapshot.rs` 已写完并编译通过：`build_ctx(state: &BattleState, opts:
  &BuildCtxOpts) -> Ctx`。BuildCtxOpts 含 team/self_idx/opp_idx/
  self_skill_idx/opp_skill_idx/turn/is_first/damage_taken_this_turn/
  skill_index/prev_*/elements_used_count_self/energy_cost_sum_self/
  skill_count_own 等。
- 队伍聚合（fainted/team_elements/devotion/lives/moe_team_stacks/
  abnormal_stacks_battle）直接从 state 计算（Python 由调用方传入，等价）。
- 关键不对称已镜像：speed_self 带 modifiers 乘子、speed_opp 不带；
  damage_reduction min(1.0, mod+skill_mod)；power/damage mult 是
  1.0+(mod-1)+(skill_mod-1) 加性组合；combo = combo_set>0 ?
  max(1,set) : max(1, base+mod)。
- Sprite 新增 stat_with_modifiers（round(base*(1+mod))，base 缺省 100）
  与 modifier(key, default)。

## 下一步（阶段2f-2/2f-3 → 阶段3 门）

1. **读 engine/battle.py 全文**（862 行 execute_skill 管线：pre_calc
   观察者 → vm.execute → journal 的 Replay/Borrow/Redirect 变换 →
   collect_modifiers+adjust_damage（modifiers.py 已读）→ replayer.
   replay → post 事件观察者触发 + swapped_view）。同时读
   observer.py 的 fire/registry 部分（已读大半）。
2. **读 sim/battle.py 剩余**（_phase_turn_start/_select_action/
   _phase_resolve/_phase_turn_end/_execute_skill_vm/换宠打断道具；
   run()/execute_turn 已读）+ sim/pipeline.py + sim/resolver.py
   （_TYPE_CHART 已导出；calc_damage 已移植）+ sim/battle_mechanics.py。
3. Rust：
   - `engine/observers.rs`：Observer 注册表（trigger 分桶 + owner 索引
     + fire；observer.py:165-360 已读大半）。
   - `engine/engine.rs`：execute_skill 管线（journal 变换：_handle_replay
     读取 skill_history、_handle_borrow、_handle_redirect + modifiers.py
     的 collect/adjust 已可移植）。
   - `engine/turn.rs`：四阶段管线 + resolver（优先级/速度/应对/胜负）。
4. 阶段3 门：headless 整局对拍。驱动方式：Python 导出 BattleSpec +
   RuleAgent 决策序列？——**决策依赖引擎状态，必须双跑对拍**：每回合
   Python/Rust 各跑同一种子对局、比较逐回合状态摘要（复用
   differential/recorder.state_digest 的字段设计，Rust 端实现同样的
   digest）。RuleAgent 是确定性的（依赖状态一致→决策一致）。
5. RNG 注意：RuleAgent 无随机；引擎随机点（battle.py 2 处 random()、
   battle_mechanics choice、replayer choice）在 Rust 用 rng.rs 的
   PyRandom——种子来自 BattleSpec.seed，Python 侧对拍脚本需
   random.seed(seed) 后构建（阵容生成）再驱动对局（Rust 只接管对局内
   随机，阵容由 Python 算好放进 spec——对局内第一个 random 调用前
   Python 的 random 流必须与 Rust 的 PyRandom(seed) 对齐：需要把
   Python 的 random 状态直接换成 Rust 同款种子流——做法：对局内
   Python 引擎用 random.Random(spec.seed) 实例？不行——引擎用全局
   random。**方案**：recorder/gate 驱动器在 random.seed(spec.seed) 后
   生成阵容，再 random.seed(spec.seed+1) 供对局内使用；Rust 侧
   PyRandom::seed_i64(spec.seed+1)。种子协议写入对拍驱动器。

## 阶段2f-2 阅读中：execute_skill 管线（engine/battle.py 已读 1-430）

**管线精确顺序**（Rust engine.rs 必须逐步镜像）：
0. counter_values：headless 用**活引用**、非 headless 用 copy（微妙差异）。
1. build_ctx（带 burst_triggered_count_own、battle_skill、kwargs）。
2. pre_calc：_fire_pre_calc(攻方 id) + _fire_pre_calc(守方 id)——只收集
   mutation 不立即回放；有产出 → 经临时 replayer 回放 + **重建 ctx**。
3. pre_mods = _fire_pre_event("pre_modifier", ctx, 攻方 id)。
4. journal = vm_execute(ctx, vm_effects)——**execute 会排序**（sort=True）；
   vm_effects = 显式 effects 或 self_skill.effects。
5. journal = pre_mods + journal。
6. pre_defend mods（守方 id）→ 前插。
7. is_first 且有 effects → burst 登记（_burst_effects/_burst_names）。
8. _handle_replay（查 _skill_history）。
9. _handle_borrow。
10. _handle_redirect。
11. apply_modifiers_to_journal(journal, ctx)（modifiers.py 已读：两遍
    set 基线→add/multiply；power_add→等效 power_mult；combo_set/add/
    base/mult；damage_reduction delta；round 全程 py_round）。
12. replayer.replay(journal)。
13. _register_counters_from_journal。
14. skill_history.append((skill_name, list(vm_effects), {"tag"})).
15. 星陨印记触发（非幻攻击 → trigger_starfall；公式 X²+24X-24，
    攻防类型随技能，消耗 watch 守望星减半——globals.py 已读）。
16. ctx.just_acted_self=True → _fire_post_event("post_skill")。
17. _fire_mutation_events（journal 扫描 → post_damage/post_ko/
    post_energy_change/post_abnormal_change/post_abnormal_apply/
    post_positive_change）。

**观察者触发关键语义**（_fire_pre_event/_fire_post_event）：
- pre 事件：owner 过滤（owner==sprite_id 或 ownerless），eval_cond 真 →
  process_effects(obs.then) **只收集不回放**；except → continue。
- post 事件：SINGLE_OWNER_TRIGGERS（post_entry/leave/skill/turn_end/
  post_abnormal_tick/turn_start/post_energy_change/post_counter/
  post_enemy_leave/post_charge/post_heal）用 owner 子索引；post_ko 双方
  owner 均可；post_damage/post_ko 且 owner==防守方 → **replayer 与 ctx
  都 swap 视角**（damage_taken_of 翻转），finally 恢复；pending_escape
  置位 → break；except → continue。
- 尚未读：430-862（_fire_mutation_events 尾部/_handle_replay/
  _handle_borrow/_handle_redirect/_register_counters_from_journal/
  _get_effects/_swap_fire 辅助）。

## 下一步动作

1. 读 engine/battle.py 430-862（journal 变换三件套 + counters 注册）。
2. 读 sim/battle.py 800-1900（四阶段 + _execute_skill_vm + 换宠）+
   sim/pipeline.py + sim/resolver.py + sim/battle_mechanics.py。
3. Rust 移植：engine/observers.rs（注册表+fire）→ engine/engine.rs
   （execute_skill）→ engine/turn.rs（四阶段+resolver）。
4. 阶段3 门：双跑对拍驱动器（种子协议：random.seed(spec.seed) 生成
   阵容 → random.seed(spec.seed+1) 供对局内；Rust PyRandom(seed+1)），
   逐回合状态摘要比对（字段复用 differential/recorder.py 的
   state_digest 设计），≥200 场 0 分歧。

## engine/battle.py 已全部读完（430-862）

**_fire_mutation_events**：扫 journal 每条 → 触发器去重（fired set）：
- Damage → post_damage（ctx.damage_taken_this_turn=amount；
  event.damage_taken_of 按 target）。
- EnergyChange → post_energy_change（energy_delta_self 来自 replayer
  _energy_deltas[id(m)]——实际能耗经 max_energy 截断后的值）。
- Heal → post_heal（heal_delta_self/opp）。
- AbnormalChange → post_abnormal_change；delta>0 再发 post_abnormal_apply
  （各自去重）。
- StatChange（is_positive 字段——注意 replayer 产的 StatChange 没有该
  字段设置，恒 False → 不触发）。
- ModifierInjection._is_positive_modifier：add 且（energy_cost<0 或
  value>0）；multiply value>1 → post_positive_change。
- 尾声：二次扫描 Damage → 任一 target is_fainted → post_ko（去重，
  ctx.event.target_fainted=True）。

**_handle_borrow**：取 ctx.power_opp/skill_type_opp/element_opp；
攻击类型且 power>0 → calc_damage（物攻 atk/def 否则 sp_atk/sp_def，
stage×0.1，damage_reduction=ctx.damage_reduction_opp，combo=ctx.combo）
追加 Damage(sprite_opp)；删除 Borrow。
**_handle_redirect**：取第一个 Redirect.target → 所有 Damage target≠
target 改写为 target；Redirect 消费。
**_handle_replay**：team_burst → _burst_effects[team] 全部
vm_execute(ctx, effects) 前插；sprite_self → skill_history 过滤
（tag/skill_type/element）后逐条 vm_execute 前插；Replay 删除。
**register_counter**：cond+then+owner 去重；Observer(listen=infer_triggers,
source="counter")；命名 counter 初始化 0。
**fire_trigger**（sim/battle.py 的外部钩子）：_fire_post_event 直通。

## 阶段2f-3 阅读完成：回合管线移植规格（turn.rs 蓝图）

**全部源码已读完**：battle.py 1809 行 / pipeline.py / resolver.py /
battle_mechanics.py / traits（轻量：DataDrivenTrait.on_turn_start=触发
turn_start 观察者；hook 系统默认空注册）。

### execute_turn 核心序列（每回合）
```
turn += 1; if !headless { save_snapshot }
_ctx_team_cache.clear()
每回合清理：双方全部精灵 interrupted=false；精灵 _modifiers/_skills
  弹出 _PER_TURN_KEYS/_SKILL_PER_TURN_KEYS；reapply_all_direct_mods
  （trait 直改重放，含 mark_energy_mod）；_load_permanent_skill_mods
_phase_turn_start:
  1. 双方 active process_pending_effects
  2. TurnPipeline.execute_turn_start:
     a. _execute_scheduled_effects('start')
     b. 双方 active dispatch_turn_start → turn_start 观察者（经
        _make_ctx(sprite,opp,None,None) fire_trigger）
     c. 双方 _apply_transmission（传动：skill 虚拟数组分块轮转，
        机械变式(ability_id 20159/机械变式) 移动槽 mech_energy_reduction-1，
        变化后 reapply_position_modifiers）
     d. headless 跳过位置预扫描
     e. 不朽：力竭≥3回合 → 复活（hp=max、energy+3≤5）
_phase_select_action: agent 循环（≤8）: choose_action；item → _resolve_item
_phase_resolve:
  - 双 switch → random() < 0.5 定序，各 _resolve_switch
  - 单 switch → 先换（mark_switch_damage/energy_loss、clear battlefield、
    entry_turn/first_action/times_entered、dispatch_entry[trait load+
    post_energy_change 初始+post_entry hook]、post_leave 观察者、
    pending_effects 应用、post_entry、入场传动、对方 post_enemy_leave
    (leaving=old)、dispatch_leave(old)）→ 对方行动 is_first=True
    （迅捷 swift 技能自动先出：_modifiers swift 存在的可用技能）
  - 双技能 → 应对判定 resolve_counter(counter 字段: 攻击↔is_attack/
    防御↔is_defense/状态↔is_status；冷却中不算) → countered: A 先执行
    （is_countered=对方 counter_b），力竭检查双方，存活才后手；双方
    post_counter+team_counter(counter_success)
  - 无应对 → priority(基础+priority_mod，gather=0) → 速度(effective
    -mark_speed_penalty) → 相等 random()<0.5 → 依次执行（先手
    is_first=True；后手若未死未换 is_first=False）
_phase_turn_end:
  1. scheduled('end')
  2. 全员 clear_effects('turn') + decrement_ttl
  3. borrowed_restore 归还；wish_restore 还原
  4. pending_return → extra_skill_use=True + _resolve_return
  5. resolver.turn_end（tick：dmg=max(1,round(hp*pct*stacks))×克制，
     decay(灼烧减半/煤渣草增长)；冷却-1；snow 暴风雪+2冻结；天气递减；
     印记 turn_end energy/damage）
  6. extra_turn_end 修饰符 → turn_end 再跑一遍
  7. post_abnormal_tick 观察者（双方视角，灼烧/中毒）
  8. turn_end 观察者（逐存活方）+ urgent escape 即时结算
  9. 能量0 替补检查（hook，默认无）→ 强制换宠（battle.py 1863+）
  10. 冻结斩杀（frozen_hp）→ _check_faint_interrupt
```

### _execute_skill_vm 内部门（顺序严格）
1. user 死 → 返回；charging 禁 gather；gather：+5E(≤max)、first_action
   清、counter(times_gathered)、对方 counter(enemy_gather)、
   post_energy_change
2. 取 bs；冷却门；载入 CompiledSkill（Rust: 技能 JSON 已含效果）
3. 蓄力门 _gate_charge_vm：charging+同技能 → 释放（charging→charged
   State）；charging+其他（usable_while_charging 或 charge_any_skill）→
   取消放行；否则阻止；has_charge(op=charge 存在)+pre_charged>0 → 跳过
   蓄力；否则进入蓄力（charging State persistent）
4. on_next pending modifiers 消耗（能量门前）
5. 奉献消耗：use_devotion 且 player.devotion → 按 DEVOTION_TYPES 加
   combo/energy_cost/power/life_drain modifiers + AbnormalChange；
   abnormal_mods 立即 replay；保存并随后恢复 4 个 modifier 键；
   player.devotion.clear()
6. burst：first_action 且 bs._burst_effects 非空 → execute_effects+replay
7. 能量支付 skill_energy_cost（weather_energy_mod×energy_cost_mod 后的
   cost；不足→blood_price HP 替代或失败）；成功后 pop _modifiers.
   energy_cost；post_energy_change
8. interrupted → nullified 返回
9. counters：skill_used:{name}、skills_used
10. 应对方直改效果注入（counter_record 非	when 效果以对方视角执行回放）
11. execute_skill（17 步管线，见上节）
12. 恢复奉献 modifiers；Escape 处理（urgent 立即/非 urgent 挂起）；
    first_action=False（_burst_extended>0 则保持并-1）；charged State
    移除；防御技能 cooldown=2；counters element:/defense_skill/status_skill

### 计划中的 Rust 模块
- `engine/observers.rs`：Observer{cond(J),then(Vec<J>),scope,name,source,
  listen:Vec<String>,threshold,reset_on_fire,owner:(team,idx)或全局,
  owner_skill_id,hit_count} + Registry{observers, by_trigger} + fire。
- `engine/engine.rs`：VmEngine{registry,burst_effects,burst_names,
  counter_values,skill_history,skill_tags,statics} + execute_skill(17步)
  + fire_trigger/fire_post/fire_pre/fire_mutation_events + 三变换。
- `engine/turn.rs`：execute_turn(state, agents, rng) 四阶段 + 换宠机制
  （mechanics 已读）+ transmission。
- 事件串：headless 门内不需要；RoundRecord 等价物延后到阶段5。

## 阶段2f-2 完成：observers.rs + engine.rs 已编译

- `observers.rs`：Observer{cond,then,scope,name,source,listen,threshold,
  reset_on_fire,owner:Option<(&'static str,usize)>,owner_skill_id,hit_count}
  + ObserverRegistry{register（source bake）,candidates,unregister_by_owner}。
- `engine.rs`：VmEngine{registry,data_dir} +
  execute_skill（17 步：build_ctx→pre_calc 双方→重建 ctx→pre_modifier→
  VM 排序执行→前插→pre_defend→burst 登记→replay/borrow/redirect 三变换
  →adjust_damage（两遍 set/add/multiply 语义）→replay→counter 注册→
  skill_history→星陨→post_skill→mutation 事件）+ fire_pre/post_event
  （post_damage/post_ko 视角交换、SINGLE_OWNER 过滤、pending_escape
  break）+ handle_replay/borrow/redirect + register_counters +
  adjust_damage_in_journal。
- battle_state.rs 的 burst_effects 改为 team→[(skill_name, effects)]、
  burst_names 为 BTreeSet（计数=distinct names，与 Python 一致）。
- ModifierInjection.value 为 Val（flag_set 保留 bool 语义），
  adjust/事件判定用 as_num()。

## 下一步（完成阶段3 门）

1. **turn.rs**：execute_turn 四阶段（蓝图见「回合管线移植规格」节）：
   _execute_turn_core → _phase_turn_start（scheduled('start')/双方
   turn_start 观察者/传动/不朽）→ _select_action（agent 接口注入，先
   RuleAgent 移植）→ _phase_resolve（双 switch 随机序/单 switch+入场
   序列/双技能应对/优先级→速度→random tie；迅捷）→ _phase_turn_end
   （scheduled('end')/turn scope 清理+TTL/借用与愿力还原/返场/
   resolver.turn_end（tick+冷却+暴风雪+天气+印记 turn_end）/
   extra_turn_end/post_abnormal_tick/turn_end 观察者/能量0换宠/
   冻结斩杀/力竭换宠）。
   - trait 加载：load_traits_for_sprite（data/traits/{ability}.json →
     observer ops 注册 + 直改 mods 应用）；dispatch_entry/leave。
   - transmission（传动）：虚拟数组分块轮转（battle_mechanics 546-663）。
   - 换宠序列 _resolve_switch（battle_mechanics 65-145）：印记入场伤/
     扣能 → 清效果 → dispatch_entry → post_leave → pending → post_entry
     → 入场传动 → 对方 post_enemy_leave → dispatch_leave(old)。
   - 力竭 _check_faint_interrupt（agent.choose_replacement → post_ko →
     扣魔力 → 胜负）。
2. **spec 构建器**：Python 侧把 SimFactory 对局导出为 BattleSpec JSON
   （species/initial_stats/skills JSON/ivs/nature/items/lives + seed）。
3. **双跑对拍驱动器**：tests/differential/test_rust_gate.py——
   Python Battle.run 与 Rust execute_battle（pyo3 入口）同种子对跑，
   逐回合 state digest 对比（Rust 端实现与 recorder.state_digest 同构
   的 digest）。≥200 场 0 分歧即过门。
4. 阶段4+：MCTS 接入（PyRandom 流对齐）、API 开关、收尾。

## 阶段2f-3 完成：turn.rs + rule_agent.rs 已编译

- `turn.rs`：execute_turn 四阶段完整实现：
  - phase_turn_start（延迟效果/turn_start 观察者/传动/不朽）
  - select_action（道具循环 ≤8）
  - phase_resolve（双 switch 随机序/single+after_switch（迅捷）/双技能
    应对判定 → countered 先后手/优先级→速度→random 平局）
  - execute_skill_vm（全部内部门：蓄力 gate_charge 三态/on_next/奉献
    消耗与恢复/迸发/能量支付（轴承支撑+天气+印记）/打断/应对方直改/
    17 步 execute_skill/奉献恢复/escape/first_action 清理/_burst_extended
    /charged 移除/防御冷却/counters）
  - resolve_switch/_check_faint_interrupt（post_ko→扣魔力→胜负）/dispatch
    _entry/leave/handle_escape/apply_transmission（虚拟数组分块轮转）
  - phase_turn_end（scheduled('end')/turn 清理+TTL/借用愿力还原/返场/
    turn_end settlement（tick+煤渣草惰性判定+冷却+暴风雪+天气+印记）
    /extra_turn_end/post_abnormal_tick 双视角/turn_end 观察者/能量0
    换宠/冻结斩杀）
  - resolve_item（进化之力经 leader_form 预计算数据/愿力经
    bloodline_skill；导入血价/愿力还原键 wish_restore 改为
    BTreeMap<String, BattleSkill> 键 "team:slot"）
- `rule_agent.rs`：RuleAgent（choose_lead/action/replacement，伤害评估
  estimate_damage 镜像 resolver.calc_damage 的空 SkillUse 路径）。
- 已知待办（阶段3 对拍验证）：skill_position_changed 观察者触发点未接；
  萌化/transform 物种库未接（若对局出现会偏差，导出器需带 pre_species
  链）；道具 resolve_item 的 leader_form/bloodline_skill 由 spec 预计算。

## 下一步（阶段3 门）

1. **spec 导出器**：Python 侧 `native/tools/export_battle_spec.py`——
   用 differential 的随机阵容逻辑生成 N 个 BattleSpec JSON（含
   leader_form/bloodline_skill 预计算 + seed）。种子协议：
   random.seed(seed)→阵容 → random.seed(seed+1)→对局内 RNG
   （Python 端 random.seed(seed+1)；Rust 端 PyRandom(seed+1)）。
2. **pyo3 入口**：`py_run_battle(spec_json) -> 逐回合 digest 列表 + 胜者`
   —— Rust 端实现与 differential/recorder.state_digest 同构的 digest
   （字段设计照 recorder.py，键用 team:sprite 稳定键）。
3. **对拍门**：`test_rust_gate.py`——同 spec 双跑（Python Battle.run
   与 Rust run_battle），逐回合 digest 比对；≥200 场 0 分歧 → 过门。
4. 速度对比：Rust 单线程回合/秒 vs Python 基线。

## 阶段3 门已构建并运行（迭代中）

已建：`native/tools/export_battle_spec.py`（spec 导出，含
leader_form/bloodline_skill 预计算；5 个 spec 在 native/gate_specs/）；
pyo3 `py_run_battle(spec)`（Rust 端逐回合 digest，turn.rs::state_digest
与 run_battle_from_spec，构造期入场已镜像 __init__ 的 index-0 怪癖）；
门测试 `backend/engine/test_rust_gate.py`（xfail 中）；
调试工具 native/tools/debug_gate.py（首分歧定位）。

**已修复的移植缺口（对拍门抓到的真实 bug）**：
1. InjectHitPass 镜像缺失 → vm_exec::execute_with_skill_header（攻击
   技能 power>0 无顶层 hit → 末尾追加隐含 hit；engine.rs 传 skill JSON
   头部）。
2. build_ctx 漏 opp 侧 atk/def/sp_atk/sp_def（伤害恰为 1.5x 偏差）。
3. 愿力还原：phase_turn_end 未还原 wish_restore（技能槽 0 替换）。
4. statics 模板加载：Effect.name 必填导致反序列化失败 → 手工构造。
5. 计数器注册：空名 CounterRegister 不入 counter_values。
6. trait 重载去重：load_traits 移除同 source 的 Observer/Modifier
   效果副本（trait_loader 78-90 镜像）。
7. enemy_switch 队伍计数器补齐。

**当前首个分歧**（spec_0001 turn 6）：`skill.迫近攻击.power` py=90
rust=135 —— 指向 trait_loader 的 **reapply_all_direct_mods（164-191）
+ _apply_direct_mods（203-269）尚未移植**。要点已读：
- reapply：仅 _direct_mod_sprite_ids 中的精灵；效果 ttl>0 先减 1、
  过期移除（并清 display effect）；然后 _apply_direct_mods。
- _apply_direct_mods：仅 op=power_mod（burst_grant 另行处理）；
  sprite 级 attrs 跳过；delta 为 dict 跳过；energy_cost ×
  energy_cost_delta_mult；按 skill_where/skill_filter 匹配技能写入
  bs_mods（energy_cost 先排序处理）；mode set 记 tracked。
- turn.rs 的 execute_turn 尚未调用 reapply（每回合清理段需加）。
- Rust 侧还需要 _trait_direct_effects 存储与 _direct_mod_sprite_ids
  集合（battle_state 的 Sprite/State 补字段）。

## 下一步动作（继续阶段3 门迭代）

1. 读 trait_loader.py 269-320（_apply_direct_mods 尾部 + tracked 应用）
   与 load_for_sprite 的 direct 效果注册路径（75-140 已读）。〔已完成〕
2. Rust：BattleState 增加 sprite 的 _trait_direct_effects: Vec<J> 与
   state._direct_mod_sprite_ids: BTreeSet<(team,idx)>；turn.rs 增加
   reapply_all_direct_mods 调用（execute_turn 每回合清理段）；
   load_traits 的 direct 部分对齐 _apply_direct_mods。〔已完成〕
3. 重跑 debug_gate → 修下一个分歧 → 循环至 spec_0001 0 分歧。〔进行中〕
4. 批量跑 spec 1-200 统计分歧场次，逐类修复直至 0 分歧 → 过门。
5. 速度对比 + 阶段4 MCTS 接入。

## 阶段3 门迭代进展（当前状态）

已修 9 个缺口：前述 7 个 + 迫近攻击 CountOp 重复注册去重（烘焙后比较）
+ wish_restore/愿力还原 + enemy_switch。spec_0001 现在 index 0-7 全一致。

**当前首个分歧**（spec_0001 index 8 = turn 8）：
- py：棋麒麟（A1）力竭 → 扣魔力（lives 3）→ 强制换宠回 sprites[0]
  （active 0、entry_turn 8、效果清空）
- rust：棋麒麟仍存活（中毒 6 层未清、active 1、lives 4）
- turn 8 入口状态（index 7 digest）完全一致 → 分歧产生于 turn 8 执行期：
  1. 候选原因 A：速度/优先级平局的 RNG tie-break 流不一致（Python 全局
     random 消耗点 vs Rust 消耗点存在遗漏——如某技能 effect 触发
     sprite_bench/mark steal/devotion 的 random.choice）
  2. 候选原因 B：伤害计算某乘区仍不一致（先查 turn8 事件串的实际伤害值）
  3. 棋麒麟 t8 死亡：py 中毒层数与 rust 相同（6 层）但 py 还有 tick+
     攻击伤害叠加致死；rust 少一击。
- 调试手段：dbg2.py 打 py 事件（含每击伤害）；rust dmg/tick 日志
  （ROCO_DEBUG_DMG=1）。

## 阶段3 门迭代：当前分歧（spec_0001 index 12 = turn 12）【重大进展】

胜者已一致（B，24 回合 vs py 24）！分歧收窄到 A2 海枝果（players[0].sprites[2]）：
- py sprites[2].effects = [贰极木?persistent, 中毒 persistent 4, 能耗 permanent 0]
  rust 缺第三个「能耗 permanent 0」效果（StatBuffEffect 显示效果，
  steps=0，由 _apply_to_all_skills 的 _sync_mult_display_effect 创建）
- py A2 的 sprite._modifiers 含 skill.伺机而动/涌泉/突袭.energy_cost = -3
  （skill.X.energy_cost 键×4），rust 缺失
- py A2 energy=6 vs rust=4（能耗 -3 生效 vs 未生效）

**根因**：A2 的毒液渗透??——不对，是某 power_mod（stat=energy_cost，
skill_filter=all，scope=permanent，target=sprite_self）走
_apply_to_all_skills 路径：分发到全部技能 bs_mods + 持久化
sprite._modifiers 键 skill.{name}.{attr} + _sync_mult_display_effect
创建「能耗 permanent 0」StatBuffEffect。该 mutation 的来源：
A2 海枝果的某个技能/特性的 power_mod energy_cost delta=-1×3层?
或 -3——需查 海枝果的 ability JSON 与 skills JSON 中
skill_filter=all 的 power_mod。

## 阶段3 门：批量门 200 场迭代（第二轮，44→75 通过）【2026-09-17】

导出 spec 1-200（native/tools/export_battle_spec.py --seeds 1-200）。工具：
`batch_gate.py`（通过/失败 + 单线程速度对比）、`gate_triage.py`
（按首分歧字段路径聚合，便于按类修）、`dbg_sprite_trace.py`（逐回合字段轨迹
+ py 动作/蓄力目标）、`dbg_events.py`（py 回合事件，含 action 事件）、
`dbg_lead.py`/`dbg_est.py`（首发选择评分对拍）、`dbg_log.py`/`dbg_ctx.py`
（日志读取——注意 PowerShell `2>` 重定向写 UTF-16，非 ASCII 调试输出会
截断行，调试行尽量 ASCII 化）。

本轮修复（批量失败 44→75，spec_0001 0 分歧保持）：

1. **initial-energy post_energy_change 缺 event 标志**：py
   dispatch_entry 尾部以 `energy_changed_of="sprite_self"` 触发
   post_energy_change（囤积等初始 mult_mod）；rust dispatch_entry 与
   gather 路径都没设 `ctx.event.energy_changed_of`（gather 还缺
   energy_delta_self）→ on_energy_changed 条件永不成立。另补 py
   battle.py 1432-1442 的技能耗能后 post_energy_change（rust 完全缺失）。
2. **首发选择**：py 先定 A 的首发再让 B 选（B 以 A 实际首发为对手）；
   rust 先算两者再赋值 → 顺序修正；另外 py `choose_lead`/
   `choose_replacement` 的对手是 `get_opponent(team).active`（active，
   非 index 0）——rust 硬编码 def_si=0，导致评分对手错位。
3. **蓄力中行动拦截**：py `_execute_skill_vm` 1245-1249 在
   `user._charging` 时拦截一切行动（含技能）并返回——蓄力技能的
   “释放”分支实际不可达（仅换宠/力竭清蓄力），属 py 既有行为。
   rust 原本只拦 gather → 会提前释放蓄力。已按 py 语义拦截全部。
4. **应对（counter）路径三处**：
   - rust 对 A 的 execute 传 `_is_countered=false`（应为 counter_b）、
     countered_skill 恒 None → 应对方直改注入（_is_countered 分支）
     不执行；已按 py 1096-1103 的 (is_countered, countered_skill,
     countering_skill) 三元组修正双方。
   - **snapshot 的 damage_reduction_opp 从未赋值**（dr_mod_opp 算了
     但没写进 Ctx）→ 减伤效果（防御/嗤痛等）不生效。已补。
   - fire_post_counter 缺 `ctx.event.counter_succeeded=true` 与
     self_skill（思维之盾等 post_counter 观察者依赖）。
5. **fire_pre_event 缺 owner 过滤**：py `_fire_pre_event` 只允许
   {ownerless ∪ owned-by-指定精灵}；rust 传 None 且忽略 team/sprite_idx
   → 例如 A 的血型吸引 pre_modifier 会在 B 出手时误触发并把威力加到 B。
   已补 owner 过滤；并修正 pre_calc 第二位(守方)/pre_defend 的 owner
   队伍参数（原都传攻方队）。
6. **StatChange 的显示效果**：py `_apply_stat_change` 对 _STAGE_STATS
   且有 source 时创建 steps=0 显示 StatBuff（速度 display_value=steps×10，
   其余 display_mult=steps×10%，additive）→ rust 缺，补
   sync_mult_display_effect 调用。

**当前 200 场分布**：通过 92（batch_gate 原始摘要）；VM 层 978 项对拍
全绿；速度：py 88 ms/局，rust 7.0 ms/局（12.5x，单线程）。

本轮追加修复（90→92 通过）：

13. **随机奉献类型顺序**：dump_static_tables.py 之前导出的是
    `sorted(DEVOTION_TYPES.keys())`，而 py 用
    `random.choice(list(DEVOTION_TYPES.keys()))`（插入序）→ 同一 RNG 抽到
    不同条目。已改为原样导出插入序并重新生成 static_tables.json。
14. 传动后位置效果重投影（第 11 条）落地后 drive/威力类归位。

## 阶段3 门：第三轮批量修复（106→137 通过）【2026-09-17】

19. **counter 事件标志未接线**：BuildCtxOpts 增加
    counter_succeeded/was_countered/prev_counter_succeeded 并由
    ExecParams 写入（反爪等 `when: counter_succeeded` 技能分支此前
    永不成立）。同时 execute_skill_vm 的 opp_skill 参数接入
    ExecParams.opp_skill_idx（坟场搏击/扇贝等 `opp_is_attack` 条件）。
20. **显示效果块的 skill_scoped 门**：py `_apply_modifier` 尾部的
    显示效果块没有 skill_scoped 限制（skill_off_0/skill_at_N 的
    mutation 也会在 acting 精灵上建 steps=0 显示效果）→ 去掉多余的门。
21. **pre_calc 回放无 self_skill**：py 的 pre_calc 用【无 _self_skill】
    的 replayer 回放 → skill_off_0 落到精灵级 modifiers。rust 曾传
    acting 技能下标 → 写到技能上（嫁祸 combo=0 类）。
22. **use_devotion 门**：py 仅 CompiledSkill.use_devotion=true 的技能
    （啃咬/虫群）触发奉献消耗并清空全部层数；rust 缺门 → 任意技能
    都消耗。SkillJson 增加 use_devotion，经 skill_by_name 查询。
23. **星陨印记精简镜像补全**：触发技能的攻防键跟随（物攻/魔攻/动态）、
    starfall_consume_ratio 修正、层数扣减与归零移除。
24. **pending_escape 结构化**：PendingEscape{team, sprite_idx, inherit,
    urgent}；py 仅 `_resolve_pending_escape_if_urgent` 结算 urgent 且
    要求逃离者仍是 active；普通脱离在 headless 下保持挂起。
25. **unregister_by_owner 极性反转（重大）**：rust 的 retain 用
    should_clear 当「保留条件」→ 语义完全相反（reload 之外全反）；
    且 "turn" scope 的观察者 py Observer.should_clear 为【永不清】。
    已按 py Observer.should_clear 精确镜像（reload 全清 / battlefield
    任何移除都清 / persistent 仅 faint / 其余永不清）。
26. **双换宠随机序 + is_finished 门**：py 双换随机先后，第二换受
    `is_finished`（winner 或 turn≥150）门——t150 的第二换被跳过；
    rust 之前只查 winner。单换分支的 _resolve_after_switch 同样补
    turn < MAX_TURNS。
27. **视角交换不换 team**：py post_damage/post_ko 的 owner 交换只换
    self/opp 精灵对象，`replayer.team` 不变 → lives/mark/inherit 等
    按 team 解析的仍用原 acting 方。Replayer 增加 `swapped` 标志，
    `self_player()` 据此翻转，team 保持。

**当前 200 场**：通过 137；全引擎测试 1257 passed（含 155 xpassed 的
门 spec 与 xfail 标记）；速度 py ~90ms vs rust ~8ms（11x）。
剩余 63：hp 22、active_index 11、lives 7、count/winner-only 6、energy 5…
（活跃 index 类多与换宠/脱离路径相关；hp 类需继续按例 dbg。
已查样例：spec_0023 t11 影狮「惊吓箱子」应对的反伤路径——counter
应对造成的伤害/驱散在 rust 侧少一跳，下一轮从这里查。）


15. **形态变换（transform/萌化）全链路移植**：
    - `native/tools/dump_species.py` → `native/species_db.json`
      （465 物种；by_display + by_number 索引，镜像 SpriteDB 查询语义）。
    - `roco-core/src/species_db.rs`：SpeciesEntry → Species 映射、
      `get(name, form)`（含「（form）」后缀解析与多形态回退）、
      `lookup_by_number`、StatsCalc 移植（half_round/初始六维/性格系数
      30 条/最终六维）。
    - `Sprite::transform`（HP 比例保留、initial_stats 重算、技能替换、
      first_action=true）、`build_moe_chain`（沿 pre_species 向下）、
      `apply_moe`/`remove_moe`（含无忧无虑 20157 溢出计数、
      萌化异常同步、首领化移除、专属技能封印/解封）。
    - replayer：`Mutation::Transform` 接入（lookup 失败时用当前物种
      数值套新名）、萌化 AbnormalChange → apply_moe、驱散萌化 →
      remove_moe；`data/skills.rs` 增加 skill_by_name/build_skills
      （CWD 相对 data/skills，OnceLock 缓存）。
16. **属性缓存镜像（关键 py 怪癖）**：py `Sprite.{atk,def,sp_atk,sp_def}
    _with_modifiers` 读缓存（`_stat_cache_dirty`），而 `Sprite.transform`
    **不**使缓存失效 → 形态变换后这些数值保持变换前的旧值，直到下一次
    `_invalidate_stat_cache`（replayer 的 atk/def/... modifier 变更、
    clear_effects 有变化时）。rust 用 `RefCell<Option<[i64;4]>>`
    （serde skip）精确镜像该语义；replayer 的默认 modifier 写入分支
    按 py 589-592 的 stat 列表失效缓存。
    （spec_0006 的 116 vs 133 伤害差即由此产生。）

**当前 200 场分布**：通过 97；VM 层 978 项对拍全绿；
速度 py 88 ms/局 vs rust 7.0 ms/局（12.5x）。

剩余失败按首分歧字段：hp 20、effects:len 13、active_index 10、
energy 8（如 spec_0003 t9「全技能能耗+1」类）、
skills[].modifiers.power_mult 7、lives 7、count/winner-only 6、
skills[].modifiers.energy_cost 5。
## 阶段3 门：Ctx 漏字段审计 + 技能对象语义（98→106 通过）【2026-09-17】

17. **Ctx 对手字段漏赋值**（`energy_opp` 等）：snapshot.rs 计算了
    `hp_opp/hp_opp_max/hp_opp_ratio/energy_opp/speed_opp` 却从未放进 Ctx
    字面量（同类此前已修 `damage_reduction_opp`）。症状：坟场搏击的
    `mult_mod power_mult value={q:energy, of:sprite_opp, scale:-0.1}`
    在 rust 解析为 -0.0（对手能量恒 0）→ 技能 power_mult=1.0 而非 0.2。
    已补全部 5 个字段。新增 `native/tools/audit_ctx_fields.py` 做
    「Ctx/EventContext 字段 vs snapshot 赋值」静态审计（缩短路的赋值
    也算命中），当前 Ctx 111 字段仅剩 event 事件类字段（由 fire 站点
    按需设置，属正常）。
18. **`self_skill` 必须是技能对象而非下标**（spec_0066 的
    `skill.压扁.energy_cost` 幽灵键根因）：py 的 replayer 持技能对象
    （`_self_skill`），post_damage/post_ko 视角交换后仍指向原 acting 精灵
    的技能——`skill_off_0` 解析到【该对象】（键名取它的名字，写入
    replayer.self 的 sprite._modifiers）。rust 之前按下标在【交换后的
    self 精灵】技能表里取 → 写到错误技能（-2 累计）。改为：Replayer 持
    `self_skill_id: Option<u64>`（技能实例 id），`find_skill(id)` 全盘
    定位；fire_post_event 在交换前解析 acting 技能的实例 id 并用
    `Replayer::new_with_skill_id` 传入。life_drain 读取同改。
    新增调试工具：dbg_persist.py（追踪 skill.X.stat 持久键写入）、
    dbg_skills_dump.py（逐回合双方全技能+修饰符对比）、dbg_find2.py。

**最新状态**：✅ **阶段3 门通过：200/200，0 分歧**（pytest 1656 passed，
xfail 已移除）；速度 py ~48ms vs rust ~5.2ms（9.2x）。

**批次四（196→200，两处 py bug + 两处 rust 缺口）**：
1. **修 py bug**（用户确认）：replayer._sync_mult_display_effect 合并分支
   `existing.display_mult += mult_value` 无 None 保护——合并谓词 steps==0
   会命中 _sync_stat_buff_effect 刚创建的真实 StatBuff（display_mult=None）
   → TypeError 炸断整个观察者 then-block，后续效果（sp_atk/灼烧/
   immune_abnormal 等）静默丢失且被 _fire_post_event 的 except 吞掉。
   修复：`(existing.display_mult or 0.0) + mult_value`（与同函数
   display_value 的 or 0 保护对齐）。rust 对应实现本就 unwrap_or(0.0)。
   一次修复 spec_0118 + spec_0130。排查要点：蓄电池类「公式 steps 解析
   为 0 + 双 stat_stage then」是触发条件；探针矛盾（journal 有两条/
   派发只一条/终态少一条）的根源就是异常在 replay 中途炸断。
2. **修 rust**：urgent pending_escape 滞留——py _resolve_pending_escape_if_urgent
   看到 pending 后【无条件清除】再校验逃离者是否在场；rust 原来只在
   槽位匹配时清除，滞留的 urgent pending 在逃离者恰好换回该槽位时
   二次触发 handle_escape（spec_0196 t11 游蛇魔使 entry_turn=11）。
   修复：先置 None 再校验。
3. **修 rust**：返场结算（过载回路类 Return → pending_return）缺完整
   入场管线——py _resolve_return 在重置标记后还有 dispatch_entry +
   fire_post_entry + _apply_entry_transmission（battle_mechanics.py 147-175）；
   rust 原来只重置标记。轴承支撑类 sprite_entered 观察者的精灵级
   power 写入与传动旋转都挂在这条管线上（spec_0004 t24 的 power:1.0
   缺失与技能顺序未旋转）。

遗留清理（下次）：本批调试 eprintln（[rust enter]/[rust entry-set]/
[rust exec-vm]/[rust pre-fire]/[rust skill-mod write]/[rust calc]）均挂在
ROCO_DEBUG_DMG 下，阶段5 收尾时可一并清理。

**批次三修复（175→196）**：
1. 传动 reapply 时机：py 的 _reapply_position_modifiers 在【所有 pass 结束
   后】由调用方执行一次；rust 原在每轮 pass 内执行，pass 间重放翼轴类
   skill_at_ 注入会改写后续 pass 的传动分块（spec_0065/0137）。
2. execute_skill 的 BuildCtxOpts 漏拷 opp_switched → 灵光类 when:opp_switched
   条件永不成立（spec_0192）。
3. 技能级 add 模式 energy_cost 注入漏乘 energy_cost_delta_mult（对流类，
   py replayer 575-576；永久键写入 py 不乘，rust 同样不乘）。
4. SkillJson.combo serde 默认 0，py 默认 -1（不参与连击）——combo_self
   差一导致伤害差（spec_0126/0084/0107/0121/0127/0170/0072 一批全修）。
5. burst 登记/skill_history 存的是【编译后】效果（py InjectHitPass 编译期
   并入隐式 hit）；rust 原 store 原始 JSON → team_burst 重放无伤害
   （spec_0100 雷暴）。新增 vm_exec::augment_with_header 共用。
6. execute_skill 漏传 burst_triggered_count_own（雷暴类威力/能耗成长）。
7. ScheduleEntry 落库时 ctx_snapshot 缺 team → 回合末执行按 A 视角解析
   sprite_opp，石天平类延时 energize 目标反了（spec_0003）。
8. py ctx 的 stat_stages/abnormal_stacks 是精灵活缓存引用（_extract_sprite_effects
   O(1) 路径）——ctx 构建后的变化对后续读取可见；rust 纯快照导致反击类
   伤害用旧阶段。新增 refresh_live_effects 在主回放后/mutation 事件逐次刷新。
9. skill_position_changed 观察者实现（齿轮扭矩类「位置变化时威力永久+20」）：
   apply_transmission 每 pass 对移动技能逐个 fire（owner_skill 绑定过滤，
   trait_sourcing=true 回放）；同一提交修 reapply-in-loop。
10. 新增 py 显示效果分支：_apply_stat_change 非舞台属性（power/energy_cost/
    priority/combo）且有 source 且 steps≠0 → display_value=steps×单位。
11. extra_turn 判定只统计【存活】在场精灵（py sprites dict 跳过 fainted，
    spec_0182）。
12. post_counter 用回合开始捕获的 s_a/s_b（对象引用语义）：rust 原执行后
    重读 active_index，中途脱离换人后视角错位，思维之盾误触发（spec_0196 t7）。
13. **修 py bug**（用户确认）：replayer._tick_element_mult 对 attributes
    split(',') 未 strip，"冰, 地" 查表 miss → 异常 Tick 元素倍率错。
    已在 py 侧修复（strip），rust 对应 apply_tick 保持 trimmed elements。
    py 其余 attributes split 均已 strip，仅此一处。

新调试工具：dbg_batch6/dbg_eff_trace/dbg_order/dbg_energy_trace（批量差异）、
dbg_py_trace/dbg_py_cost/dbg_py_dmg/dbg_py_tick2（py 侧 hook 地面真值，
注意 patch _DISPATCH 字典而非类属性）、dbg_rust_trans（fd2 级 stderr 重定向
绕开 PowerShell UTF-16 截断）、dbg_grep_kw/dbg_calc_ctx（按回合提取 rust 日志）。

待查样例：无——阶段3 门已通过。下一步：阶段4 MCTS/训练接入 →
阶段5 ROCO_ENGINE 开关与完整对局/API 路径 → 阶段6 文档与提交。
- hp 类（约 13）：继续逐例 dbg——伤害乘区（减伤负值/多段）或时序。
- 门通过（200 场 0 分歧）后移除 test_rust_gate.py 的 xfail → 阶段4
  MCTS 接入 → 阶段5 ROCO_ENGINE 开关 → 阶段6 文档与提交。
- spec_0019 t4 等 5 场：B0 skills[0/1].energy_cost py=-2 ru=-4 已修；
  若仍复现，查 morph 类「每回合随机变成未携带技能」的每回合重掷。
- hp 类（约 15）：继续逐例 dbg（伤害乘区/段次/连击）。
- 门通过（200 场 0 分歧）后移除 test_rust_gate.py 的 xfail → 阶段4。

19. **对手技能上下文（ctx.skill_type_opp）**：rust 的
    `execute_skill_vm` 忽略了 `_opp_skill` 参数（ExecParams.opp_skill_idx
    恒 None）→ 技能内 `when: {cond: and, conditions:[is_first,
    opp_is_attack]}` 之类的条件永不成立（例：坟场搏击/扇贝的自条件
    威力加成）。已按 py 语义 `opp_skill = countered_skill or
    countering_skill or opp_skill` 解析成下标；回合调度按
    `opp_skill_for_first/second` 传入对手技能。
20. **InheritEffects 实现**（洁癖类「离场后增益被继承」）：rust 原为
    空实现。已镜像 py `_apply_inherit_effects_mutation`：
    inherit_stat_effects → 全部 StatBuff（排除 is_inherent），否则按
    scope；via_pending → 序列化进 state.pending_effects（entry 时生效），
    否则直接 add_effect 到 enemy_new/self。VmEngine 新增
    `leaving_loc`（post_enemy_leave 前设置，供 source=sprite_opp 解析，
    镜像 py 的 leaving_sprite）。

**待查（effects:len 类，spec_0046 t9）**：py 的「能耗 persistent ttl=2」
显示效果在 t9 消失（rust 保留）。已排除 decrement_ttl / clear_effects /
remove_effect 三个路径（打桩无命中），说明 py 经 trait_loader 的
`active_effects = [...]` 直赋或其它未打桩路径移除；且 py 在 t5-t8
每回合刷新 ttl=3→回合末 2，t9 无刷新（A 聚能非技能）应在 t9 末为 1。
下一步：给 trait_loader 的两处直赋打桩 + 打印 t9 的全部 effects 变更点。





**下一轮候选方向**（按类）：
- 形态变换（上，14 例）
- hp 类（~19）：逐例 dbg——伤害乘区（减伤负值/多段）或时序
- effects:len（13）/effects 内容（10）：按 py replayer 各 handler 尾部核对
- active_index（9）/lives（7）：换宠/无替补多扣魔力路径
- energy（7）：能耗修正（energy_cost:missing 类）与时序
- 门通过（200 场 0 分歧）后移除 test_rust_gate.py 的 xfail → 阶段4

## 阶段3 门迭代：spec_0001 达成 0 分歧（本轮 5 类修复）【2026-09-17】

批量门前的最后一轮循环修复，spec_0001 py/rust 摘要全等（B 胜、24 回合）：

1. **build_ctx 缺事件标志**（t12 根因）：py build_ctx 有
   self_switched/opp_switched/target_fainted kwargs → ctx.event；
   rust BuildCtxOpts 没有这些字段 → EventContext 永远 false →
   `sprite_left of=sprite_opp`（珊瑚木 post_enemy_leave 观察者）与
   `sprite_left of=sprite_self` 条件永不成立。修复：BuildCtxOpts 增
   3 字段并在 build_ctx 写入 event；resolve_switch 的 post_leave ctx
   传 self_switched=true、post_enemy_leave ctx 传 opp_switched=true；
   check_faint_interrupt 的 post_ko ctx 传 target_fainted+self_switched。
   涉及 traits：海枝果 珊瑚木（post_enemy_leave → energy_cost -3
   skill_filter=all permanent）、风滚暖蜥蜴 共鸣（post_entry → 虫鸣
   power+20 → 缺「威力 battlefield 0」显示效果）。
2. **apply_modifier 技能级 power_mod 缺显示效果与直改登记**：
   py _apply_to_all_skills 尾部 source 非空时 _sync_mult_display_effect
   创建/更新 (stat_key, source, steps==0) 显示 StatBuff（能耗/威力）；
   _apply_to_matching_skills 尾部 applied 时登记 effect_dict 进
   sprite._trait_direct_effects + trait_loader._direct_mod_sprite_ids
   （跨回合 reapply），source 非空时按 stat 分支（energy_cost→
   display_value=delta；power_mod→delta×10 scope=battlefield；比率型→
   display_mult=delta(-1)；普通→display_value=delta；ttl=max）创建
   显示效果。rust 两个分支全部照补（sync_mult_display_effect /
   sync_matching_display_effect + stat_label + RATIO_STATS 常量）。
3. **无替补力竭的双扣魔力时序**（t23 根因）：py _execute_turn_core
   四阶段之间无 winner 短路——turn_end 阶段无条件执行；酸拉力竭无
   替补时 active 不变 → 行动后检查扣 2→1（设 winner）+ 回合末双方
   检查再扣 1→0。rust execute_turn 有两处 winner return → 删；
   check_faint_interrupt 修正 agent 配对（先手后查 first+second 双方、
   各用己方 agent；后手后仅查后手方——py 1150/1151/1161），第二行动
   的跳过条件补 `turn < MAX_TURNS`（py is_finished 含回数上限）。
4. **handle_escape 缺观察者触发**：py 脱离路径 dispatch_leave/entry
   后触发 post_leave(self_switched=true)/pending_entry/post_entry/
   transmission——rust 补齐（含应急/继承两条路径共用）。
5. **能量0 强制换宠重写为星地善良语义**：py battle.py 1851-1898 仅当
   板凳有 ability_id==20158（星地善良）才换（hook 无注册回调）；
   换宠走 dispatch_leave→dispatch_entry→post_leave/pending/post_entry/
   transmission 全钩链（无棘刺/降灵印记伤害）。rust 之前"虚构"的
   首 个替补强制换宠已删，按 py 语义重写。

回归：51+927 项 rust 对拍测试全绿（3 xfailed 2 xpassed）。
批量门：native/tools/batch_gate.py（spec 1-200，含耗时统计）。

## 下一步动作（继续阶段3 门迭代）

py turn 序列（最新）：t5/t6 A=迫近攻击 B=叶绿光束；t7 A=愿力 B=聚能；
t8 A=switch 棋麒麟 B=叶绿光束 → 棋麒麟 -25 力竭 → lives 3 → 换回酸拉。

rust t8 行动一致（A=switch:1 B=skill:1），攻击命中，但**力竭换宠未触发**：
- index 8 差异：py lives=3/active=0/酸拉 entry_turn=8/棋麒麟效果清空/
  棋麒麟 skills[1].modifiers.power=90；rust lives=4/active=1/棋麒麟
  效果保留（中毒6+保卫）/skills[1].modifiers.power 缺失。
- 两侧棋麒麟 hp 均为 0（都打死了）→ 分歧在死后处理：rust 的
  check_faint_interrupt 未触发强制换宠。
- 另：rust 迫近攻击的 +45 永久成长序列（t5=45/t6=90/t6=135 双跳）
  在 dedup 修复后已变（perm persist 日志以最新重跑为准）；且 py t6=90
  之后 t7/t6 不再增——注意迫近攻击的 +45 来自其自身的 observer
  （cond skill_use → post_skill 触发，then power_mod permanent 45，
  source=迫近攻击），每次使用 +45，去重后每次使用只应 +45 一次。

## 下一步动作（继续阶段3 门迭代）

1. **t11 愿力道具循环差异（当前阻塞）**：py t11 B 用了愿力×2
   （resolve_item 两次：藤鞭→萌芽切割→再替换），rust t11 B 最终
   action=skill:1（叶绿光束）。两侧 select_action 道具循环逻辑需对比：
   - py：item → _resolve_item → continue → 重新 choose_action；
     愿力 used 后 can_use=false → 落到评分选技 → 最终=skill；
   - rust：同结构，但需验证 resolve_item(愿力) 的 bloodline_skill
     替换、uses 递增、can_use 冷却判定与 py 完全一致；
   - 另：rust 的 replaced 技能带完整 effects JSON，py 的
     skill_loader 替换技能 effects=[]（元数据）——若替换技能
     产生的效果不一致也会放大分歧（对拍阶段重点核对）。
2. t12 的「能耗 permanent 0」显示效果 + skill.X.energy_cost=-3 键×4
   （A2 全技能能耗分布）：来自 海枝果 ability=珊瑚木(20096) 的
   trait observer（cond sprite_left of sprite_opp，post_enemy_leave，
   then=[power_mod energy_cost -3 skill_filter=all scope permanent
   target sprite_self]）→ _apply_to_all_skills 分发+持久化+显示。
   rust 侧该 observer 的 post_enemy_leave 触发与 _apply_to_all_skills
   分发已实现，需核对 t11/t12 的触发时序。
3. 循环修至 spec_0001 0 分歧。
4. 批量 spec 1-200 统计分歧，逐类修复至 0 分歧过门。
5. 门通过后移除 test_rust_gate.py 的 xfail 标记 → 阶段4 MCTS 接入。

## 阶段3 门迭代：最新进展（reapply 移植后）

- 已移植：reapply_all_direct_mods + _apply_direct_mods + per_turn_cleanup
  （每回合键弹出/直改重放/永久技能恢复——**load_permanent 恢复双方全部
  精灵而非仅 active**）+ unload_traits（faint 时 should_clear 清
  persistent/battlefield 效果 + _remove_direct_mods + 登记移除）。
- 重跑 debug_gate：分歧从 index 8 推进到 index 11。
- **index 11 差异**：py B 的 active=0（韫Magikarp Flower hp 37）；rust
  B 的 active=1（风滚暖蜥蜴，entry_turn=11，共鸣 battlefield 效果）。
  两侧 t11 行动：A=skill:0（突袭 70 → B0 hp 107→37）、B=skill:1。
- **py t11 事件**：B 用了愿力(藤鞭→萌芽切割)——B0 hp 107，ratio
  0.31 < 0.5 → 愿力门触发 ✓。rust 侧愿力门同样应触发（hp 相同），但
  rust 的 resolve_item(愿力) 后的行为与 py 不一致（select_action 的
  道具循环：item → resolve_item → 重新选择——py 愿力使用后替换
  藤鞭→萌芽切割并继续循环；rust 的 resolve_item 愿力路径需复查
  bloodline_skill 替换与 uses 递增）。
- 另：rust perm persist 日志 t6 出现 90→135 双跳（+45 两次）——
  迫近攻击 observer 在 t6 触发了两次，需在 fire_post_event 加日志
  （trigger/owner/candidates 数）定位是注册双份还是触发双次。

## 阶段3 门迭代：最新进展（reapply 移植后）

- 已移植：reapply_all_direct_mods + _apply_direct_mods + per_turn_cleanup
  （每回合键弹出/直改重放/永久技能恢复——**load_permanent 恢复双方全部
  精灵而非仅 active**）+ unload_traits（faint 时 should_clear 清
  persistent/battlefield 效果 + _remove_direct_mods + 登记移除）。
- 重跑 debug_gate：分歧从 index 8 推进到 index 11。
- **index 11 差异**：py B 的 active=0（韫Magikarp Flower hp 37）；rust
  B 的 active=1（风滚暖蜥蜴，entry_turn=11，共鸣 battlefield 效果）。
  两侧 t11 行动：A=skill:0（突袭 70 → B0 hp 107→37）、B=skill:1。
- **py t11 事件**：B 用了愿力(藤鞭→萌芽切割)——B0 hp 107，ratio
  0.31 < 0.5 → 愿力门触发 ✓。rust 侧愿力门同样应触发（hp 相同），但
  rust 的 resolve_item(愿力) 后的行为与 py 不一致（select_action 的
  道具循环：item → resolve_item → 重新选择——py 愿力使用后替换
  藤鞭→萌芽切割并继续循环；rust 的 resolve_item 愿力路径需复查
  bloodline_skill 替换与 uses 递增）。
- 另：rust perm persist 日志 t6 出现 90→135 双跳（+45 两次）——
  迫近攻击 observer 在 t6 触发了两次，需在 fire_post_event 加日志
  （trigger/owner/candidates 数）定位是注册双份还是触发双次。
- **最新日志**（dbg2.out，t9-t12）：rust t11 无 B 换宠 action，但
  B1（风滚暖蜥蜴）entry_turn=11 + 共鸣效果 + t12 时 B active=1——
  **rust 在 t11 某处让 B1 入场**。py t12 事件 "B used: 风滚暖蜥蜴" 是
  t12 的换宠 action。需查 rust t11 中 B1 入场的调用链
  （check_faint_interrupt? handle_escape? 共鸣 dispatch?）。

## 下一步动作（继续阶段3 门迭代）

1. 查 t11 rust B1 入场链：在 enter_sprite/dispatch_entry 入口加
   ROCO_DEBUG 日志（team/idx/调用来源），复跑定位；
2. 查 t11 愿力：rust resolve_item(愿力) 的替换与 uses 递增是否与 py
   一致（py 愿力 used 后 can_use=false → 落到技能选择）；
3. 循环修至 spec_0001 0 分歧；
4. 批量 spec 1-200 统计分歧，逐类修复至 0 分歧过门；
5. 门通过后移除 test_rust_gate.py 的 xfail 标记 → 阶段4 MCTS 接入。

## 移植方法

- 每个 Rust 模块对应 Python 源文件逐行翻译，保持运算顺序与分支结构；
  不"顺手优化"（优化在阶段4+ 且必须过对拍）。
- 每个 Python 源文件移植前先通读，确认调用关系；用
  `Select-String`/Read 读取。
- 每模块完成即写 Python↔Rust 单元对拍（复用 py_ 绑定模式）。

## 阶段3 门迭代：最新调查锚点（t11 B1 入场之谜）【当前状态】

- spec_0001 分歧推进到 index 11（前 10 回合含 skill modifiers 全一致）。
- py t11：B 用愿力×2（藤鞭→萌芽切割→再替换，uses 耗尽），最终
  action=skill:1（叶绿光束 -91 打 A2）；B active 保持 0。
- rust t11：B0 hp 107→37（同被突袭 -70 ✓）、B=skill:1、**但 B1
  （风滚暖蜥蜴）entry_turn=11 first_action=true 共鸣 battlefield 效果
  ——rust 侧 B1 在 t11 入场了，py 没有**。
- dispatch_entry 的 #[track_caller] 日志显示 t11 的 B1 入场经
  enter_sprite（turn.rs:1610）调用。enter_sprite 的调用者仅
  resolve_switch 与 check_faint_interrupt——但 t11 B 无 switch action
  且 B0 hp 37 未力竭。
- 疑点：resolve_switch(B) 是否被错误调用（如 phase_resolve 对 B 的
  action 判定），或 check_faint_interrupt(agent_b,"B") 的 is_fainted
  误判。下一步在两处入口分别打标记（EnterViaSwitch/EnterViaFaint），
  并在 resolve_switch/check_faint_interrupt/handle_escape/immortal 的
  active_index 变更处打日志，重跑 dbg2 定位。
- 另注意：t11 rust B=skill:1 的 op_hit 未出现在日志（B 的叶绿光束
  power=120 未打）——说明 t11 B 的实际执行可能不是叶绿光束。

## 下一步动作（继续阶段3 门迭代）

1. 在 enter_sprite 的两个调用者入口分别加 EnterViaSwitch /
   EnterViaFaint 标记日志，重跑 dbg2 定位 B1 入场来源；
2. 根据来源修复（预计是 resolve_switch 被错误调用或
   check_faint_interrupt 的 is_fainted 误判）；
3. 循环修至 spec_0001 0 分歧；
4. 批量 spec 1-200 统计分歧，逐类修复至 0 分歧过门；
5. 门通过后移除 test_rust_gate.py 的 xfail 标记 → 阶段4 MCTS 接入。

## 阶段4：MCTS 接入 Rust（进行中）

### 已完成

1. **numpy legacy RNG 镜像** `native/roco-core/src/np_random.rs`：
   - `np.random.seed(int)` = `mt19937_seed`（init_genrand；**不是** CPython random 的 init_by_array），state[0]==seed、pos==624；
   - `rk_double` / `standard_exponential` / `standard_gamma`（shape<1 与 >=1 两分支）/ `dirichlet` 逐位对齐；
   - 校准基准 `native/tools/probe_np_rng.py`（numpy 2.4.6）；单测 `cargo test -p roco-core np_random` 7/7 通过（含 17 维 dirichlet）。
2. **MCTS 主体** `native/roco-core/src/mcts.rs`：树/PUCT/选择/扩展/回退/终局价值/合法动作/固定动作步进/轨迹采集。
   - PUCT 与 `q+u` 按 numpy 2（NEP 50）的 **float32** 语义求值（校准：`native/tools/probe_np_arith.py`）；
   - 根噪声 `prior[a] = f32( f32((1-rn)*prior[a]) as f64 + rn*noise[i] )`；
   - 终局价值 `battle_outcome_a` + `team_battle_score`（outcome.py 镜像）；
   - 回滚：每轮仿真前克隆 (BattleState, VmEngine)，**不还原 RNG**（= py save_mutable_state 语义）；搜索结束整体复原对局 RNG（= py `random.setstate`）。
3. **引擎侧新增**：
   - `turn::execute_turn_fixed` / `execute_turn_a_fixed_b_agent`（py `execute_turn_headless` + `fixed_action_*`）；
   - `ReplacementPolicy` trait（RuleAgent / RuleReplPair / FirstAliveRepl / MCTS 的网络策略头 `EvalRepl` = py `_choose_policy_replacement`）；
   - `BattleState.mcts_sim` + `Replayer.headless`（py `battle._mcts_sim`）。
4. **对拍工装**：`native/roco-py::py_mcts_stub`（确定性桩：value=0、policy=归一化 mask）+ `native/tools/mcts_gate.py`
   （比对项：概率位模式、根节点访问次数、每轮仿真动作轨迹、逐步状态摘要、numpy/对局 RNG 状态）。

### 本轮由 MCTS 对拍暴露并修掉的 4 个引擎级根因

（整局 200/200 门未覆盖这些路径——RuleAgent 不会用到的动作/时机组合）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 应对方 when/then/else 效果未注入 | py 过滤的是**编译后 IR**：带 `when` 字段的只有 `CountOp`，`WhenBlock` 的字段名是 `cond`；rust 此前按源 JSON 的 `"when"` 键过滤 → 整块被排除 | `turn.rs` 反制注入过滤改为仅排除 `op=="count" && 有 when` |
| 2 | 被应对方多 1 点能耗（spec_0037） | 同上：被注入的 WhenBlock 在注入 ctx 里按 **else 分支**生效（注：注入 ctx 的 counter_succeeded=False） | 同上 |
| 3 | 仿真中创建了 UI 显示效果（spec_0001 `combo=3` 等） | py `is_headless` 跳过纯 UI 显示效果（replayer.py:421 / :630）；rust 无条件创建 | `BattleState.mcts_sim` → `Replayer.headless`，两处显示效果创建前 return |
| 4 | 结算中力竭的精灵不再触发 turn_end 观察者（spec_0019 毒蘑菇偷能量） | py 在 turn_end 结算**之前**快照存活在场精灵列表（battle.py:1797-1801），结算（异常 tick/暴风雪/印记）中力竭者仍在列表里 | `phase_turn_end` 先快照 sprites 再结算；extra_turn / post_abnormal_tick / turn_end 三处遍历快照 |

### 当前对拍结果

- **阶段3 整局门仍 200/200**（本批修复后复跑通过）；pytest **1656 passed**。
- **MCTS 桩对拍**（sims=200，单仿真路径，specs 1..50）：**41/50 通过**。
  剩余失败样例（首个分歧点）：
  - `spec_0008` sim42：`P1S1`（双向光速 `extra_turn_end`）py 受伤 388 vs rust 269，且 rust B 侧 `status_skill` 计数多 1；
  - `spec_0016` sim114 / `spec_0019` sim26：回合末 hp/energy 差（26~63）——疑似 extra_turn 二次结算范围或 turn_end 快照后的行为差异；
  - `spec_0023 / 0030 / 0040 / 0041 / 0042 / 0044`：待分类。

### 阶段4 未完成项

1. `leaf_batch_size>1` 的叶节点批量评估路径（训练默认 16，生产路径必须实现——当前 rust 只实现单仿真路径）；
2. encoder 移植（10 数组 + vocab + AST tokenization）→ 由 Rust 产 numpy；
3. evaluator 回调协议（Rust → Python 批量推理队列）+ `mcts_search` 薄壳（签名不变）；
4. 阶段4 门：samples/s ≥5x + 同种子结果一致。

### 调试方法备忘（用于继续追踪剩余分歧）

- `mcts_gate.py` 已带 `digest_trace`（每步状态摘要）+ trace（每步动作），定位首个分歧的 sim/step；
- `dbg_mcts01.py <spec> <sims>`：打印首个分歧的全部差异字段与双方精灵状态；
- `dbg_mcts_rust.py <spec> <sims> <关键字>`：带 `ROCO_DEBUG_DMG` 跑 rust 桩，筛选 stderr 调试行；
- **注意**：所有脚本必须 `os.chdir(ROOT)`——`data/skills/*.json` 是 CWD 相对路径，CWD 不对会导致 py 侧技能 JSON 缺失而静默 no-op（曾误判为分歧）。

### 全量对拍结果（本批修复后）

- MCTS 桩对拍 specs 1..200（sims=200）：**159/200 通过**（本批修复前为 91/200）。
- 阶段3 整局门 200/200、pytest 1656 passed 均保持。
- 剩余 41 例的首个分歧集中在：回合末 `extra_turn`（双向光速 extra_turn_end）二次结算、
  结算中力竭者的后续处理、以及疑似 B 侧动作选择差异（当前 trace 只记录 A 侧动作，
  建议下一步在 `mcts_gate.py` + `mcts_search_traced` 中同时记录 B 侧动作以区分
  “动作不同”与“结算不同”）。

### 阶段4-3 续：逐步记录扩充 + 复现工装 + 3 个新根因

**工装增强（已落地）**

- 每步记录由「状态摘要」扩为 `{"b": 对手动作索引, "mti": 对局 RNG 游标,
  "np": numpy RNG 游标, "d": 状态摘要}` —— 首个分歧点可直接区分「动作不同/
  随机数消耗不同」与「同动作同游标、纯结算不同」（实测剩余分歧几乎全是后者）。
- `mcts_gate.py` 改由 python 直接写 UTF-8 报告 `native/tools/_gate_last.txt`
  （经 PowerShell 管道会被按 ANSI 重编码成乱码）；digest 步数不一致也会报。
- `native/tools/dbg_fixed_turn.py <spec> "a,b;a,b"`：固定动作逐回合 py/rust 双跑比对。
- `native/tools/dbg_replay_sim.py <spec> <sim>`：从 MCTS 的 trace+digest_trace
  反推动作序列，重放 0..sim 轮（跨轮必须重放，因为对局 RNG 不随仿真回滚）。
- rust 新入口 `py_fixed_turns(spec, actions)`：多轮仿真固定动作重放，轮间回滚
  state/engine 但**不回滚 RNG**（= py `save_mutable_state` 语义）。
- 调试环境变量：`ROCO_DEBUG_DMG`（rust 全链路）、`ROCO_DBG_DMG`（py 伤害链）、
  `ROCO_DBG_CNT`（py 队伍计数）、`ROCO_DBG_MOD`（py modifier 落点）。
  **坑**：`JournalReplayer._DISPATCH` 在 import 期固化，patch 类方法后必须同步
  替换该字典，否则补丁不生效（曾误以为 py 没走到 `_apply_modifier`）。

**本轮新根因**

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 5 | spec_0140 sim0：连续爪击应对反伤 py 61 vs rust 30（连击 2 vs 1） | py 应对效果注入用 `counter_record`（CompiledSkill）作 self_skill、battle_skill=None 构 ctx → `combo_base = sk.combo`（技能记录值）；rust 未传技能身份 → combo_base=1 | `BuildCtxOpts.self_skill_record`（power/combo/energy_cost/element/skill_type），反制注入处按 `countering.base` 填充 |
| 6 | spec_0081 sim3：先手击杀后替补**打出了**本回合动作 | py 反制路径先手行动后要求「B 在场精灵仍是行动前那一只」（`b_sprite_now is s_b`，battle.py:1080-1081）；rust 只查 `!is_fainted` | `resolve_both_skills` 反制分支先记 `b_active_before` 并加同体判定 |
| 7 | spec_0175 sim20：叠势「应对成功本技能连击+2」落点不同（py 写 `sprite.combo`，rust 写 `skill.combo` + `skill.叠势.combo` 持久键） | py `fire_trigger("post_counter", …)` **不传 self_skill** → 观察者 then 的 `power_mod(target=skill_off_N)` 落 `sprite._modifiers`；rust 传了 countering 技能 id → 落技能 `_modifiers` 并写持久键 | `fire_post_counter(..., None)` |
| 8 | 重放路径与 MCTS 记录不符（工装自身） | 复用同一份快照会被污染：`restore_mutable_state` 直接**别名** `saved` 的内层 dict（`team_counters` 等），仿真期间写入会改到快照本身；MCTS 每轮重新 save 才避开 | 复现工装每轮重新 save/restore；`py_fixed_turns` 每轮 clone 初始 state/engine |
| 9 | spec_0149 sim9：寄生回合末 tick py 44 vs rust 22（×层数 vs 固定） | py `replayer.py:886-896` 从 `ABNORMAL_TEMPLATES` 构造 AbnormalEffect 时**漏拷 `tick_per_stack`** → 实际生效 dataclass 默认 True（按层数）；寄生模板明明是 False | rust `sync_abnormal_effect` 模板分支强制 `tick_per_stack=true`（复刻 quirk；`Sprite.update_stacks`/effect_factory 路径 py 是完整拷贝，勿改） |

**定位 #9 的关键**：py 事件版重放（`ROCO_DBG_EVENTS=1`，`dbg_fixed_turn._compare`
里非 headless 跑一遍并打印每回合事件串）直接给出「小皮球 寄生-44HP」，
与 rust `[rust tick]` 的 stacks/pct 一对照即见分晓——比逐条对伤害公式快得多。

**hp 簇的剩余 3 例（0042/0067/0101）**：同方法复现到具体回合后，
先用事件版确定机制，再决定是对公式输入还是对效果落点。

**active_index 簇（0124/0141/0164）的线索**：均含**紧急脱离**（一回合内
脱离→换人→再脱离）。py/rust 紧急脱离都用 `random.choice`/`rng.choice`
随机选替补（消耗对局 RNG），但选出的下标不同 → 脱离链路里 RNG 消耗次数
或 bench 列表仍有差异（py bench 排除 `player.active_index`，rust 排除
`user_idx`，双重脱离时两者可能不再相等）。另注意：`dbg_fixed_turn` 的
`_FirstAliveProxy.choose_replacement` 已对齐 rust `FirstAliveRepl`
（排除当前在场位、无可换返回 -1）。

**spec_0164 的硬数据（下一步从这里下手）**：sim19 step1 的逐步记录
`mti py=8 ru=9`——**rust 在该回合多消耗了一次对局 RNG**（np 一致、B 动作
一致）。两个坑要记住：
1. `_first_diff` 的键序是 b→d→mti→np，`d` 先分叉时 **mti 差异会被掩盖**，
   「RNG 游标一致」的结论必须用 `dbg_mcts_step.py` 的表头确认；
2. `ROCO_DBG_EVENTS` 的事件版重放（`_compare` 里的 battle2）用的是**全新
   RNG**，只能看机制、不能对齐 sim N（N>0）的精确轨迹——前序轮已消耗 RNG。

### 阶段4-3 续二：紧急脱离两连修（191 → 193/200）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 10 | spec_0164 sim19：rust 多脱离一次/多抽一次随机（mti py=8 ru=9） | py 的 pending escape 存 `user_name`（字符串），urgent 结算按【当前在场精灵名字】比对（battle_mechanics.py:458-461）；rust 存下标，双重脱离时新登场者下标恰好相等 → 误结算 | `PendingEscape` 增加 `user_name`，`phase_turn_end` 结算按名字比对 |
| 11 | 同上，名字比对后仍多结算一次 | py 的 urgent pending 结算在 turn_end 观察者循环内、且被 `has_candidates("turn_end", id(sprite))` 的 continue 挡住（battle.py:1834-1843）——双方都无 turn_end 候选时【不结算】；rust 无此门 | `observers.rs` 增加 `has_candidates_for(trigger, owner)`，`phase_turn_end` 循环开头照 py continue |

调试工具新增：`ROCO_DBG_CHOICE`（py `random.choice` 落点）、`ROCO_DBG_TICK`
（py `SkillResolver.turn_end` 的 tick 扫描输入）、rust `handle_escape` /
escape-pending / escape-immediate 的 `ROCO_DEBUG_DMG` 打印。

**spec_0042 的现状（下一个要修的）**：sim84 turn3，声波缇塔 hp py=158
rust=148（rust 多 10 点伤害）。已确认：两次攻击（36/83）两边一致；
turn_end 时 py 侧该精灵有 `灼烧(1层,0.02)` 与 `中毒(2层,0.03)`（tick-scan
实拍），rust 两条 tick 都打了。下一步：给 rust 的 `[rust tick]` 打印补上
最终伤害值，与 py 事件串的 `灼烧-14HP`/中毒 tick 逐一比对——重点查
tick 的元素克制乘数（毒/火 vs 声波缇塔属性）与 max_hp 取值。

### 阶段4-3 续三：修复 oracle 自身 bug（193 → 194/200）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 12 | spec_0042 sim84：rust 的中毒 tick 比 py 多 10（py 只 tick 灼烧） | **py oracle bug（经用户确认按 bug 修，不复刻）**：`SkillResolver.turn_end` 直接迭代 `active_effects`，灼烧衰减到 0 层时 `update_stacks` 把效果从列表移除，Python 迭代器顺势跳过紧随其后的中毒 → 中毒整轮不 tick；rust 是快照迭代（收集名字后逐个 tick），行为才是修复后的语义 | `resolver.py` 改为 `for ae in list(active):`（快照迭代）+注释；rust 无需改 |

**spec_0141 的硬数据（下一步从这里下手）**：sim108 turn3 伤害链比对——
同一批 `power=40 物攻` 打击，早段两边一致（atk=192 a_st=0 → 33），
**末段分叉：py `atk=176 a_st=0.2` → 37（adjust ×4 → 148），rust
`atk=192 a_st=0.2` → 40（×4 → 160）**。即「晕晕鸡 物攻+20%」生效后，
py 的 `ctx.atk_self`（= `Sprite.atk_with_modifiers` = round(base ×
(1 + _modifiers["atk"]))，sprite.py:469）变成了 176，rust 仍是 192。
根因方向：`物攻+20%`（stat_stage atk +2）在 py 里同时动了
`_modifiers["atk"]`（或某效果把 atk 修正从 0.2 改到 0.1 / base 192→176），
rust 的 snapshot atk_self 没有跟随。下一步：在两边分别打印
`_modifiers["atk"]`/initial_stats 与 rust `atk_self` 的来源
（snapshot.rs 的 atk_self 计算），对齐 `stat_stage` → `_modifiers` 的
联动；注意 digest 里晕晕鸡还挂着 `["atk","permanent",4]`（steps=4）。
已核实：rust `compute_stat_cache`（battle_state.rs:1000）公式与 py
`_rebuild_stat_cache`（sprite.py:452）逐行一致；失效点 replayer.rs:813
与 py:592 名单一致——所以嫌疑集中在 **stat_stage（StatChange）路径是否
也写 `_modifiers["atk"]` 并失效缓存**：py 的写入点在 replayer 的
StatChange 处理（replayer.py ~640 区域 sync 后），rust 的
`apply_stat_change` 是否同样写 `modifiers["atk"]` + `invalidate`，
一处一处对。

**0141 再收窄（ROCO_DBG_MOD 实拍）**：py 全程【没有】`stat=atk` 的
ModifierInjection → 176/192 的差异不在修正值，而在**晕晕鸡的 atk 基底
initial_stats 本身**：py 176 vs rust 192。晕晕鸡挂着
`["萌化","persistent",1]`——萌化退化会换种族并重算 initial_stats
（py `_apply_moe_via_replayer`，replayer.py:846）。下一步：比对
`apply_moe` 的退化种族表与 initial_stats 重算（含 nature/IV）在两边的
差异；`dbg_replay_sim.py 141 108` + 双侧 dump 晕晕鸡的 initial_stats 即可定位。

剩余 6 例：0064（effects 仅顺序不同）、0067/0101（hp 差，同 0042 法）、
0068（power_mult 翻倍）、0141（如上）、0152（技能级 combo py=0.0 rust=1.0）。

**0152 已修（oracle 第 3 处）**：`_make_ctx`/`_ctx_team_kwargs` 从不传
`abnormal_stacks_battle` → `of:"battle"` 查询恒 0（月光合奏等技能的
全场萌化层数加成失效）。已让 `_ctx_team_kwargs` 现场合计双方在场精灵的
异常层数并传入（对齐 rust snapshot.rs:299-305 只算双方在场）。195/200。
**剩余 4 例**：0064（effects 仅顺序不同）、0067/0101（hp 差）、
0068（`skills[].modifiers.skill.山火.power_mult` py=4.0 rust=8.0，
mult_mod 被应用了两次——用 `[py mod]`+`[rust pre-adjust mod]` 打印
对比该注入的出现次数与 mode）。

**0068 现场**：山火（data/skills/山火.json）=
`mult_mod target=skill_off_0 scope=permanent attr=power_mult value=2
mode=multiply`（每次使用威力倍率×2 永久生效）。py=4（×2 两次）、
rust=8（×2 三次）——rust 多一次乘算。嫌疑：counter-injection 或
观察器重放把同一 journal 重复应用一次（参考 #7 同类）；或
持久键 `skill.山火.power_mult` 的 add/multiply 记账在两侧
restore 语义不同。排查：`dbg_replay_sim.py 68 161` +
`ROCO_DBG_MOD`+`ROCO_DEBUG_DMG`，数 `[py mod] stat=power_mult` 与
`[rust skill-mod write] stat=power_mult` 的出现次数与位置。
**实测**：注入数一致（各 8 次 ×2 multiply，rust 均 sid=7）——差异不在
注入次数，在**持久键记账**：py replayer.py:545-555 仅当
`self._self_skill.skill` 真值才更新 `skill.<名>.power_mult` 键，每回合
`_load_permanent_skill_mods_for_sprite` 按键值还原技能修正 → py 的键
停在 4（部分乘算被还原语义"丢掉"）；rust `self_skill_id` 存在即记账 →
键一直增长到 8。下一步：打印两侧每次 power_mult 注入时
`_self_skill.skill`/`self_skill_id` 的有无，确认 py 哪些次没记账，
再决定 rust 对齐哪一侧语义（transform 场景下 py 的 _self_skill 可能为
None——与 #7 同族但方向相反）。
**0068 再收窄**：py 注入时 `_self_skill=BattleSkill`、`.skill=Skill(id=10605
山火)` 恒为真值（4/4 都会记账）——"py 不记账"假设排除。实际差异：
py 终值 4、rust 终值 8（同样的 4 次 ×2 注入）→ **rust 多一次有效乘算**。
最大嫌疑：每回合持久键还原语义——py `_load_permanent_skill_mods_for_sprite`
（battle.py:92-118）每回合把 `skill._modifiers[power_mult]` **重置为键值**
（先 pop 再写），rust 的 `load_permanent_skill_mods` 是否做了同样的
"重置"还是"累加"；以及 rust `apply_modifier` 的 skill 写入在
permanent 键之外的技能本体上是否多乘了一次（对照 replayer.rs:752-780
的 bs.modifiers 写入与键写入的两段）。用
`dbg_replay_sim.py 68 161` + `ROCO_DBG_MOD` 打每次注入后两侧的
`skill._modifiers[power_mult]`/键值即可定位。

### 阶段4-3 续四：回滚清缓存（对齐 py restore）+ 0141 最新证据

**已落地**：`BattleState::invalidate_all_stat_caches()`（battle_state.rs，
impl BattleState 块）+ mcts.rs 两处回滚点、roco-py `py_fixed_turns` 每轮回滚
后调用——对齐 py `restore_mutable_state` 尾部的全量失效（battle.py:493-497）。
引擎门 200/200 保持（完整对局无回滚，不受影响）。

**0141 现状**：加回滚失效后 sim108 仍在 step2 分叉（active_index py=0
rust=1）。给 `compute_stat_cache` 加了 population 打印（`[rust stat-cache]`），
py 侧 `_rebuild_stat_cache` 打印（`ROCO_DBG_TRANSFORM`）。**关键发现：整个
108+ 轮重放里，晕晕鸡的属性缓存在 rust 侧零次重算**（其余精灵都有多次），
即它从未进入"失效→重算"循环——要么其缓存从未被失效，要么 rust 读取
晕晕鸡四维的路径没走 `compute_stat_cache`。下一步（按序）：
1. 确认 rust 读取晕晕鸡 atk 的实际调用点（ctx.atk_self 对 B 为 self 时
   走 snapshot.rs:353 `stat_with_modifiers`）；打印该缓存是否曾为 None；
2. 检查 `transform`（battle_state.rs:529）是否应该失效（当前不失效，与 py
   一致）；若 rust 在退化前以旧形态身份算过一次缓存且回滚克隆链一直携带，
   需要定位是哪次克隆绕过了 `invalidate_all_stat_caches`；
3. py 的 176 已实锤为退化后 initial_stats（transform 打印两边一致都是 176），
   所以 rust 的 192 一定是旧形态缓存——问题只剩"为什么没被失效/重算"。
4. **决定性线索**：晕晕鸡=绅士鸡萌化退化后的新名字（同一 Sprite 对象，
   transform 改名）。rust 的 `[rust stat-cache]` 日志里有大量「绅士鸡」重算、
   **零次「晕晕鸡」重算** → rust 退化后从未重算（沿用绅士鸡的 192）；
   py 重算出了 176。下一手：找 py 在 transform 之后、读取之前的那次失效
   （`_apply_abnormal_change` 的 `_invalidate_battle_ctx_cache` 只清 ctx 缓存，
   sprite stat 缓存另有来源——怀疑 `transform`/`apply_moe` 链上某处调了
   `_invalidate_stat_cache`，或 py 的首次读取恰好发生在退化后）。
   验证方法：py 侧在 `_rebuild_stat_cache` 打印里带 `self.name`，看退化后
   第一次以「晕晕鸡」名字重算发生在哪两个打印之间；rust 侧对应补
   `[rust stat-cache]` 退化后读取路径。**注意两侧 transform 后的
   initial_stats 完全一致（176）**，分歧纯粹是缓存生命周期。

**0141 最新现场（counter-ctx 打印）**：退化发生在 A 的反弹 journal 重放中
（transform → 晕晕鸡）。A 执行期间的应对注入 ctx（self=晕晕鸡）读到
`stages_atk=Some(2)`、buffs=[("atk","permanent",2)]——**正确**；但随后
B 自己的主执行 ctx：`atk_self=192, a_st=0`——atk 基底（旧缓存）与阶段
（丢了 +2）双双不对。即：**transform 之后，A 视角能读到晕晕鸡的正确
阶段，B 主 ctx 却读不到，且 atk 缓存未重算**。下一步：
1. 对比两条 ctx 构建路径的差异：注入 ctx 是 `build_ctx(opts)` 直构
   （self_skill_record），B 主 ctx 走 ExecParams/snapshot 的另一条路——
   查两条路读 `sprite_effects_summary` / `stat_with_modifiers` 的精灵
   定位（team/self_idx）是否在 transform 改名/换种族后错位；
2. 重点查 rust `apply_moe`/`sync_moe_status_effect` 之后 sprite 的
   active_effects 是否被错误重建（buff permanent 2 是否还在）；
3. `stat_cache` 在 B 主 ctx 读到 192：确认 B 主 ctx 用的 Sprite 对象
   与注入 ctx 是同一个（是否 ExecParams 缓存了旧引用/旧克隆）。

**0141 收窄（最终）**：stage 没问题（rust 注入 ctx 也读到 stages_atk=2），
**唯一差异 = 注入点的 atk_self 缓存新鲜度**（py 重算=176，rust 旧=192）。
反弹的 journal 恰好 2 条：[萌化+1, 对晕晕鸡的 atk 变更]——py 侧该 atk
变更的 replay 路径（`_apply_modifier` stat=atk → :592 `_invalidate_stat_cache`，
或 `_apply_stat_change`→`_sync_stat_buff_effect`）很可能顺带把缓存置脏；
rust 的对应 replay（`apply_stat_change`/`apply_modifier`）是否同样失效——
对齐即可修。验证：两侧打印该第二条 mutation 的类型与 stat。
已核实：rust `apply_stat_change`/py `_apply_stat_change` 都【不】失效
属性缓存（stages 不参与该缓存）——所以"py 因 atk 变更置脏"的假设排除。
**实测计数（决定性）**：py 重算 = 绅士鸡 165 次 + **晕晕鸡 4 次**（退化后、
stages 已带 atk:2）；rust = 绅士鸡 128 次 + 晕晕鸡 0 次。即 py 在部分仿真
轮内、transform 之后确有「以晕晕鸡名字」的重算（dirty 被置位的来源未明），
rust 完全没有。两侧 snapshot 读取来源一致（都走缓存属性）、transform 都
不失效——**py 的置脏来源是下一个要找的东西**（候选：`clear_effects`
sprite.py:341-343、某条 ModifierInjection、萌化链上的其它调用；grep
`_invalidate_stat_cache` 已知仅 replayer:592/battle:497/sprite:343 三处，
重点排查 sprite.py:343 `clear_effects` 在该回合是否被调到——比如
transform 之后某效果的 scope 清理）。找到后把同样的置脏时机补进 rust
（transform 或对应路径），0141 即合。

**实验环境坑（重要）+ 已实拍事实**：dbg 工具的 py 补丁统一由 `_compare`
顶部的 `ROCO_DBG_DMG` 总开关安装，分项开关只控打印；Bash 工具每次调用是
新 PowerShell 进程，env 不跨调用保留——**每次必须三变量
（ROCO_DBG_TRANSFORM + ROCO_DBG_DMG + ROCO_DEBUG_DMG）同一条命令设齐**，
否则 py 打印静默缺失、计数对比作废（曾据此误判"py 0 次重算"；正确计数
py=绅士鸡165+晕晕鸡4、rust=绅士鸡128+晕晕鸡0）。已实拍：transform 发生在
仿真轮最后一个回合（反弹的萌化），之后没有回合末 clear_effects——py 的
4 次晕晕鸡重算必来自 transform 之后、读取之前的某次置脏。复跑后对
这 4 次打印加 `traceback.print_stack()` 即可定位置脏调用链，rust 对齐。

**0141 反伤现场（ROCO_DEBUG_DMG 实拍，按序）**：`A 反弹 → [rust transform
→ 晕晕鸡 atk=176] → B 闪燃 → 反伤`。rust 反伤 `atk=192, a_st=0 → 33`（×4
power_mult=132/160），py `atk=176, a_st=0.2 → 37`（×4=148）。**两处输入
都不一致**：atk_self（192=绅士鸡旧缓存 vs 176=退化后）与 atk 阶段
（0 vs 0.2，py 此刻晕晕鸡已有 atk+2 阶段——来源是 digest 的
`["atk","permanent",4]`？py 提取为 0.2 而 rust 为 0，需查
`_extract_stat_stages`/`sprite_effects_summary` 对 permanent scope
显示效果的计数差异）。下一步：比对两侧该注入 ctx 的
stat_stages_self 与 atk_self 来源；注意反伤走 counter-injection ctx
（self_skill_record=闪燃，#5 修复引入），若 py 的反伤实际不走注入而走
post_counter 观察器载荷，则 ctx 来源不同——先确认 37/40 的产生路径。

**本轮对拍结果**

- MCTS 桩对拍 specs 1..200（sims=200）：**191/200**（本轮起点 159/200；
  修 #5 → 167，修 #6 → 183，修 #7 → 187，修 #9 → 191）。
- 阶段3 整局门 200/200、pytest 1656 passed 保持（每次改动后都复跑）。
- 剩余 9 例（首分歧）：0042/0067/0101（hp 差）、0124/0141/0164（`active_index`，
  均涉紧急脱离）、0152（技能级 `combo` py=0.0 rust=1.0）、0068（`power_mult`
  翻倍）、0064（`effects` **顺序**差：py `[atk,物攻,def,…]` 在前、`虫群鼓舞`
  在后，rust 相反，效果集合相同）。

**继续追踪的建议**

1. 先修 #7 后复跑，确认 combo 簇（0040/0044/0175/0152）是否一并消失；
2. hp 差类优先看「同一步内 py/rust 伤害链」：`ROCO_DBG_DMG` + `ROCO_DBG_MOD`
   抓同一回合的 op_hit/calc/adjust 三段数值，定位是公式输入还是结算范围差异；
3. `active_index` 类用 `dbg_replay_sim` 复现到具体回合后，盯
   `check_faint_interrupt` / `handle_escape` / `resolve_switch` 的调用序列；
4. 0064 的 effects 顺序差若要修，需比对 `_sync_stat_buff_effect` /
   `_apply_to_matching_skills` 等显示效果创建点与 headless 门的覆盖范围。

**0101 最终现场（已实锤）**：泥巴喷射 effects=[]（无任何效果），rust 序列
`pre_calc 观察者(cond false) → op_hit calc 61 → [rust main replay] →
[rust dmg] amount=122`（61×2）
——翻倍不在技能效果/adjust（无 pre-adjust 行），
而是隐含 hit 的 Damage 在 main replay 入账时已是 122 或入账两次。
下一步：查 execute_skill_vm 的 journal 构建（execute_with_skill_header →
adjust_damage_in_journal → 隐含 hit 注入是否重复）与 [rust dmg] 打印点
（Replayer apply_damage）是否对同一 journal 执行两次。0067 大概率同根因。
剩余：0064（effects 顺序）、0067/0101（本条）、（0068/0141/0152 已修）。

## 阶段4-3 续五：199/200（0101/0067 修复；0064 实锤并修复）✅ 200/200

### #16 combo_mult 对无注入普攻静默失效（py oracle bug → 修 py）
**现象**：0101 sim15 step1，泥巴喷射（effects=[]）rust 61×2=122、py 61。
两侧 `_modifiers["combo_mult"]` 均为 1.0（暴风守护 10706「行动时连击数
+100%」battlefield 注入），状态无分歧，纯 adjust 行为差。
**机制**：py `modifiers.py:_collect_modifiers_from_entries` 顶部
`if not entries: return {}` 快速路径把 ctx 播种的 `combo_mult_self`
（sprite 级跨技能持久修正）整个丢弃——只有当本技能 journal **恰好还有**
其他 ModifierInjection 时 +100% 才生效，纯伤害技能静默失效。
snapshot.py 注释明示设计意图「combo_mult 留给 adjust_damage 乘入」，
vm ops 不消费 combo_mult_self，唯一消费点就是 adjust_damage ⇒ py bug。
**修复**：`modifiers.py` 空 entries 分支改为 combo_mult>0 时返回
`{"combo_base": max(1, ctx.combo_self), "combo_mult": ctx.combo_mult_self}`
（其余种子值中性可安全跳过；=0 时维持 `{}` 快速路径）。rust 无需改
（rust adjust 本就应用 combo_mult_self，与修复后 py 语义一致）。
0101/0067 同时收敛，引擎门 200/200、pytest 1656 全绿。

### #17 restore_mutable_state 效果顺序污染跨 sim 泄漏（py oracle bug → 修 py）
**现象**：0064 sim2 step0，A 方待席花魁蜂后 effects 顺序：py
`[atk,物攻,def,物防,speed,速度,虫群鼓舞]` vs rust 构造序 `[虫群鼓舞,…]`。
**机制**：sim1 A 换宠（动作 10）入场时 `clear_effects(scope=battlefield)`
删除旧虫群鼓舞 ObserverEffect、`load_for_sprite` 新建追加尾部（当局内
两侧一致）；但 py `battle.py:restore_mutable_state` 效果恢复段注释写
「先清空再按保存顺序重建」，实现却是「按 id 删新增 + 缺失 append 末尾」
——被替换过的旧对象 id 不在当前列表，恢复时被 append 到末尾，顺序污染
在 sim2+ 全部泄漏（探针 dbg_probe64.py 实锤：sim1 后每局 step0 都是错序）。
rust 回滚 = 整体克隆 + invalidate_all_stat_caches，顺序精确恢复。
**修复**：`sim/battle.py` restore 段改为
`sprite.active_effects = [snap[0] for snap in saved_effects]`
先整体重建（等价旧「删新增」），再逐个恢复属性，删除尾部 append 兜底。
**教训**：save/restore 注释与实现不符时信实现；「append if missing」
兜底是顺序破坏源。探针工具 `native/tools/dbg_probe64.py`（钩
load/unload_for_sprite、clear/add/remove_effect、dispatch_entry/leave）。

**复现/验证命令**（均在项目根，PowerShell）：
- `$env:ROCO_DBG_DMG='1'; $env:ROCO_DBG_MOD='1'; $env:ROCO_DEBUG_DMG='1';`
  `.\env\python.exe native\tools\dbg_replay_sim.py 101 15`（修前 DIFF
  hp py=349 rust=288，修后一致）
- `.\env\python.exe native\tools\dbg_probe64.py 64`（看 step0 顺序）
- `.\env\python.exe native\tools\mcts_gate.py`（报告写
  native\tools\_gate_last.txt，UTF-8 直读避免管道乱码）

## 阶段4-4：批量叶评估路径（leaf_batch_size>1）✅ batch=1/16 双 200/200

**py 批量分支语义**（mcts_search batch_leaf_eval 分支，逐行镜像）：
1. 收集期每 sim：save → PUCT 下降（step_battle 推进）→ 非终局叶只
   `visit_count += 1`（正序 path + 叶自身），value/prior 压入 pending；
   终局叶立即完整回传（reversed path，counts+value）；每叶 finally restore。
2. 整批收集完：一次 `batch_eval(pending_states, pending_masks)` +
   一次 `opponent_agent.evaluate_policy_batch`（对手无合法动作的叶在
   收集期就写 `opp_policy = 归一化掩码`，不进批次）。
3. 回填期：`node.valid_actions/prior/children 空壳` 都在此时才设——
   **批内后选的叶子看不到批内先到的评估结果**，树形状与单仿真路径
   不同（py 语义即如此，batch=16 ≠ batch=1，勿「优化」成等价）。
4. py `num_simulations = 0`：批量分支跑完整局搜索，串行主循环跳过。

**rust 实现**（mcts.rs）：
- `SearchEvaluator::evaluate_batch` 默认逐个 evaluate（与 py
  UniformStub.evaluate_batch 逐状态堆叠同义）；真实网络后端在 4-6 覆写。
- selection 下降抽成 `selection_descent`（单仿真/批量共用，含终端守卫、
  失败返回 (None, path)）。
- `mcts_search_batched`：`Pending{state 快照, sim_valid, node, path,
  opp_mask}`；A 视角批 + B 视角批（opp_slots 稀疏索引，与 py opp_idx
  对位）；回填期先设字段再 `arena.push` 子壳（避开 nodes 借用冲突）。
- 叶状态快照用 `state.clone()`（py encode_battle_state 在收集期编码的
  对应物）；叶掩码回填期由快照重算（与收集期 determinism 一致）。
- 绑定层 `leaf_batch_size` 早已透传（roco-py lib.rs:289）；
  mcts_gate 加 `--batch N`（两侧同走批量路径，报告标注 batch）。

**验证**：`mcts_gate.py --batch 1`（重构回归）与 `--batch 16`（新路径）
各 200 spec 全过；pytest 1656。训练默认 `DEFAULT_MCTS_LEAF_BATCH_SIZE=16`
（train.py:70）即此路径。吞吐收益在 4-6 接真网络后体现（stub 逐个
evaluate 无批收益，只验语义）。

## 阶段4-5a：编码器实体数组移植 ✅（ast 归 4-5b）

**先跑的测量**（`bench_encode_share.py` + benchmark_mcts，决策依据）：
encode 在自对弈热路径 612 次/300 sims（≈2.0 次/模拟：叶+对手策略），
~190µs/次（6 精灵训练阵容，15 回合后 203µs），占搜索 ~15-20%
（cProfile 13.5%，对小函数偏保守）；不移植则每叶还要跨 PyO3 物化整棵
状态（进程内快照已 57.5µs、全状态遍历 29.7µs，跨语言 3-10 倍），比
encode 本身更贵。torch 批量（batch=16）仅 9 次/100 sims、~6.8ms/次
——4-6 吞吐地板在 torch 侧，需大 batch/多线程压。

**移植范围**（4-5a = 每叶热路径的 8 个实体数组；roco-core/src/encoder.rs）：
sprite_stats(12,7)/sprite_elements(12,2)/sprite_states(12,105)/
skill_stats(10,2)/skill_elements(10,2)/skill_states(10,9)/
global_stats(15)/global_elements(1)。ast_tokens/ast_values（384×2，
按技能名缓存的 tokenizer）4-5b 接入，当前 rust 恒 0、门跳过。

**镜像要点（都是"照抄 py 怪癖"而非修复）**：
1. 元素 ID = ELEMENT_ORDER 索引+1（0=PAD）；天气 ID = 索引无 +1（none=0）。
2. **印记特征 global_stats[4..8] 恒 0**：py `_classify_marks_global` 读
   `getattr(g,'marks',{})` 而 Globals 根本没有 marks 属性——py 里就是
   死特征，训练权重基于恒 0，rust 必须同样置 0，"修复"会破坏分布。
3. initial_stats 缺省 0（encoder 的 .get(key,0)），不是属性缓存的缺省
   100——两处默认值不同。
4. stages/abnormals/charging 由 active_effects 现算 = `_rebuild_effects_cache`
   语义（charging 看 StateEffect 的 state_type/name，不看 sprite._charging）。
5. `int(modifier)` 向零截断 = rust `as i64`；py round = py_round。
6. 技能属性用 BattleSkill 既有方法（power/energy_cost/element/skill_type/
   combo，含 nullified/replaced_by/overrides 语义）。

**对拍**：`gate_encoder.py`（RuleAgent 同轨迹，逐回合、双视角 A/B、
逐位比较 8 数组；报告 _gate_encoder_last.txt）。词表已 dump：
`roco-core/src/vocab_ids.json`（410 项，py VOCAB_TO_ID 静态表导出，
4-5b 的 token id 查找用）。

**首跑 170/200，30 失败全部同一模式**：`skill_states[*][7]（combo 裸值）
py=1/2 rust=-1`。根因 = py 双技能加载器 combo 缺省值不一致：
- `skill.py:68 Skill.load`：`combo=data.get('combo', -1)`（门 Harness
  battle_from_spec / 初始技能走它）
- `factory.py:116 SimFactory._build_skill_list`：`combo=data.get('combo', 1)`
  （运行时愿力血脉替换/进化技能走它——训练管线实际语义）
字段注释（skill.py:35）：`-1=不参与连击，1=单次，2+=多次`。普通攻击
技能（泥巴喷射/金属噪音等 JSON 无 combo 键）语义上是"单次"=1，
factory 的缺省 1 才与字段约定一致；-1 应保留给 JSON 里显式写 -1 的
技能（如暴风守护）。引擎未暴露矛盾：ctx `combo_self=max(1, ...)` 归一。
**待办（4-5b 一并）**：py 侧统一 `Skill.load` 缺省为 1（skill.py:68），
rust `skills.rs default_combo()` 同步改 1，重跑引擎门/MCTS 门/编码门
确认三绿；然后接 AST tokenizer。

## 阶段4-5b：AST tokenizer 移植 ✅ 编码门 200/200

**组合修复**：`Skill.load` 缺省 combo -1→1（skill.py:68，与
factory.py:116 统一）+ rust `default_combo()` 同步 1 → 编码门 170/200
→ 200/200，引擎门/MCTS 门/pytest 全部无回归。

**rust 侧新增**（encoder.rs 续）：
- `vocab_ids.json`（include_str + OnceLock）+ `_token_id`（缺省 <UNK>）
- `ALIAS_MAP`/upper 别名表、`ENUM_PREFIX_GROUPS`（21 组前缀按 py 顺序）
- `encode_value`/`parse_query`/`parse_cond`/`tokenize_effect_dfs` 逐行镜像
- `skill_effect_ids`：data/skills/{name}.json 惰性读盘缓存（py 同语义）
- `collect_ast_token_ids`：10 技能槽（SEP/EMPTY/SEALED(1.0|0.5)/ACTIVE +
  按技能名展平）+ 场上精灵 ObserverEffect 逐个 token 化；SAFE_MARGIN=50

**serde_json 开 preserve_order**（workspace Cargo.toml）：py dict 按插入
序迭代，token 序列依赖 JSON 对象键序；默认 BTreeMap 字母序会错位。
开启后引擎门/MCTS 门复验无回归（引擎按键名访问，不依赖迭代序）。

**三个 py 侧行为真相（rust 逐条镜像）**：
1. **then 树烘焙**（observer.py `_index`）：注册时 `_bake_inject_source`
   恒注入 source；`_bake_inject_scope` 仅当 listen ∩ POST_EVENT_TRIGGERS
   非空且 then[0] 为 dict 时注入——原地改共享 dict，编码器（经
   effect_factory 的 then 引用）看到烘焙后形态。listen 缺省时由
   `infer_triggers(cond)` 推断（trait_loader/compiler 语义）。pre 系
   （如 pre_modifier）不烘 scope。
2. **cond 真值**：`"cond": "always"` 字符串判真——py `if eff.cond:` 对
   非空字符串为真；rust 首版只认非空 dict 导致漏掉（spec_0090 类）。
3. **listen frozenset 哈希序（py bug → 修 py）**：py 编码器
   `list(eff.listen)` 顺序随 PYTHONHASHSEED 跨进程漂移，训练 token 序列
   不可复现——py 编码器两处改为 `sorted(eff.listen)`，rust 同步按字典序。

**验证**：编码门 200/200（双视角、逐回合、10 数组逐位含 ast）；引擎门
200/200（preserve_order 回归）；MCTS 门 batch=16 200/200；pytest 1656。
**量化**（bench_encode_share.py）：encode ~190µs/次、2 次/模拟、占
搜索 15-20%，是训练热路径最大可移植开销——本阶段后归零（rust 侧
直读 struct + 定长数组，py 侧仅剩 torch 批量推理边界）。

## 阶段4-6：评估器回调协议 ✅ 一致性门绿；性能门受 torch 制约（见下）

**已落地**（roco-py lib.rs + native/tools/gate_phase4.py）：
- `PyEvaluator`：包装 py `evaluate_batch(states, masks) -> (values, priors)`
  对象（TorchEvaluator/QueuePolicyEvaluator/BatchedInferenceServer 统一
  签名——批量推理协议零改动）。perspective A→主模型、B→对手模型
  （缺省回退 a）。rust 侧 `encode_battle_state` → numpy 数组（rust-numpy
  0.24，numpy crate 与 pyo3 0.24 配套）→ list[dict[str, np.ndarray]] 传
  py；py 侧薄适配 `_RustEvalAdapter` 把返回值转 plain list（pyo3 直接抽
  np.float32 不可靠）。
- `py_mcts_search(spec_json, cfg_json, evaluator_a=None, evaluator_b=None)`
  ：与 py_mcts_stub 同输出（probs_bits/counts/trace/digest/RNG 头），
  evaluator 缺省回退 stub。
- 一致性门：真实模型（checkpoints/exp16/model_rl.pt，
  ModularBattleNet.load）下 py 路径 vs rust 路径 probs_bits/counts/双
  RNG 头逐位一致（specs×双路径）。

**性能实测与瓶颈定位**（CPU 单线程，exp16 模型，spec_0001）：
- 纯 rust MCTS（stub）：200 sims = 40ms ≈ **4962 sims/s**（引擎侧移植收益满额）
- rust + 真实 torch 回调：1.74s/搜索 ≈ 8.7ms/sim —— **torch 推理占 ~97%**
- py 路径同模型：1.90s/搜索（torch ≈ 1.7s + py 引擎/编码 ≈ 0.2s）
- 加速比：24 sims 1.39x / 200 sims batch32 1.09x
**结论**：5x 门卡在 torch 而非引擎——transformer×384 AST 的单批推理
成本（~百 ms/批@1线程）两路径同付，rust 只能省掉非 torch 部分
（py 里也仅 ~20%）。可选杠杆（训练基础设施侧，非移植侧）：
torch 多线程/GPU、replacement 候选批量评估、AST 截断/蒸馏、
BatchedInferenceServer 满批率。门达标策略需按此重估。

**拓扑忠实复测**（bench_worker_topology.py：BatchedInferenceServer
batch128/5ms + QueuePolicyEvaluator 队列 + torch 默认 14 线程，
即 train.py 的真实推理架构）：
- py worker 2423 ms/move；rust worker 1460 ms/move（含 spec 重建，
  保守值——真实 rust worker 状态驻留 rust，无重建）→ **1.66x**
- 单 worker 拓扑下 server 攒不满批（≤16/批），torch 利用率低，
  双方都被推高；4-worker 真实训练里跨 worker 攒批会同时改善两侧，
  rust worker 的最终比值需等 rust 自对弈 worker 集成后实测（阶段5）。
- 定性结论：移植侧工作（4-1..4-6）已完成且逐位一致；纯引擎
  4962 sims/s vs py 引擎+编码 ~500 sims/s；训练吞吐的下一级杠杆
  是推理服务侧（GPU/满批率/候选批量/AST 截断），与移植正交。

## 阶段5：rust 自对弈 worker（进行中）

**已落地**：
- `roco-core/src/selfplay.rs`：`play_game`（_play_one_rl_battle 主循环
  镜像：双侧 MCTSAgent 决策 + 记录 + execute_turn_fixed + EvalRepl
  网络换人 + battle_outcome）；B 侧决策 = 克隆 state 交换 players[0]/[1]
  的 swap 视图上搜索（py swap 等价；globals/mark 键不换与 py 一致）。
- `turn.rs`：`state_from_spec_selfplay`（无 RuleAgent 选首发——py 自对弈
  active=队首；曾有 5x 内因智能选首发导致 B 队槽位旋转的分歧，已修）
  + `begin_turn`/`resolve_turn`（决策发生在回合内：py execute_turn 先
  turn+=1+清理+回合开始，agent 才搜索/编码——entry_age/每回合技能修正
  的决策时点差异曾致样本 0 分歧，已修）。
- `roco-py`：`py_selfplay_game(spec, cfg, evaluator_a, evaluator_b)` 返回
  {states(list[dict of np]), P, M, v, winner, turns, outcome_a}。
- `gate_phase5.py`：一致性（逐样本 P/M/v/状态）+ 速度测试（server+队列
  真实拓扑）。

**现状**：
- 一致性：样本 0（A 侧决策）逐位一致；**样本 1（B 侧 swap 搜索）起
  分叉**——访问数差 1-2 次/动作，疑似 py swapped 搜索内某单模拟路径
  （终局判定或替换候选评估）与 rust 有别；`_PlayerSwappedAgent.team="A"`
  已核对与 rust 视图方案一致，下一嫌疑是 `_fire_pre_event/_fire_post_event`
  的 listen 路由在 swap 视图下的 owner 归属。需专项排查。
- 速度测试已跑通（gate_phase5 --skip-consistency）：48 sims 真实拓扑
  （server+队列+torch 14 线程）单 worker：py 1.7 samples/s vs rust
  2.2 samples/s ≈ 1.27x——因 B 侧语义未对齐、双方轨迹不同长，该数字
  为初步值；语义对齐后需复测才算验收。

### 阶段5a 排查收尾（同日追加）
- **拼接错位澄清**（dbg_moves_cmp.py）：py 数据集 = a.history+b.history 串联，
  py a_len=21 故拼接索引 21 是 B 首条记录，rust a_len=52 拼接索引 21 是
  A 第 22 条——此前的样本 21 差异是错位比较。按侧对拍：**A 流 21/21、
  B 流 21/21 全部逐位一致**（py 整局 42 样本全匹配）；采样序列 42/42
  一致（含道具循环与重选）。
- **剩余唯一分歧 = 终局判定**：py 回合 19 A 海枝枝中毒 -27HP 死亡、替补
  全灭 → 立即判负（battle_mechanics.py:207，lives=2>0 也判），winner=B
  结束；rust 同一回合未触发该分支，继续打到 50 回合 A 胜（终局 lives
  py[2,4] vs rust[2,1]）。下一嫌疑：rust 回合末中毒 tick 伤害或
  check_faint_interrupt 的 no-replacement 分支在该网络动作局面未触发
  （引擎门 RuleAgent 局面未覆盖此路径）。
- **速度测试数字**（gate_phase5 --skip-consistency，48 sims，server+队列
  真实拓扑，torch 14 线程）：py 17.75s/31 样本=1.7 samples/s；rust
  12.87s/21 样本=1.6 samples/s；per-game 1.38x——因终局判定分叉、双方
  对局长度不同（19 vs 50 回合），为初步值；终局判定修复后须复测。
- 修复累计（本轮）：回合内决策时点（begin_turn/resolve_turn）、
  state_from_spec_selfplay（无选首发）、道具循环（_select_action 镜像）、
  numpy float32 成对求和（np_sum_f32）。
- HP 证据（spec_0001 样本 41，B 视角决策时）：py 海枝枝(team[2]→slot8)
  372/372 满血，但回合 19 事件链为 中毒-27HP → 力竭 → 替补全灭判负
  ——py 的力竭并非该 tick 直接打死（345>0），而是 faint_check 判定
  海枝枝 HP 已≤0：说明 py 回合 12-18 间海枝枝实际 HP 远低于编码值或
  中毒层数/结算路径与 rust 不同。下一步：双侧 dump 回合 12-19 海枝枝
  的 HP/中毒层数逐回合轨迹（dbg_item_trace 扩展），对比 tick 公式
  （stacks×base、tick_per_stack、decay_on_tick）与层数累加路径。
  注：spec_0001 中 A 还有 超级糖果（自体萌化+1 层）等自伤类效果，
  萌化层与中毒层的回合末结算顺序是重点。
- **回合 19 结算的精确分歧**（spec_0001）：海枝枝(team[2])中毒 -27HP 死亡
  两侧一致。py：check_faint → choose_replacement 返回替补 → 『替补已死』
  → lives 3→2 → 立即判负 winner=B，active 停在 2。rust：EvalRepl 返回
  team[1] 棋齐垒（已死 0/414！），active 切到死精灵继续打（A lives 未扣，
  终局 4），B 之后连损 3 命到 1 → 50 回合 A 胜。
  待查：EvalRepl.choose_replacement 兜底步行/argmax 为何选中死亡替补
  （replacement_mask 的 alive 集、mask 0 槽位的归一化概率、以及
  check_faint_interrupt:2079 的 is_fainted 守卫为何未拦下）。
  修复判据：rust 与 py 同样走『替补已死 → 立即判负』分支。
## 阶段5a/5b 收官（2026-09-18）：gate_phase5 一致性 PASS + 速度门结论

### 一致性（铁律门）✅
- `gate_phase5.py`（不带 --skip-consistency）：**一致性 PASS — 60 回合 / 124 样本逐位一致**，
  py/rust 双方整局 digest、P、M、v、ast_tokens、sprite_states 全部相同，
  结局相同（60 回合超时平局，lives=[2,2]）。
- `dbg_simpaths.py`：**124/124 次搜索的每条模拟动作路径完全一致**。

### 本轮三处修复
1. **rust `policy_select_idx` 采样语义**（mcts.rs）：旧实现 `cutoff = u*total` + f64 累加
   与 py `np.random.choice`（p→f64 顺序 cumsum → /cdf[-1] → searchsorted right）在边界处
   有 1e-7 量级翻转概率；回合 22 的换宠 vs 技能分叉即此。现按 py 精确镜像：
   f32 成对求和归一化 → f32 逐元素除 → f64 widen → f64 顺序 cumsum → `cdf[i]/total <= u`
   计数式 searchsorted。注意 py 在 choice 内部会把 p 转 float64 再 cumsum。
2. **rust B 视图观察者 owner 翻转**（engine.rs `flip_owner_teams` + selfplay.rs `decide`）：
   py 观察者 owner = id(精灵对象)，交换 player_a/b 天然免疫；rust owner 是 (team, idx)
   静态标签，swap 后失配 → B 侧搜索的模拟里特性不触发（如 生物碱 post_skill 中毒+2）。
   B 搜索前后对称翻转注册表 owner 队伍标签（搜索期间新注册的同样对称还原）。
3. **rust 直改修饰标签随视图翻转**（selfplay.rs）：`direct_mod_sprite_ids` 也是 (team, idx)
   标签（state 上），B 视图克隆里同步翻转；否则模拟内 begin_turn 重注入把
   共鸣的 虫鸣 power+20 加到错误队伍 → 编码不同 → 树 walk 分叉（k=34）。
4. **py 真 bug 修复（oracle）**（backend/engine/ai/core/mcts.py）：`_execute_turn_core`
   每次把传入 agent 写进 `battle._agent_a/_agent_b`，MCTS 仿真经由
   `execute_turn_headless` 把**搜索代理**（`_PlayerSwappedAgent`/`_OppFixedAgent`，
   其 player 绑定还是交换期的对方队对象）永久留在真实战斗上；回合末力竭换宠
   `_get_agent` 拿到代理 → 按对方队伍选替补 → 引擎用该下标查本队 → 误触
   『替补已死 → 立即判负』。修复：mcts_search 保存并在 finally 还原
   `_agent_a/_agent_b`（与 _mcts_sim/RNG 还原同范式）。pytest 1656 全绿。

### 速度门（48 sims / batch16 / CPU torch 14 线程）
- 队列拓扑（BatchedInferenceServer batch128/5ms + 队列，种子对齐后）：
  py 2.7 samples/s vs rust 2.9 samples/s（3 局 146/156 样本），端到端 ≈1.0x。
- 直连 torch（无队列）耗时分解：**双方评估调用次数完全相同**（967 次/7845 状态）；
  torch 推理占 73%（py）/80%（rust），引擎+编码：py 3.38s vs rust 2.36s = **1.43x**。
- 结论：当前瓶颈是 CPU 推理而非引擎；引擎收益要兑现需 GPU 推理/更大批
  （多 worker 攒批）/评估器进 Rust。gate_phase5 速度段已修为同种子对局（此前
  py 用 seed+100+g、rust 固定 spec.seed+1，比较不公平）。

### 排查工具（native/tools/，全部可复跑）
- `dbg_first_div.py` 全 digest 扫首个分歧；`dbg_b22.py` A/B 流对齐比较记录；
- `dbg_eval_trace.py` 评估指纹轨迹（FNV-1a64 over 10 数组）按搜索分段对比；
- `dbg_simpaths.py` 每次搜索的模拟动作路径对比 + 指定搜索的逐步 digest 倾存
  （rust 侧 env：ROCO_NP_TRACE=1 输出 np 事件/路径；ROCO_SIM_DIGEST=k 倾存第 k 次搜索）；
- `dbg_np_trace.py` py/rust np 随机事件流对齐；`dbg_ulp.py` 逐位/反推访问计数；
- `dbg_split5.py` 评估 vs 引擎耗时分解；`dbg_bench5.py` 直连 torch 吞吐。

### 遗留
- 阶段5b：ROCO_ENGINE 开关 + 4-worker 拓扑吞吐；阶段5c：FastAPI/API 路径 + 事件串一致。
- rust 端 np_sum_f64 已无调用方（保留备用）；ROCO_NP_TRACE/ROCO_SIM_DIGEST
  为 env 门控调试插桩，生产路径零开销（OnceLock 判断）。

## 训练提速优化（2026-09-18 续）：leaf_batch 是最大杠杆，直连/队列在大 leaf 下打平

### 瓶颈定性（本盒 RTX 5060 Ti / 20 核 / Windows WDDM）
- 每次 evaluator 调用有 **~25ms 固定成本**（CPU 端 list[dict] 堆叠 + H2D/D2H 拷贝
  + WDDM 内核同步），与批大小弱相关。sims=100/leaf=16 时每决策 ~24 次调用
  → 自博墙钟被调用次数支配。
- 16 worker 共享一个 CUDA context（队列拓扑）时延迟来自排队；16 个独立
  context（直连拓扑）时来自 WDDM 上下文切换——**leaf=16 下直连反而更慢**
  （5.2/s vs 队列 9.2/s）。
- rust 引擎单进程 44-53ms/决策（含编码与 numpy 转换税，stub 评估器实测），
  py 引擎 ~86ms；但高并发下该差异被共享的每调用固定成本淹没。

### 吞吐全景（spec_0001 阵容 × 16 种子，同种子跨配置可比，2 局/worker）
| 配置 | decisions/s | vs 队列基线 |
|---|---|---|
| queue-rust leaf=16 w=16（≈exp13 拓扑+rust 引擎） | 9.2 | 1.00x |
| direct-rust leaf=16 w=16 | 5.2 | 0.57x |
| direct-rust leaf=64 w=16 | 14.3 | 1.56x |
| direct-rust leaf=128 w=16 | 25.3 | 2.75x |
| direct-py leaf=16 w=16 | 11.0 | 1.20x |
| queue-py leaf=64 w=16 | 23.1 | 2.51x |
| queue-py leaf=128 w=16 | 33.6 | 3.65x |
| queue-rust leaf=128 w=16 | 31.1 | 3.38x |
| direct-py leaf=128 w=16 | 32.8 | 3.57x |
| **direct-py leaf=256 w=16** | **36.3** | **3.95x** |
| direct-py leaf=128 w=8 | 33.1 | 3.60x |

结论：**leaf_batch_size 16→128 一项 ≈ 2.3-2.5x 真实训练吞吐**（exp13 实产
14.7/s → 同盒折算 ~33/s）；直连与队列在 leaf≥128 后打平（±8%）；
worker 8 与 16 无差异（共享饱和），8 个可省一半 CUDA context 显存。

### 交付的接入件（对只读四文件零改动）
1. `backend/engine/ai/core/evaluator.py`：QueuePolicyEvaluator env 门控直连
   （ROCO_SELFPLAY_EVAL=direct + ROCO_SELFPLAY_MODEL/DEVICE/TORCH_THREADS），
   构造签名不变，未设 env 时行为不变。
2. `backend/engine/ai/rust_selfplay_hook.py`：ROCO_SELFPLAY_GAME=rust 时把
   worker 内 `_play_one_rl_battle` 换成 rust 引擎 shim（team/item 沿用 worker
   random 流，battle_seed 供 rust RNG；记录 6 元组同构）。挂载点在 evaluator
   模块底部 install_if_enabled()——worker 进程先导 evaluator 后延迟导 train，
   patch 生效；主进程 train 自己的 def 后定义覆盖 patch，不受影响。
3. rust `py_selfplay_game` cfg 支持 draw_margin/gamma/tanh_k（lib.rs）。
4. 冒烟：native/tools/smoke_hook.py（hook 生效 + 直连激活 + 单局正常）。
   基准：native/tools/bench_selfplay_pool.py（三拓扑 sweep）。

### 推荐训练配置（exp13 参数基础上）
```
--leaf-batch-size 128 --inference-batch-size 256   # CLI 即可，零代码改动
# 可选直连（省排队、少一跳 pickle）：
ROCO_SELFPLAY_EVAL=direct ROCO_SELFPLAY_MODEL=checkpoints/exp16/model_rl.pt
ROCO_SELFPLAY_DEVICE=cuda ROCO_SELFPLAY_TORCH_THREADS=2
# 可选 rust 引擎（与 py 版同吞吐，消除 mcts_sim 代理污染类问题；全 rust 路径）：
ROCO_SELFPLAY_GAME=rust
```
预期：53.4h（exp13）→ ~22-24h；train 阶段占 ~45% 成为新瓶颈，下一步是
训练侧 GPU 利用率（现 ~2.2 step/s）。

### 备注
- leaf 变大会改变 MCTS 批组成 → torch 结果 ulp 级不同 → 同种子轨迹不再
  与 leaf=16 逐位一致（训练语义不受影响；引擎逐位一致性由 gate_phase5 在
  固定批组成下保证）。
- rust 引擎版与 py 版大 leaf 下吞吐相当；rust 的收益（引擎侧 1.4x）要在
  消除每调用固定成本后（Linux/MPS、CUDA graphs、或推理进 rust 进程）才能
  兑现为端到端优势。

## 训练诊断与模型交付（2026-09-19）：价值头记忆化是历史实验卡死的病根

### 诊断（基于 exp2-16 全部运行日志 + 锦标赛）
- **存量 checkpoint 锦标赛**（vs RuleAgent，配对种子，40 局，sims=64）：
  exp11 0.512 / exp12 0.487 / exp13 0.525 / formal_v1 0.487 —— 全部 ~50%，
  两周实验对 rule 无净进步。随机初始化模型 vs rule = 0.167（rule 很强，
  训练确实学到过东西，但卡在平台）。
- **病根 1 —— 价值头零泛化**：exp13 60 轮 val_v_loss 始终 0.98-1.42，
  而 ±1 目标下常数预测器 MSE=1.0 → 价值头在验证集上从未超过常数；
  train_v_loss 0.139→0.066 = 纯记忆化。MCTS 叶评估等于用噪声 → 搜索无法
  变强 → 自博弈数据质量锁死 → 全线平台。根因：lr 1e-3 × 10-15 epochs 在
  5 轮小缓冲上反复重训。
- **病根 2 —— 门控噪声**：100 局 σ≈5%，0.55 晋升线恰在 1σ 处，
  假晋升/假拒绝率高，与振荡史一致。
- 校准：exp13 best @sims=200 vs rule = 0.717（s64 时 0.525）→ 搜索能补偿
  策略，价值头修复后强度应随 sims 兑现。

### exp17_deliver（25 轮，4.36h，base=exp13 best）
- 配方：**lr 3e-4 余弦→3e-5、epochs 4、battles 280、buffer 8、weight-decay
  3e-4、dropout 0.1、temp-decay 0.97、leaf 128（吞吐 45 样本/s = exp16 的 3.2x）**
  + 用户未提交的 game_id 分组切分（val 指标不再泄漏）。
- 结果：**3 次晋升（第 7/10/17 轮）**；val_acc 峰值 0.85（exp13 平台 ≤0.68）；
  **val_v_loss 0.32-0.59（exp13 恒 ≥0.98）** —— 价值头首次泛化，病根修复确认。
- 终评：vs exp13_best 配对 200 局 = **51.75%**；vs rule @s64 = **0.625**
  （exp13 同设定 0.525）；vs rule @s200 = 0.688。
- 教训：晋升后门控分回落至 47-52% 属正常（基线抬高），跨迭代复利靠的是
  反复小幅晋升；单次 4h 只买到 +2~10%。

### exp18_deliver（进行中）：base=exp17 best，lr 2e-4、sims=150（数据质量↑）、
  eval 150 局 / gate 0.54（门控噪声↓）、16 轮 ≈ 5h。交付取两者最优。

### exp18/19 结果与最终交付（2026-09-19）
- **exp18（sims=150 训练）已中止**：门控 39-47% 全面退化。教训：**训练与
  门控的 sims 必须一致**——候选向 sims150 数据分布特化后在 sims100 门控下
  打不过基座。
- **exp19（热重启 lr2e-4，sims100）**：16 轮 1 晋升（第 12 轮 54.17%），
  但 200 局复评未保持：vs exp17_best 51.0%、vs exp13 48.75%、vs rule@s64
  0.525 —— 该晋升为噪声晋升。
- **最终交付 = exp17_deliver 第 17 轮晋升权重**，副本：
  `checkpoints/delivered/model_v1.pt`（含 README.md 证据与续训指南）。
  全部配对种子证据：vs rule@s64 0.625（基座 0.525）、vs 基座 200 局 51.75%、
  val_acc 0.85 / val_v_loss 0.32（历史平台 0.68 / ≥0.98）。
- 后续若继续冲强度：保持本配方 Sims 一致性 + eval≥150 局降门控噪声，
  或提高网络容量（1.5M 参数可能是 val_acc 0.85 后的下一个瓶颈）。

## 换边增强验证（2026-09-19）：已天然存在，无可挖收益
- 探针 `native/tools/dbg_swap_sym.py`：spec_0001 实战 8 个阶段（turn 1-20），
  encode(B视角) vs swap(players)+encode(A视角) —— **全部 10 数组严格对称**
  （atol 1e-6，零失配）。
- 结论：自博弈为同步决策制，每个决策点 MCTSAgent 双方各自搜索并记录，
  A/B 双视角样本天然成对且编码一致——"换边增强"想要的信息已经在训练集里，
  样本级增强为零增益。假设证伪，未做无谓改动。
- 探针同时确认了价值头此前泛化失败与视角不对称无关（对称性完好）。

## exp20_long（进行中）：当前配方长跑主线
- base=delivered/model_v1（exp17 best），lr 3e-4 余弦→3e-5，40 轮 ≈ 9h，
  battles 350、buffer 12、epochs 4、leaf 128、**eval 200 局（σ≈3.5%）、
  gate 0.55**、mirror 0.2、temp-decay 0.98。
- 验收看三点：晋升次数（期望 5-8）、val_v_loss 是否稳定 <0.6、
  终局 vs rule @s64 相对 0.625 的增量。

## 6v6 实战格式对齐（2026-09-19 下午）：训练分布重大修复

### 用户确认实战格式：自组队 6v6，先力竭 4 只精灵一方失败
- 规则侧已天然对齐：lives=4（每次力竭扣 1、归零判负）≡ 先力竭 4 只判负。
- **训练分布严重失配**：_random_teams 原 1-3v1-3（1/3 还是 1v1 快棋），而
  编码器 12 精灵槽、动作空间 10-14 换人槽都是按 6v6 设计的——3v3 训练下
  槽位 3-5/9-11 与换人动作 12-14 从未被训练。exp20（3v3 长跑）当即中止。

### 修改
- `_random_teams`：队伍大小固定 6（实战同分布；多样性来自精灵/技能/性格/
  IV/道具采样）。6v6 下"整队零攻击"概率≈(8.2%)^6≈0，无需加约束。
- `test_random_teams_use_one_shared_team_size` 更新为 6v6 语义（断言两队=6）。
- 6v6 py/rust 整局一致性抽查（dbg_6v6_parity.py，复用 run_python/run_rust）：
  **5/5 逐位一致**（含一局 150 回合打满平局）。
- pytest 1656 全绿。

### 冒烟（exp21_smoke，40 局×2 轮）
- 6v6 每局 ~161 样本（3v3 的 2.4 倍），自博 34-35 样本/s，管线全链路正常，
  第 2 轮即出现晋升。max_turns 60→80（6v6 对局更长，减少截断平局）。

### exp21_6v6（进行中）：base=delivered/model_v1，24 轮 ≈ 12h
- battles 180 / buffer 8 / epochs 4 / lr 3e-4 余弦 / leaf 128 / mirror 0.2 /
  eval 150 局 @s100 / gate 0.55 / max_turns 80。
- 这是**对齐实战格式的第一次训练**；此前所有历史模型（含 model_v1）都是
  1-3v1-3 分布下训出来的，6v6 强度需重新积累。

## RuleAgentV2 —— 社区 PVP 攻略经验重写（2026-09-19）

### 背景
行为克隆（BC）预训练需要一个行为模式健康的"专家"。旧 RuleAgent 对局长
（6v6 内战 mean 68 回合）且无速度/斩杀/威胁概念。

### 检索到的社区经验（B站/游民星空/4399/3DM/腾讯新闻等）
1. 速度为王：先手权决定攻防次序；速度落后时应先控场。
2. 斩杀优先：快攻体系"一两个回合结束战斗"；本回合能杀立即杀。
3. 能量管理：初期避免高耗能大招，低耗试探；能量不足最强攻击先聚能。
4. 换宠轮换：首发控场 → 次发爆发 → 尾发续航；换人有 tempo 成本，只在
   被杀威胁/对位无效时换。
5. T0 工具人+推队：开印记/撒钉子磨血 → 推队宠拿强化后一招一个。
6. 属性克制内嵌于伤害公式（calc_damage 已含克制/印记/天气）。

### 实现（backend/sim/agent_v2.py，独立类不动旧 RuleAgent）
- 决策优先级：斩杀（可击杀→最低耗斩杀技）→ 被杀威胁换位（仅慢速+威胁+
  对位更优才换）→ 进攻（最高伤害，平手低耗）→ 强化推队（无有效输出且
  能量富余时用 stat 增益）→ 残血触发换位（对位伤害高 30% 才换）→ 聚能。
- 首发对位评分：伤害期望×2 + 速度先手 0.2 + 面板生存 0.3。
- 伤害/威胁全部走引擎 calc_damage（克制/天气/印记自动包含），
  速度用 effective_stat('speed')（含 buff）。

### 验证（native/tools/validate_v2.py，100 个 6v6 角色化对阵，配对种子）
- V2 vs 旧 RuleAgent：**53% 胜率**（53胜12平35负）。
- V2 内战对局长度：**mean 32 / median 28 回合，100% 自然分胜负、零打满**
  （旧版内战 mean 68），直方图集中 10-40 回合——与实战 2-30 回合对齐。
- pytest 69（AI+sim）全绿。

### 用途
作为 BC 预训练的专家策略：RuleAgentV2 vs V2 快速生成对局（无 MCTS，
秒级/局），每个决策点记录 (编码, V2 动作, mask, 终局价值) → 策略头交叉熵
+ 价值头 MSE 预训练 → 自博弈微调（AlphaStar/绝悟范式）。


## 8. BC 预训练管线（池无关部分，2026-09-18）

目标：AlphaStar/绝悟范式——规则专家数据行为克隆预训练 → 自博弈微调，
解决从零自博弈前几轮的随机起步与换人拖招。

### 已交付（不依赖精灵池内容）

- `backend/sim/agent_v2.py` 参数化：`SpriteStrategy`/`TeamStrategy` 数据类 +
  `RuleAgentV2(team, player, strategy=None)`。每队可声明 精灵→(首发候选 lead /
  必保 preserve / 能量预算 energy_hold / 换宠阈值覆盖)；strategy=None 时行为
  与旧版逐字节一致（默认阈值即原常量）。
- `backend/engine/ai/data/meta_teams.py`：meta_teams.json 加载（mtime 缓存，
  `ROCO_META_TEAMS` 可覆盖路径）、`validate_meta_teams`（精灵/技能必须在池内）、
  `spec_from_team`（第三项 IV + 性格逐局扰动）、`strategy_from_team`（阈值抖动
  增加对局多样性）。文件不存在 → 一切自动退化为纯随机采样。
- `backend/engine/ai/bc_record.py`：`RecordingAgent`（合法掩码内录制
  (编码状态, 动作索引, 掩码)，动作/掩码冲突时跳过录制不喂脏标签）+
  `run_recorded_battle`（agent 工厂注入 player，双视角样本，`_random_item` 与
  train 一致）。
- `native/tools/gen_bc_data.py`：CLI。meta_frac 概率用原型队对战（双方各抽，
  含镜像比例），其余 `_random_teams` 角色化随机；输出 npz（float16/int16 压缩）
  + json sidecar（含 reason_counts / team_game_counts / 整队留出提示）。
  实测 20 局/秒级 → 2500 局约 2-3 分钟。
- `backend/engine/ai/bc_pretrain.py`：加载 npz → 整队留出切分（留出最后
  `--holdout-teams` 支 meta 队的全部对局 + 随机对局按 game 抽样）→ 复用
  `train_rl`（value MSE + masked policy CE，目标为专家 one-hot）→ 按验证集
  policy top-1 回调存最优权重 `bc_init.pt`。
- `train.py`：`_random_teams` 以 `_META_FRAC`（默认 0.6，`--meta-frac`/
  `ROCO_META_FRAC` 控制）概率两侧改用 meta 队 spec——BC 与自博弈共享分布；
  `train_rl` 新增 `val_indices`/`on_epoch` 参数（自博弈路径不受影响）；
  `--bc-init` 在自博弈启动前加载 BC 权重（`--resume` 优先级更高）。rust hook
  复用 `train._random_teams`，meta 混合自动生效。
- 测试：`backend/engine/ai/tests/test_bc_pipeline.py` 6 项（运行时从池取精灵，
  不硬编码队名），含 gen→pretrain→加载 小规模闭环。全量 pytest 501 passed。

### 池子定稿后剩余步骤

1. 用 `explore_pool.py`/`pool_summary.py` 重导池子摘要，按战术原型
   （快攻推队/均衡轮转/消耗坦克/强化爆发）选 4-6 队，写
   `backend/engine/ai/data/meta_teams.json`（格式见 meta_teams.py docstring）。
2. `python native/tools/gen_bc_data.py --games 2500 --meta-frac 0.6
   --out checkpoints/bc_data.npz`（校验失败会拒绝生成）。
3. `python -m backend.engine.ai.bc_pretrain --data checkpoints/bc_data.npz
   --out checkpoints/bc_init.pt`，看留出队 policy top-1 是否 ≥ 训练队（不崩即泛化 OK）。
4. 自博弈微调：`python -m backend.engine.ai.train --run-name bc_v1 --bc-init
   checkpoints/bc_init.pt --meta-frac 0.6` + exp17 配方（lr 3e-4, epochs 4,
   buffer 8, eval-games 150-200, gate 0.55, sims 100, leaf 128）。

## 9. Python 侧新增语义（2026-09-19，Rust 端待镜像）

对拍门禁当前为 skip（
oco_engine 未在本机构建），以下三条只在 Python 生效，移植时需同步：

1. **速度点数通道 speed_flat**：stat_stage{speed} 仍是 10 点/步；mult_mod{attr:"speed"} 是百分比；
   speed_flat 1 点 = 1 点。口径：
ound((base + speed_steps*10 + speed_flat) × (1 + speed_ratio))。
   影响：attle_state.rs（ffective_stat 速度分支）、snapshot.rs（speed 计算）、
   
eplayer.rs（STAGE_STATS 需含 speed_flat，value→steps 1:1）、ncoder.rs（speed 列）。
2. **回合末抑制 	urn_end_block**（陨落）：flag 值 > 0 时跳过双方 SkillResolver.turn_end、
   xtra_turn_end 额外触发、	urn_end 观察者与 post_abnormal_tick 通知；ttl/延迟队列照常。
3. **授予作用域 ffects: "team"**（齐鸣）：morph/lement_convert/grant_choice 的授予者查找
   增加「同队（含场下）	eam 声明」来源；队伍级声明用 scope:"persistent" 以跨换人存活。
   另：morph.category 的 spec dict 不再被 str() 化（Python 侧已修）。


## 9. form/appearance 维度拆分（2026-09-19）

### 背景

精灵数据原先把"形态阶段"和"外观"都塞在 `form` 字段：
`''`（基础）/ `'首领形态'`（首领）/ `'夏天的样子'`（外观）。
外观变体文件（`鸭吉吉国王（急急急鸭）` 等）form 写外观名 →
引擎 `'首领' in form` 判断失灵（首领外观不被识别）、
池子的"前形态排除"规则被自引用 `pre_species` 误触发
（11 个编号的普通形态被连带剔除）。

### 新数据模型

- `form`：阶段标记，`''`（基础）或 `'首领形态'`（首领）
- `appearance`：外观名，`''`（默认外观）或「夏天的样子」等
- 两维度独立：任何阶段都可以有多个外观；首领外观 form='首领形态'

迁移脚本 `native/tools/migrate_appearance_field.py`（已对 640 个文件执行，幂等）：
同 name 组内存在 form='首领形态' 的文件 → 该组全部标为首领阶段，
原 form 值移入 appearance。旧格式数据由
`SpriteDB._normalize_form_appearance` 读取时自动规范化（向后兼容）。

### 代码改动

- `common/models.py`：`SpeciesStats.appearance` + `display_name()` 用外观 +
  `is_leader_stage()`
- `common/sprite_db.py`：索引键 = `名字（外观）`；`get(name, appearance)`；
  `get_by_stage`；`list_appearances`（旧名 list_forms 兼容）；
  `lookup_by_number` 优先基础阶段；`save` 写 appearance
- `sim/factory.py`：`build_sprite(..., appearance=)`（form 参数保留兼容）；
  `build_player` 读 spec 的 appearance 字段
- `sim/battle_mechanics.py`：`_find_leader_form(number, appearance)` 优先同外观、
  回退默认外观；进化之力首领化**保留当前外观**
- `sim/battle.py`：`lookup_species/lookup_species_by_number` 参数语义改为 appearance
- `engine/serializer.py`：快照 species 引用带 appearance（旧快照回退读 form）
- `engine/differential/recorder.py`：状态摘要加 species_form/species_appearance
- `engine/ai/data/sprite_random_pool.py`：新池规则（见下）
- `api/main.py`：/api/sprites 按（名字,外观）独立条目（原来按名字去重会吞掉外观）

### 池规则 v3（sprite_random_pool._build_pool）

1. 首领阶段（`'首领' in form`）不入选；
2. `evolved_from` = 非首领条目的 pre_species 集合；编号在其中且**非**
   「同编号第二形态」的基础阶段剔除；
3. 同编号第二形态（pre_species == 自身 number）= 该编号最终形态，保留；
4. 池键 = `名字（外观）`，每个外观是独立条目。
结果：191 → 206（修规则前）→ **274 个条目**（外观独立后）。
注意：只有外观文件、没有默认外观的精灵（皇家狮鹫、遁地鼠等 12 个名字）
池键从「名字」变为「名字（外观）」——不是丢失。

### Rust 同步待办（重要）

- `native/roco-core` 的物种数据结构加 `appearance` 字段（serde 默认空串兼容）；
- 首领化（进化之力）目标查询需带同外观优先逻辑
  （对应 py 的 `_find_leader_form(number, appearance)`）；
- 快照序列化字段与 py 对齐（species_ref 增加 appearance）；
- 对拍夹具已按新池重录（种子 1-24），Rust 同步后重跑对拍门。

### 其他待办

- 用户侧精灵数据生成器需要输出 `appearance` 字段（当前旧格式仍可被兼容读取）；
- 前端若需展示外观名/阶段，可从 /api/sprites 的新条目名解析，
  或后续在 payload 里显式加 appearance/form 字段。


## 9.1 首领形态入池（2026-09-19）

战斗合法性确认：首领形态可直接参战，因此进入训练池。

- 池规则 v4（`sprite_random_pool._build_pool`）：
  1. 首领阶段不再排除，首领及其外观都作为独立条目入池；
  2. 前形态仍剔除：`evolved_from` = 非首领条目的 `pre_species`
     （跳过自引用、跳过首领条目）——首领化是"同编号形态切换"而非进化链，
     基础形态与首领形态都要保留；
  3. 同编号第二形态（`pre == number`）保留。
- 数据修正：全库扫描只有 2 组未标首领的第二形态——暮风隐者、满月砣
  （经用户对照 B洛克 Wiki 确认为首领），已标 `form='首领形态'`；
  圣X迪莫等首领形态原本已标记。工具：`native/tools/fix_leader_marks.py`。
- 池规模：206（修规则前）→ 274（外观独立）→ **344 条**（首领入池，净增 70 零移除）。
- 注意：首领形态的 `attributes` 是元素列表（如 深渊罗隐 ['地','恶']），
  血脉默认取首元素而非『首领』；若要训练模型学习「进化之力」首领化路径，
  组队 spec 需显式给 `bloodline='首领'`。
- 夹具已重录（种子 1-24），全量 pytest 552 passed / 5 skipped（rust 未编译）。


## 9.2 首领形态不可二次进化 + 道具可用性掩码（2026-09-19）

规则确认（用户）：**首领精灵不能再用进化之力**。只有带『首领血脉』的
基础阶段精灵可以使用进化之力变成同编号首领形态
（例：首领血脉的迪莫 → 圣X迪莫）。

实现：
- `sim/battle_mechanics.py` 新增 `item_usable(team)`：道具可用性的唯一判定，
  被 `_resolve_item`（结算）与动作掩码共用。
  进化之力条件 = 首领血脉 + **非首领阶段**（`species.is_leader_stage()` 为假）
  + 同编号存在首领形态；愿力条件 = 元素血脉。
- `engine/ai/core/mcts.get_valid_actions`：动作 16（道具）仅在
  `battle.item_usable(team)` 为真时给出——修复此前"道具动作恒可用"导致
  MCTS 选中非法道具白费一回合的隐患（对愿力同样生效）。
- `api/main.py` `/sprites/{name}/evolution`：首领形态直接返回
  `can_evolve=False`。
- 测试：`backend/engine/test_leader_appearance.py` 3 项
  （外观条目构建 / 首领化保留外观 / 首领不可再进化且掩码=0）。
  全量 pytest 553 passed / 5 skipped（rust 未编译）。

Rust 同步待办（追加）：道具可用性判定（含阶段检查）与动作掩码 16 号位行为需一致。

训练待办：要让模型覆盖「首领血脉 + 进化之力」路径，组队 spec 需显式
`bloodline='首领'`（首领形态的 attributes 是元素，默认血脉不是首领）。
多首领家族（如迪莫有 圣光/圣水/圣火/圣草迪莫 4 种）当前取同外观首个匹配，
若游戏内是按元素/血脉决定具体形态，需补充规则。

## 10. meta 阵容爬取（rocopvp 共享阵容）+ 40 支原型队（2026-09-20）

数据源定位：rocopvp.tzrain.wiki / roco-pvp-asst.pages.dev / roco-showdown.com
是同一个 Next.js 部署，API 端点藏在 chunk 的路径字面量里：
  GET /api/popular/teams   → 87 支阵容（item.snapshot = {team, builds}）
  GET /api/popular/builds  → 444 个单精灵配置（含 tags/描述）
每 build 含 name(可被玩家改) / creatureId / selectedSkillNames / bloodline /
statMarks(extremeStat + plusStats×3 + minusStat) / gameTeamCode。

工具：`native/tools/scrape_meta_teams.py`（fetch/verify/build 三个子命令）
- fetch：指数退避（429/567/5xx，1→60s，6 次）抓两个端点 → 缓存
  `backend/engine/ai/data/scraped_teams.json`（入库，保证可复现）
- 精灵解析优先级：池内显示名 → SpriteDB（含外观回退）→ creatureId 投票表
  → 技能集合匹配。实测 478 名字 + 33 creatureId + 11 技能匹配 = 87/87 全解析
  （站点名字被玩家改过：`肉千棘盔` / `速度 音速犬` / `狼王队-卡瓦重（雪山附近的样子）`）
- 字段映射：plusStats(3) → iv_fixed；extremeStat(加)+minusStat(减) → nature_fixed；
  bloodline.attribute → bloodline（首领血脉 → '首领'）；gameTeamCode 的
  `魔法：X` 行 → item（进化之力/愿力；无该行时首领队 → 进化之力，其余 → 愿力）
- 技能合法性 = 池内技能 ∪ 所选血脉的血脉技能（站点阵容可携带血脉技能，如
  火神的 跺地 / 翠顶夫人的 水弹枪）；不合法的剔除并记账，不足 3 个从池内同类型补齐

选择 40 支（`build --target 40 --sprite-cap 4 --boss-teams 8 --flex-per-team 1`）：
按（修补数, 站点标签优先级 比赛/排位>体系>平衡>科研>娱乐, 点赞数）贪心，
限制单精灵最多 5 队 + 禁止重复组合 + 每队挂 1 个同角色替补（alt）：
99 只去重精灵 / 240 槽位（平均 2.42 次出场），首领血脉 20 队，道具 20/20，
元素与原型分散（排位 19 / 体系 7 / 首领进化流 5 / 平衡 2 / 比赛 1 / 娱乐 1）。

审计：`native/tools/audit_meta_teams.py` → 40 队 0 问题，
26 只首领血脉精灵全部可首领化（否则进化之力会被掩码屏蔽）。

队伍道具（魔法）随队伍传入对局：`meta_teams.item` → `item_from_team()` →
`_random_teams` 返回 (team_a, team_b, item_a, item_b)。首领队的进化之力
必须随队传入，否则「首领进化流」在数据里根本不出现（随机道具只有 50% 概率）。
`_random_teams` 返回值从 2 元组变 4 元组，所有调用点（evaluate_checkpoints /
rust_selfplay_hook / native 诊断工具 / 测试）已同步。

meta_teams.json 新字段：`iv_fixed` 3 项 = 精确拉满（爬取来源），2 项 = 第三项
随机（手写队伍的多样性来源）；`nature_fixed` 精确性格；`bloodline` 血脉透传；
`item` 队伍道具。

## 10.1 动作空间 17 → 22：首领形态由玩家选（2026-09-20）

规则确认（用户）：**首领形态是玩家选择的**（血脉=首领不能变、元素自带），
所以「进化之力变成哪个形态」必须进动作空间。

布局：0-9 技能 / 10-14 换宠 / 15 聚能 / **16 愿力** / **17-21 进化之力的
首领形态槽位**（最多 5 个候选，实测最多 4：迪莫 → 圣光/圣水/圣火/圣草迪莫）。

实现：
- `common/constants.py`：`ITEM_VARIANT_SLOTS=5` / `ITEM_VARIANT_ACTION_BASE=17`
- `common/sprite_db.py`：`leader_form_candidates(number, appearance)`——
  同外观 → 默认外观 → 其余（仅前两类都空时）按名排序；动作索引与候选下标同序
- `sim/action.py`：`Action.variant`（道具形态槽位）
- `sim/battle_mechanics.py`：`item_variants(team)`（掩码与编码器同源）+
  `_resolve_item(team, variant)`（越界/缺省取首位，兼容 API 与旧调用点）
- `engine/ai/core/mcts.py`：`NUM_ACTIONS=22`、掩码 17-21、`action_index_to_action`
  支持 variant；`mcts_parallel.NUM_ACTIONS` 改为从 mcts 导入（单一来源）
- `sim/agent_v2.py`：`_leader_form_action` —— 多候选家族按属性克制和挑形态
  （单候选退化 `Action(kind='item')`，行为与旧版一致）
- `engine/ai/bc_record.py`：`action_to_index` 支持 variant → 17+k
- 编码器/模型：新增 `form_elements (5,2)` + `form_avail (5,)` 输入块；
  模型 `item_head` 从 1 维扩到 6 维（16 愿力 + 17-21），融合层 1248 → 1328
- `api/schemas.py`：`ActionRequest.variant`、`ItemState.variants`（前端可选形态）
- `replay_buffer.check_action_width`：拒绝 17 维旧数据（动作空间扩展前生成），
  报错信息提示重新生成数据

测试：`test_leader_appearance.py` 增 2 项（多候选家族掩码 17-21 + 指定槽位
首领化到该形态 / 单候选缺省落首位）；`test_bc_pipeline.py` 增 2 项
（meta 精确 IV/性格/血脉/道具；随仓库交付的 meta_teams.json 数据完整性门禁）。
全量 pytest 557 passed / 5 skipped。

差分夹具**无需重录**：夹具走 `differential/recorder` 的 RuleAgent + 种子道具
（不受 V2 agent 与掩码改动影响），24 个夹具事件流逐字节一致。

Rust 同步待办（重要，追加）：
1. 动作空间 22：道具动作带 variant（17-21 = 首领形态槽位），掩码按
   `leader_form_candidates` 顺序放开；单候选族只放 17。
2. `_resolve_item(team, variant)` 的形态选择语义 + 越界回退首位。
3. 编码器候选形态块（5×2 元素 ID + 可用性），模型输入契约变更 →
   Rust 侧 batch 协议需同步（Python 侧由 encoder/evaluator 键集合驱动）。
4. `item_variants` 的排序必须与 Python 完全一致（同外观 → 默认外观 → 名称序），
   否则动作索引与形态错位。
5. `spec["leader_forms"]`（候选列表）已加入 `rust_selfplay_hook._build_sprite_spec`，
   Rust 侧需解析该字段（旧的单数 `leader_form` 保留兼容）。
