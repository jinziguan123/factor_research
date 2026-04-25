-- Phase 2.a：新增 fr_industry_current + fr_index_constituent。
--
-- 背景：Phase 1 已落地 fr_instrument + fr_trade_calendar；Phase 2.a 解决两个高频
-- 偏差问题：行业归属（中性化前置）+ 指数成分回溯（构造 HS300/ZZ500/ZZ1000 池）。
--
-- 关键设计取舍（详见 probe_baostock 输出）：
--
-- 1) Baostock query_stock_industry 只返回**当前快照**；全市场所有股票的 updateDate
--    都是同一天（如 2026-04-20），即 updateDate 是接口数据刷新日，**不是行业归属
--    变更日**。所以本平台无法从 Baostock 还原"历史行业归属"。
--    → 不建带 effective/end 区间的"伪历史表"——那会把数据假装能用、留下未来偏差。
--    → 改建 fr_industry_current（当前快照），仅 snapshot_date 标注刷新日；
--    → 历史回溯能力延后到 Phase 2.5：接 Akshare 申万/中信行业（带历史区间）。
--
-- 2) Baostock query_hs300_stocks(date=...) / query_zz500_stocks(date=...) **支持任意
--    历史日期**，且每行返回的 updateDate 就是**该次成分调整公告日**（HS300/ZZ500
--    每年 6 月 / 12 月调整）。所以本表可以**直接按 updateDate 翻篇**建真历史，
--    不需要按月做盲快照。
--    → fr_index_constituent 用 (index_code, symbol, effective_date) 做主键，
--      effective_date = baostock updateDate；end_date NULL 表示当前仍在指数中。
--    → 同步时拿到一组成分，逐条比对：旧成员未在新一批 → 写 end_date；
--      新成员未在旧一批 → 插入新行；effective_date 相同的视为延续不写。
--
-- 幂等：CREATE TABLE IF NOT EXISTS；adapter 同步逻辑也是幂等 upsert + end_date 关闭。

-- ============================================================
-- fr_industry_current：行业归属（当前快照）
-- ============================================================
-- 用途：因子中性化（行业哑变量）、风险模型行业暴露。
-- 警告：本表**不是历史表**。同一 symbol 只有一行，覆盖式更新。回测某历史日期的
-- 行业暴露时，本表会给"将来"才确定的归属——这本身是一种轻微未来偏差，仅用于
-- "目前没真历史源"的临时方案。Phase 2.5 接 Akshare 后会迁移到 fr_industry_history。
CREATE TABLE IF NOT EXISTS `fr_industry_current` (
  `symbol`                  varchar(16) NOT NULL,
  `industry_l1`             varchar(64) NOT NULL DEFAULT '',  -- 一级行业（"C15酒、饮料和精制茶制造业"）
  `industry_classification` varchar(32) NOT NULL DEFAULT '',  -- "证监会行业分类" 等
  `snapshot_date`           date        NOT NULL,             -- 来自 baostock updateDate
  `data_source`             varchar(16) NOT NULL DEFAULT 'baostock',
  `updated_at`              datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol`),
  KEY `idx_industry_l1`     (`industry_l1`),
  KEY `idx_snapshot_date`   (`snapshot_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- fr_index_constituent：指数成分历史（真历史）
-- ============================================================
-- 支持的指数（Phase 2.a）：
--   '000300.SH'  HS300
--   '000905.SH'  ZZ500
--   '000852.SH'  ZZ1000  -- baostock 提供 query_zz1000_stocks
-- 时间区间表达：[effective_date, end_date]，end_date NULL = 当前在该指数。
-- 查询某日 X 的 HS300 成分 SQL：
--   SELECT symbol FROM fr_index_constituent
--    WHERE index_code='000300.SH'
--      AND effective_date <= X
--      AND (end_date IS NULL OR end_date > X);
-- 注：调整公告日就开始算"在指数内"——回测如果想用调整生效日（隔日开盘），上层
-- 在查询时自己 +1 个交易日即可，本表只忠实存接口给的 updateDate。
CREATE TABLE IF NOT EXISTS `fr_index_constituent` (
  `index_code`     varchar(16) NOT NULL,                 -- '000300.SH' / '000905.SH' / '000852.SH'
  `symbol`         varchar(16) NOT NULL,
  `effective_date` date        NOT NULL,                 -- 加入指数的日期（来自 updateDate）
  `end_date`       date        DEFAULT NULL,             -- 离开指数的日期；NULL = 当前在
  `weight`         decimal(10,6) DEFAULT NULL,           -- 权重，baostock 该接口不返回，预留
  `data_source`    varchar(16) NOT NULL DEFAULT 'baostock',
  `updated_at`     datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`index_code`, `symbol`, `effective_date`),
  KEY `idx_index_active`   (`index_code`, `end_date`),
  KEY `idx_index_eff`      (`index_code`, `effective_date`),
  KEY `idx_symbol_index`   (`symbol`, `index_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
