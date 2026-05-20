# Changelog

格斗小九 (Roco) — Battle VM as a Platform.

## [0.1.0] — 2026-05-20

### T1 — SDK 核心包 (`roco/`)
- 创建 `roco/ai/` — `BattleAgent` Protocol + `ActionKind` 枚举 + frozen 观察类型 (`Action`, `BattleObservation`, `SpriteSnapshot`)
- `RandomAgent` 参考实现
- `pyproject.toml` — `setuptools.build_meta`，Python 3.10+，[dev] extras (pytest, ruff)

### T2 — VM-to-SDK 桥接层 (`roco/bridge.py`)
- `build_observation()` — 可变 sim 状态 → 不可变 SDK 观察
- `adapt_agent()` — SDK `BattleAgent` → sim `Agent` 协议适配器
- `legal_actions_filter()` — 合法操作过滤（昏厥=仅换宠，充能=仅释放，传动主轴不可选）
- 12 个桥接测试：观察结构、对称性、合法操作完整覆盖

### T3 — TournamentRunner (`roco/tournament.py`)
- 循环赛引擎：`TournamentResult` / `MatchResult` dataclass
- Checkpoint/resume (JSON)，Ctrl+C 优雅中断
- CLI: `python -m roco.tournament agent_a.py agent_b.py --rounds 50`
- Agent 加载器：importlib 动态加载 + auto-discover (`_find_agent`, `_try_agent`)
- ASCII 胜负矩阵输出，排名表
- 13 个锦标赛测试：矩阵、排名、checkpoint 往返、错误隔离

### T4 — RuleAgent 分支覆盖测试
- `test_rule_agent.py`: 21 个测试覆盖 `choose_action` 全部 9 个分支
- 昏厥换宠 / 无替补聚能 / 道具使用 / 低HP换宠 / 低能量聚能 / 充能释放 / 技能评分 / 全部冷却聚能
- `choose_lead`, `choose_replacement`, `on_game_end` 覆盖

### T5 — Agent 模板 + README
- `examples/my_agent.py` — 15 行 `DamageAgent` 模板（`agent = DamageAgent()` 模块级导出）
- `README.md` — Quickstart, Agent 编写指南, Architecture 架构图, Development 说明

### T6 — 终端锦标赛 Demo (`scripts/demo.py`)
- HealBot + AggroBot + Random 三智能体 10 轮循环赛
- 实时进度条，ASCII 矩阵 + 排名输出
- `--quick` 标志 (3 轮)，墙钟时间统计

### T7 — API 智能体端点 + AgentSelector 组件
- `AGENT_REGISTRY` 白名单安全架构 (5 个已注册智能体：Random, DamageAgent, RuleAgent, HealBot, AggroBot)
- `_load_ai_agent()` — SDK bridge 适配器加载
- `GET /api/agents` + `GET /api/agents/{name}` (含 404)
- `AgentSelector.vue` — v-model 驱动，加载中/空状态/错误状态，本地持久化
- `InitRequest.ai_agent` — 战斗初始化时选择 AI 对手

### T8 — 错误信息标准化
- API 错误信息：简短提示 → "问题 → 原因 → 修复" 诊断格式
- CLI 错误信息：ImportError/ValueError 增加排查指引
- 前端：alert/toast/内联错误增加后端状态提示

### T9 — GitHub Actions CI
- `.github/workflows/ci.yml` — push/PR 触发
- 矩阵：ubuntu/windows × Python 3.10/3.11/3.12
- pytest + ruff check
- 全项目 ruff auto-fix (导入排序、未使用变量)

### T10 — 开发环境配置
- `Makefile` — install, dev-install, test, lint, lint-fix, demo, clean
- `.pre-commit-config.yaml` — trailing-whitespace, ruff, ruff-format
- `.gitignore` 更新 (pre-commit, .github 例外)

### T11 — 批量对战 API + 结果面板
- `POST /api/battle/batch` — N 轮聚合统计 (胜/负/平/胜率/平均回合/耗时)
- `BatchResults.vue` — 可视化结果面板 (胜率条, 统计卡片, 轮数选择)
- TeamSelection 集成批量测试按钮

### T12 — CHANGELOG.md
- 版本跟踪：`CHANGELOG.md`，遵循 Keep a Changelog 风格
- `pyproject.toml` `__version__ = "0.1.0"`
