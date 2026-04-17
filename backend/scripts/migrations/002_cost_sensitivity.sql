-- 新建 fr_cost_sensitivity_runs 表：交易成本敏感性分析任务。
-- 语义独立于 fr_backtest_runs：一条 run 跑多个 cost_bps，结果汇总入 points_json。
-- 对已有库：直接执行本脚本；已初始化新库会走 init_mysql.sql 里同名定义，幂等。

CREATE TABLE IF NOT EXISTS `fr_cost_sensitivity_runs` (
  `run_id`           varchar(64) NOT NULL,
  `factor_id`        varchar(64) NOT NULL,
  `factor_version`   int unsigned NOT NULL,
  `params_hash`      char(40) NOT NULL,
  `params_json`      longtext,
  `pool_id`          bigint unsigned NOT NULL,
  `freq`             varchar(8) NOT NULL DEFAULT '1d',
  `start_date`       date NOT NULL,
  `end_date`         date NOT NULL,
  `n_groups`         tinyint unsigned NOT NULL DEFAULT 5,
  `rebalance_period` int unsigned NOT NULL DEFAULT 1,
  `position`         varchar(16) NOT NULL DEFAULT 'top',
  `init_cash`        double NOT NULL DEFAULT 1e7,
  `cost_bps_list`    varchar(500) NOT NULL,
  `points_json`      longtext,
  `status`           varchar(16) NOT NULL,
  `progress`         tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`    text,
  `created_at`       datetime(6) NOT NULL,
  `started_at`       datetime(6) DEFAULT NULL,
  `finished_at`      datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
