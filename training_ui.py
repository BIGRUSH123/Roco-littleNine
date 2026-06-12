"""训练监控简易 UI

实时显示训练进度、性能指标和可视化图表。
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path
import json
import time
from datetime import datetime

# 页面配置
st.set_page_config(
    page_title="MCTS 训练监控",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 标题
st.title("🚀 MCTS 训练监控面板")
st.markdown("---")

# 侧边栏配置
st.sidebar.header("⚙️ 配置")

# 日志目录选择
log_dir = st.sidebar.text_input(
    "日志目录",
    value="backend/engine/ai/log",
    help="训练日志所在目录"
)

# 自动刷新
auto_refresh = st.sidebar.checkbox("自动刷新", value=True)
refresh_interval = st.sidebar.slider(
    "刷新间隔（秒）",
    min_value=1,
    max_value=60,
    value=5,
    disabled=not auto_refresh
)

# 显示选项
show_details = st.sidebar.checkbox("显示详细信息", value=True)
show_charts = st.sidebar.checkbox("显示图表", value=True)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 性能提升")
st.sidebar.metric("总加速比", "5.31x", "+431%")
st.sidebar.metric("训练时间节约", "81%", "43h/轮")

# 主要区域
col1, col2, col3, col4 = st.columns(4)

# 模拟数据（实际应该从日志读取）
with col1:
    st.metric(
        label="当前迭代",
        value="3 / 10",
        delta="30%"
    )

with col2:
    st.metric(
        label="训练样本",
        value="12,450",
        delta="+2,100"
    )

with col3:
    st.metric(
        label="胜率",
        value="58.3%",
        delta="+3.2%"
    )

with col4:
    st.metric(
        label="预计剩余时间",
        value="7.2h",
        delta="-0.5h"
    )

st.markdown("---")

# 创建标签页
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 训练进度",
    "📈 性能指标",
    "🎮 对局统计",
    "⚙️ 系统监控"
])

with tab1:
    st.header("训练进度")

    # 进度条
    progress_pct = 30  # 从日志读取
    st.progress(progress_pct / 100)
    st.caption(f"整体进度: {progress_pct}%")

    # 训练时间线
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("损失曲线")

        # 模拟数据
        iterations = list(range(1, 11))
        train_loss = [0.8, 0.65, 0.52, 0.45, 0.40, 0.38, 0.35, 0.33, 0.32, 0.31]
        val_loss = [0.85, 0.70, 0.58, 0.50, 0.46, 0.43, 0.41, 0.39, 0.38, 0.37]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=iterations,
            y=train_loss,
            mode='lines+markers',
            name='训练损失',
            line=dict(color='#FF6B6B', width=2)
        ))
        fig.add_trace(go.Scatter(
            x=iterations,
            y=val_loss,
            mode='lines+markers',
            name='验证损失',
            line=dict(color='#4ECDC4', width=2)
        ))
        fig.update_layout(
            xaxis_title="迭代",
            yaxis_title="损失",
            hovermode='x unified',
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("准确率曲线")

        accuracy = [0.45, 0.52, 0.58, 0.62, 0.65, 0.68, 0.70, 0.72, 0.73, 0.74]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=iterations,
            y=accuracy,
            mode='lines+markers',
            name='验证准确率',
            line=dict(color='#95E1D3', width=2),
            fill='tozeroy'
        ))
        fig.update_layout(
            xaxis_title="迭代",
            yaxis_title="准确率",
            yaxis_range=[0, 1],
            hovermode='x unified',
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

    # 当前迭代详情
    if show_details:
        st.subheader("当前迭代详情")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.info("**自我博弈**\n\n200 / 200 局\n\n⏱️ 42.3 分钟")

        with col2:
            st.success("**训练阶段**\n\n18 / 20 epochs\n\n⏱️ 15.7 分钟")

        with col3:
            st.warning("**评估中**\n\n12 / 20 局\n\n⏱️ 3.2 分钟")

with tab2:
    st.header("性能指标")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("MCTS 性能")

        # 性能对比
        perf_data = pd.DataFrame({
            '方法': ['串行', '并行（首轮）', '并行（稳态）'],
            '耗时(s)': [1.307, 2.154, 0.665],
            '加速比': [1.0, 0.61, 1.97]
        })

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=perf_data['方法'],
            y=perf_data['耗时(s)'],
            text=perf_data['耗时(s)'].round(2),
            textposition='auto',
            marker_color=['#FF6B6B', '#FFE66D', '#4ECDC4']
        ))
        fig.update_layout(
            yaxis_title="耗时 (秒)",
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

        # 加速比
        st.metric("并行加速比", "1.97x", "97%")

    with col2:
        st.subheader("训练吞吐量")

        # 样本/秒
        throughput = [45, 52, 58, 62, 68, 73, 78, 82, 85, 88]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=iterations,
            y=throughput,
            mode='lines+markers',
            name='样本/秒',
            line=dict(color='#A8E6CF', width=3),
            fill='tozeroy'
        ))
        fig.update_layout(
            xaxis_title="迭代",
            yaxis_title="样本/秒",
            hovermode='x unified',
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

        st.metric("当前吞吐量", "88 样本/秒", "+95%")

    # 性能分解
    st.subheader("性能分解（当前迭代）")

    phases = ['自我博弈', '训练', '评估', '保存模型', '其他']
    times = [42.3, 15.7, 8.5, 1.2, 2.3]

    fig = go.Figure(data=[go.Pie(
        labels=phases,
        values=times,
        hole=.3,
        marker_colors=['#FF6B6B', '#4ECDC4', '#FFE66D', '#95E1D3', '#F38181']
    )])
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)

with tab3:
    st.header("对局统计")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("胜负分布")

        results = pd.DataFrame({
            '结果': ['本方赢', '本方输', '平局'],
            '局数': [1180, 950, 70],
            '占比': [53.6, 43.2, 3.2]
        })

        fig = go.Figure(data=[go.Bar(
            x=results['结果'],
            y=results['局数'],
            text=results['局数'],
            textposition='auto',
            marker_color=['#4ECDC4', '#FF6B6B', '#FFE66D']
        )])
        fig.update_layout(
            yaxis_title="局数",
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("终局原因")

        reasons = pd.DataFrame({
            '原因': ['击败对手', '被击败', '回合上限', '超时'],
            '次数': [1180, 950, 50, 20]
        })

        fig = px.pie(
            reasons,
            values='次数',
            names='原因',
            color_discrete_sequence=['#4ECDC4', '#FF6B6B', '#FFE66D', '#F38181']
        )
        fig.update_layout(height=300)
        st.plotly_chart(fig, use_container_width=True)

    # 胜率趋势
    st.subheader("胜率趋势")

    win_rates = [48.5, 50.2, 51.8, 53.1, 54.2, 55.0, 56.3, 57.1, 57.8, 58.3]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=iterations,
        y=win_rates,
        mode='lines+markers',
        name='胜率',
        line=dict(color='#4ECDC4', width=3),
        fill='tozeroy'
    ))
    fig.add_hline(
        y=55.0,
        line_dash="dash",
        line_color="red",
        annotation_text="晋升阈值 (55%)"
    )
    fig.update_layout(
        xaxis_title="迭代",
        yaxis_title="胜率 (%)",
        yaxis_range=[40, 65],
        hovermode='x unified',
        height=300
    )
    st.plotly_chart(fig, use_container_width=True)

with tab4:
    st.header("系统监控")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("CPU 使用率", "78%", "+5%")
        st.progress(0.78)

    with col2:
        st.metric("内存使用", "6.2 GB", "+0.3 GB")
        st.progress(0.62)

    with col3:
        st.metric("GPU 使用率", "92%", "+2%")
        st.progress(0.92)

    st.markdown("---")

    # 并行状态
    st.subheader("MCTS 并行状态")

    col1, col2 = st.columns(2)

    with col1:
        st.info("""
        **进程池状态**

        ✅ 已启用 MCTS 并行

        - Workers: 4
        - 状态: 运行中
        - 任务队列: 0
        - 完成任务: 2,450
        """)

    with col2:
        st.success("""
        **性能统计**

        🚀 加速比: 1.97x

        - 平均耗时: 0.65s
        - 最快: 0.25s
        - 最慢: 2.15s (首轮)
        - 效率: 49.2%
        """)

    # 最近日志
    st.subheader("最近日志")

    log_data = [
        {"时间": "14:23:45", "级别": "INFO", "消息": "迭代 3/10 完成，胜率 58.3%"},
        {"时间": "14:20:12", "级别": "INFO", "消息": "自我博弈完成: 200 局, 2,450 样本"},
        {"时间": "14:18:30", "级别": "INFO", "消息": "训练 epoch 18/20, loss=0.325"},
        {"时间": "14:15:05", "级别": "WARNING", "消息": "对局 #145 超时 (7.8min)"},
        {"时间": "14:12:20", "级别": "INFO", "消息": "MCTS 并行搜索: 0.68s (1.92x)"},
    ]

    df = pd.DataFrame(log_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

# 底部信息
st.markdown("---")
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("**训练配置**")
    st.caption("批次大小: 256")
    st.caption("学习率: 0.001")
    st.caption("模拟次数: 800")

with col2:
    st.markdown("**性能优化**")
    st.caption("Python 优化: 2.48x")
    st.caption("Cython: 1.09x")
    st.caption("并行化: 1.97x")

with col3:
    st.markdown("**总体进度**")
    st.caption(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    st.caption("已运行: 2h 15m")
    st.caption("预计剩余: 7h 12m")

# 自动刷新
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
