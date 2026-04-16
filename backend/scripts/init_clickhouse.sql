-- factor_research 专属 ClickHouse schema（quant_data 库；幂等 DDL）。
-- 完整字段定义详见 docs/plans/2026-04-16-factor-research-design.md §3.2。
--
-- 字段单位要点：
--   stock_bar_1d.volume   = UInt64  —— 日级累加值可能接近 UInt32 上限
--   stock_bar_1d.amount_k = UInt32  —— 千元单位，上限约 4.29 万亿元够用
--   factor_value_1d.value = Float64 —— 避免 IC/收益累积下的精度损失

CREATE DATABASE IF NOT EXISTS quant_data;

-- 【新增】日频 K 线物化表（存未复权；读取时与 fr_qfq_factor 相乘得到前复权）
CREATE TABLE IF NOT EXISTS quant_data.stock_bar_1d
(
    `symbol_id`  UInt32,
    `trade_date` Date,
    `open`       Float32,
    `high`       Float32,
    `low`        Float32,
    `close`      Float32,
    `volume`     UInt64,
    `amount_k`   UInt32,
    `version`    UInt64,
    `updated_at` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYear(trade_date)
ORDER BY (symbol_id, trade_date)
SETTINGS index_granularity = 8192;

-- 【新增】因子值窄表（长格式；按 (factor_id, version, params_hash) 复用缓存）
CREATE TABLE IF NOT EXISTS quant_data.factor_value_1d
(
    `factor_id`      LowCardinality(String),
    `factor_version` UInt32,
    `params_hash`    FixedString(40),
    `symbol_id`      UInt32,
    `trade_date`     Date,
    `value`          Float64,
    `version`        UInt64,
    `updated_at`     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (factor_id, toYear(trade_date))
ORDER BY (factor_id, factor_version, params_hash, symbol_id, trade_date)
SETTINGS index_granularity = 8192;
