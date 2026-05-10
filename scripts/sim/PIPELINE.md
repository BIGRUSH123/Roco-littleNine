# 技能执行管线

技能从使用到结算的完整执行模型。所有效果按时间点分入 7 层（+ gate + 回合末）。

## 管线总览

```
gate  能量支付              energy < cost → 短路，不进入管线
─── trait modifier hook ───
L0   前置层    [once]  modifier 预计算      → SkillUse.modifiers + ignore_mods flag
L1   威力层    [once]  动态威力解算          → power_override
─── trait damage hook ───
L2   伤害层    [loop]  calc_damage + drain  → HP 变化, burst/ignore_mods 生效
─── faint 中断可介入 ───
L3   状态层    [once]  按 effects 数组顺序执行
L4   反击层    [once]  counter_damage（独立公式，不走 L2）
L5   换宠层    [once]  escape/return/borrow → 场上精灵切换
─── trait entry hook ───
─── faint 中断可介入 ───
L6   回合末    [once]  tick/cooldown/mark   → 双方所有精灵（含后备）
─── trait turn end hook ──
```

## gate — 能量支付

**时机**: 管线入口，L0 之前

**逻辑**:
1. 计算实际能耗：`skill.energy_cost × weather_energy_mod - mark_energy_mod`
2. 若 `user.energy < cost` → 短路，管线不执行，返回 `[能量不足]` 事件
3. 否则扣能，继续进入管线

**短路意味着**: L0-L6 全部跳过。技能不产生任何效果。

## 特性钩子

以下 4 个钩子预留给将来的精灵特性系统。当前均为空实现。

| 钩子 | 位置 | 触发时机 | 签名 |
|------|------|----------|------|
| `_on_trait_modifier` | gate / L0 之间 | 技能使用前，modifier 预计算后 | `(user, use) → list[str]` |
| `_on_trait_damage` | L1 / L2 之间 | 伤害计算前，每 hit | `(user, target, use) → list[str]` |
| `_on_trait_entry` | L5 / L6 之间 | 精灵入场时（换宠/脱离/返场/力竭替补） | `(sprite) → list[str]` |
| `_on_trait_turn_end` | L6 之后 | 回合末结算完成后 | `(sprite) → list[str]` |

### L0 hook — trait modifier

特性可在此处修改技能参数（威力、耗能、先手等），或注入额外 modifiers。

### L2 hook — trait damage

特性可在此处影响单 hit 伤害。注意这是在 L2 [loop] 之前调用一次，不是每 hit 调用。

### L5 hook — trait entry

精灵因任何原因入场（主动换宠、脱离/折返、返场、力竭替补）时触发。
实现位置：`_resolve_switch`、`_resolve_return`、`_handle_escape`、`_handle_escape_inherit`、`_check_faint_interrupt` 中，`entry_turn` 赋值之后。

### L6 hook — trait turn end

回合末，对双方 active 精灵各调用一次。在天气递减、印记结算之后触发。

## 各层详解

### L0 前置层 — modifier 预计算

**时机**: SkillUse 构造时（`SkillUse._collect_modifiers()`）+ `dispatch_modifiers()`

**处理效果**:
- `power_bonus` → modifiers['power_bonus']
- `power_mult` → modifiers['power_mult']
- `damage_mult` → modifiers['damage_mult']
- `damage_reduction` → modifiers['damage_reduction']
- `multi_hit` → modifiers['multi_hit']
- `adjacent_power_bonus` → 相邻技能 power_mod += val
- `ignore_mods` → modifiers['ignore_mods'] = True（flag，L2 消费）

**产出的 modifiers dict** 由 L2 的 `calc_damage()` 消费。

### L1 威力层 — 动态威力解算

**时机**: `_execute_single_action` 中，L2 伤害计算前

**处理效果**:
- `power_by_enemy_energy` → 根据对方技能总耗能动态设 power_override
- `power_by_adjacent` → 根据相邻技能威力动态设 power_override
- `next_attack_mult` → 消费热身倍率，注入 modifiers['power_mult']

**产出**: `BattleSkill.power_override`（确定本次实际威力）

### L2 伤害层 — per-hit loop

**时机**: 连击循环内，每 hit 执行一次

**标注**: `[loop]` — effective_combo 次迭代

**处理**:
1. `calc_damage()` — 伤害公式：`base × type × weather × mark × stab × burst × damage_mult × multi_hit × (1 - damage_reduction)`
2. `ignore_mods` 在此生效：`effective_stat(atk, ignore_negative=True)` / `effective_stat(def, ignore_positive=True)`
3. `burst` ×1.5：当技能含 BURST 且 `user.first_action` 为 True
4. `life_drain` — 按伤害百分比回血

**faint 中断**: 目标力竭后连击循环 break

### L3 状态层 — 效果数组顺序执行

**时机**: 连击循环结束后，执行一次

**标注**: `[once]`

**核心规则**: 执行顺序由技能 JSON 的 `effects` 数组定义。子标签仅作分类，不强制执行顺序。

#### 子标签

| 标签 | 效果 | 影响域 |
|------|------|--------|
| **资源** | `heal`, `direct_heal`, `gain_energy`, `steal_energy` | self / self+target |
| **状态** | `stat`, `abnormal`, `mark`, `weather`, `dispel`, `double`, `charge` | self / target / team / global |
| **交换** | `exchange_hp_ratio`, `exchange_effects`, `exchange_skills`, `random_devotion` | both |

#### 状态子标签内部建议顺序

当技能同时包含多个状态效果时，推荐按以下顺序排列 effects 数组：

```
dispel → double → stat → abnormal → mark → weather → charge
```

理由：
- dispel 在 double 前：先清再翻，避免翻倍被浪费
- double 在 stat 前：翻倍作用于已有效果，不翻新挂上的
- stat/abnormal/mark/weather 互不影响（不同目标域）
- charge 最后（纯标记事件）

### L4 反击层

**时机**: L3 之后

**处理**: `counter_damage` — 使用独立简化伤害公式（`power × atk / def × type`），不走 L2 的完整公式。不含 STAB、天气、印记、burst 倍率。

### L5 换宠层

**时机**: L4 之后，回合结束前

**处理**:
- `escape` → 切换精灵（清 battlefield 效果）
- `escape_inherit` → 切换精灵（继承增益）
- `force_return` → 强制对方切换
- `return_self` → 设置 pending_return 标记（L6 结算）
- `borrow_skill` → 借用队友技能

### L6 回合末

**时机**: 双方行动结算完毕

**处理**:
- 中毒/灼烧/寄生伤害 tick
- 灼烧层数衰减
- 暴风雪天气 → 冻结
- 印记回合末效果（光合回能/中毒扣血等）
- 冷却递减（含后备精灵）
- 天气回合递减
- 借用技能还原
- 返场结算（`pending_return`）

## 前置 gate 语义

### 能量 gate

`user.energy < cost` 时，整个管线短路。这是唯一硬 gate —— 不支付能量，技能完全无效。

### interrupt / reflect_damage 的隐式 gate

`interrupt`（打断）和 `reflect_damage`（技能替换）在 L3 中按 effects 数组顺序执行。其 gate 效果由 `_resolve_both_skills` 的 A→B 顺序执行隐式保证：

1. A 的 L3 中 `interrupt` 将 B 的 `countered_skill` 设为 null
2. B 的 `_execute_single_action` 中 `_get_skill` 返回 null
3. B 的管线短路：`skill is None → return []`

## faint 中断规则

对方力竭时可在以下位置介入：
- L1 与 L2 之间（伤害前）
- L2 循环内（per-hit）
- L3 后、L4 前
- L5 后（换宠可能触发新精灵入场）
