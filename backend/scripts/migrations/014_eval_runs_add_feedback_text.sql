-- 014: fr_factor_eval_runs 加 feedback_text 列（LLM 反馈循环数据基底）
--
-- 借鉴 RD-Agent 的 (task_description, code, execution_feedback) 三元组：
-- - error_message：用户面向的报错文本（traceback / 友好错误说明），保持不变
-- - feedback_text（新增）：LLM 友好的"问题诊断 + 改进建议"
--
-- 用例（L1.1 auto-eval）：factor_assistant 生成因子后自动跑一次轻量 IC 评估，
-- 把"IC=0.001 偏弱 / 与已有因子 X 相关 0.99 / 单调性破坏"等结论写到这里，
-- 前端展示给用户、将来 LLM 反馈闭环也读这里决定下一轮怎么改。
--
-- 与 error_message 分开的原因：error_message 是 status='failed' 的副产物，
-- feedback_text 是 success / failed 都可能写——例如成功跑完但 IC 极低，
-- 仍需要"诊断"的语义槽位。
ALTER TABLE fr_factor_eval_runs
ADD COLUMN feedback_text TEXT DEFAULT NULL AFTER error_message;
