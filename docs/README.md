# 文档索引（docs/README.md）

本仓文档散在四处：`docs/`、`data/IR_GUIDE.md`（契约）、`backend/engine/ai/TRAINING.md`（训练）、
`native/PORTING_NOTES.md`（双实现）。**本文是唯一入口**——新加文档请登记到这里。

## 权威口径（改代码前先读，冲突时以它们为准）

| 文档 | 内容 | 什么时候必须读 |
|---|---|---|
| `data/IR_GUIDE.md` | **IR 契约**：寄存器、指令集、控制流、执行时序 | 任何引擎/机制改动（先改契约再改代码） |
| `docs/引擎机制对账-游戏描述图鉴.md` | 机制 ↔ 游戏描述**逐条对账** + 每轮修复/实验记录（含负结果） | 想知道"某个机制为什么这么实现"、或写实验记录 |
| `docs/培养方案-pvp口径.md` | 配装权威（95% 最优 + 5% 抽样、道具随血脉、队内编号唯一） | 动 AI 数据生成 / 队伍构建 |
| `docs/博弈-概率预判口径.md` | 信念 + 期望值层的口径与实测结论 | 动规则层 / EV / 预判 |
| `backend/engine/ai/TRAINING.md` | 训练全流程、门禁数字、数据事故记录 | 跑任何训练/评估 |
| `docs/洛克王国世界-PVP攻略要点.md` | 社区攻略整理（设计专家规则的依据） | 新增阵容专精专家 |

## 参考与记录

| 文档 | 内容 |
|---|---|
| `docs/博弈-双agent对战记录.md` | 双 agent 对战实验记录 |
| `docs/nrc-对账报告.md` | nrc wiki 数据抓取与对账 |
| `native/PORTING_NOTES.md` | Rust/Cython 双实现移植说明（含大量 py/rust 对拍细节） |
| `native/tools/README.md` | 工具索引（现役 / 历史取证脚本，200+ 脚本怎么找） |
| `native/tools/remote/README.md` | 远端 DSW 实验通道（登录、上传、跑批） |
| `checkpoints/README.md`、`checkpoints/archive/README.md` | 训练产物索引（权重本体不入库） |
| `docs/superpowers/{specs,plans}` | gstack 工具生成的规格/计划（非项目口径） |
| `AGENTS.md` / `CLAUDE.md` | Agent 协作约定与目录约定 |

## 目录约定（与根 README「项目结构」一致）

- **生产代码只放 `backend/`**；`native/` 是双实现与工具，`frontend/` 是前端。
- **测试**：与包代码同目录的 `test_*.py`（如 `backend/engine/test_integration.py`）放模块级/集成测试；
  跨模块或端到端的新测试放 `backend/tests/`。`pytest` 的 `testpaths` 已收敛为 `backend`。
- **一次性脚本**：放 `native/tools/`，脚本入库、**它写出的转储不入库**
  （`.gitignore` 已按后缀/`_` 前缀覆盖：`*.txt/.log/.err/.out/.pkl`、`_*.json/.html/.js`、`dbg_*.json`）。
- **实验记录**：结论性文字进 `docs/`（对账文档的实验小节）；原始日志留在被忽略的
  `backend/engine/ai/log/`，**不要**把 doc 的引用指向那里（新开的目录不入库，链接会失效）。
- **契约变更**：`data/**/*.json` 与 `IR_GUIDE` 是数据面契约，改数据要重录差分夹具
  （`backend/engine/differential/`）。

## 已知的体积问题

入库 654 MB，其中 454 MB 是 73 个 >1 MB 的二进制：`frontend/public/sprites/*.png` 占 68 个（约 430 MB），
`backend/engine/ai/data/training_reference.json` 31 MB。`.git` 已 765 MB。
若要瘦身：立绘与 reference 表迁 Git LFS 或改按需拉取（需要重写历史，属独立决策）。

## 自查命令（改完代码跑一遍）

```powershell
env\python.exe -m pytest -q            # 全量 968 条（testpaths = backend）
env\python.exe -m ruff check backend   # 项目声明的 dev 依赖；pre-commit 里也配了 ruff + ruff-format
env\python.exe native\tools\find_undefined_names.py backend   # 秒级扫"未绑定名"（ruff 没装时的兜底）
```

2026-09-23 的组织清理台账：`/tests/` 里 69 条测试收编到 `backend/tests/sim/`（此前只在本机、不入库）；
`pyproject.toml` 的 `testpaths` 由失效的 `["tests","roco/tests","backend"]` 收敛为 `["backend"]`；
ruff 清掉 F821/F811/F841/F401/F541 共 39 处（含 4 处真 bug，见 §27）。

