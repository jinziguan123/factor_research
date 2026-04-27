-- Phase 3：实盘信号系统的两张 ClickHouse 行情表。
--
-- 用途：
--   stock_spot_realtime —— 盘中（9:25-15:00）每 5min 一次全市场快照，
--                          供 signal_service 取"当下报价"作为今日 close 估计；
--   stock_bar_1m         —— 1m K 线归档（盘后批量写；默认 worker 不开启），
--                          供历史回看 + 未来分钟级因子开发。
--
-- 关键设计取舍：
--
-- 1) symbol_id (UInt32) vs symbol (String)：沿用 stock_bar_1d / factor_value_1d
--    的惯例，主键 symbol_id；字符串 symbol 由 backend.storage.symbol_resolver
--    在写入和读取层做 ↔ 互转。**设计文档草稿用了 LowCardinality(String)，
--    本文件按项目实际惯例做了调整**——原因是 spot 数据要和 stock_bar_1d
--    联表（取昨收 / 复权因子），symbol_id 一致才能 JOIN 不重做映射。
--
-- 2) 价格字段 Float32：A 股价格区间 0.01~3000 内 Float32 精度足（约 7 位有效），
--    与 stock_bar_1d 一致；Float64 翻倍空间没必要。
--
-- 3) ReplacingMergeTree(version) 而非纯 ReplacingMergeTree：与项目其它表口径
--    一致，version=now() 的纳秒级时间戳，重复写时新版本覆盖。spot 数据按
--    (symbol_id, snapshot_at) 主键，正常情况下 5min 一次写入不会重复；但若
--    worker 重启 / 重试，幂等性靠 version 兜底。
--
-- 4) 分区策略：
--    - spot 表 PARTITION BY trade_date：盘中查询绝大多数带 trade_date 过滤
--      （取"今日最新一行"），单日分区扫描成本最低；A 股一年 ~250 个分区在
--      ClickHouse 推荐范围内。
--    - 1m K 表 PARTITION BY toYearMonth：1m K 归档量大（~120 万行/日 × 250 日
--      ≈ 3 亿行/年），按月分区年累 12 个；按日分区 10 年就 2500 个，超推荐
--      上限会拖慢 merge / part 管理。
--
-- 5) is_suspended：spot_em 接口对停牌票返回 last_price=0 / amount=0 / volume=0；
--    在写入层判断后落库，下游使用时 mask 掉这类票避免污染因子计算。
--
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS quant_data.stock_spot_realtime
(
    `symbol_id`     UInt32,
    `snapshot_at`   DateTime,                    -- 拉取时刻（秒精度）
    `trade_date`    Date,                        -- 当日交易日
    `last_price`    Float32,                     -- 最新成交价（spot_em.最新价）
    `open`          Float32,
    `high`          Float32,
    `low`           Float32,
    `prev_close`    Float32,                     -- 昨收（spot_em.昨收）
    `pct_chg`       Float32,                     -- 当下涨跌幅（小数，0.01=1%）
    `volume`        UInt64,                      -- 累计成交量（手）
    `amount`        Float64,                     -- 累计成交额（元）
    `bid1`          Float32,                     -- 买一价
    `ask1`          Float32,                     -- 卖一价
    `is_suspended`  UInt8,                       -- 0/1：spot 中 last=0 视为停
    `version`       UInt64,                      -- ReplacingMergeTree 去重锚
    `updated_at`    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY trade_date
ORDER BY (symbol_id, snapshot_at)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS quant_data.stock_bar_1m
(
    `symbol_id`     UInt32,
    `trade_time`    DateTime,                    -- bar 起始时间（如 2026-04-27 09:30:00）
    `trade_date`    Date,                        -- 便于分区
    `open`          Float32,
    `high`          Float32,
    `low`           Float32,
    `close`         Float32,
    `volume`        UInt64,                      -- bar 内成交量（手）
    `amount`        Float64,                     -- bar 内成交额（元）
    `version`       UInt64,
    `updated_at`    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol_id, trade_time)
SETTINGS index_granularity = 8192;
