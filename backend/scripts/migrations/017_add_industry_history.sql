-- 017: fr_industry_history（行业分类历史快照）
-- 每日拉取申万一级行业分类，仅写入新增/变化的行，天然形成历史快照
-- snapshot_date=快照日期，查询时取 as_of_date 之前最近的快照即为该日行业归属

CREATE TABLE IF NOT EXISTS `fr_industry_history` (
  `symbol`         varchar(16)  NOT NULL,
  `snapshot_date`  date         NOT NULL,
  `industry_l1`    varchar(64)  DEFAULT NULL COMMENT '申万一级行业',
  `industry_l2`    varchar(64)  DEFAULT NULL COMMENT '申万二级行业',
  `classification` varchar(32)  NOT NULL DEFAULT 'sw' COMMENT '分类标准：sw/csrc',
  PRIMARY KEY (`symbol`, `snapshot_date`),
  KEY `idx_snapshot` (`snapshot_date`),
  KEY `idx_symbol_date` (`symbol`, `snapshot_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
