-- fr_signal_runs 加 subscription_id 列：标记某条 run 是否来自订阅刷新。
--
-- NULL = 用户手动一次性触发（沿用之前行为）；
-- 非 NULL = worker 在该订阅的某次刷新中创建（前端订阅详情页据此查询历史）。

ALTER TABLE `fr_signal_runs`
  ADD COLUMN `subscription_id` varchar(64) DEFAULT NULL
    COMMENT '关联订阅；NULL=一次性触发';

ALTER TABLE `fr_signal_runs`
  ADD KEY `idx_subscription` (`subscription_id`);
