#!/usr/bin/env bash
# 一键停止：前后端（macOS / Linux）
# 用法：./stop.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_PID="$ROOT/.run/backend.pid"
FRONTEND_PID="$ROOT/.run/frontend.pid"

kill_pid_tree() {
  local pid="$1" name="$2"
  [[ -z "${pid:-}" ]] && return 0
  if ! ps -p "$pid" > /dev/null 2>&1; then
    echo "   (跳过) $name 进程 $pid 已不存在"
    return 0
  fi
  # 先杀子进程，再杀父
  local children
  children=$(pgrep -P "$pid" 2>/dev/null || true)
  [[ -n "$children" ]] && kill $children 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  sleep 0.5
  if ps -p "$pid" > /dev/null 2>&1; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "   ✅ 已停止 $name (PID=$pid)"
}

kill_by_port() {
  local port="$1" name="$2"
  local pids
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.3
    pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
    fi
    echo "   ✅ 已清理 $name 端口 :$port"
  fi
}

echo "==> 按 PID 停止"
if [[ -f "$BACKEND_PID" ]];  then kill_pid_tree "$(cat "$BACKEND_PID")"  后端; rm -f "$BACKEND_PID"; fi
if [[ -f "$FRONTEND_PID" ]]; then kill_pid_tree "$(cat "$FRONTEND_PID")" 前端; rm -f "$FRONTEND_PID"; fi

echo "==> 按端口兜底清理"
kill_by_port "$BACKEND_PORT"  后端
kill_by_port "$FRONTEND_PORT" 前端

echo
echo "✅ 已停止"
