# 训练监控 UI 使用指南

## 📦 两种监控工具

我们提供了两种训练监控工具：

### 1. Web UI（Streamlit）- 功能丰富 ⭐⭐⭐

**特点**：
- 📊 实时图表和可视化
- 📈 多维度性能分析
- 🎨 美观的 Web 界面
- 🔄 自动刷新

**使用方法**：

```bash
# 安装依赖
pip install streamlit plotly pandas

# 启动 UI
streamlit run training_ui.py

# 自动打开浏览器：http://localhost:8501
```

**截图预览**：
- 训练进度实时跟踪
- 损失/准确率曲线
- MCTS 性能对比
- 对局统计分析
- 系统资源监控

---

### 2. 终端监控（Terminal）- 轻量简洁 ⭐⭐

**特点**：
- 💻 纯终端界面
- ⚡ 无额外依赖
- 🚀 快速启动
- 📝 实时日志

**使用方法**：

```bash
# 直接运行（无需安装依赖）
python training_monitor.py

# 自定义刷新间隔
python training_monitor.py --refresh 10

# 自定义日志目录
python training_monitor.py --log-dir backend/engine/ai/log/run1
```

**界面示例**：

```
================================================================================
                         🚀 MCTS 训练监控
================================================================================

📊 训练状态
--------------------------------------------------------------------------------
  迭代进度: 3/10  [████████████░░░░░░░░░░░░░░░░] 30.0%

训练样本              胜率                  已用时间              剩余时间              
12450                58.3%                2h 15m              7h 12m              

🚀 性能指标
--------------------------------------------------------------------------------
  吞吐量: 88 样本/秒
  加速比: 1.97x (并行化)
  总加速: 5.31x (Python 2.48x × Cython 1.09x × 并行 1.97x)

📍 当前阶段: 训练阶段
--------------------------------------------------------------------------------
  [██████████████████████████████████████████████████████████░░] 90.0%  (18/20)

📈 最近指标
--------------------------------------------------------------------------------
  训练损失: 0.3250  |  验证损失: 0.3870  |  验证准确率: 73.20%

⚙️  系统状态
--------------------------------------------------------------------------------
  CPU: 78%  |  内存: 6.2 GB  |  MCTS Workers: 4

📝 最近日志
--------------------------------------------------------------------------------
  [14:23:45] INFO    迭代 3/10 完成，胜率 58.3%
  [14:20:12] INFO    自我博弈完成: 200 局, 2,450 样本
  [14:18:30] INFO    训练 epoch 18/20, loss=0.325
  [14:15:05] WARN    对局 #145 超时 (7.8min)
  [14:12:20] INFO    MCTS 并行搜索: 0.68s (1.92x)

================================================================================
  刷新时间: 2026-06-13 14:25:30  |  按 Ctrl+C 退出
================================================================================
```

---

## 🚀 推荐使用流程

### 方案 A：单独窗口监控

```bash
# 终端 1：启动训练
python -m backend.engine.ai.train \
    --battles 200 \
    --sims 800 \
    --mcts-parallel \
    --mcts-workers 4 \
    --run-name experiment1

# 终端 2：启动监控
python training_monitor.py --log-dir backend/engine/ai/log/experiment1
```

### 方案 B：后台训练 + 前台监控

```bash
# 后台启动训练
nohup python -m backend.engine.ai.train \
    --battles 200 \
    --sims 800 \
    --mcts-parallel \
    --mcts-workers 4 \
    --run-name experiment1 \
    > train.log 2>&1 &

# 前台监控
python training_monitor.py --log-dir backend/engine/ai/log/experiment1
```

### 方案 C：Web UI 监控（推荐）

```bash
# 终端 1：启动训练
python -m backend.engine.ai.train --battles 200 --sims 800 --mcts-parallel

# 终端 2：启动 Web UI
streamlit run training_ui.py

# 浏览器打开 http://localhost:8501
```

---

## 📊 功能对比

| 功能 | Web UI | 终端监控 |
|------|--------|---------|
| 实时刷新 | ✅ | ✅ |
| 图表可视化 | ✅ | ❌ |
| 损失曲线 | ✅ | ✅（文本）|
| 性能分析 | ✅ | ✅ |
| 对局统计 | ✅ | ✅ |
| 系统监控 | ✅ | ✅ |
| 历史数据 | ✅ | ❌ |
| 导出报告 | ✅ | ❌ |
| 依赖要求 | 高 | 无 |
| 启动速度 | 慢 | 快 |

**建议**：
- 开发/调试：使用**终端监控**（快速启动）
- 生产/演示：使用 **Web UI**（功能丰富）

---

## 🎯 监控指标说明

### 训练状态

- **迭代进度**：当前迭代 / 总迭代数
- **训练样本**：已收集的训练样本总数
- **胜率**：当前模型的胜率（vs 上一版本）
- **时间估算**：已用时间和预计剩余时间

### 性能指标

- **吞吐量**：每秒处理的样本数
- **加速比**：相比基准版本的加速倍数
  - Python 优化：2.48x
  - Cython：1.09x
  - 并行化：1.97x
  - **总计：5.31x**

### 当前阶段

- **自我博弈**：MCTS 自我对局收集数据
- **训练阶段**：神经网络训练
- **评估阶段**：评估新模型 vs 最优模型
- **保存模型**：保存检查点

### 最近指标

- **训练损失**：训练集上的损失函数值
- **验证损失**：验证集上的损失函数值
- **验证准确率**：验证集上的策略预测准确率

### 系统状态

- **CPU**：CPU 使用率
- **内存**：内存占用
- **MCTS Workers**：并行 worker 数量

---

## ⚙️ 配置选项

### Web UI 配置

在侧边栏可以配置：

- **日志目录**：训练日志所在目录
- **自动刷新**：是否自动刷新
- **刷新间隔**：刷新频率（秒）
- **显示选项**：显示详细信息/图表

### 终端监控配置

命令行参数：

```bash
python training_monitor.py \
    --log-dir backend/engine/ai/log/experiment1 \  # 日志目录
    --refresh 5                                     # 刷新间隔（秒）
```

---

## 🐛 故障排查

### 问题 1：Web UI 无法启动

**错误**：`ModuleNotFoundError: No module named 'streamlit'`

**解决**：
```bash
pip install streamlit plotly pandas
```

### 问题 2：监控数据不更新

**原因**：日志目录路径错误

**解决**：
```bash
# 检查日志目录是否存在
ls backend/engine/ai/log/

# 指定正确的路径
python training_monitor.py --log-dir 正确的路径
```

### 问题 3：终端监控显示乱码

**原因**：终端不支持 Unicode 字符

**解决**：
- Windows：使用 Windows Terminal 或 PowerShell
- Linux/Mac：应该正常工作

---

## 📝 TODO：实际数据集成

当前监控使用模拟数据。要集成实际训练数据：

### 1. 修改训练代码输出 JSON 日志

```python
# 在 train.py 中
import json

# 每轮迭代后
metrics = {
    "iteration": iteration,
    "samples": len(X),
    "win_rate": win_rate,
    "train_loss": train_loss,
    "val_loss": val_loss,
    "val_acc": val_acc,
    "timestamp": time.time(),
}

with open(f"{log_dir}/metrics.jsonl", "a") as f:
    f.write(json.dumps(metrics) + "\n")
```

### 2. 监控工具读取 JSON 日志

```python
# 在 training_monitor.py 中
def read_latest_metrics(log_dir):
    metrics_file = Path(log_dir) / "metrics.jsonl"
    if not metrics_file.exists():
        return None

    with open(metrics_file) as f:
        lines = f.readlines()
        if lines:
            return json.loads(lines[-1])  # 最新一行
    return None
```

---

## 🎉 使用建议

1. **开发阶段**：
   - 使用终端监控（快速、无依赖）
   - 关注性能指标和错误日志

2. **生产训练**：
   - 使用 Web UI（功能丰富）
   - 保存监控截图和报告

3. **长时间训练**：
   - 后台运行训练
   - Web UI 随时查看
   - 设置通知（训练完成时）

4. **性能调优**：
   - 对比不同配置的性能
   - 分析瓶颈阶段
   - 优化超参数

---

## 🚀 快速开始

```bash
# 最简单的使用方式
python training_monitor.py
```

就这么简单！🎉
