@echo off
rem Daily tender/subsidy digest - called by Windows Task Scheduler.
rem Keep this file ASCII-only: cmd.exe misreads UTF-8 Chinese text.
rem %~dp0 locates the project folder so the Chinese path never appears here.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYTHON=C:\Users\user\anaconda3\python.exe

if not exist data mkdir data
echo ===== %date% %time% ===== >> data\daily_task.log
"%PYTHON%" -m app.run_daily >> data\daily_task.log 2>&1
if errorlevel 1 (
    echo run_daily failed, skip LINE push >> data\daily_task.log
    exit /b 1
)

rem Personalized push to subscribers (LINE token is read from .env)
"%PYTHON%" scripts\push_line.py >> data\daily_task.log 2>&1
