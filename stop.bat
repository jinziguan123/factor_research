@echo off
REM One-click stopper for backend + frontend (Windows).
REM Pure ASCII on purpose - see start.bat header for reasoning.
REM Usage: stop.bat
setlocal EnableDelayedExpansion

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo ==^> Killing by port
call :kill_port %BACKEND_PORT% backend
call :kill_port %FRONTEND_PORT% frontend

echo ==^> Killing leftover windows by title
taskkill /F /FI "WINDOWTITLE eq factor-research-backend*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq factor-research-frontend*" >nul 2>&1

echo.
echo [OK] Stopped
exit /b 0

:kill_port
set "p=%~1"
set "name=%~2"
set "found="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%p% .*LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
  if not errorlevel 1 (
    echo    [OK] stopped %name% ^(PID=%%a port=%p%^)
    set "found=1"
  )
)
if not defined found echo    [--] %name% not running ^(:%p%^)
exit /b 0
