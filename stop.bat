@echo off
REM 本文件为 UTF-8 无 BOM，先切 cmd 到 UTF-8 避免中文 mojibake（和 start.bat 一致）。
chcp 65001 > nul 2>&1
REM 一键停止：前后端（Windows）
REM 用法：stop.bat
setlocal EnableDelayedExpansion

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo ==^> 按端口清理
call :kill_port %BACKEND_PORT% 后端
call :kill_port %FRONTEND_PORT% 前端

echo ==^> 按窗口标题清理残留
taskkill /F /FI "WINDOWTITLE eq factor-research-backend*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq factor-research-frontend*" >nul 2>&1

echo.
echo [OK] 已停止
exit /b 0

:kill_port
set "p=%~1"
set "name=%~2"
set "found="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%p% .*LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
  if not errorlevel 1 (
    echo    [OK] 已停止 %name% ^(PID=%%a^ port=%p%^)
    set "found=1"
  )
)
if not defined found echo    [--] %name% 未在运行 ^(:%p%^)
exit /b 0
