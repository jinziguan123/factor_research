-- 新建 fr_param_sensitivity_runs 表：参数敏感性扫描任务（异步化）。
-- 语义独立于 fr_factor_eval_runs：一条 run 扫同一因子的一个超参在 N 个取值下，
-- 每点都是一次完整 evaluate_factor_panel，结果汇总入 points_json。
-- 对已有库：直接执行本脚本；已初始化新库会走 init_mysql.sql 里同名定义，幂等。

CREATE TABLE IF NOT EXISTS `fr_param_sensitivity_runs` (
  `run_id`           varchar(64) NOT NULL,
  `factor_id`        varchar(64) NOT NULL,
  `factor_version`   int unsigned NOT NULL,
  -- 被扫的参数名与各采样点；values_json 是前端请求原样（去重升序后）保存的。
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
  -- 把 default_value / schema_entry 放进 JSON 而不是独立列：这两个跟因子版本绑定，
  -- 仅用于前端展示"峰值/默认点"的标注，不参与查询/过滤。
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
