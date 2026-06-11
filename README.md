# 格斗小九 (Roco) — 洛克王国 PVP 对战模拟器

《洛克王国·世界》回合制 PVP 对战模拟器，提供完整的战斗引擎、可视化对战界面与 AI 对战能力。v1.1 在数据驱动核心之上新增回合回溯、对局导入导出功能——所有技能、特性、精灵均由 JSON 数据驱动，无需修改代码即可自由定制。

---

## 快速开始

**环境要求**：Python 3.10+、Node.js 18+

```bash
# 1. 安装后端依赖
pip install -e .

# 2. 启动后端 API 服务
python -m uvicorn backend.api.main:app --reload --port 8000

# 3. 新终端：安装并启动前端
cd frontend
npm install
npm run dev

# 4. 浏览器打开 http://localhost:5173
```

---

## 功能

### 可视化对战

精灵选择、技能释放、HP/能量条的实时渲染。完整的回合制对战流程：回合开始 → 行动选择 → 行动结算 → 回合结束，支持应对、迸发、蓄力、传动等核心战斗机制。

### 战斗回放

通过 TimelineReplay 组件逐步查看每回合的详细过程——伤害计算、特性触发、状态变化，适合复盘分析。

### 回合回溯 <sup>v1.1</sup>

一键回溯到任意历史回合——引擎自动保存每回合开始前的完整状态快照。回溯后可选择不同策略继续对战，尤其适合 AI 训练（MCTS 探索树、自我博弈）和对局复盘。

### 导入导出 <sup>v1.1</sup>

支持对局和队伍的 JSON + 文本双格式导出与导入。前端提供导出/导入对话框，CLI 工具 `python -m roco.serializer` 支持命令行操作。导出文件带时间戳和回合信息，方便归档和分享。

### 队伍编辑

自由组建 6 精灵队伍——选择精灵、配置技能、调整定位，所见即所得。

### 批量对战

一键发起 N 轮自动对战，自动聚合胜率、平均回合数、耗时等统计数据，用于测试队伍强度或 Agent 策略。

### AI 对手

内置 RuleAgent（基于启发式评分的规则 AI）、RandomAgent（随机选择），也支持加载外部 Python 脚本作为自定义 Agent。提供锦标赛模式，可进行多 Agent 循环赛并输出胜负矩阵。

强化学习管线位于 `backend/engine/ai/`：当前主线使用实体化状态编码、`ModularBattleNet` 双头网络、17 维动作空间、MCTS 自我博弈、门控晋升和固定种子 checkpoint 评估。完整训练、评估和实战建议流程见 `backend/engine/ai/TRAINING.md`。

### Wiki 知识库

Markdown 格式的游戏维基，包含 701 篇精灵图鉴、494 篇技能图鉴、174 篇特性文档，以及完整的对战机制说明（属性克制、状态效果、回合流程等）。按属性分类组织，支持 Obsidian 阅读。

---

## 核心特色：数据驱动架构

格斗小九与其他模拟器最大的不同在于：**技能效果和特性不是硬编码的**。

所有游戏内容都以 JSON 文件定义（`data/` 目录），由一条编译器—虚拟机流水线加载执行：

```
JSON 数据  →  编译器（解析/校验/注入命中/排序）  →  RISC 风格 IR 操作码  →  VM 虚拟机执行
```

这意味着：
- **新增技能**：只需编写一个 JSON 文件，组合现有操作码
- **新增特性**：定义触发条件 + 触发效果即可，无需触碰战斗逻辑
- **调整平衡**：直接修改 JSON 数值，立即生效

当前数据规模：**465 只精灵 · 472 个技能 · 166 个特性**，全部由 JSON 驱动。

---

## 自定义技能

### 技能 JSON 结构

每个技能是一个 JSON 文件，核心字段如下：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 技能名称 |
| `element` | string | 属性（火/水/草/电/冰/龙/萌……共 18 种） |
| `skill_type` | string | 技能类型：`物攻` / `魔攻` / `状态` |
| `power` | int | 基础威力（`状态`类型为 0） |
| `energy_cost` | int | 能量消耗 |
| `counter` | string | 应对判定类别（可选），用于双向应对机制 |
| `effects` | array | 效果列表，每个效果是一个操作码对象 |
| `description` | string | 技能描述文本 |

### 基础技能示例

最简单的攻击型技能——仅造成伤害，无需额外效果：

```json
{
  "name": "地震",
  "element": "地",
  "skill_type": "物攻",
  "power": 190,
  "energy_cost": 10,
  "effects": [],
  "description": "✦对敌方精灵造成物理伤害。"
}
```

当 `effects` 为空时，VM 会自动生成默认命中效果——根据 `power` / `element` / `skill_type` 计算伤害。

### 带效果的技能示例：滚雪球

```json
{
  "name": "滚雪球",
  "element": "冰",
  "skill_type": "物攻",
  "power": 55,
  "energy_cost": 3,
  "counter": "状态",
  "effects": [
    {
      "when": {
        "cond": "counter_succeeded"
      },
      "then": [
        {
          "op": "abnormal",
          "target": "sprite_opp",
          "name": "冻结",
          "stacks": 4,
          "scope": "persistent"
        },
        {
          "op": "mult_mod",
          "target": "skill_off_0",
          "attr": "power_mult",
          "value": 2
        }
      ],
      "else": [
        {
          "op": "abnormal",
          "target": "sprite_opp",
          "name": "冻结",
          "stacks": 2,
          "scope": "persistent"
        }
      ]
    }
  ],
  "description": "✦造成物伤，敌方获得2层冻结，应对状态：额外获得2层，本次技能威力翻倍。"
}
```

这个技能展示了 **条件分支（when/then/else）** 的用法：根据是否应对成功，产生不同的效果组合。

### 常用操作码一览

`effects` 中的每个对象代表一个 IR 操作码，通过 `op` 字段指定类型。以下是常用操作码：

| 操作码 | 用途 | 示例参数 |
|--------|------|----------|
| `abnormal` | 施加异常状态 | `name: "灼烧"`, `stacks: 4` |
| `mult_mod` | 数值乘算修正 | `attr: "atk"`, `value: 1.4`, `mode: "add"` |
| `stat_stage` | 属性等级变化 | `stat: "speed"`, `steps: 15` |
| `weather` | 设置天气 | `name: "雨天"`, `ttl: 5` |
| `charge` | 蓄力/释放 | `charge_skill: "蓄力技能ID"` |
| `interrupt` | 中断敌方技能 | |
| `heal` | 回复生命 | `ratio: 0.3` |
| `shield` | 施加护盾 | `amount: 100` |
| `flag_set` | 设置标记（冷却等） | `flag: "cooldown"`, `ttl: 2` |
| `escape` | 逃离/换宠 | |

完整的操作码列表见 `backend/vm/ops/` 目录，共 30 余种。

### 条件系统

效果的触发可以通过 `when` 包裹条件判断：

```json
{
  "when": { "cond": "counter_succeeded" },
  "then": [ /* 应对成功时执行 */ ],
  "else": [ /* 应对失败时执行 */ ]
}
```

支持的条件类型包括：`counter_succeeded`（应对成功）、`target_fainted`（目标昏厥）、`hit_crit`（暴击）、`compare`（数值比较）、`skill_use`（技能使用）、`always`（始终触发）等。

---

## 自定义特性

### 特性 JSON 结构

特性通过**观察者模式**实现——监听战斗事件，条件满足时自动触发效果：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 特性名称 |
| `description` | string | 特性描述文本 |
| `effects` | array | 观察者列表 |

每个观察者包含：

| 字段 | 说明 |
|------|------|
| `op` | 固定为 `"observer"` |
| `listen` | 监听时机（见下表） |
| `cond` | 触发条件 |
| `then` | 触发后执行的效果列表（操作码数组） |
| `scope` | 作用范围：`persistent`（持续）/ `battlefield`（战场） |

### 监听时机

| 时机 | 触发点 |
|------|--------|
| `pre_calc` | 伤害计算前，可修改威力/攻击/防御 |
| `pre_defend` | 防御方被命中前 |
| `post_skill` | 技能执行完成后 |
| `post_damage` | 造成伤害后 |
| `post_ko` | 击倒敌方精灵后 |
| `post_entry` | 精灵入场时 |
| `post_leave` | 精灵离场时 |
| `turn_start` | 回合开始时 |
| `turn_end` | 回合结束时 |
| `post_counter` | 应对判定完成时 |
| `post_abnormal_tick` | 异常状态触发时 |

### 特性示例：勇敢

```json
{
  "name": "勇敢",
  "description": "携带的能耗大于3的技能，威力+40%。",
  "effects": [
    {
      "op": "observer",
      "cond": {
        "cond": "compare",
        "q": "energy_cost",
        "of": "skill_off_0",
        "op": "gt",
        "value": 3
      },
      "then": [
        {
          "op": "mult_mod",
          "target": "skill_off_0",
          "attr": "power_mult",
          "value": 1.4
        }
      ],
      "listen": "pre_calc",
      "scope": "battlefield"
    }
  ]
}
```

这个特性在 **伤害计算前（`pre_calc`）** 检查使用的技能能耗是否大于 3（`compare` 条件），满足时将技能威力乘以 1.4。

### 特性示例：威慑

```json
{
  "name": "威慑",
  "description": "打断敌方时，被打断技能进入2回合冷却。",
  "effects": [
    {
      "op": "observer",
      "cond": {
        "cond": "counter_succeeded"
      },
      "then": [
        {
          "op": "flag_set",
          "target": "skill_opp_current",
          "flag": "cooldown",
          "value": true,
          "scope": "persistent",
          "ttl": 2
        }
      ],
      "listen": "post_counter",
      "scope": "battlefield"
    }
  ]
}
```

这个特性在 **应对成功后（`post_counter`）** 触发，给被打断的敌方技能设置 2 回合冷却标记。

### 组合条件

条件支持逻辑组合，实现复杂的触发规则：

```json
{
  "cond": "or",
  "conditions": [
    { "cond": "sprite_entered", "of": "sprite_self" },
    { "cond": "always" }
  ]
}
```

以上示例（来自"暴食"特性）表示：**精灵入场时 OR 始终**——在 `post_entry` 和 `turn_start` 两个时机都会检查并触发。

---

## 项目结构

```
格斗小九/
├── backend/
│   ├── api/          FastAPI 服务端（对战 API、精灵/技能数据接口）
│   ├── engine/       VM 执行器、快照、回放、序列化、特性观察者、AI 训练管线
│   ├── vm/           Battle VM 编译器管线（解析 → 校验 → 排序 → 执行）
│   ├── sim/          模拟层（精灵、技能、对战流程、回合记录、回溯快照）
│   └── common/       共享模型、数值公式、常量、精灵种族值数据库
├── frontend/         Vue 3 + Pinia + Tailwind CSS 前端 SPA
│   └── src/
│       ├── components/   12 个对战相关组件
│       └── stores/       Pinia 状态管理（对战状态、精灵素材）
├── roco/             SDK 包：AI Agent 协议、VM 桥接层、锦标赛运行器
├── data/             游戏数据：精灵(465) / 技能(472) / 特性(166) JSON
├── wiki/             Markdown 知识库：精灵图鉴 / 技能图鉴 / 对战机制
├── tests/            集成测试
├── scripts/          工具脚本（demo.py 等）
└── examples/         自定义 Agent 示例
```

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行全部测试
pytest -x --tb=short

# 代码检查
ruff check .

# 自动修复
ruff check --fix .

# 运行 Agent 锦标赛 Demo
python scripts/demo.py
```

亦可使用 Makefile：

```bash
make dev-install   # 安装开发依赖
make test          # 运行测试
make lint          # 代码检查
make demo          # Agent 锦标赛 Demo
```

---

## 许可证

MIT
