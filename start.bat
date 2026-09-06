@echo off
title Stechpay Dev Launcher
echo ========================================
echo Starting Stechpay Backend and Frontend...
echo ========================================

:: Backend (Django)
start "Stechpay Backend (Django)" cmd /k "cd /d "%~dp0backend" && if exist "appenv\Scripts\activate.bat" (call appenv\Scripts\activate.bat) && python manage.py runserver"

:: Frontend (Vite)
start "Stechpay Frontend (Vite)" cmd /k "cd /d "%~dp0frontend\stechpay" && npm run dev"

echo.
echo Both servers are starting in separate windows:
echo  - Backend:  http://127.0.0.1:8000/
echo  - Frontend: http://localhost:5173/
echo.
