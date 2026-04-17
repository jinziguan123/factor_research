# Factor Research - 因子研究平台

全栈因子研究与回测平台，支持因子注册、评估、回测全流程，提供 Web 可视化界面。

## 技术栈

- **后端**: Python 3.10+ / FastAPI / MySQL / ClickHouse / uv
- **前端**: Vue 3 / TypeScript / Naive UI / ECharts / TanStack Query
- **部署**: Docker Compose / Nginx

## 本地开发

### 前置条件

- Python 3.10+, Node.js 20+, MySQL 8, ClickHouse
- 后端配置: 复制 `backend/.env.example` 为 `backend/.env` 并填写数据库连接信息

### 后端

```bash
# 安装依赖
cd backend && uv sync

# 初始化数据库 schema
uv run --project backend python -m backend.scripts.run_init

# 启动开发服务器
uv run --project backend uvicorn backend.api.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173，API 代理到 http://localhost:8000。

## Docker 部署

```bash
# 在项目根目录
docker-compose up -d
```

前端访问 http://localhost，后端 API 通过 Nginx 反向代理到 `/api/`。

## 测试

```bash
uv run --project backend pytest backend/tests/ -v
```

## 数据初始化

```bash
# 1. 初始化数据库表
uv run --project backend python -m backend.scripts.run_init

# 2. 导入复权因子
uv run --project backend python -m backend.scripts.import_qfq

# 3. 聚合日线数据
uv run --project backend python -m backend.scripts.aggregate_bar_1d
```

也可通过 Web 管理面板 (`/admin`) 触发聚合和导入操作。

## API 概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/factors` | GET | 因子列表 |
| `/api/factors/{id}` | GET | 因子详情 |
| `/api/pools` | GET/POST | 股票池 CRUD |
| `/api/pools/{id}` | GET/PUT/DELETE | 单个股票池操作 |
| `/api/evals` | GET/POST | 评估任务列表/创建 |
| `/api/evals/{run_id}` | GET/DELETE | 评估详情/删除 |
| `/api/backtests` | GET/POST | 回测任务列表/创建 |
| `/api/backtests/{run_id}` | GET/DELETE | 回测详情/删除 |
| `/api/backtests/{run_id}/equity` | GET | 下载净值 parquet |
| `/api/backtests/{run_id}/orders` | GET | 下载委托 parquet |
| `/api/backtests/{run_id}/trades` | GET | 下载成交 parquet |
| `/api/admin/bar_1d:aggregate` | POST | 触发日线聚合 |
| `/api/admin/qfq:import` | POST | 触发复权因子导入 |
| `/api/admin/jobs` | GET | 查看导入任务历史 |
| `/api/bars/1d` | GET | 查询日线行情 |
| `/api/health` | GET | 健康检查 |
