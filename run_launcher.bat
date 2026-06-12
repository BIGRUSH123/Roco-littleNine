@echo off
REM 训练启动器 - 使用虚拟环境

echo ========================================
echo 训练启动器
echo ========================================
echo.

REM 检查虚拟环境 Python（支持多种路径）
if exist "env\python.exe" (
    echo ✅ 找到虚拟环境: env\python.exe
    set PYTHON_EXE=env\python.exe
) else if exist "env\Scripts\python.exe" (
    echo ✅ 找到虚拟环境: env\Scripts\python.exe
    set PYTHON_EXE=env\Scripts\python.exe
) else (
    echo ⚠️  虚拟环境不存在，使用系统 Python
    set PYTHON_EXE=python
)

echo.
echo 检查依赖...
%PYTHON_EXE% -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo [警告] 未安装 streamlit
    echo 正在安装依赖...
    %PYTHON_EXE% -m pip install streamlit plotly pandas
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
echo 使用 Python: %PYTHON_EXE%
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

%PYTHON_EXE% -m streamlit run training_launcher.py

pause
