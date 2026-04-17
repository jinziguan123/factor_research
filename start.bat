@echo off
REM 一键启动：前后端（Windows）
REM 用法：start.bat
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "RUN_DIR=%ROOT%\.run"
set "BACKEND_LOG=%RUN_DIR%\backend.log"
set "FRONTEND_LOG=%RUN_DIR%\frontend.log"

if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"

REM ---- 依赖检查 ----
where uv >nul 2>&1 || ( echo [ERROR] 未安装 uv ^(https://github.com/astral-sh/uv^) & exit /b 1 )
where node >nul 2>&1 || ( echo [ERROR] 未安装 Node.js ^(^>=20^) & exit /b 1 )
where npm >nul 2>&1 || ( echo [ERROR] 未安装 npm & exit /b 1 )

REM ---- .env 检查 ----
if not exist "%ROOT%\backend\.env" (
  echo [ERROR] 缺少 backend\.env，请从 backend\.env.example 复制并填写
  exit /b 1
)

REM ---- 端口占用检查 ----
call :check_port %BACKEND_PORT% 后端 || exit /b 1
call :check_port %FRONTEND_PORT% 前端 || exit /b 1

REM ---- 后端依赖同步 ----
echo ==^> 同步后端依赖 (uv sync)
pushd "%ROOT%\backend"
call uv sync >nul
if errorlevel 1 ( popd & echo [ERROR] uv sync 失败 & exit /b 1 )
popd

REM ---- 启动后端（独立窗口） ----
echo ==^> 启动后端 (uvicorn :%BACKEND_PORT%)
start "factor-research-backend" cmd /c ^
  "cd /d %ROOT% ^&^& uv run --project backend uvicorn backend.api.main:app --reload --host 0.0.0.0 --port %BACKEND_PORT% > %BACKEND_LOG% 2^>^&1"

REM ---- 前端依赖 ----
if not exist "%ROOT%\frontend\node_modules" (
  echo ==^> 安装前端依赖 (npm install)
  pushd "%ROOT%\frontend"
  call npm install
  if errorlevel 1 ( popd & echo [ERROR] npm install 失败 & exit /b 1 )
  popd
)

REM ---- 启动前端（独立窗口） ----
echo ==^> 启动前端 (vite :%FRONTEND_PORT%)
start "factor-research-frontend" cmd /c ^
  "cd /d %ROOT%\frontend ^&^& npm run dev > %FRONTEND_LOG% 2^>^&1"

REM ---- 等待就绪 ----
call :wait_port %BACKEND_PORT% 后端
call :wait_port %FRONTEND_PORT% 前端

echo.
echo [OK] 启动完成
echo     前端: http://localhost:%FRONTEND_PORT%
echo     后端: http://localhost:%BACKEND_PORT%
echo     日志: %BACKEND_LOG%
echo           %FRONTEND_LOG%
echo     停止: stop.bat
exit /b 0

:check_port
set "p=%~1"
set "name=%~2"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%p% .*LISTENING"') do (
  echo [WARN] %name%端口 %p% 已被进程 %%a 占用，请先运行 stop.bat
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
  echo    [OK] %name% 已就绪 ^(:%p%^)
  exit /b 0
)
set /a cnt+=1
if %cnt% GEQ 40 (
  echo    [WARN] %name% 启动超时，请查看日志
  exit /b 0
)
timeout /t 1 /nobreak >nul
goto wait_loop
