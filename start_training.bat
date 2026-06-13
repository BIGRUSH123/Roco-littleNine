@echo off
chcp 65001 >nul
REM Start Training - Run in background with log

echo ========================================
echo Start Training
echo ========================================
echo.

REM Check virtual environment Python
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

echo Using Python: %PYTHON_EXE%
echo.

REM Get parameters from user or use defaults
set /p BATTLES="Battles per iteration [200]: " || set BATTLES=200
set /p SIMS="MCTS simulations [800]: " || set SIMS=800
set /p ITERATIONS="Iterations [10]: " || set ITERATIONS=10
set /p WORKERS="MCTS workers [4]: " || set WORKERS=4
set /p RUN_NAME="Run name [default]: " || set RUN_NAME=

echo.
echo ========================================
echo Configuration
echo ========================================
echo Battles: %BATTLES%
echo Simulations: %SIMS%
echo Iterations: %ITERATIONS%
echo MCTS Workers: %WORKERS%
if not "%RUN_NAME%"=="" echo Run Name: %RUN_NAME%
echo.
echo Output will be saved to: training_output.log
echo ========================================
echo.

pause

echo.
echo Starting training in background...
echo.

REM Build command
set CMD=%PYTHON_EXE% -m backend.engine.ai.train --battles %BATTLES% --sims %SIMS% --iterations %ITERATIONS% --mcts-parallel --mcts-workers %WORKERS%

if not "%RUN_NAME%"=="" (
    set CMD=%CMD% --run-name %RUN_NAME%
)

REM Start training and redirect output to file
start "Training" /B %CMD% > training_output.log 2>&1

echo.
echo [OK] Training started in background!
echo.
echo To monitor progress:
echo   1. Open training_output.log
echo   2. Or run: run_monitor.bat
echo.
echo Press Ctrl+C in this window to stop
echo.

pause
