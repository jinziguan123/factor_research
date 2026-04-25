-- Phase 2.b：新增 fr_fundamental_profit。
--
-- 背景：因子库需要"截至日期 X，symbol Y 已公告的最新季报"——这是任何价值因子
-- (PE/PB/ROE/利润增速等) 的 PIT (Point-In-Time) 基础。Baostock query_profit_data
-- 返回每条 row 都带 pubDate（公告日）+ statDate（报告期），是最关键的双时间戳。
--
-- 关键设计取舍：
--
-- 1) 宽表 vs key-value：选**宽表**。query_profit_data 字段集稳定（10 个数值字段，
--    探查 2018Q1 / 2020Q4 / 2023Q2 / 2024Q3 字段一致），且因子查询经常需要多个
--    比率组合 (PE = price / eps，需要同时拿 epsTTM + totalShare)。宽表查询无需
--    PIVOT，索引也利于 PIT 检索。Phase 2.b 之后扩 balance / cashflow 时也采用
--    分表宽表（fr_fundamental_balance、fr_fundamental_cashflow）。
--
-- 2) 主键 (symbol, report_date)：同一报告期一条最终值。如果财报被修订重发
--    (rare but happens)，该接口返回的应该是修订后的最新版本，所以单一行就够。
--    若未来需保留修订历史，再改成三键 (symbol, report_date, announcement_date)。
--
-- 3) PIT 索引 (symbol, announcement_date)：用于查"在某日之前最新已公告的财报"，
--    SQL 形如：
--      SELECT * FROM fr_fundamental_profit
--       WHERE symbol=? AND announcement_date <= ?
--       ORDER BY announcement_date DESC LIMIT 1
--
-- 4) 数值精度：
--    - 比率字段 roe_avg / np_margin / gp_margin / eps_ttm: DECIMAL(20,8)
--      （baostock 返回 6 位小数，留 2 位余量；最大值不会超 10 位整数部分）；
--    - 金额字段 net_profit / mb_revenue: DECIMAL(28,4)
--      （万亿级别，比如四大行年报净利约 3000 亿，留出整数 24 位很安全）；
--    - 股本字段 total_share / liqa_share: DECIMAL(20,2)
--      （baostock 字符串就是 2 位小数）。
--
-- 幂等：CREATE TABLE IF NOT EXISTS；adapter 写入用 ON DUPLICATE KEY UPDATE。

CREATE TABLE IF NOT EXISTS `fr_fundamental_profit` (
  `symbol`             varchar(16)  NOT NULL,
  `report_date`        date         NOT NULL,            -- baostock statDate（"2024-09-30"）
  `announcement_date`  date         NOT NULL,            -- baostock pubDate（公告日，PIT 关键）
  `roe_avg`            decimal(20,8) DEFAULT NULL,       -- 净资产收益率（平均）
  `np_margin`          decimal(20,8) DEFAULT NULL,       -- 销售净利率
  `gp_margin`          decimal(20,8) DEFAULT NULL,       -- 销售毛利率
  `net_profit`         decimal(28,4) DEFAULT NULL,       -- 净利润（元）
  `eps_ttm`            decimal(20,8) DEFAULT NULL,       -- 每股收益 TTM
  `mb_revenue`         decimal(28,4) DEFAULT NULL,       -- 主营营业收入（元；季报常缺失）
  `total_share`        decimal(20,2) DEFAULT NULL,       -- 总股本（股）
  `liqa_share`         decimal(20,2) DEFAULT NULL,       -- 流通 A 股股本
  `data_source`        varchar(16)  NOT NULL DEFAULT 'baostock',
  `updated_at`         datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`, `report_date`),
  KEY `idx_pit` (`symbol`, `announcement_date`),
  KEY `idx_announcement` (`announcement_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
