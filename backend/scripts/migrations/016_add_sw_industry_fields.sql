-- 016: 为 fr_industry_current 新增申万行业三级字段。
-- 数据源：Akshare 申万行业接口（sw_index_*_info + index_component_sw）。
ALTER TABLE `fr_industry_current`
  ADD COLUMN `sw_l1` varchar(32) NOT NULL DEFAULT '' AFTER `industry_classification`,
  ADD COLUMN `sw_l2` varchar(32) NOT NULL DEFAULT '' AFTER `sw_l1`,
  ADD COLUMN `sw_l3` varchar(64) NOT NULL DEFAULT '' AFTER `sw_l2`,
  ADD KEY `idx_sw_l1` (`sw_l1`);
