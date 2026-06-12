# 🚀 使用虚拟环境运行 UI 工具

## 快速开始

### 方法 1：双击运行脚本（最简单）⭐⭐⭐

直接双击以下文件即可：

- **`run_launcher.bat`** - 训练启动器（配置参数 + 启动训练）
- **`run_monitor_ui.bat`** - 监控 UI（图表可视化）
- **`run_monitor.bat`** - 终端监控（轻量版）

脚本会自动：
1. ✅ 激活虚拟环境
2. ✅ 检查并安装依赖
3. ✅ 启动对应工具

---

### 方法 2：命令行运行

```bash
# 训练启动器
run_launcher.bat

# 监控 UI
run_monitor_ui.bat

# 终端监控
run_monitor.bat
```

---

## 虚拟环境说明

### 自动检测

训练启动器会自动检测并使用 `env` 虚拟环境：

- ✅ **存在**：使用 `env\Scripts\python.exe`
- ⚠️ **不存在**：使用当前 Python（会显示警告）

### 手动创建虚拟环境（如果需要）

```bash
# 创建虚拟环境
python -m venv env

# 激活虚拟环境
env\Scripts\activate.bat

# 安装依赖
pip install -r requirements.txt
```

---

## 完整流程示例

### 场景：第一次使用训练启动器

**步骤 1：启动训练启动器**
```bash
双击 run_launcher.bat
```

或命令行：
```bash
run_launcher.bat
```

**步骤 2：浏览器自动打开**
- 地址：`http://localhost:8501`
- 如未自动打开，手动访问该地址

**步骤 3：配置参数**
- 在「基本参数」标签页配置
- 推荐：
  - 每轮对局数：200
  - MCTS 模拟：800
  - ✅ 启用 MCTS 并行化
  - Worker 数：4

**步骤 4：启动训练**
- 切换到「启动训练」标签页
- 查看命令预览
- 点击「🚀 启动训练」
- 界面显示：「✅ 使用虚拟环境: ...\env\Scripts\python.exe」

**步骤 5：监控进度**

打开新终端：
```bash
双击 run_monitor.bat
```

---

## 各工具对比

| 工具 | 脚本 | 虚拟环境 | 依赖 |
|------|------|---------|------|
| 训练启动器 | `run_launcher.bat` | ✅ 自动 | Streamlit |
| 监控 UI | `run_monitor_ui.bat` | ✅ 自动 | Streamlit+Plotly |
| 终端监控 | `run_monitor.bat` | ✅ 自动 | 无 |

---

## 故障排查

### Q1：双击脚本后立即关闭？

**原因**：虚拟环境不存在

**解决**：
```bash
# 创建虚拟环境
python -m venv env

# 重新运行
run_launcher.bat
```

---

### Q2：提示"找不到 streamlit"？

**原因**：依赖未安装

**解决**：脚本会自动安装，等待完成即可

或手动安装：
```bash
env\Scripts\activate.bat
pip install streamlit plotly pandas
```

---

### Q3：训练启动后找不到模块？

**原因**：虚拟环境缺少训练依赖

**解决**：
```bash
env\Scripts\activate.bat
pip install torch numpy pandas
# 或安装完整依赖
pip install -r requirements.txt
```

---

### Q4：想用系统 Python 而不是虚拟环境？

**方法 1**：重命名虚拟环境
```bash
ren env env_backup
```

**方法 2**：直接运行（不用脚本）
```bash
streamlit run training_launcher.py
```

---

## 推荐工作流

### 流程 1：完整 UI 体验

**终端 1：启动器**
```bash
run_launcher.bat
```

**终端 2：监控**
```bash
run_monitor.bat
```

---

### 流程 2：命令行 + 终端监控

**终端 1：命令行训练**
```bash
env\Scripts\activate.bat
python -m backend.engine.ai.train --battles 200 --sims 800 --mcts-parallel
```

**终端 2：监控**
```bash
run_monitor.bat
```

---

## 验证虚拟环境

### 检查虚拟环境是否激活

```bash
# 激活后，命令提示符前会显示 (env)
(env) D:\projects\Roco-LittleNine>

# 检查 Python 路径
where python
# 应该显示：D:\projects\Roco-LittleNine\env\Scripts\python.exe
```

### 检查已安装的包

```bash
env\Scripts\activate.bat
pip list
```

---

## 快速命令参考

```bash
# ========== 使用虚拟环境 ==========

# 激活虚拟环境
env\Scripts\activate.bat

# 退出虚拟环境
deactivate

# 安装依赖
pip install streamlit plotly pandas

# 安装训练依赖
pip install torch numpy pandas


# ========== 运行 UI 工具 ==========

# 训练启动器（自动使用虚拟环境）
run_launcher.bat

# 监控 UI（自动使用虚拟环境）
run_monitor_ui.bat

# 终端监控（自动使用虚拟环境）
run_monitor.bat


# ========== 手动运行（已激活虚拟环境）==========

# 训练启动器
streamlit run training_launcher.py

# 监控 UI
streamlit run training_ui.py

# 终端监控
python training_monitor.py

# 训练命令
python -m backend.engine.ai.train --battles 200 --sims 800 --mcts-parallel
```

---

## 总结

✅ **自动化**：脚本自动激活虚拟环境
✅ **智能检测**：UI 自动使用虚拟环境中的 Python
✅ **依赖管理**：自动检查和安装依赖
✅ **简单易用**：双击即可运行

**最简单的使用方式：**
```bash
双击 run_launcher.bat
```

**就这么简单！** 🎉
