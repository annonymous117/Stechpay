@echo off
title Stechpay Dev Launcher
echo ========================================
echo Starting Stechpay Backend and Frontend...
echo ========================================

:: Backend (Django)
start "Stechpay Backend (Django)" cmd /k "cd /d "%~dp0backend" && if exist "appenv\Scripts\activate.bat" (call appenv\Scripts\activate.bat) && python manage.py runserver"

:: Frontend (Vite)
start "Stechpay Frontend (Vite)" cmd /k "cd /d "%~dp0frontend\stechpay" && npm run dev"

:: Hidden durable payment worker (requires the app virtual environment).
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0backend\appenv\Scripts\python.exe' -ArgumentList 'manage.py','process_payments','--watch' -WorkingDirectory '%~dp0backend' -WindowStyle Hidden -RedirectStandardOutput '%~dp0backend\worker.log' -RedirectStandardError '%~dp0backend\worker-error.log'"

echo.
echo Both servers are starting in separate windows:
echo  - Backend:  http://127.0.0.1:8000/
echo  - Frontend: http://localhost:5173/
echo.
