-- Phase 3 续：实盘监控订阅表 fr_signal_subscriptions。
--
-- 用途：从"一次性快照"（fr_signal_runs）升级到"持续监控"模型。
--   - signal_run = 一次性触发的快照（保留为审计 / 历史结果）；
--   - subscription = 用户开启的"实盘监控"，worker 周期性按 refresh_interval_sec
--     重算并产出新的 signal_run（关联 subscription_id）。
--
-- 用户在前端 toggle "开启实盘监控" → INSERT 一条 row（is_active=1）；
-- toggle "关闭" → UPDATE is_active=0；worker 主循环每 tick 查表，自然响应。
--
-- 设计取舍：
-- 1) 字段几乎与 fr_signal_runs 重复（factor_items / pool / method / n_groups / ...）
--    没有抽公共表——subscription 是"配置模板 + 运行状态"，signal_run 是"快照结果"，
--    职责不同；强行 normalize 会让 worker 每次刷新都要 join 两表，得不偿失。
-- 2) refresh_interval_sec 默认 300（5min）：与原 worker spot_interval 一致。允许
--    每个订阅独立设置（如 1min 高频 / 30min 低频）。
-- 3) is_active 是 toggle 入口；删除订阅用 DELETE。两者语义有别：
--    - is_active=0：暂停，保留历史 last_run_id 链；
--    - DELETE：清理订阅记录，保留 fr_signal_runs 中的历史 run（subscription_id
--      字段在 run 中变成"指向已删除订阅"，对审计无影响）。
-- 4) last_refresh_at + last_run_id 缓存最新一次刷新结果，前端列表页直接展示
--    无需跨表 join。

CREATE TABLE IF NOT EXISTS `fr_signal_subscriptions` (
  `subscription_id`      varchar(64) NOT NULL,
  -- 订阅配置（对齐 fr_signal_runs）
  `factor_items_json`    longtext NOT NULL,
  `method`               varchar(32) NOT NULL DEFAULT 'equal',
  `pool_id`              bigint unsigned NOT NULL,
  `n_groups`             tinyint unsigned NOT NULL DEFAULT 5,
  `ic_lookback_days`     smallint unsigned NOT NULL DEFAULT 60,
  `filter_price_limit`   tinyint(1) NOT NULL DEFAULT 1,
  `top_n`                int unsigned DEFAULT NULL,
  -- 调度
  `refresh_interval_sec` int unsigned NOT NULL DEFAULT 300,
  -- 状态
  `is_active`            tinyint(1) NOT NULL DEFAULT 1,
  `last_refresh_at`      datetime(6) DEFAULT NULL,
  `last_run_id`          varchar(64) DEFAULT NULL,
  -- 时间
  `created_at`           datetime(6) NOT NULL,
  `updated_at`           datetime(6) NOT NULL,
  PRIMARY KEY (`subscription_id`),
  KEY `idx_active` (`is_active`),
  KEY `idx_pool` (`pool_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
