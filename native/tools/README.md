# native/tools 索引

这个目录有 200+ 个脚本，其中大部分是**历史上一次性使用的调试/取证脚本**，保留是为了让当时的
结论可追溯（文档里引用了它们）。真正**现役**（工程依赖、文档复现命令、回归门禁）的是下面这些。

运行方式统一为（PowerShell 7）：

```powershell
env\python.exe native\tools\<脚本名> --help
```

## 决策/评估（AI 侧）

| 脚本 | 作用 |
|---|---|
| `gen_bc_data.py` | 生成 BC 训练数据（专家口径；`plan_depth=1` 的规划层现在默认开启） |
| `eval_expert_change.py` | 单条规则 / 规划层的**同局配对 A/B**（`--ab trade\|antiloop\|defend\|status\|plan\|...`） |
| `eval_fixed_rosters.py` | 固定阵容（非对称）评估，出实战分位 |
| `eval_vs_previous_agent.py` | 对上一版专家的配对评估 |
| `eval_prediction_mix.py` | 预判混合（belief/EV 层）评估，含两臂 A/B |
| `eval_bc_holdout.py` / `final_eval.py` | BC 留出集 / 终评 |
| `measure_prediction_stakes.py` | 预判收益度量（猜错代价分布） |
| `tune_ev_params.py` | EV 参数校准 |

## 审计（"为什么输/亏在哪"）

| 脚本 | 作用 |
|---|---|
| `audit_skill_choice_regret.py` | **E0**：出招 vs 一回合最优的 regret（`--leaf board\|value`、`--agent rule\|plan`） |
| `audit_ruleagent_decisions.py` | 规则命中/漏用的决策审计 |
| `audit_counter_and_lives.py` | 应对三角与心力事件的审计 |
| `audit_selfplay_pool.py` / `audit_selfplay_samples.py` / `audit_selfplay_distribution.py` | 自博弈数据/池分布 |
| `determinism_probe.py` | 引擎跨进程确定性探针（指纹必须一致） |

## 对战骨架

| 脚本 | 作用 |
|---|---|
| `duel_harness.py` | 双 agent（LLM 子 agent 或人）**固定流程**对战：`new/prompt/answer/apply/status/record/show/selftest/notes` |
| `duel_teams/*.json` | 可复用的队伍规格 |

## 数据/资源

`import_training_reference.py`（抓 wiki 训练参考）、`scrape_meta_teams.py`（meta 队）、
`pool_summary.py` / `rebuild_pool.py` / `explore_pool.py` / `show_team_gen.py`（精灵池）、
`dump_species.py` / `dump_sprite_files.py` / `dump_static_tables.py` / `export_battle_spec.py`（导出）、
`check_pre_species_cycles.py`（萌化链不变量）、`fixture_summary.py`（差分夹具）、`skill_editor.py` / `trait_editor.py`。

## 性能/门禁

`bench_encode_share.py`、`bench_worker_topology.py`、`bench_selfplay_pool.py`、
`batch_gate.py`、`mcts_gate.py`、`mask_gate.py`、`gate_*.py`、`validate_v2.py`。

## 其余（历史脚本，按前缀识别）

- `dbg*.py` / `dbg_*.py` —— 当时排查某个具体分歧/机制的一次性探针（约 90 个）。
- `diag_*.py` —— 诊断脚本（配装/池/形态等）。
- `append_notes*.py` —— 给文档追加实验笔记的一次性脚本。
- `probe_*.py`、`read_logs*.py`、`quick_grep.py`、`find_*.py`、`flt_*.py`、`smoke_hook.py`、
  `run_with_traceback.py`、`migrate_*.py`、`fix_*.py` —— 同类。

这些**不删**：文档里的结论常常引用它们（"本次数字来自某个 dbg 脚本"），删了会破坏可追溯性。
需要清理时按前缀批量 git mv 到 `scratch/` 并同步更新文档引用即可。

## 约定

- 脚本入库，转储忽略（`.gitignore` 里挡掉 `native/tools/*.txt|*.log|*.err|*.out|*.pkl|_*.json|dbg_*.json`）。
- 新脚本请写清用途与复现命令到模块 docstring，并在引用它的文档里给出完整命令行。
