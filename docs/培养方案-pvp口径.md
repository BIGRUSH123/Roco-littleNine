# PVP 培养方案（wiki 推荐驱动）

> 目标口径：**PVP**（`模块:Pets/data/TrainingReference` 的 `pvp` 块；`world` 仅作缺失回退）。
> 本文是实现契约，先定规则再改代码。相关代码：`backend/engine/ai/train.py`、`native/tools/gen_bc_data.py`、
> `backend/engine/ai/data/role_from_reference.py`、`native/tools/import_training_reference.py`。

## 0. 前置修复 P0：wiki 数据对位错了（必须先做）

**问题**：`import_training_reference.py::parse_catalog` 用 `id = 3000 + 序号` 当精灵 id 去取
`TrainingReference`，但 Catalog 块里真正的 id 是 **`game_id` 字段**。实测 **613/621（98.7%）** 条目的
`game_id != 3000 + 序号`（如 花衣蝶：`pet_000127` 的 `game_id=3141`，不是 3127）。

**证据**（`_probe14`，223 个可比池条目）：

| join 方式 | 花衣蝶推荐前六技能命中其可学集 | 全池"最优前四技能全合法" |
|---|---|---|
| 旧（3000+序号=3127，另一只精灵） | 1/6（只有「防御」） | **1 / 223** |
| 新（`game_id=3141`） | 6/6 | **189 / 223 = 84.8%** |

也就是说 `backend/engine/ai/data/training_reference.json`（30 MB，已入库）里绝大部分推荐
**属于别的精灵**：角色分桶（当前 174 攻击手/118 辅助/106 坦克）、以及所有基于它的结论都要重算。

**修法**：
1. `parse_catalog` 改读 depth-1 的 `game_id` 作为 id（`number/name/form/title/stage/relation_key/learnset_id` 一并保留；
   注意 `name` 必须走 depth-1 解析——块内嵌套表（如 `activities`）里有同名 `name`，会串）。
2. 用 `game_id` 作键取 `TrainingReference`；`by_number` 仍按 `number` 组织条目。
3. 新增自校验断言：**推荐技能 ∩ 该精灵可学集 ≥ 85%**（现在会立刻失败，正好当门禁）。
4. 重导入 → 重跑角色分桶 → 用新数据重取 `test_sprite_roles.py` 的抽样期望（喵喵那条可能不变，花衣蝶类会变）。

**待一并核对的开放项（已结清，2026-09-20）**：`data/related/*`（487×3 个"精灵技能/可学技能石/血脉技能"文件）
已**删除**——它只是 `data/sprites/*.json` 里 `skills`/`stone_skills`/`bloodline_skills` 三个字段的"名字版导出"，
却已经过时（343/487 个可学技能石缺了新导入的技能、33+34 个落后、6 个是孤儿文件），而且对合法性模型零贡献
（`池 ∪ 精灵技能 ∪ 血脉技能` 与加上可学技能石后命中率完全相同：98.3%），全仓唯一消费者是个一次性调试脚本。
**权威源统一为 `data/sprites`**；`模块:Pets/data/Learnsets` 里 `stone_skills` 恒为空（311 个 learnset 全 0），
技能石机制不在该模块，未来若要做"带等级的人读视图"应从 Learnsets + `模块:Pets/data/Skills` 重新导出。

## 1. 培养五元组与合法性

`SpriteBuild = {skills(1–4), bloodline, nature, talent(3 项拉满), item(队级)}`

### 1.1 合法集（每个精灵各自的可用资源）

- **技能池 `S(sprite)`** = `data/sprites/{编号}_{显示名}.json` 的 `skills`（本系可学）∪ `stone_skills`（技能石）∪
  {所选血脉的血液技能}（`bloodline_skills[血脉]`）；技能必须能在 `data/skills/{name}.json` 里构建。
  池子 `sprite_random_pool.json` 已等于前两项的并集，实现时直接用池 + 血脉技能即可。
- **血脉池 `B(sprite)`** = `species.bloodline_skills` 的键 ∪ `{'首领'}`（当
  `sprite_db.leader_form_candidates(number, appearance)` 非空）；剔除 `LOCKED_BLOODLINES`（污染/奇异）。
  （池里 344 条中有 105 条同编号存在首领形态——这个数取自本地精灵数据；"wiki 首选首领的比例"要等 P0 重导入后重算。）
- **性格池 `N`** = `NATURE_TABLE` 的 30 个（wiki 性格词表与本地**完全一致**，已验证）。
- **天赋 `T`** = 6 项里取 3 项拉满（引擎 iv 语义：`iv_fixed` 三项=10，其余 0）。
- **道具（队级）**：`首领血脉 ∈ 队伍 → 必须「进化之力」`；否则「愿力」。
  依据引擎判定：`battle_mechanics.item_variants` 要求 `bloodline=='首领'` 且非首领阶段且同编号有首领形态；
  `愿力` 要求 `bloodline ∈ ELEMENTAL_BLOODLINES`（首领血脉精灵用不了愿力）。进化之力只有 1 次、且会用
  首领形态种族值 + 原 IV/性格重算六维。

### 1.2 无冲突规则（硬不变量）

生成顺序固定为 **血脉 → 技能 → 主攻 → 天赋 → 性格**，于是：

1. `skills ⊆ S(sprite)`，无重复，1–4 个。
2. **主攻 `M`** = 技能中攻击技能的类型（物攻/魔攻）；两类都有时取 wiki 技能权重更高的一类。
3. 若 `M` 存在：`T` 必须含 `M` 的 stat；性格**加项 ∈ T**、**减项 ∉ T**、**减项 ≠ M 的 stat**。
4. 若 `M` 不存在（纯辅助/坦克）：`T` = wiki 天赋前三，性格加项 ∈ T、减项 ∉ T。
5. 首领血脉 → 队级道具 = 进化之力（§1.1）；首领血脉只能给"同编号有首领形态"的精灵。
6. （P1 增强）首领血脉精灵：天赋/性格在**首领形态面板**下也要满足第 3 条（进化后不塌）。

用户举的反例都被 2–3 条覆盖：天赋加物攻 + 性格减物攻 → 违规；天赋加物攻但技能没有物攻 → 由
技能修复（第 3 步）先把主攻握在手里，或在没有攻击技能时按第 4 条改天赋。

### 1.3 修复算法（把违规组合拉回合规）

`repair(entry_pvp, sprite) -> (build, fix_log)`：

- **技能**：按 wiki 技能权重降序 → 过滤到 `S(sprite)` → 去重 → 取前 4；不足 4 个时按角色模板补
  （沿用 `_role_skills` 的"输出 3–4 攻击 / 辅助坦克 1–2 攻击"）；若一个攻击技能都没有而天赋投了攻击，
  用 `S(sprite)` 里最强的攻击技能替掉权重最低的状态技能。
- **天赋**：取 wiki 天赋前三；若缺 `M` 的 stat，把权重最低那项换成 `M`。
- **性格**：在 wiki 性格榜里取权重最高且满足 1.2-3 的；榜内没有就在 30 性格里按同规则挑；
  再退化为"只要求减项 ∉ T"。
- 全过程记 `fix_log`，落盘统计修复率（§3 门禁要用）。

### 1.4 两种取法

| | BC 预训练数据 | 自博弈 |
|---|---|---|
| 技能 | wiki 权重降序取合规前四（**最优**） | 合法集上按权重**不放回加权抽 4 个** |
| 血脉 | 权重最高的**合法**者 | 合法子集上按权重抽 |
| 天赋 | wiki 前三（含 `M` 修正） | 按权重**不放回抽 3 项** |
| 性格 | §1.2-3 规则下权重最高者 | 按权重抽，违规则**在合规子集上条件重抽**（上限 8 次，超限取修复值） |
| 道具 | 队内首领血脉 → 进化之力，否则愿力 | 同左 |

性格的条件重抽在数学上等价于"在合规子集上取 wiki 权重的条件分布"——既按占比随机，又天然剔除冲突。

## 2. 接入点

- 新模块 `backend/engine/ai/data/build_from_reference.py`：
  `legal_skills / legal_bloodlines / optimal_build / sample_build / repair / validate_build / item_for_team`。
- `train.py::build_team`：`_role_skills` + `_role_iv_nature` → `sample_build`；角色分桶只保留"谁进队"。
- `train.py::_random_item` → `item_for_team(specs, meta_item)`；`bc_record.py:36` 的自造道具同样改走它。
- BC 生成：`native/tools/gen_bc_data.py` 支持 `ROCO_BUILD_MODE=optimal|sample`（BC 用 optimal，自博弈用 sample）。
- meta 队：生成逻辑不动，但 `validate_meta_teams` 增加同样的一致性校验（含"首领血脉队须声明进化之力"、
  技能须在 `S(sprite)` 内）。
- 对拍夹具：`differential/recorder._seeded_teams` 是独立实现，不受改build影响；但按项目惯例，数据一改仍要重录夹具。

## 3. 分布差异（有意为之，需知情）

BC 只吃最优配置，自博弈吃真实占比分布——这是"BC 当先验、自博弈学应对多样性"的刻意迁移。代价：策略网在
非最优配置上的先验偏弱。**建议 P1 做一个 A/B**：BC 100% 最优 vs 95% 最优 + 5% 抽样，比较微调起点。
另外要清楚：wiki 占比是**玩家实际使用分布**（含强弱偏差），不是均匀探索分布。

## 4. 验证门禁

1. **零违规**：344 池条目 × 两种取法 × 每次抽样，`validate_build` 违规数 = 0。
2. **合法率**：最优取法下"wiki 前四技能全合法"比例 ≥85%（P0 后基线 84.8%）；不合法者被过滤而非报错。
3. **分布保真**：抽样 10k 次，实测频率 vs wiki 权重（KL / χ²）；重抽+修复率 <10%。
4. **首领规则**：含首领血脉的队 100% 带进化之力，否则 100% 带愿力；进化之力队至少 1 只
   `bloodline=='首领'` 且 `leader_form_candidates` 非空。
5. **覆盖**：344/344 条目都能拿到 build（无 wiki 数据的 40 条走角色模板 + 数值分位兜底）。
6. **回归**：`pytest -x --tb=short` 全绿；`test_sprite_roles.py` 期望值按修正后数据重取。

## 5. 实施顺序

1. **P0** 修 importer（game_id 对位）→ 重导入 → 重跑角色分桶 → 更新测试期望 → 提交。
2. **P1** `build_from_reference.py` + 单测（先不接线）：合法性、修复、分布三段。
3. **P2** 接入 `train.py` / `gen_bc_data.py`（模式开关）+ meta 队校验 + 200 局冒烟。
4. **P3** 重生成 BC 数据（10000 局，每支 meta 队约 130 局）→ BC 预训练 → 自博弈微调。
