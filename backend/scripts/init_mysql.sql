-- factor_research 专属 MySQL schema（全部 fr_ 前缀，均为 CREATE TABLE IF NOT EXISTS 幂等）。
-- 生产已有的 stock_symbol / stock_pool / stock_pool_symbol / stock_bar_import_job / backtest_runs
-- 等业务表不在此脚本范围内，由 timing_driven 维护；factor_research 只读或以 owner_key 隔离。
-- 完整字段定义详见 docs/plans/2026-04-16-factor-research-design.md §3.1。

-- 【新增】前复权因子（生产库无此表，本项目负责维护）
CREATE TABLE IF NOT EXISTS `fr_qfq_factor` (
  `symbol_id`         int unsigned NOT NULL,
  `trade_date`        date NOT NULL,
  `factor`            double NOT NULL,
  `source_file_mtime` bigint unsigned NOT NULL DEFAULT 0,
  `created_at`        datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子元数据（由热加载机制维护；code_hash 变化时 version 自增）
CREATE TABLE IF NOT EXISTS `fr_factor_meta` (
  `factor_id`       varchar(64)  NOT NULL,
  `display_name`    varchar(128) NOT NULL,
  `category`        varchar(64)  NOT NULL,
  `description`     varchar(1000) DEFAULT NULL,
  `params_schema`   longtext,
  `default_params`  longtext,
  `supported_freqs` varchar(64) NOT NULL DEFAULT '1d',
  `code_hash`       char(40) NOT NULL,
  `version`         int unsigned NOT NULL DEFAULT 1,
  `is_active`       tinyint(1) NOT NULL DEFAULT 1,
  `updated_at`      datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`factor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子评估任务（run 级元数据 + 状态机）
CREATE TABLE IF NOT EXISTS `fr_factor_eval_runs` (
  `run_id`          varchar(64) NOT NULL,
  `factor_id`       varchar(64) NOT NULL,
  `factor_version`  int unsigned NOT NULL,
  `params_hash`     char(40) NOT NULL,
  `params_json`     longtext,
  `pool_id`         bigint unsigned NOT NULL,
  `freq`            varchar(8) NOT NULL DEFAULT '1d',
  `start_date`      date NOT NULL,
  `end_date`        date NOT NULL,
  `forward_periods` varchar(64) NOT NULL,
  `n_groups`        tinyint unsigned NOT NULL DEFAULT 5,
  `status`          varchar(16) NOT NULL,
  `progress`        tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`   text,
  `created_at`      datetime NOT NULL,
  `started_at`      datetime DEFAULT NULL,
  `finished_at`     datetime DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子评估结果（结构化指标 + 曲线 JSON payload）
CREATE TABLE IF NOT EXISTS `fr_factor_eval_metrics` (
  `run_id`             varchar(64) NOT NULL,
  `ic_mean`            double,
  `ic_std`             double,
  `ic_ir`              double,
  `ic_win_rate`        double,
  `ic_t_stat`          double,
  `rank_ic_mean`       double,
  `rank_ic_std`        double,
  `rank_ic_ir`         double,
  `turnover_mean`      double,
  `long_short_sharpe`  double,
  `long_short_annret`  double,
  `payload_json`       longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测任务（独立一份，不改动生产 backtest_runs）
CREATE TABLE IF NOT EXISTS `fr_backtest_runs` (
  `run_id`          varchar(64) NOT NULL,
  `name`            varchar(255) DEFAULT NULL,
  `factor_id`       varchar(64) NOT NULL,
  `factor_version`  int unsigned NOT NULL,
  `params_hash`     char(40) NOT NULL,
  `params_json`     longtext,
  `pool_id`         bigint unsigned NOT NULL,
  `freq`            varchar(8) NOT NULL DEFAULT '1d',
  `start_date`      date NOT NULL,
  `end_date`        date NOT NULL,
  `status`          varchar(16) NOT NULL,
  `progress`        tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`   text,
  `created_at`      datetime(6) NOT NULL,
  `started_at`      datetime(6) DEFAULT NULL,
  `finished_at`     datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测指标
CREATE TABLE IF NOT EXISTS `fr_backtest_metrics` (
  `run_id`        varchar(64) NOT NULL,
  `total_return`  double NOT NULL DEFAULT 0,
  `annual_return` double NOT NULL DEFAULT 0,
  `sharpe_ratio`  double NOT NULL DEFAULT 0,
  `max_drawdown`  double NOT NULL DEFAULT 0,
  `win_rate`      double NOT NULL DEFAULT 0,
  `trade_count`   int NOT NULL DEFAULT 0,
  `payload_json`  longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测产物路径（equity / orders / trades / stats 等 parquet 产物）
CREATE TABLE IF NOT EXISTS `fr_backtest_artifacts` (
  `run_id`         varchar(64) NOT NULL,
  `artifact_type`  varchar(64) NOT NULL,
  `artifact_path`  varchar(500) NOT NULL,
  PRIMARY KEY (`run_id`, `artifact_type`),
  KEY `idx_run_id` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
