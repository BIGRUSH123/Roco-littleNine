@echo off
REM 终端监控 - 使用虚拟环境

echo ========================================
echo 训练终端监控
echo ========================================
echo.

echo 激活虚拟环境...
call env\Scripts\activate.bat

echo.
echo 启动终端监控...
echo 按 Ctrl+C 停止
echo.

python training_monitor.py %*

pause
