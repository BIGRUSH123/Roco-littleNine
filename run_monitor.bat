@echo off
REM 终端监控 - 使用虚拟环境

echo ========================================
echo 训练终端监控
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
echo 启动终端监控...
echo 使用 Python: %PYTHON_EXE%
echo 按 Ctrl+C 停止
echo.

%PYTHON_EXE% training_monitor.py %*

pause
