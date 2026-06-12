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
) else (
    if exist "env\Scripts\python.exe" (
        echo ✅ 找到虚拟环境: env\Scripts\python.exe
        set PYTHON_EXE=env\Scripts\python.exe
    ) else (
        echo ⚠️  虚拟环境不存在，使用系统 Python
        set PYTHON_EXE=python
    )
)

echo.
echo 使用 Python: %PYTHON_EXE%
echo.

REM 测试 Python 是否可用
%PYTHON_EXE% --version
if errorlevel 1 (
    echo.
    echo [错误] Python 无法运行！
    echo 请检查虚拟环境是否正确安装
    pause
    exit /b 1
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
    echo ✅ 依赖安装完成
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

%PYTHON_EXE% -m streamlit run training_launcher.py
if errorlevel 1 (
    echo.
    echo [错误] 启动失败！
    echo.
    pause
    exit /b 1
)

pause
