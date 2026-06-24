-- -------------------------------------------------------------
-- TablePlus 6.8.1(655)
--
-- https://tableplus.com/
--
-- Database: quant_data
-- Generation Time: 2026-05-11 14:24:42.7210
-- -------------------------------------------------------------


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;


CREATE TABLE `backtest_artifacts` (
  `run_id` varchar(64) NOT NULL,
  `artifact_type` varchar(64) NOT NULL,
  `artifact_path` varchar(500) NOT NULL,
  PRIMARY KEY (`run_id`,`artifact_type`),
  KEY `idx_run_id` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `backtest_metrics` (
  `run_id` varchar(64) NOT NULL,
  `total_return` double NOT NULL DEFAULT '0',
  `annual_return` double NOT NULL DEFAULT '0',
  `sharpe_ratio` double NOT NULL DEFAULT '0',
  `max_drawdown` double NOT NULL DEFAULT '0',
  `win_rate` double NOT NULL DEFAULT '0',
  `trade_count` int NOT NULL DEFAULT '0',
  `payload_json` longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `backtest_runs` (
  `run_id` varchar(64) NOT NULL,
  `name` varchar(255) DEFAULT NULL,
  `strategy_name` varchar(255) DEFAULT NULL,
  `status` varchar(32) NOT NULL,
  `params_json` longtext,
  `error_message` text,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_status_created_at` (`status`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_backtest_artifacts` (
  `run_id` varchar(64) NOT NULL,
  `artifact_type` varchar(64) NOT NULL,
  `artifact_path` varchar(500) NOT NULL,
  PRIMARY KEY (`run_id`,`artifact_type`),
  KEY `idx_run_id` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_backtest_metrics` (
  `run_id` varchar(64) NOT NULL,
  `total_return` double NOT NULL DEFAULT '0',
  `annual_return` double NOT NULL DEFAULT '0',
  `sharpe_ratio` double NOT NULL DEFAULT '0',
  `max_drawdown` double NOT NULL DEFAULT '0',
  `win_rate` double NOT NULL DEFAULT '0',
  `trade_count` int NOT NULL DEFAULT '0',
  `payload_json` longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_backtest_runs` (
  `run_id` varchar(64) NOT NULL,
  `name` varchar(255) DEFAULT NULL,
  `factor_id` varchar(64) NOT NULL,
  `factor_version` int unsigned NOT NULL,
  `params_hash` char(40) NOT NULL,
  `params_json` longtext,
  `pool_id` bigint unsigned NOT NULL,
  `freq` varchar(8) NOT NULL DEFAULT '1d',
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`,`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_composition_runs` (
  `run_id` varchar(64) NOT NULL,
  `pool_id` bigint unsigned NOT NULL,
  `freq` varchar(8) NOT NULL DEFAULT '1d',
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `method` varchar(32) NOT NULL,
  `factor_items_json` longtext NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `forward_periods` varchar(64) NOT NULL DEFAULT '[1,5,10]',
  `ic_weight_period` tinyint unsigned NOT NULL DEFAULT '1',
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `ic_mean` double DEFAULT NULL,
  `ic_std` double DEFAULT NULL,
  `ic_ir` double DEFAULT NULL,
  `ic_win_rate` double DEFAULT NULL,
  `ic_t_stat` double DEFAULT NULL,
  `rank_ic_mean` double DEFAULT NULL,
  `rank_ic_std` double DEFAULT NULL,
  `rank_ic_ir` double DEFAULT NULL,
  `turnover_mean` double DEFAULT NULL,
  `long_short_sharpe` double DEFAULT NULL,
  `long_short_annret` double DEFAULT NULL,
  `corr_matrix_json` longtext,
  `per_factor_ic_json` longtext,
  `weights_json` longtext,
  `payload_json` longtext,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_pool_status` (`pool_id`,`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_cost_sensitivity_runs` (
  `run_id` varchar(64) NOT NULL,
  `factor_id` varchar(64) NOT NULL,
  `factor_version` int unsigned NOT NULL,
  `params_hash` char(40) NOT NULL,
  `params_json` longtext,
  `pool_id` bigint unsigned NOT NULL,
  `freq` varchar(8) NOT NULL DEFAULT '1d',
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `rebalance_period` int unsigned NOT NULL DEFAULT '1',
  `position` varchar(16) NOT NULL DEFAULT 'top',
  `init_cash` double NOT NULL DEFAULT '10000000',
  `cost_bps_list` varchar(500) NOT NULL,
  `points_json` longtext,
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`,`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_daily_market_cap` (
  `symbol_id` int unsigned NOT NULL,
  `trade_date` date NOT NULL,
  `total_mv` decimal(18,2) DEFAULT NULL COMMENT '总市值（元）',
  `float_mv` decimal(18,2) DEFAULT NULL COMMENT '流通市值（元）',
  PRIMARY KEY (`symbol_id`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_daily_pb` (
  `symbol_id` int unsigned NOT NULL,
  `trade_date` date NOT NULL,
  `pb` decimal(10,4) DEFAULT NULL COMMENT '市净率',
  PRIMARY KEY (`symbol_id`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_factor_eval_metrics` (
  `run_id` varchar(64) NOT NULL,
  `ic_mean` double DEFAULT NULL,
  `ic_std` double DEFAULT NULL,
  `ic_ir` double DEFAULT NULL,
  `ic_win_rate` double DEFAULT NULL,
  `ic_t_stat` double DEFAULT NULL,
  `rank_ic_mean` double DEFAULT NULL,
  `rank_ic_std` double DEFAULT NULL,
  `rank_ic_ir` double DEFAULT NULL,
  `turnover_mean` double DEFAULT NULL,
  `long_short_sharpe` double DEFAULT NULL,
  `long_short_annret` double DEFAULT NULL,
  `payload_json` longtext,
  `neut_ic_mean` double DEFAULT NULL,
  `neut_ic_ir` double DEFAULT NULL,
  `neut_rank_ic_mean` double DEFAULT NULL,
  `neut_rank_ic_ir` double DEFAULT NULL,
  `neut_long_short_annret` double DEFAULT NULL,
  `neut_payload_json` longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_factor_eval_runs` (
  `run_id` varchar(64) NOT NULL,
  `factor_id` varchar(64) NOT NULL,
  `factor_version` int unsigned NOT NULL,
  `params_hash` char(40) NOT NULL,
  `params_json` longtext,
  `pool_id` bigint unsigned NOT NULL,
  `freq` varchar(8) NOT NULL DEFAULT '1d',
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `forward_periods` varchar(64) NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `split_date` date DEFAULT NULL,
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `feedback_text` text,
  `created_at` datetime NOT NULL,
  `started_at` datetime DEFAULT NULL,
  `finished_at` datetime DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`,`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_factor_meta` (
  `factor_id` varchar(64) NOT NULL,
  `display_name` varchar(128) NOT NULL,
  `category` varchar(64) NOT NULL,
  `description` varchar(1000) DEFAULT NULL,
  `hypothesis` text,
  `params_schema` longtext,
  `default_params` longtext,
  `supported_freqs` varchar(64) NOT NULL DEFAULT '1d',
  `code_hash` char(40) NOT NULL,
  `version` int unsigned NOT NULL DEFAULT '1',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `parent_factor_id` varchar(64) DEFAULT NULL,
  `parent_eval_run_id` varchar(64) DEFAULT NULL,
  `generation` tinyint NOT NULL DEFAULT '1',
  `is_sota` tinyint NOT NULL DEFAULT '0',
  `root_factor_id` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`factor_id`),
  KEY `idx_root` (`root_factor_id`),
  KEY `idx_parent` (`parent_factor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_fundamental_profit` (
  `symbol` varchar(16) NOT NULL,
  `report_date` date NOT NULL,
  `announcement_date` date NOT NULL,
  `roe_avg` decimal(20,8) DEFAULT NULL,
  `np_margin` decimal(20,8) DEFAULT NULL,
  `gp_margin` decimal(20,8) DEFAULT NULL,
  `net_profit` decimal(28,4) DEFAULT NULL,
  `eps_ttm` decimal(20,8) DEFAULT NULL,
  `mb_revenue` decimal(28,4) DEFAULT NULL,
  `total_share` decimal(20,2) DEFAULT NULL,
  `liqa_share` decimal(20,2) DEFAULT NULL,
  `data_source` varchar(16) NOT NULL DEFAULT 'baostock',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`,`report_date`),
  KEY `idx_pit` (`symbol`,`announcement_date`),
  KEY `idx_announcement` (`announcement_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_index_constituent` (
  `index_code` varchar(16) NOT NULL,
  `symbol` varchar(16) NOT NULL,
  `effective_date` date NOT NULL,
  `end_date` date DEFAULT NULL,
  `weight` decimal(10,6) DEFAULT NULL,
  `data_source` varchar(16) NOT NULL DEFAULT 'baostock',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`index_code`,`symbol`,`effective_date`),
  KEY `idx_index_active` (`index_code`,`end_date`),
  KEY `idx_index_eff` (`index_code`,`effective_date`),
  KEY `idx_symbol_index` (`symbol`,`index_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_industry_current` (
  `symbol` varchar(16) NOT NULL,
  `industry_l1` varchar(64) NOT NULL DEFAULT '',
  `industry_classification` varchar(32) NOT NULL DEFAULT '',
  `sw_l1` varchar(32) NOT NULL DEFAULT '',
  `sw_l2` varchar(32) NOT NULL DEFAULT '',
  `sw_l3` varchar(64) NOT NULL DEFAULT '',
  `snapshot_date` date NOT NULL,
  `data_source` varchar(16) NOT NULL DEFAULT 'baostock',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`),
  KEY `idx_industry_l1` (`industry_l1`),
  KEY `idx_snapshot_date` (`snapshot_date`),
  KEY `idx_sw_l1` (`sw_l1`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_industry_history` (
  `symbol` varchar(16) NOT NULL,
  `snapshot_date` date NOT NULL,
  `industry_l1` varchar(64) DEFAULT NULL COMMENT '申万一级行业',
  `industry_l2` varchar(64) DEFAULT NULL COMMENT '申万二级行业',
  `classification` varchar(32) NOT NULL DEFAULT 'sw' COMMENT '分类标准：sw/csrc',
  PRIMARY KEY (`symbol`,`snapshot_date`),
  KEY `idx_snapshot` (`snapshot_date`),
  KEY `idx_symbol_date` (`symbol`,`snapshot_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_instrument` (
  `symbol` varchar(16) NOT NULL,
  `market` char(2) NOT NULL DEFAULT 'CN',
  `exchange` varchar(8) NOT NULL,
  `name` varchar(64) NOT NULL,
  `asset_type` varchar(16) NOT NULL DEFAULT 'stock',
  `list_date` date DEFAULT NULL,
  `delist_date` date DEFAULT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'active',
  `is_st` tinyint(1) NOT NULL DEFAULT '0',
  `data_source` varchar(16) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`),
  KEY `idx_status_list` (`status`,`list_date`),
  KEY `idx_delist` (`delist_date`),
  KEY `idx_market_exch` (`market`,`exchange`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_param_sensitivity_runs` (
  `run_id` varchar(64) NOT NULL,
  `factor_id` varchar(64) NOT NULL,
  `factor_version` int unsigned NOT NULL,
  `param_name` varchar(64) NOT NULL,
  `values_json` varchar(1000) NOT NULL,
  `base_params_json` longtext,
  `pool_id` bigint unsigned NOT NULL,
  `freq` varchar(8) NOT NULL DEFAULT '1d',
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `forward_periods` varchar(64) NOT NULL DEFAULT '[1,5,10]',
  `points_json` longtext,
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`,`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_qfq_factor` (
  `symbol_id` int unsigned NOT NULL,
  `trade_date` date NOT NULL,
  `factor` double NOT NULL,
  `source_file_mtime` bigint unsigned NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol_id`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_signal_runs` (
  `run_id` varchar(64) NOT NULL,
  `factor_items_json` longtext NOT NULL,
  `method` varchar(32) NOT NULL DEFAULT 'equal',
  `pool_id` bigint unsigned NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `ic_lookback_days` smallint unsigned NOT NULL DEFAULT '60',
  `as_of_time` datetime NOT NULL,
  `as_of_date` date NOT NULL,
  `use_realtime` tinyint(1) NOT NULL DEFAULT '1',
  `filter_price_limit` tinyint(1) NOT NULL DEFAULT '1',
  `status` varchar(16) NOT NULL,
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `error_message` text,
  `created_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `n_holdings_top` int unsigned DEFAULT NULL,
  `n_holdings_bot` int unsigned DEFAULT NULL,
  `payload_json` longtext,
  `top_n` int unsigned DEFAULT NULL COMMENT '可选 top K 限制；NULL=qcut 顶组全部',
  `subscription_id` varchar(64) DEFAULT NULL COMMENT '关联订阅；NULL=一次性触发',
  PRIMARY KEY (`run_id`),
  KEY `idx_as_of_date` (`as_of_date`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_subscription` (`subscription_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_signal_subscriptions` (
  `subscription_id` varchar(64) NOT NULL,
  `factor_items_json` longtext NOT NULL,
  `method` varchar(32) NOT NULL DEFAULT 'equal',
  `pool_id` bigint unsigned NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `ic_lookback_days` smallint unsigned NOT NULL DEFAULT '60',
  `filter_price_limit` tinyint(1) NOT NULL DEFAULT '1',
  `top_n` int unsigned DEFAULT NULL,
  `refresh_interval_sec` int unsigned NOT NULL DEFAULT '300',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `last_refresh_at` datetime(6) DEFAULT NULL,
  `last_run_id` varchar(64) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`subscription_id`),
  KEY `idx_active` (`is_active`),
  KEY `idx_pool` (`pool_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_paper_accounts` (
  `account_id` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `factor_items_json` longtext NOT NULL,
  `method` varchar(32) NOT NULL DEFAULT 'equal',
  `pool_id` bigint unsigned NOT NULL,
  `n_groups` tinyint unsigned NOT NULL DEFAULT '5',
  `top_n` int unsigned DEFAULT NULL,
  `init_cash` double NOT NULL,
  `cash` double NOT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'active',
  `created_at` datetime(6) NOT NULL,
  `last_rebalance_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`account_id`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_paper_positions` (
  `account_id` varchar(64) NOT NULL,
  `symbol` varchar(16) NOT NULL,
  `qty` double NOT NULL,
  `avg_price` double NOT NULL,
  PRIMARY KEY (`account_id`,`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_paper_nav` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `account_id` varchar(64) NOT NULL,
  `ts` datetime(6) NOT NULL,
  `nav` double NOT NULL,
  `cash` double NOT NULL,
  `market_value` double NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_account_ts` (`account_id`,`ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_paper_trades` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `account_id` varchar(64) NOT NULL,
  `ts` datetime(6) NOT NULL,
  `symbol` varchar(16) NOT NULL,
  `side` varchar(8) NOT NULL,
  `qty` double NOT NULL,
  `price` double NOT NULL,
  `fee` double NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_account_ts` (`account_id`,`ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `fr_trade_calendar` (
  `market` char(2) NOT NULL,
  `trade_date` date NOT NULL,
  `is_open` tinyint(1) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`market`,`trade_date`),
  KEY `idx_market_open` (`market`,`is_open`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `stock_bar_import_job` (
  `job_id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `job_type` tinyint unsigned NOT NULL COMMENT '1=full,2=incremental',
  `status` tinyint unsigned NOT NULL COMMENT '1=running,2=success,3=partial_success,4=failed',
  `symbol_count` int unsigned NOT NULL DEFAULT '0',
  `success_symbol_count` int unsigned NOT NULL DEFAULT '0',
  `failed_symbol_count` int unsigned NOT NULL DEFAULT '0',
  `inserted_rows` bigint unsigned NOT NULL DEFAULT '0',
  `updated_rows` bigint unsigned NOT NULL DEFAULT '0',
  `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` datetime DEFAULT NULL,
  `note` varchar(500) DEFAULT NULL,
  PRIMARY KEY (`job_id`)
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `stock_pool` (
  `pool_id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `owner_key` varchar(64) NOT NULL,
  `pool_name` varchar(128) NOT NULL,
  `description` varchar(500) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`pool_id`),
  UNIQUE KEY `uk_owner_pool_name` (`owner_key`,`pool_name`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `stock_pool_symbol` (
  `pool_id` bigint unsigned NOT NULL,
  `symbol_id` int unsigned NOT NULL,
  `sort_order` int unsigned NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`pool_id`,`symbol_id`),
  KEY `idx_symbol_id` (`symbol_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `stock_symbol` (
  `symbol_id` int unsigned NOT NULL AUTO_INCREMENT,
  `symbol` char(9) NOT NULL,
  `code` char(6) NOT NULL,
  `market` tinyint unsigned NOT NULL COMMENT '1=SH,2=SZ,3=BJ',
  `name` varchar(32) DEFAULT NULL,
  `dat_path` varchar(255) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol_id`),
  UNIQUE KEY `uk_symbol` (`symbol`),
  KEY `idx_market_code` (`market`,`code`)
) ENGINE=InnoDB AUTO_INCREMENT=54444 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;