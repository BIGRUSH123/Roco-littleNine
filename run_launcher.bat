@echo off
chcp 65001 >nul
REM Training Launcher - Use Virtual Environment

echo ========================================
echo Training Launcher
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

REM Test if Python is available
%PYTHON_EXE% --version
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not available!
    echo Please check if virtual environment is installed correctly
    pause
    exit /b 1
)

echo.
echo Checking dependencies...
%PYTHON_EXE% -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo [WARN] streamlit not installed
    echo Installing dependencies...
    %PYTHON_EXE% -m pip install streamlit plotly pandas
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
    echo.
)

echo.
echo ========================================
echo Starting Training Launcher
echo ========================================
echo Browser will open at http://localhost:8501
echo.
echo Features:
echo   - Configure training parameters
echo   - Start training with one click
echo   - Auto use virtual environment
echo.
echo Press Ctrl+C to stop
echo ========================================
echo.

%PYTHON_EXE% -m streamlit run training_launcher.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start!
    echo.
    pause
    exit /b 1
)

pause
