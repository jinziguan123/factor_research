#!/usr/bin/env bash
# 一键启动：前后端（macOS / Linux）
# 用法：./start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_LOG="$ROOT/.run/backend.log"
FRONTEND_LOG="$ROOT/.run/frontend.log"
BACKEND_PID="$ROOT/.run/backend.pid"
FRONTEND_PID="$ROOT/.run/frontend.pid"

mkdir -p "$ROOT/.run"

# ---- 依赖检查 ----
command -v uv >/dev/null 2>&1 || { echo "❌ 未安装 uv (https://github.com/astral-sh/uv)"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "❌ 未安装 Node.js (>=20)"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "❌ 未安装 npm"; exit 1; }

# ---- .env 检查 ----
if [[ ! -f "$ROOT/backend/.env" ]]; then
  echo "❌ 缺少 backend/.env，请从 backend/.env.example 复制并填写连接信息"
  exit 1
fi

# ---- 端口占用检查 ----
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
if port_in_use "$BACKEND_PORT"; then
  echo "⚠️  后端端口 $BACKEND_PORT 已被占用，请先运行 ./stop.sh 或手动清理"
  exit 1
fi
if port_in_use "$FRONTEND_PORT"; then
  echo "⚠️  前端端口 $FRONTEND_PORT 已被占用，请先运行 ./stop.sh 或手动清理"
  exit 1
fi

# ---- 后端 ----
echo "==> 同步后端依赖 (uv sync)"
( cd "$ROOT/backend" && uv sync ) >/dev/null

echo "==> 启动后端 (uvicorn :$BACKEND_PORT)"
(
  cd "$ROOT"
  nohup uv run --project backend uvicorn backend.api.main:app \
      --reload --host 0.0.0.0 --port "$BACKEND_PORT" \
      > "$BACKEND_LOG" 2>&1 &
  echo $! > "$BACKEND_PID"
)

# ---- 前端 ----
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "==> 安装前端依赖 (npm install)"
  ( cd "$ROOT/frontend" && npm install )
fi

echo "==> 启动前端 (vite :$FRONTEND_PORT)"
(
  cd "$ROOT/frontend"
  nohup npm run dev > "$FRONTEND_LOG" 2>&1 &
  echo $! > "$FRONTEND_PID"
)

# ---- 等待就绪 ----
wait_port() {
  local port="$1" name="$2" max=40
  for ((i=0; i<max; i++)); do
    if port_in_use "$port"; then echo "   ✅ $name 已就绪 (:$port)"; return 0; fi
    sleep 0.5
  done
  echo "   ⚠️  $name 启动超时，请查看日志：${3}"
  return 1
}
wait_port "$BACKEND_PORT"  "后端"   "$BACKEND_LOG"  || true
wait_port "$FRONTEND_PORT" "前端"   "$FRONTEND_LOG" || true

echo
echo "🎉 启动完成"
echo "   前端: http://localhost:$FRONTEND_PORT"
echo "   后端: http://localhost:$BACKEND_PORT"
echo "   日志: $BACKEND_LOG / $FRONTEND_LOG"
echo "   停止: ./stop.sh"
