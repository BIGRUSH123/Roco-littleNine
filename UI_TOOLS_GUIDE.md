# 🎨 训练 UI 工具完整指南

## 三个 UI 工具

我们提供了三个互补的 UI 工具，满足不同需求：

### 1. 训练启动器 🚀 - 配置和启动

**文件**：`training_launcher.py`

**用途**：配置参数并启动训练

**特点**：
- ⚙️ 基本参数（对局数、MCTS、模型）
- 🔧 高级参数（20+ 可调参数）
- 👁️ 命令预览和配置摘要
- 🚀 一键启动训练
- ⏹️ 停止训练

**启动**：
```bash
streamlit run training_launcher.py
```

**最适合**：首次使用、参数调优

---

### 2. 训练监控 📊 - 实时监控

**文件**：`training_ui.py`

**用途**：监控训练进度和性能

**特点**：
- 📈 实时图表（损失、准确率）
- 📊 性能分析（MCTS、吞吐量）
- 🎮 对局统计（胜率、终局原因）
- ⚙️ 系统监控（CPU、内存、GPU）
- 🔄 自动刷新

**启动**：
```bash
streamlit run training_ui.py
```

**最适合**：训练进行中、数据分析

---

### 3. 终端监控 💻 - 轻量监控

**文件**：`training_monitor.py`

**用途**：终端实时监控

**特点**：
- 💻 纯终端，无需依赖
- ⚡ 快速启动
- 📊 关键指标展示
- 📝 最近日志
- 🔄 自动刷新

**启动**：
```bash
python training_monitor.py
```

**最适合**：远程服务器、快速查看

---

## 推荐使用流程

### 流程 A：完整 UI 体验（推荐新手）

```bash
# 步骤 1：配置和启动训练
streamlit run training_launcher.py
# → 在浏览器中配置参数
# → 点击「启动训练」

# 步骤 2：打开新终端，启动监控
streamlit run training_ui.py
# → 浏览器新标签页自动打开
# → 实时查看图表和指标
```

**优点**：
- ✅ 全程可视化
- ✅ 参数配置简单
- ✅ 监控功能丰富

---

### 流程 B：启动器 + 终端监控（推荐）

```bash
# 步骤 1：Web 配置启动
streamlit run training_launcher.py
# → 配置参数并启动

# 步骤 2：终端监控
python training_monitor.py
# → 同一终端或新终端
# → 轻量级实时监控
```

**优点**：
- ✅ 配置方便（Web）
- ✅ 监控轻量（终端）
- ✅ 资源占用少

---

### 流程 C：命令行 + 终端监控（推荐老手）

```bash
# 步骤 1：命令行启动（后台）
python -m backend.engine.ai.train \
    --battles 200 \
    --sims 800 \
    --mcts-parallel \
    --mcts-workers 4 \
    --iterations 10 \
    --run-name exp1 &

# 步骤 2：终端监控
python training_monitor.py --log-dir backend/engine/ai/log/exp1
```

**优点**：
- ✅ 完全控制
- ✅ 适合脚本化
- ✅ 无 GUI 依赖

---

## 功能对比

| 功能 | 启动器 | 监控 UI | 终端监控 |
|------|--------|---------|----------|
| **参数配置** | ✅ 图形界面 | ❌ | ❌ |
| **启动训练** | ✅ 一键启动 | ❌ | ❌ |
| **实时监控** | ⚠️ 简化 | ✅ 丰富 | ✅ 基础 |
| **图表可视化** | ❌ | ✅ | ❌ |
| **性能分析** | ❌ | ✅ | ✅ |
| **系统监控** | ❌ | ✅ | ✅ |
| **依赖要求** | Streamlit | Streamlit+Plotly | 无 |
| **启动速度** | 慢 | 慢 | 快 |
| **资源占用** | 中 | 高 | 低 |

---

## 使用场景

### 场景 1：第一次训练

**推荐**：训练启动器

**原因**：
- 参数多，界面配置更清晰
- 有帮助提示和推荐值
- 实时预览命令

**操作**：
```bash
streamlit run training_launcher.py
```

---

### 场景 2：日常训练

**推荐**：命令行 + 终端监控

**原因**：
- 熟悉参数，命令行更快
- 终端监控足够用
- 资源占用少

**操作**：
```bash
# 后台启动训练
python -m backend.engine.ai.train --battles 200 --sims 800 --mcts-parallel &

# 前台监控
python training_monitor.py
```

---

### 场景 3：调参实验

**推荐**：训练启动器 + 监控 UI

**原因**：
- 快速调整参数
- 对比不同配置效果
- 图表分析性能

**操作**：
```bash
# 终端 1：启动器
streamlit run training_launcher.py

# 终端 2：监控
streamlit run training_ui.py
```

---

### 场景 4：远程服务器

**推荐**：命令行 + 终端监控

**原因**：
- SSH 连接，终端最稳定
- 无需 X11 转发
- 资源占用最小

**操作**：
```bash
# SSH 连接到服务器
ssh user@server

# 启动训练
nohup python -m backend.engine.ai.train ... > train.log 2>&1 &

# 监控
python training_monitor.py
```

---

### 场景 5：演示/汇报

**推荐**：监控 UI

**原因**：
- 图表美观专业
- 多维度展示
- 易于理解

**操作**：
```bash
streamlit run training_ui.py
```

---

## 快速命令参考

```bash
# ============ 启动器 ============
# 基础使用
streamlit run training_launcher.py

# 指定端口
streamlit run training_launcher.py --server.port 8501


# ============ 监控 UI ============
# 基础使用
streamlit run training_ui.py

# 指定端口
streamlit run training_ui.py --server.port 8502

# 远程访问
streamlit run training_ui.py --server.address 0.0.0.0


# ============ 终端监控 ============
# 基础使用
python training_monitor.py

# 指定日志目录
python training_monitor.py --log-dir backend/engine/ai/log/exp1

# 自定义刷新间隔
python training_monitor.py --refresh 10


# ============ 命令行训练 ============
# 标准配置
python -m backend.engine.ai.train \
    --battles 200 \
    --sims 800 \
    --mcts-parallel \
    --mcts-workers 4

# 后台运行
python -m backend.engine.ai.train ... > train.log 2>&1 &
```

---

## 常见组合

### 组合 1：全 GUI（新手友好）

```bash
# 终端 1
streamlit run training_launcher.py

# 终端 2
streamlit run training_ui.py
```

**优点**：全程可视化，易上手
**缺点**：资源占用较大

---

### 组合 2：混合模式（推荐）

```bash
# 终端 1：Web 启动器
streamlit run training_launcher.py

# 终端 2：终端监控
python training_monitor.py
```

**优点**：配置方便，监控轻量
**缺点**：无图表

---

### 组合 3：全终端（高效）

```bash
# 后台训练
python -m backend.engine.ai.train ... &

# 前台监控
python training_monitor.py
```

**优点**：快速、轻量、稳定
**缺点**：需记参数

---

## 安装依赖

### 最小依赖（终端监控）

```bash
# 无需安装，直接使用
python training_monitor.py
```

### 标准依赖（启动器 + 监控）

```bash
pip install streamlit plotly pandas
```

### 验证安装

```bash
streamlit --version
```

---

## 故障排查

### Q1：Streamlit 无法启动

**错误**：`ModuleNotFoundError: No module named 'streamlit'`

**解决**：
```bash
pip install streamlit plotly pandas
```

---

### Q2：浏览器无法打开

**原因**：防火墙或端口占用

**解决**：
```bash
# 手动打开
http://localhost:8501

# 或指定其他端口
streamlit run training_launcher.py --server.port 8502
```

---

### Q3：训练启动器无法启动训练

**原因**：路径或权限问题

**解决**：
1. 检查是否在项目根目录
2. 验证 Python 环境
3. 查看终端错误输出

---

### Q4：监控显示模拟数据

**原因**：当前版本使用模拟数据

**解决**：等待实际数据集成（TODO）

---

## 总结

### 三个工具，各有所长

| 工具 | 定位 | 最适合 |
|------|------|--------|
| 训练启动器 | 配置启动 | 新手、调参 |
| 监控 UI | 详细监控 | 数据分析、演示 |
| 终端监控 | 轻量监控 | 日常使用、远程 |

### 推荐搭配

- **新手**：启动器 + 监控 UI
- **日常**：启动器 + 终端监控
- **高级**：命令行 + 终端监控

---

## 立即开始

```bash
# 最简单的开始方式
streamlit run training_launcher.py
```

**3 分钟配置，一键启动！** 🚀
