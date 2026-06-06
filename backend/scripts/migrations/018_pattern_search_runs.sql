-- 018: 图形相似度检索（截图找相似股票 by_image）异步任务表
-- by_image 涉及视觉 LLM + 全池 DTW，耗时长、易超时，改为异步任务 + 记录列表。
-- 不存原始截图二进制，只存文件名 + 识别曲线 + 检索结果。

CREATE TABLE IF NOT EXISTS `fr_pattern_search_runs` (
  `run_id`        varchar(64)       NOT NULL,
  `pool_id`       bigint unsigned   NOT NULL,
  `image_names`   text COMMENT '上传截图文件名 JSON 数组',
  `num_images`    smallint unsigned NOT NULL DEFAULT 0,
  `hint`          text,
  `scales_json`   varchar(200)      DEFAULT NULL,
  `top_k`         int unsigned      NOT NULL DEFAULT 20,
  `agg`           varchar(8)        NOT NULL DEFAULT 'min' COMMENT '多图聚合 min/mean',
  `status`        varchar(16)       NOT NULL,
  `progress`      tinyint unsigned  NOT NULL DEFAULT 0,
  `error_message` text,
  `created_at`    datetime(6)       NOT NULL,
  `started_at`    datetime(6)       DEFAULT NULL,
  `finished_at`   datetime(6)       DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_pattern_search_results` (
  `run_id`            varchar(64) NOT NULL,
  `query_curves_json` longtext COMMENT '每张图识别出的归一化曲线 [[...],...]',
  `matches_json`      longtext COMMENT '检索结果（相似股票列表）',
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
