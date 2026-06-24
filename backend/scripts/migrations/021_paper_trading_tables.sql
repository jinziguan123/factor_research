-- 021: 模拟盘(纸上交易)四张表。
-- 回测与实盘之间的桥：用真实(快照)价、有状态逐步推进、不真下单。
-- accounts 存账户+策略(factor_items_json/method/pool/top_n)；positions 当前持仓；
-- nav 净值时序(画曲线)；trades 成交流水。详见 services/paper_trading_service.py。

CREATE TABLE IF NOT EXISTS `fr_paper_accounts` (
  `account_id`        varchar(64)      NOT NULL,
  `name`              varchar(128)     NOT NULL,
  `factor_items_json` longtext         NOT NULL COMMENT '策略因子项(同 signal 的 factor_items)',
  `method`            varchar(32)      NOT NULL DEFAULT 'equal' COMMENT '多因子合成方法',
  `pool_id`           bigint unsigned  NOT NULL,
  `n_groups`          tinyint unsigned NOT NULL DEFAULT '5',
  `top_n`             int unsigned     DEFAULT NULL COMMENT '持仓只数；空=取头部分组',
  `init_cash`         double           NOT NULL,
  `cash`              double           NOT NULL COMMENT '当前现金(随调仓更新)',
  `status`            varchar(16)      NOT NULL DEFAULT 'active',
  `created_at`        datetime(6)      NOT NULL,
  `last_rebalance_at` datetime(6)      DEFAULT NULL,
  PRIMARY KEY (`account_id`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_paper_positions` (
  `account_id` varchar(64) NOT NULL,
  `symbol`     varchar(16) NOT NULL,
  `qty`        double      NOT NULL,
  `avg_price`  double      NOT NULL,
  PRIMARY KEY (`account_id`, `symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_paper_nav` (
  `id`           bigint unsigned NOT NULL AUTO_INCREMENT,
  `account_id`   varchar(64)     NOT NULL,
  `ts`           datetime(6)     NOT NULL,
  `nav`          double          NOT NULL,
  `cash`         double          NOT NULL,
  `market_value` double          NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_account_ts` (`account_id`, `ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_paper_trades` (
  `id`         bigint unsigned NOT NULL AUTO_INCREMENT,
  `account_id` varchar(64)     NOT NULL,
  `ts`         datetime(6)     NOT NULL,
  `symbol`     varchar(16)     NOT NULL,
  `side`       varchar(8)      NOT NULL,
  `qty`        double          NOT NULL,
  `price`      double          NOT NULL,
  `fee`        double          NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_account_ts` (`account_id`, `ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
