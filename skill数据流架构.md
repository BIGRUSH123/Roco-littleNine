# Skill 数据流架构

> 最后更新：2026-05-06（P0 重整完成 — 统一 6 层命名前缀 + 层级模板）

---

## 六层架构总览

```
                          ┌──────────────────┐
                          │    用户界面层      │
                          │  (CLI / slash cmd) │
                          └────────┬─────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
  ┌─────▼─────┐            ┌───────▼───────┐          ┌──────▼──────┐
  │  注入层    │            │  实时问答层     │          │ 离线问答层   │
  │ inject-*  │            │  roco-*        │          │ ask-*       │
  │           │            │               │          │             │
  │ extract   │            │ suggest        │          │ research    │
  │ record    │            │               │          │             │
  │ ingest    │            │               │          │             │
  │ save      │            │               │          │             │
  │ defuddle  │            │               │          │             │
  └─────┬─────┘            └───────┬───────┘          └──────┬──────┘
        │                          │                          │
        └──────────┬───────────────┼──────────────────────────┘
                   │               │
            ┌──────▼──────┐  ┌─────▼──────┐
            │   检测层     │  |    计算引擎层   │
            │  verify-*   │  |    calc-*       │
            │             │  |                 │
            │ review(协调)│  | state           │
            │ noun        │  | speed           │
            │ claim       │  | damage          │
            │ record      │  |                 │
            │ consistency │  |                 │
                   │               │
            ┌──────▼───────────────▼──────┐
            │           Wiki 层            │
            │          wiki-*              │
            │                             │
            │  wiki (协调器)               │
            │  ├── wiki-query (查询)       │
            │  ├── wiki-tidy (整理)        │
            │  ├── wiki-fold (折叠)        │
            │  └── wiki-lint (检查)        │
            │                             │
            │  wiki/ (数据)               │
            │  ├── 精灵图鉴/ 技能图鉴/     │
            │  ├── 对战机制/               │
            │  ├── 对局记录/               │
            │  └── hot.md log.md _index   │
            └─────────────────────────────┘
```

---

## 各层职责与命名

### 注入层 (inject-*) — 外部数据 → wiki

将外部数据（字幕、网页、攻略、用户输入）转化为结构化格式。

| 技能 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `inject-extract` | 视频字幕 → 知识总结 | 字幕 .txt | `raw/summaries/{BV}.md` |
| `inject-record` | 视频字幕 → 对局记录草稿 | 字幕 .txt | `raw/records/{BV}.md` |
| `inject-ingest` | 游戏数据批量导入 | 网页/数据源 | wiki 页面 |
| `inject-save` | 对局记录/对手数据归档 | 结构化记录 | wiki/对局记录/ |
| `inject-defuddle` | 网页内容清洗 | 脏 HTML/文本 | 干净结构化文本 |

**数据流**：外部 → inject-* → verify-* → wiki

---

### 检测层 (verify-*) — 知识正确性验证

注入层和 wiki 之间的把关人。

| 技能 | 职责 | 输入 |
|------|------|------|
| `verify-review` | 协调器：路由到子技能 | 不确定时走此入口 |
| `verify-noun` | 专有名词验证（同音字/俗称/进化链/反查） | 名词列表 + 上下文 |
| `verify-claim` | 断言对比 + wiki 写入 | `raw/summaries/*.md` |
| `verify-record` | 对局记录审查 + 归档 | `raw/records/*.md` |
| `verify-consistency` | 跨页面语义一致性检查 | 全 wiki 扫描 |

**数据流**：inject-* 产出 → verify-review → 子技能 → wiki

---

### Wiki 层 (wiki-*) — 知识库维护

直接操作 wiki 文件系统。所有 wiki 读写都经过此层。

| 技能 | 职责 |
|------|------|
| `wiki` | 协调器：路由用户意图到正确的技能 |
| `wiki-query` | 结构化查询 wiki 内容 |
| `wiki-tidy` | 整理冗余、修复链接 |
| `wiki-fold` | 长页面折叠管理 |
| `wiki-lint` | 知识库健康检查 |

---

### 计算引擎层 (calc-*) — 数值计算与推演

从 wiki 读取公式和数据，执行确定性计算，输出结果供上层消费。

| 技能 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `calc-state` | 逐回合能量/HP/CD/buff 追踪 | 对局记录 | 带状态快照的对局记录 |
| `calc-speed` | 速度线计算（种族+个体+性格+技能修正） | 精灵名+配置 | 排序后的速度表 |
| `calc-damage` | 伤害范围估算（含属性克制+随机数浮动） | 攻守精灵+技能 | 伤害区间 |

**数据流**：wiki → calc-* → roco-* / ask-* / 用户

---

### 实时问答层 (roco-*) — 实时对局战术支持

用户 PVP 对局中，基于当前回合状态提供操作建议。

| 技能 | 职责 | 状态 |
|------|------|------|
| `roco-suggest` | 单回合操作建议 | ✅ 已有 |
| `roco-predict` | 对手操作预测 | 🔜 待建设 |
| `roco-plan` | 多回合线路规划 | 🔜 待建设 |

**数据流**：用户回合状态 → roco-* ← calc-* ← wiki

---

### 离线问答层 (ask-*) — 策略研究与分析

非实时的洛克王国 PVP 知识问答。

| 技能 | 职责 |
|------|------|
| `ask-research` | 阵容/战术/环境研究 |

**数据流**：用户分析请求 → ask-* ← wiki + calc-* + WebSearch

---

### 辅助工具

不属于任何层，但被多层共用：

| 技能 | 职责 |
|------|------|
| `obsidian-markdown` | Obsidian 格式处理 |
| `obsidian-bases` | Obsidian Bases 插件支持 |

---

## 层间数据协议

### 回合快照 (Turn Snapshot) — calc-* → roco-*

```yaml
turn_snapshot:
  turn: 6
  active:
    ours:
      name: 岚鸟
      hp: [400, 400]
      energy: 9
      buffs:
        - {name: 物攻+100%, source: 力量增效, remaining: null}
        - {name: 全技能能耗-3, source: 赤子之心(继承), remaining: null}
      debuffs:
        - {name: 萌化, source: 甜心续航, remaining: null}
      defense_cd: false
      available_skills:
        - {name: 水刃, cost: 1, counter: 状态}
    theirs:
      name: 圆号鱼
      hp: [???, ???]
      energy: 10
  bench:
    ours: [{name: 圣羽翼王, hp: 全满, energy: ~10}]
    theirs: [{name: 寂灭骨龙, hp: 全满, energy: ~10}]
  speed_line:
    - {name: 岚鸟, speed: 115, side: ours}
    - {name: 圆号鱼, speed: 105, side: theirs}
```

### 技能记录 (Skill Record) — inject-* → wiki

```yaml
skill_record:
  name: 水刃
  attribute: 水
  type: 物攻
  power: 115
  energy_cost: 4
  counter: 状态
  effects:
    - type: cost_reduction
      condition: counter_success
      value: {self: {energy_cost: -4}, permanent: true}
  source: wiki
  confidence: high
```

### 对手档案快照 (Opponent Snapshot) — wiki → roco-*

```yaml
opponent_snapshot:
  name: 阿梓
  style:
    switch_frequency: medium
    read_accuracy: low
    aggression: conservative
    common_openings: [圆号鱼首发甜心续航]
    weakness_patterns:
      - "劣势时选慢性死亡操作"
      - "化茧保命后不转化为反击"
  preferred_teams:
    - {archetype: 首领化消耗, winrate: 0/1}
```

---

## 新技能创建流程

1. 确定技能属于哪一层
2. 复制 `_templates/{layer}-template.md` 作为起点
3. 按模板填写：层级定位、数据流方向、工作流程、命名规则、不应做的事
4. 命名：`{layer-prefix}-{verb}`
5. 在 `wiki/SKILL.md` 路由表中注册

### 层级模板清单

| 模板文件 | 对应层 |
|---------|--------|
| `_templates/inject-template.md` | 注入层 (inject-*) |
| `_templates/verify-template.md` | 检测层 (verify-*) |
| `_templates/wiki-template.md` | Wiki 层 (wiki-*) |
| `_templates/calc-template.md` | 计算引擎层 (calc-*) |
| `_templates/roco-template.md` | 实时问答层 (roco-*) |
| `_templates/ask-template.md` | 离线问答层 (ask-*) |

---

## 优先级路线图

| 优先级 | 事项 | 状态 |
|--------|------|------|
| P0 | 统一 6 层命名前缀（inject-*/verify-*/wiki-*/calc-*/roco-*/ask-*） | ✅ 完成 |
| P0 | 创建 6 个层级模板 | ✅ 完成 |
| P1 | 拆分 verify-review → 4 个 verify-* 技能 | ✅ 完成 |
| P1 | 充实计算引擎层（calc-speed、calc-damage） | ✅ 完成 |
| P2 | 实时问答层首批技能（roco-suggest） | ✅ 完成 |
| P3 | 离线问答层扩充（ask-build、ask-analyze） | 待开始 |
