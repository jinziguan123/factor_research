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

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
```

- Prometheus：http://localhost:9090
- Grafana：http://localhost:3000 （admin / admin），已预置数据源与「Factor Research 监控」面板。

## 网络

`prometheus.yml` 的抓取目标默认是 `backend:8000`（假设 backend 与本栈在同一 docker
网络）。若 backend 跑在宿主机或别处，把 targets 改成 `host.docker.internal:8000` 或实际地址。

## 生产化（外部依赖，本仓库不内置）

完整生产监控还需结合实际环境补充：持久化卷（Prometheus TSDB / Grafana DB）、告警规则
（Alertmanager）、抓取鉴权、以及把指标埋点扩展到全部任务路径与数据健康检查。
