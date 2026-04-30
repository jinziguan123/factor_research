-- 013: fr_factor_meta 新增 hypothesis 字段（研究假设）
--
-- 借鉴 RD-Agent 把 Hypothesis 作为一等公民的设计：让每个因子带"为什么相信
-- 这个因子有 alpha"的主观陈述，区别于 description 的事实陈述。
--
-- - description（事实）："120 日动量跳 5 日"
-- - hypothesis（直觉）："过去 1 周易有情绪反弹噪声，跳过它聚焦中长动量"
--
-- 旧因子未填的留 NULL（兼容）；factor_assistant 之后生成新因子会强制填这一项。
ALTER TABLE fr_factor_meta
ADD COLUMN hypothesis TEXT DEFAULT NULL AFTER description;
