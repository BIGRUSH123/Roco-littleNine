# Superpowers 文档索引

本目录记录项目重大功能的设计文档（specs/）和实现计划（plans/）。每条标注当前状态，便于快速定位有效文档。

## 状态说明

- **[done]** — 已完成并上线，对应功能已在 README 或代码中可用
- **[active]** — 开发中或部分完成，仍有未勾选的任务待实现
- **[stale]** — 已过时或被更新方案替代，保留作为历史参考

---

## Plans (实现计划)

| 文件 | 状态 | 说明 |
|---|---|---|
| [`2026-05-17-ir-vm-engine-plan.md`](plans/2026-05-17-ir-vm-engine-plan.md) | **[done]** | IR/VM 战斗引擎实现计划。已上线（见 `backend/vm/`、`backend/sim/`），README 第 70 行描述了编译器-虚拟机流水线。Git 历史显示多次 perf 优化（`6c39e12`、`c8c4c52` 等）已应用此架构。 |
| [`2026-05-18-ir-compiler-plan.md`](plans/2026-05-18-ir-compiler-plan.md) | **[done]** | IR 类型化 + 编译层三层重构。已合入主线，`backend/vm/compiler/`、`ir_skill.py`、`ir_trait.py` 等文件均存在。所有技能/特性现由 JSON → Compiler → IR 路径加载。 |
| [`2026-05-19-battle-visualization-plan.md`](plans/2026-05-19-battle-visualization-plan.md) | **[done]** | 精灵对战可视化 + 回合时间线回放。已上线：`frontend/src/components/BattleArena.vue` (32KB)、`TimelineReplay.vue`、`TypeChart.vue` 均存在。README 明确提到"可视化对战"和"战斗回放"功能。 |
| [`2026-05-31-backtrack-import-export-plan.md`](plans/2026-05-31-backtrack-import-export-plan.md) | **[done]** | 回溯 & 导入导出实现计划。已上线为 **v1.1**（见 README 第 38-44 行）。`backend/engine/serializer.py` 提供序列化层，`backend/engine/snapshot.py` 实现快照。Commit `695b119` 修复了 snapshot key 偏移 bug，`42cf677` 更新了 README 至 v1.1。 |

---

## Specs (设计文档)

| 文件 | 状态 | 说明 |
|---|---|---|
| [`2026-05-17-ir-vm-engine-design.md`](specs/2026-05-17-ir-vm-engine-design.md) | **[done]** | IR VM 引擎架构设计。对应 plan 已实施完成，本 spec 是其设计蓝图。 |
| [`2026-05-18-ir-compiler-design.md`](specs/2026-05-18-ir-compiler-design.md) | **[done]** | IR 编译器设计文档。4-Pass 技能编译和 3-Pass 特性编译已实现（见 `backend/vm/compiler/passes/`）。 |
| [`2026-05-18-ir-vm-engine-technical-report.md`](specs/2026-05-18-ir-vm-engine-technical-report.md) | **[done]** | IR VM 引擎技术报告。总结实施过程与架构决策，属于已完成功能的回顾文档。 |
| [`2026-05-19-battle-visualization-design.md`](specs/2026-05-19-battle-visualization-design.md) | **[done]** | 精灵对战可视化设计文档。Token 体系 + GSAP 动画 + 三栏布局均已实现（对应 plan 已完成）。 |
| [`2026-05-31-backtrack-import-export-design.md`](specs/2026-05-31-backtrack-import-export-design.md) | **[done]** | 回溯 & 导入导出设计文档。序列化层架构和快照机制已实施（v1.1 功能）。 |

---

## 治理建议

1. **新增功能时**：先写 spec 到 `specs/YYYY-MM-DD-<name>-design.md`，经评审后再写 plan 到 `plans/` 下，并在本索引中添加 `[active]` 条目。
2. **功能完成后**：将对应 plan/spec 状态改为 `[done]`，并在说明栏中简述上线证据（commit hash、README 引用或关键文件路径）。
3. **方案废弃后**：状态改为 `[stale]`，说明栏中注明被哪个新方案替代或为何过时。**不要删除文档**——历史决策有参考价值。
4. **索引更新频率**：每次新增/完成/废弃 plan/spec 时同步更新本索引，保持一致性。

---

最后更新：2026-06-17（P1b 治理）
