@echo off
REM 训练监控 UI - 使用虚拟环境

echo ========================================
echo 训练监控 UI
echo ========================================
echo.

echo 激活虚拟环境...
call env\Scripts\activate.bat

echo.
echo 检查依赖...
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo [错误] 未安装 streamlit
    echo 正在安装依赖...
    pip install streamlit plotly pandas
    echo.
)

echo.
echo 启动监控 UI...
echo 浏览器将自动打开 http://localhost:8501
echo.
echo 按 Ctrl+C 停止
echo.

streamlit run training_ui.py

pause
