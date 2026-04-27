-- Phase 3：实盘信号 run 主表 fr_signal_runs。
--
-- 用途：每次"用户手动触发或定时触发的实盘信号计算"作为一行记录。
--   ─ 结构对齐 fr_composition_runs（因为信号计算复用合成 service 的部分逻辑），
--   ─ 但取消了 start_date / end_date / forward_periods（信号不评估 IC，只看当下排名），
--   ─ 新增 as_of_time / as_of_date / use_realtime / filter_price_limit。
--
-- 关键设计取舍：
--
-- 1) 复用 fr_composition_runs 的 factor_items_json + method：单因子时 method='single'，
--    factor_items_json=[{factor_id, params}]；多因子时和 composition 完全一致。
--    避免引入第二种"多因子配置"协议。
--
-- 2) as_of_time vs as_of_date：as_of_time 是触发时刻（精确到秒，盘中 = NOW()），
--    决定取哪一条 spot 快照；as_of_date 是当日交易日（决定从 stock_bar_1d
--    取昨日 close + 当日 spot）。两者分开便于查询与索引。
--
-- 3) use_realtime: 1=用 stock_spot_realtime 的最新一条作为今日 close，
--                  0=用昨日 close 当今日 close（盘前 / spot 不可用时降级）。
--
-- 4) filter_price_limit 默认 1：盘中场景比回测更需要剔除涨跌停票
--    （明天买不到 / 卖不掉），与 backtest 默认 0 不同。
--
-- 5) n_holdings_top / n_holdings_bot：剔除涨跌停 + qcut 后实际入选的票数；
--    < 5 时前端给出"信号不可靠"提示。
--
-- 6) 状态机沿用现有 abort 协议：pending / running / aborting / aborted /
--    success / failed。失败时 error_message 留 traceback。
--
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS `fr_signal_runs` (
  `run_id`              varchar(64) NOT NULL,
  -- 信号配置（与 composition 同结构）
  `factor_items_json`   longtext NOT NULL,                 -- [{factor_id, params}, ...]
  `method`              varchar(32) NOT NULL DEFAULT 'equal',
                                                            -- equal / ic_weighted / orthogonal_equal / single
  `pool_id`             bigint unsigned NOT NULL,
  `n_groups`            tinyint unsigned NOT NULL DEFAULT 5,
  `ic_lookback_days`    smallint unsigned NOT NULL DEFAULT 60,
                                                            -- 仅 method='ic_weighted' 用：IC 加权回看天数
  -- 触发时机
  `as_of_time`          datetime NOT NULL,                  -- 触发时刻（精确到秒）
  `as_of_date`          date NOT NULL,                      -- 当日交易日
  -- 数据源选择
  `use_realtime`        tinyint(1) NOT NULL DEFAULT 1,
  `filter_price_limit`  tinyint(1) NOT NULL DEFAULT 1,
  -- 状态机
  `status`              varchar(16) NOT NULL,
  `progress`            tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`       text,
  -- 时间戳（datetime(6) 与现有 composition 一致）
  `created_at`          datetime(6) NOT NULL,
  `started_at`          datetime(6) DEFAULT NULL,
  `finished_at`         datetime(6) DEFAULT NULL,
  -- 输出摘要
  `n_holdings_top`      int unsigned DEFAULT NULL,
  `n_holdings_bot`      int unsigned DEFAULT NULL,
  `payload_json`        longtext,                           -- {top: [...], bottom: [...], spot_meta: {...}}
  PRIMARY KEY (`run_id`),
  KEY `idx_as_of_date` (`as_of_date`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
