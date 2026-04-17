-- 给 fr_factor_eval_runs 加 split_date 列，支持样本内 / 样本外切分评估。
-- 幂等：IF NOT EXISTS（MySQL 8.0.29+）。旧版 MySQL 会抛 Duplicate column 错，可忽略。

ALTER TABLE `fr_factor_eval_runs`
  ADD COLUMN IF NOT EXISTS `split_date` date DEFAULT NULL AFTER `n_groups`;
