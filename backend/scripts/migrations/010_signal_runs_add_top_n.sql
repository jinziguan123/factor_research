-- Phase 3 续：fr_signal_runs 加 top_n 列。
--
-- 用途：用户期望"前 K 只"而非"qcut 顶组的全部"。n_groups=5 时顶组在
-- 5000 池中含 ~1000 只，量级太大；top_n 在 service 层 qcut 后再切片
-- [:top_n]，让用户拿到真正可执行的小集合（如 top 20）。
--
-- 兼容：NULL 表示回退到原有行为（qcut 顶组全部），已有记录无需迁移。

ALTER TABLE `fr_signal_runs`
  ADD COLUMN `top_n` INT UNSIGNED DEFAULT NULL COMMENT '可选 top K 限制；NULL=qcut 顶组全部';
