-- 022: fr_backtest_runs 新增 config_json 字段（完整回测配置快照）
--
-- 原本 fr_backtest_runs 只落了因子参数(params_json) + 池/日期/频率，执行/成本/
-- 分位或信号模式的全部参数只在创建时传给 worker、未落库。导致：
-- 1) 跑完的回测无法完整复现（"重新回测"只能拿默认值补齐缺失字段）；
-- 2) 无法审计"当初这条回测到底用了什么配置"。
--
-- config_json 存创建请求体的完整快照(CreateBacktestIn.model_dump)，让 /rerun
-- 能忠实重跑，也便于前端展示/克隆。旧 run 该列为 NULL，/rerun 时回退到用已有
-- 列重建一个最小配置（其余走默认）。
ALTER TABLE fr_backtest_runs
ADD COLUMN config_json longtext DEFAULT NULL AFTER params_json;
