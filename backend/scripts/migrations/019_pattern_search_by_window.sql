-- 019: 图形检索任务表支持「相似K线选股 by_window」。
-- by_window 用一段或多段真实走势在股票池里选股，也走异步任务（全池 DTW 较慢易超时）。
-- 复用 fr_pattern_search_runs：加 kind 区分截图/走势，query_json 存查询窗口（多段）。

ALTER TABLE `fr_pattern_search_runs`
  ADD COLUMN `kind` varchar(16) NOT NULL DEFAULT 'by_image' COMMENT 'by_image 截图 / by_window 走势'
    AFTER `run_id`;

ALTER TABLE `fr_pattern_search_runs`
  ADD COLUMN `query_json` longtext COMMENT 'by_window 的查询窗口列表 [{symbol,start,end}]'
    AFTER `image_names`;
