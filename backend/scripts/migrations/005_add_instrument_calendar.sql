-- Phase 1：新增 fr_instrument + fr_trade_calendar。
--
-- 背景：factor_research 依赖的 stock_symbol 由 timing_driven 维护，且只包含
-- 在市标的。要解决幸存者偏差 / 指数成分未来偏差，需要一张独立的"标的全集"表，
-- 可以灌入**退市股票**以及**历史成分**。
--
-- 设计取舍（详见 docs 讨论记录 "方案 A"）：
-- - 本表主键用 symbol VARCHAR(16)（QMT 格式，如 "600000.SH"），**与 stock_symbol
--   的整数 symbol_id 解耦**。在市股票两侧都有记录；退市股票只在本表中存在。
-- - 新增的研究数据表（行业 / 指数成分 / ST / 财务 / 市值）后续一律以 symbol 为外键，
--   避免再次被 stock_symbol 的覆盖面卡住。
-- - fr_qfq_factor 继续沿用 int symbol_id，不做冗余；关联在市数据时走 SymbolResolver。
--
-- 幂等：直接执行本脚本即可；已有库重复执行无副作用（CREATE TABLE IF NOT EXISTS）。

-- ============================================================
-- fr_instrument：标的基础信息（含退市）
-- ============================================================
-- 该表是"所有历史存在过的标的"的真相表，解决幸存者偏差：
-- - 回测构造股票池时，按 list_date/delist_date 动态裁剪；
-- - 指数成分回溯、行业历史都以本表为外键锚点。
--
-- data_source：区分 baostock / qmt / manual，便于对账和 re-sync。
-- status：active / delisted；is_st 是**当前**是否 ST，历史 ST 变更留给后续表处理。
CREATE TABLE IF NOT EXISTS `fr_instrument` (
  `symbol`       varchar(16) NOT NULL,            -- 统一 QMT 格式，如 "600000.SH"
  `market`       char(2)     NOT NULL DEFAULT 'CN', -- CN/HK/US（Phase 1 只灌 CN）
  `exchange`     varchar(8)  NOT NULL,            -- SH/SZ/BJ
  `name`         varchar(64) NOT NULL,
  `asset_type`   varchar(16) NOT NULL DEFAULT 'stock', -- stock/etf/index/...
  `list_date`    date        DEFAULT NULL,
  `delist_date`  date        DEFAULT NULL,        -- NULL = 在市
  `status`       varchar(16) NOT NULL DEFAULT 'active', -- active/delisted
  `is_st`        tinyint(1)  NOT NULL DEFAULT 0,  -- 当前是否 ST（不记历史）
  `data_source`  varchar(16) NOT NULL,            -- baostock/qmt/manual
  `updated_at`   datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`),
  KEY `idx_status_list`  (`status`, `list_date`),
  KEY `idx_delist`       (`delist_date`),
  KEY `idx_market_exch`  (`market`, `exchange`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- fr_trade_calendar：交易日历（多市场）
-- ============================================================
-- 为什么单独建表而不是复用 timing_driven 的日历：
-- - 本平台未来会支持港股 / 美股（MarketAdapter 设计），需要按 market 分拆；
-- - 生产侧 timing_driven 的日历目前只覆盖 A 股，字段口径也可能与本平台需要的不同；
-- - 交易日历的写入成本极低（百级别 rows/年），冗余一份换解耦很划算。
CREATE TABLE IF NOT EXISTS `fr_trade_calendar` (
  `market`      char(2)    NOT NULL,              -- CN/HK/US
  `trade_date`  date       NOT NULL,
  `is_open`     tinyint(1) NOT NULL,              -- 1=交易日 0=非交易日
  `updated_at`  datetime   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`market`, `trade_date`),
  KEY `idx_market_open` (`market`, `is_open`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
