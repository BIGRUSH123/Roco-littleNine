# checkpoints/ — 模型权重存放与治理

## 目录约定

```
checkpoints/
├── README.md                ← 本文档（入库）
├── formal_v1/               ← active 主线模型，被 service/agent.py 默认引用
├── exp2/                    ← active 最新实验，待评估
└── archive/                 ← 已归档的历史实验
    ├── README.md            ← 归档清单（入库）
    ├── mcts_v2_formal/
    ├── modular_v4/
    ├── validation_leaf16_20260608/
    └── legacy_root_model_rl.pt
```

- **active 区**：根目录直接子目录，通常仅 1–2 个，对应"当前在用"或"待评估"的实验。
- **archive 区**：已不再活跃但保留作为对比基线的实验。整个 `checkpoints/` 已被 `.gitignore` 排除，仅本仓库的两份 README 入库，其他 `.pt` 文件不进 git。

## 新增实验命名规范

- 用 `<标识>_<日期 YYYYMMDD>` 或 `<标识>_v<n>`，例如 `mcts_leaf32_20260620/`。
- 每个实验目录里放一份本地 `NOTES.md`（不入库），记录：跑的时间、关键配置、结果摘要、是否值得保留。
- 评估只需 `model_rl_best.pt`（gate 晋升的最佳模型）+ `model_rl_iter1.pt`（基线），中间 `iter*.pt` 可按需清理以省空间。

## 何时归档

- 不再被任何代码或文档引用、且超过 30 天未使用 → 移入 `archive/`。
- 仅用于历史复现或对比基线 → 保留在 `archive/`，但删除非 best 的 iter 文件。
- 完全失去价值（无对比意义、网络结构已不可加载）→ 直接删除，**不要进 archive**。

## 如何获取/分享模型

由于 `.pt` 不进 git，团队成员或新机器需要模型时：

1. 从训练机器手动拷贝相应目录到本地 `checkpoints/<name>/`。
2. 或重新执行 `python -m backend.engine.ai.train ...`（见 `backend/engine/ai/TRAINING.md`）。
3. 长期方案：考虑接入 git-lfs 或对象存储（S3 / 阿里云 OSS）。
