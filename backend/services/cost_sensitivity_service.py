"""成本敏感性分析服务（CostSensitivityService）。

一次 ``run_cost_sensitivity`` 对同一个因子 / 股票池 / 窗口，在一组 ``cost_bps``
下分别跑 vbt 组合回测，输出每个点的关键指标，供前端画"成本 → 年化收益 / Sharpe /
换手"的敏感曲线。

设计取舍：
- 复用 ``backtest_service._prepare_backtest_inputs`` 准备一次 F / close / size：
  因子值和目标仓位在不同 cost_bps 下完全一致，只有 vbt fees 不同。
- **不写 equity/orders/trades 三份 parquet**：N 个 cost_bps 会产出 N 份冗余 artifact，
  磁盘压力不值当；前端可视化只需各点的结构化指标 + 原始 vbt stats，统一进 points_json。
- 单条 MySQL 记录入 ``fr_cost_sensitivity_runs``：status/progress 同其它 run 表同构，
  ``points_json`` 是 ``[{cost_bps, total_return, annual_return, sharpe_ratio, ...}, ...]``。

安全护栏：
- ``cost_bps_list`` 会在 API schema 层（CreateCostSensitivityIn）校验长度 / 非负 /
  升序，service 这里不再重复（但去重一次保险）。
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.services.backtest_service import (
    BacktestInputs,
    _prepare_backtest_inputs,
    _stats_to_payload,
)
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def _update_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    points_json: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新 fr_cost_sensitivity_runs 的状态列（与 backtest_service._update_status 同构）。"""
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status=%s")
        vals.append(status)
    if progress is not None:
        sets.append("progress=%s")
        vals.append(progress)
    if error is not None:
        sets.append("error_message=%s")
        vals.append(error)
    if points_json is not None:
        sets.append("points_json=%s")
        vals.append(points_json)
    if started:
        sets.append("started_at=%s")
        vals.append(datetime.now())
    if finished:
        sets.append("finished_at=%s")
        vals.append(datetime.now())
    if not sets:
        return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE fr_cost_sensitivity_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _nan_to_none(x: Any) -> Any:
    """NaN / inf / -inf → None。JSON 序列化前清洗用。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _compute_point(
    inputs: BacktestInputs, cost_bps: float
) -> dict[str, Any]:
    """对单个 cost_bps 跑一次 vbt，提取结构化指标。

    关键指标：
    - total_return / annual_return / sharpe_ratio / max_drawdown / win_rate / trade_count：
      和 fr_backtest_metrics 同款语义，单位统一成小数（不是百分比）；
    - turnover_total：总周转 = Σ|order_size * order_price| / init_cash。表征"成本撬动"
      —— 同样换手下，不同 cost_bps 的 trade_count 会相同，annual_return 却会差，
      方便前端用"换手 × cost_bps"直观解读曲线走势。
    - stats：vbt pf.stats() 原样 dict（Timedelta / Timestamp 已 str 化）。
    """
    import vectorbt as vbt

    pf = vbt.Portfolio.from_orders(
        close=inputs.close,
        size=inputs.size,
        size_type="targetamount",
        fees=cost_bps / 1e4,
        freq="1D",
        init_cash=inputs.init_cash,
        cash_sharing=True,
        group_by=True,
    )
    stats = pf.stats()
    stats_payload = _stats_to_payload(stats)

    total_return = float(stats.get("Total Return [%]", 0.0) or 0.0) / 100.0
    max_drawdown = float(stats.get("Max Drawdown [%]", 0.0) or 0.0) / 100.0
    sharpe_ratio = float(stats.get("Sharpe Ratio", 0.0) or 0.0)
    win_rate = float(stats.get("Win Rate [%]", 0.0) or 0.0) / 100.0
    trade_count = int(stats.get("Total Trades", 0) or 0)

    # 年化：与 backtest_service 完全一致的公式，保证同窗口同池下
    # cost_bps=3 的敏感性点 ≈ 对应单次 backtest 的 annual_return。
    n_bars = inputs.n_bars
    years = max(n_bars / 252.0, 1e-9)
    base = 1.0 + total_return
    if n_bars == 0:
        annual_return = 0.0
    elif base <= 0:
        annual_return = -1.0
    else:
        annual_return = base ** (1.0 / years) - 1.0

    # turnover：用订单明细算。pf.orders.records 字段名 size / price，不受 vbt 版本语言变更影响。
    # 防御：records 可能为空（空仓全程）；空时 turnover = 0。
    try:
        orders = pf.orders.records
        if len(orders) == 0:
            turnover_total = 0.0
        else:
            sizes = np.asarray(orders["size"], dtype=float)
            prices = np.asarray(orders["price"], dtype=float)
            turnover_total = float(
                np.nansum(np.abs(sizes) * np.abs(prices)) / max(inputs.init_cash, 1e-9)
            )
    except Exception:  # noqa: BLE001
        # vbt API 漂移兜底：算不出换手不该让整个敏感性失败。
        log.exception("compute turnover failed at cost_bps=%s", cost_bps)
        turnover_total = 0.0

    return {
        "cost_bps": float(cost_bps),
        "total_return": _nan_to_none(total_return),
        "annual_return": _nan_to_none(annual_return),
        "sharpe_ratio": _nan_to_none(sharpe_ratio),
        "max_drawdown": _nan_to_none(max_drawdown),
        "win_rate": _nan_to_none(win_rate),
        "trade_count": trade_count,
        "turnover_total": _nan_to_none(turnover_total),
        "stats": stats_payload,
    }


def run_cost_sensitivity(run_id: str, body: dict) -> None:
    """执行一次成本敏感性分析。

    Args:
        run_id: ``fr_cost_sensitivity_runs.run_id``，API 层 INSERT 时生成。
        body: 请求体 dict，字段（与 CreateBacktestIn 对齐 + cost_bps_list）：
            - factor_id / pool_id / start_date / end_date；
            - params / n_groups / rebalance_period / position / init_cash / freq；
            - ``cost_bps_list``（list[float]，基点；每个 cost_bps / 10000 为单边费率）。

    副作用：
        - 更新 fr_cost_sensitivity_runs 的 status / progress / started_at / finished_at /
          error_message / points_json。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=5)

        cost_bps_list = list(body.get("cost_bps_list") or [])
        # 去重 + 保持顺序（用户可能意外传 [3, 5, 3]，保留一份）。保留用户给的顺序
        # 方便曲线 x 轴自然从左到右；schema 层应已升序但这里二次保险。
        seen = set()
        unique_list = []
        for v in cost_bps_list:
            if v in seen:
                continue
            seen.add(v)
            unique_list.append(float(v))
        if not unique_list:
            raise ValueError("cost_bps_list 不能为空")

        # 一次准备所有 vbt 共享输入
        inputs = _prepare_backtest_inputs(body)
        _update_status(run_id, progress=40)

        # 每个点大致 5~30s（vbt JIT）；总进度：40 → 95 线性分配。
        points: list[dict[str, Any]] = []
        total = len(unique_list)
        for idx, cost_bps in enumerate(unique_list):
            point = _compute_point(inputs, cost_bps)
            points.append(point)
            # 进度限制在 40..95 区间，避免最后一点结束时 progress=95（留 5% 给入库）。
            prog = 40 + int(55 * (idx + 1) / total)
            _update_status(run_id, progress=min(prog, 95))

        points_json = json.dumps(
            {"points": points}, ensure_ascii=False, allow_nan=False
        )
        _update_status(
            run_id,
            status="success",
            progress=100,
            points_json=points_json,
            finished=True,
        )
    except Exception:
        log.exception("cost_sensitivity failed: run_id=%s", run_id)
        try:
            _update_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception(
                "_update_status 记录失败时自身也抛异常: run_id=%s", run_id
            )
