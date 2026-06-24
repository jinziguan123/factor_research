# Factor Research - 因子研究平台

全栈因子研究与回测平台，支持因子注册、评估、回测全流程，提供 Web 可视化界面。
回测引擎已按 A 股实盘改造（T+1 成交、不对称成本、滑点 + 市场冲击、涨跌停滞留、容量约束），
并提供样本外验证、组合优化、组合风控、执行层抽象与 Prometheus / Grafana 监控。
设计文档见 [`docs/plans/2026-06-23-backtest-realism-redesign.md`](docs/plans/2026-06-23-backtest-realism-redesign.md)。

## 核心能力

### 回测真实性（贴近 A 股实盘）
- **T+1 成交**：信号次日以开盘价 / VWAP 成交，消除前视偏差（成交价口径可配）
- **不对称成本**：佣金（双边）+ 印花税（仅卖出）+ 过户费（双边），拆分可配
- **滑点 + 市场冲击**：固定滑点 + 平方根冲击模型（∝ √(订单额 / 当日成交额)）
- **涨跌停滞留**：封涨停买不进、封跌停卖不掉，持仓滞留至可成交日
- **成交量容量约束**：单日成交额 ≤ 当日成交额的 k%

### 样本外验证（识别过拟合）
- **Walk-Forward 滚动回测**：每个测试窗只用其之前的数据，拼接连续 OOS 权益曲线
- **IS / OOS IC 衰减**：对比训练段 / 测试段 IC，衰减比 < 0.5 或变号即过拟合告警
- **Purged K-Fold**：带 embargo 的交叉验证（防 forward return 标签泄露）

### 组合构建与风控
- **组合优化器**：均值-方差 / 风险平价 / 逆波动率 / IC 加权 + 换手预算
- **组合级风控**：个股 / 行业集中度、目标波动率缩放、回撤熔断

### 执行层
- 统一 `Broker` 抽象 + 内存模拟盘（A 股不对称费用 / 资金 / 持仓约束）；实盘 QMT / CTP 按接口扩展

### 可观测性
- `/metrics` 暴露 Prometheus 指标（任务计数 / 时延 / 数据健康），Grafana 预置监控面板

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
# 在项目根目录（backend / frontend / prometheus / grafana 一并启动）
docker-compose up -d
```

- 前端：http://localhost ，后端 API 通过 Nginx 反向代理到 `/api/`
- **Grafana 监控**：http://localhost:3000 （首次 `admin` / `admin`，**请改密码**），已预置「Factor Research 监控」面板
- **Prometheus**：http://localhost:9090 （`/targets` 查看 backend 抓取状态）

监控配置见 [`deploy/observability/`](deploy/observability/)；生产部署需收紧端口暴露与 Grafana 默认密码。

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
| `/api/backtests/walk-forward` | POST | 创建 walk-forward 样本外验证 |
| `/api/cost-sensitivity` | GET/POST | 成本（滑点）敏感性分析 |
| `/api/admin/bar_1d:aggregate` | POST | 触发日线聚合 |
| `/api/admin/qfq:import` | POST | 触发复权因子导入 |
| `/api/admin/jobs` | GET | 查看导入任务历史 |
| `/api/bars/1d` | GET | 查询日线行情 |
| `/api/health` | GET | 健康检查 |
| `/metrics` | GET | Prometheus 运维指标（任务计数 / 时延 / 数据健康） |
