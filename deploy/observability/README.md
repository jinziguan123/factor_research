# 可观测性栈（Prometheus + Grafana）

后端通过 `GET /metrics`（根路径，Prometheus 文本格式 0.0.4）暴露运维指标，无需第三方
依赖（手写导出，见 `backend/observability/metrics.py`）。本目录是开箱即用的监控栈样例。

## 指标

| 指标 | 类型 | 标签 | 含义 |
|------|------|------|------|
| `fr_task_total` | counter | `kind`, `status` | 因子任务计数（eval/backtest…×success/failed/aborted）|
| `fr_task_duration_seconds` | histogram | `kind` | 任务时延分布 |
| `fr_data_health` | gauge | `check` | 数据健康度（1=健康，0=异常）|

埋点见 `backend/observability/metrics.py:observe_task`，已接入 `backtest_service.run_backtest`
的 success/aborted/failed 三分支；其他 service（eval / cost_sensitivity）按同样方式调用
`observe_task` 即可接入。

## 启动

**方式一（推荐）：随主 compose 一起起。** 监控已整合进项目根 `docker-compose.yml`，
backend / frontend / prometheus / grafana 在同一网络，开箱即用：

```bash
docker compose up -d
```

**方式二：单独起监控栈**（backend 已在别处运行时）：

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
```

起好后：
- Prometheus：http://localhost:9090 （`/targets` 看 backend 是否 UP）
- Grafana：http://localhost:3000 （首次 admin / admin，**请改密码**），已预置数据源与「Factor Research 监控」面板。

## 网络

随主 compose 启动时，`prometheus.yml` 的 `backend:8000` 直接走 compose 网络的服务名，无需改动。
单独起监控栈且 backend 跑在宿主机时，把 targets 改成 `host.docker.internal:8000`。

## 生产化（外部依赖，本仓库不内置）

完整生产监控还需结合实际环境补充：持久化卷（Prometheus TSDB / Grafana DB）、告警规则
（Alertmanager）、抓取鉴权、以及把指标埋点扩展到全部任务路径与数据健康检查。
