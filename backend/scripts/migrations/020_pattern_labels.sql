-- 020: 学习型选股的标注表。
-- 用户对某个"命名形态(pattern_name)"标正例(label=1)/反例(label=0)，
-- worker 据此训练打分器再给股票池打分。一段标注 = 一只股票的一个窗口。

CREATE TABLE IF NOT EXISTS `fr_pattern_labels` (
  `id`           bigint unsigned NOT NULL AUTO_INCREMENT,
  `pattern_name` varchar(64)     NOT NULL COMMENT '正在教的形态名',
  `symbol`       varchar(16)     NOT NULL,
  `start_date`   varchar(16)     DEFAULT NULL COMMENT '窗口起；空=该股最近60日',
  `end_date`     varchar(16)     DEFAULT NULL,
  `label`        tinyint         NOT NULL COMMENT '1=正例 / 0=反例',
  `created_at`   datetime(6)     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_pattern` (`pattern_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
