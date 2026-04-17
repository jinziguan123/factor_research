@echo off
REM One-click launcher for backend + frontend (Windows).
REM
REM Why English only: Windows cmd on Chinese locale parses .bat as CP936 (GBK)
REM and mis-tokenizes UTF-8 multibyte sequences, which can break even REM
REM lines (the "REM [usage] ..." line was being executed as a bogus command).
REM chcp 65001 changes output encoding but CANNOT retro-fix parsing of
REM already-read lines. Keeping this file pure ASCII avoids the whole
REM minefield.
REM
REM Usage: start.bat
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "RUN_DIR=%ROOT%\.run"
set "BACKEND_LOG=%RUN_DIR%\backend.log"
set "FRONTEND_LOG=%RUN_DIR%\frontend.log"

if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"

REM ---- Dependency checks ----
where uv >nul 2>&1 || ( echo [ERROR] uv not found ^(install from https://github.com/astral-sh/uv^) & exit /b 1 )
where node >nul 2>&1 || ( echo [ERROR] Node.js not found ^(need ^>=20^) & exit /b 1 )
where npm >nul 2>&1 || ( echo [ERROR] npm not found & exit /b 1 )

REM ---- .env check ----
if not exist "%ROOT%\backend\.env" (
  echo [ERROR] Missing backend\.env - copy from backend\.env.example and fill in values
  exit /b 1
)

REM ---- Port-in-use check ----
call :check_port %BACKEND_PORT% backend || exit /b 1
call :check_port %FRONTEND_PORT% frontend || exit /b 1

REM ---- Sync backend deps ----
echo ==^> Syncing backend deps (uv sync)
pushd "%ROOT%\backend"
call uv sync >nul
if errorlevel 1 ( popd & echo [ERROR] uv sync failed & exit /b 1 )
popd

REM ---- Launch backend in a separate window ----
echo ==^> Starting backend (uvicorn :%BACKEND_PORT%)
start "factor-research-backend" cmd /c ^
  "cd /d %ROOT% ^&^& uv run --project backend uvicorn backend.api.main:app --reload --host 0.0.0.0 --port %BACKEND_PORT% > %BACKEND_LOG% 2^>^&1"

REM ---- Frontend deps ----
if not exist "%ROOT%\frontend\node_modules" (
  echo ==^> Installing frontend deps (npm install)
  pushd "%ROOT%\frontend"
  call npm install
  if errorlevel 1 ( popd & echo [ERROR] npm install failed & exit /b 1 )
  popd
)

REM ---- Launch frontend in a separate window ----
echo ==^> Starting frontend (vite :%FRONTEND_PORT%)
start "factor-research-frontend" cmd /c ^
  "cd /d %ROOT%\frontend ^&^& npm run dev > %FRONTEND_LOG% 2^>^&1"

REM ---- Wait for readiness ----
REM wait_port sets STARTUP_FAILED=1 on timeout so we do not print a misleading
REM [OK] message when services actually failed to bind their ports.
set "STARTUP_FAILED="
call :wait_port %BACKEND_PORT% backend
call :wait_port %FRONTEND_PORT% frontend

if defined STARTUP_FAILED (
  echo.
  echo [ERROR] One or more services failed to start. Check logs:
  echo     backend: %BACKEND_LOG%
  echo     frontend: %FRONTEND_LOG%
  echo Common causes: port in use, deps missing, bad .env, vite/uvicorn error
  exit /b 1
)

echo.
echo [OK] Started
echo     Frontend: http://localhost:%FRONTEND_PORT%
echo     Backend:  http://localhost:%BACKEND_PORT%
echo     Logs:     %BACKEND_LOG%
echo               %FRONTEND_LOG%
echo     Stop:     stop.bat
exit /b 0

:check_port
set "p=%~1"
set "name=%~2"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%p% .*LISTENING"') do (
  echo [WARN] %name% port %p% is in use by PID %%a - run stop.bat first
  exit /b 1
)
exit /b 0

:wait_port
set "p=%~1"
set "name=%~2"
set /a cnt=0
:wait_loop
netstat -ano | findstr /R /C:":%p% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo    [OK] %name% ready ^(:%p%^)
  exit /b 0
)
set /a cnt+=1
if %cnt% GEQ 40 (
  echo    [WARN] %name% start timeout - check log
  REM Set a shared var instead of exit /b 1 so caller can detect reliably
  REM under setlocal EnableDelayedExpansion without errorlevel pitfalls.
  set "STARTUP_FAILED=1"
  exit /b 0
)
timeout /t 1 /nobreak >nul
goto wait_loop
