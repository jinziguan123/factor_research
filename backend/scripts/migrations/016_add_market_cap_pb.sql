-- 016: fr_daily_market_cap + fr_daily_pb（日频市值和市净率）
-- fr_daily_market_cap: 日频总市值 / 流通市值，symbol_id 与 stock_bar_1d 对齐
-- fr_daily_pb: 日频市净率，从 akshare spot 快照拉取

CREATE TABLE IF NOT EXISTS `fr_daily_market_cap` (
  `symbol_id`  int unsigned NOT NULL,
  `trade_date` date         NOT NULL,
  `total_mv`   decimal(18,2) DEFAULT NULL COMMENT '总市值（元）',
  `float_mv`   decimal(18,2) DEFAULT NULL COMMENT '流通市值（元）',
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_daily_pb` (
  `symbol_id`  int unsigned NOT NULL,
  `trade_date` date         NOT NULL,
  `pb`         decimal(10,4) DEFAULT NULL COMMENT '市净率',
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Add neutralization result columns to eval metrics table
ALTER TABLE fr_factor_eval_metrics
    ADD COLUMN neut_ic_mean            double DEFAULT NULL,
    ADD COLUMN neut_ic_ir              double DEFAULT NULL,
    ADD COLUMN neut_rank_ic_mean       double DEFAULT NULL,
    ADD COLUMN neut_rank_ic_ir         double DEFAULT NULL,
    ADD COLUMN neut_long_short_annret  double DEFAULT NULL,
    ADD COLUMN neut_payload_json       longtext;
