-- 新建 fr_composition_runs 表：多因子合成与因子相关性分析任务。
-- 一次 run 对应一组因子（factor_items_json） + 一种合成方法，产出合成后因子的
-- 评估指标（与 fr_factor_eval_metrics 同构）+ 两两相关矩阵 + 每个原因子 IC。
-- payload_json 直接复用 eval_service.evaluate_factor_panel 的 payload 结构，
-- 前端详情页可以完全复用评估详情页的图表组件。
-- 对已有库：直接执行本脚本；已初始化新库会走 init_mysql.sql 里同名定义，幂等。

CREATE TABLE IF NOT EXISTS `fr_composition_runs` (
  `run_id`             varchar(64) NOT NULL,
  `pool_id`            bigint unsigned NOT NULL,
  `freq`               varchar(8) NOT NULL DEFAULT '1d',
  `start_date`         date NOT NULL,
  `end_date`           date NOT NULL,
  `method`             varchar(32) NOT NULL,
  `factor_items_json`  longtext NOT NULL,
  `n_groups`           tinyint unsigned NOT NULL DEFAULT 5,
  `forward_periods`    varchar(64) NOT NULL DEFAULT '[1,5,10]',
  `ic_weight_period`   tinyint unsigned NOT NULL DEFAULT 1,
  `status`             varchar(16) NOT NULL,
  `progress`           tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`      text,
  `ic_mean`            double DEFAULT NULL,
  `ic_std`             double DEFAULT NULL,
  `ic_ir`              double DEFAULT NULL,
  `ic_win_rate`        double DEFAULT NULL,
  `ic_t_stat`          double DEFAULT NULL,
  `rank_ic_mean`       double DEFAULT NULL,
  `rank_ic_std`        double DEFAULT NULL,
  `rank_ic_ir`         double DEFAULT NULL,
  `turnover_mean`      double DEFAULT NULL,
  `long_short_sharpe`  double DEFAULT NULL,
  `long_short_annret`  double DEFAULT NULL,
  `corr_matrix_json`   longtext,
  `per_factor_ic_json` longtext,
  `weights_json`       longtext,
  `payload_json`       longtext,
  `created_at`         datetime(6) NOT NULL,
  `started_at`         datetime(6) DEFAULT NULL,
  `finished_at`        datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_pool_status` (`pool_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
