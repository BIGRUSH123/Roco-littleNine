@echo off
REM 训练启动器 - 使用虚拟环境

echo ========================================
echo 训练启动器
echo ========================================
echo.

echo 激活虚拟环境...
if not exist "env\Scripts\activate.bat" (
    echo [错误] 虚拟环境不存在！
    echo 请先创建虚拟环境：python -m venv env
    pause
    exit /b 1
)

call env\Scripts\activate.bat

echo.
echo 检查依赖...
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo [警告] 未安装 streamlit
    echo 正在安装依赖...
    pip install streamlit plotly pandas
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo.
)

echo.
echo ========================================
echo 启动训练启动器
echo ========================================
echo 浏览器将自动打开 http://localhost:8501
echo.
echo 功能：
echo   - 配置训练参数
echo   - 一键启动训练
echo   - 自动使用虚拟环境
echo.
echo 按 Ctrl+C 停止
echo ========================================
echo.

streamlit run training_launcher.py

pause
