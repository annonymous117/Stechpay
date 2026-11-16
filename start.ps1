Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Starting Stechpay Backend and Frontend..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$backendDir = Join-Path $PSScriptRoot "backend"
$frontendDir = Join-Path $PSScriptRoot "frontend\stechpay"
$pythonExe = Join-Path $backendDir "appenv\Scripts\python.exe"

# Backend command
if (Test-Path $pythonExe) {
    $backendCmd = "Set-Location '$backendDir'; & '$pythonExe' manage.py runserver"
} else {
    $backendCmd = "Set-Location '$backendDir'; python manage.py runserver"
}

# Frontend command
$frontendCmd = "Set-Location '$frontendDir'; npm run dev"

# Launch in separate PowerShell windows
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd

# Durable payment event and notification processing. The helper is hidden.
$workerPython = if (Test-Path $pythonExe) { $pythonExe } else { 'python' }
$workerProcess = Start-Process -FilePath $workerPython -ArgumentList @('manage.py', 'process_payments', '--watch') -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backendDir 'worker.log') -RedirectStandardError (Join-Path $backendDir 'worker-error.log')
Write-Host "Payment worker PID: $($workerProcess.Id). Logs: backend/worker.log" -ForegroundColor Yellow

Write-Host ""
Write-Host "Both servers are starting in separate windows:" -ForegroundColor Green
Write-Host " - Backend:  http://127.0.0.1:8000/" -ForegroundColor Yellow
Write-Host " - Frontend: http://localhost:5173/" -ForegroundColor Yellow
Write-Host ""
