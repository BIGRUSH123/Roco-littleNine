@echo off
chcp 65001 >nul
REM Terminal Monitor - Use Virtual Environment

echo ========================================
echo Training Terminal Monitor
echo ========================================
echo.

REM Check virtual environment Python (support multiple paths)
if exist "env\python.exe" (
    echo [OK] Found virtual environment: env\python.exe
    set PYTHON_EXE=env\python.exe
) else (
    if exist "env\Scripts\python.exe" (
        echo [OK] Found virtual environment: env\Scripts\python.exe
        set PYTHON_EXE=env\Scripts\python.exe
    ) else (
        echo [WARN] Virtual environment not found, using system Python
        set PYTHON_EXE=python
    )
)

echo.
echo Using Python: %PYTHON_EXE%
echo.
echo Starting terminal monitor...
echo Press Ctrl+C to stop
echo.

%PYTHON_EXE% training_monitor.py %*

pause
