@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Starting Django server from "%cd%"
echo Using Python: %PYTHON_EXE%

%PYTHON_EXE% manage.py runserver 0.0.0.0:8000

if errorlevel 1 (
    echo.
    echo Server stopped with an error.
    pause
)
