@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
if not exist .venv ( %PY% -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection and Python installation.
  pause
  exit /b 1
)
echo.
echo Service POS is running: http://127.0.0.1:8000
echo Press Ctrl+C to stop the server.
echo.
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
