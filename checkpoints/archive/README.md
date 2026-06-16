# checkpoints/archive/ — 归档清单

本目录保留已不再活跃的历史实验，作为对比基线或回溯参考。这些目录无代码引用，可随时删除（但请先确认无其它人使用）。

## 清单（治理时间：2026-06-17）

| 路径 | 大小 | 最后修改 | 来源 / 说明 |
|---|---|---|---|
| `mcts_v2_formal/` | ~73 MB | 2026-06-05 | 20 轮 MCTS 自我博弈训练，已被 `formal_v1` 取代。文件大小 3.31 MB 提示与主线网络结构不同。完整 iter1-20 + best + 最终 model_rl.pt |
| `modular_v4/` | ~10 MB | 2026-06-03 | ModularBattleNet 早期版本，2 轮迭代 + best。`service/agent.py` 旧注释提及过 modular_v3 路径，v4 短命未上主线 |
| `validation_leaf16_20260608/` | ~12 MB | 2026-06-08 | leaf=16 配置的验证实验，仅 1 轮 + 终态 model_rl.pt。属于参数扫描产物 |
| `legacy_root_model_rl.pt` | ~15 MB | 2026-06-09 | 原 `checkpoints/model_rl.pt`，由 `train.py` 默认 `--output` 路径产生。无所属实验目录，治理时归档 |

> 单文件大小 5.74 MB 与 3.31 MB 的差异源自不同的网络结构（实体编码维度或隐层尺寸不同）。如需加载，先确认 `core/model.py` 当前结构是否兼容。

## 清理策略

如需进一步省空间，对每个归档目录只保留 `model_rl_best.pt`（如有）即可，中间 `iter*.pt` 已无评估价值。
