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
  -- hypothesis：研究假设（为什么相信这个因子有 alpha），区别于 description 的
  -- 事实陈述。借鉴 RD-Agent 把 Hypothesis 作为一等公民。旧因子未填留 NULL；
  -- factor_assistant 之后生成新因子会强制填这一项（system prompt 约束）。
  `hypothesis`      text DEFAULT NULL,
  `params_schema`   longtext,
  `default_params`  longtext,
  `supported_freqs` varchar(64) NOT NULL DEFAULT '1d',
  `code_hash`       char(40) NOT NULL,
  `version`         int unsigned NOT NULL DEFAULT 1,
  `is_active`       tinyint(1) NOT NULL DEFAULT 1,
  -- L2.D 因子进化 / 血缘 / SOTA 选择
  `parent_factor_id`   varchar(64) DEFAULT NULL,
  `parent_eval_run_id` varchar(64) DEFAULT NULL,
  `generation`         tinyint     NOT NULL DEFAULT 1,
  `is_sota`            tinyint     NOT NULL DEFAULT 0,
  `root_factor_id`     varchar(64) DEFAULT NULL,
  `updated_at`      datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`factor_id`),
  KEY `idx_root` (`root_factor_id`),
  KEY `idx_parent` (`parent_factor_id`)
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
  -- split_date：可选。提供时 eval 会把窗口切成 train / test 两段，各自汇总 IC。
  -- NULL = 不切分，行为与老评估完全一致。
  `split_date`      date DEFAULT NULL,
  `status`          varchar(16) NOT NULL,
  `progress`        tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`   text,
  -- feedback_text：LLM 友好的"诊断 + 改进建议"，与 error_message 互补：
  -- error_message 仅 failed 写；feedback_text 在 success / failed 都可写
  -- （例如 success 但 IC 极低也算需要诊断）。借鉴 RD-Agent 反馈三元组。
  `feedback_text`   text,
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

-- 【新增】成本敏感性分析任务：一条 run 跑多个 cost_bps 点、各点指标汇总入 points_json。
-- 语义上独立于单次 fr_backtest_runs：敏感性不存 equity/orders/trades artifact（N 份冗余），
-- 只存每个点的结构化指标 + 原始 stats 字典，前端画曲线用。
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
  -- cost_bps_list / points_json 都是 JSON array，前者是用户请求输入、后者是结果。
  -- 分开存：即使任务失败、points 空，我们也能看到当初请求了哪些点。
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

-- 【新增】多因子合成与因子相关性任务（run 级元数据 + 合成结果 + 相关性矩阵）。
-- 一条 run = (pool, [因子集合], method, 窗口) 唯一一次合成 + 评估；
-- payload_json 完全复用 eval_service.evaluate_factor_panel 的结构，前端详情页和
-- 评估详情页可以共用同一套图表组件。
-- per_factor_ic_json / weights_json 是合成场景独有：前者给用户看"原始因子谁贡献"，
-- 后者只在 ic_weighted 下有值，展示实际权重。
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

-- 【新增】参数敏感性扫描任务：扫同一因子的一个超参在 N 个取值下的评估指标。
-- 和 fr_cost_sensitivity_runs 结构对齐：一条 run 跑多个采样点，结果汇总进 points_json。
-- 每个 value 走一次完整 evaluate_factor_panel；同一 run 内 close panel 复用。
CREATE TABLE IF NOT EXISTS `fr_param_sensitivity_runs` (
  `run_id`           varchar(64) NOT NULL,
  `factor_id`        varchar(64) NOT NULL,
  `factor_version`   int unsigned NOT NULL,
  -- 被扫的参数名 + 各采样点（用户请求原样、去重升序）。
  `param_name`       varchar(64) NOT NULL,
  `values_json`      varchar(1000) NOT NULL,
  -- 其它参数的覆盖项（可选），和单因子评估的 params 语义对齐。
  `base_params_json` longtext,
  `pool_id`          bigint unsigned NOT NULL,
  `freq`             varchar(8) NOT NULL DEFAULT '1d',
  `start_date`       date NOT NULL,
  `end_date`         date NOT NULL,
  `n_groups`         tinyint unsigned NOT NULL DEFAULT 5,
  `forward_periods`  varchar(64) NOT NULL DEFAULT '[1,5,10]',
  -- points_json = {"points": [...], "default_value": ..., "schema_entry": {...}}
  -- default_value / schema_entry 跟因子版本走，只做前端展示，不参与查询。
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
