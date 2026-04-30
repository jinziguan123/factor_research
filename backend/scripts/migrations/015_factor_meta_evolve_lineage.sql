-- 015: fr_factor_meta 加 evolve / lineage / SOTA 5 列（L2.D 因子进化）
--
-- 借鉴 RD-Agent Synthesis Agent + SOTA 集概念：让因子之间形成血缘链，用户能
-- 看到"v1 → v2 → v3"演化、并把同链路最优标记成 SOTA 供后续进化默认起点。
--
-- 字段语义：
-- - parent_factor_id: 直接父代（factor_id）；NULL = 根（手写 / translate / negate）
-- - parent_eval_run_id: 进化时基于哪条评估的反馈；可空，方便回溯"哪份诊断启发了这次改动"
-- - generation: 第几代；根=1，每次 evolve 父代 generation+1
-- - is_sota: 同链路下用户标记的"当前最优"（应用层保证 same-root 唯一）
-- - root_factor_id: 同链最早祖先；用于 SOTA 唯一性约束 + 族谱查询
--   * 根自己: NULL（约定，避免循环引用，应用层查询时 fallback 到 factor_id）
--
-- 索引说明：
-- - idx_root：lineage 查询常按 root 反查兄弟节点 + SOTA 检查
-- - idx_parent：descendants 查询按 parent_factor_id WHERE
ALTER TABLE fr_factor_meta
    ADD COLUMN parent_factor_id   varchar(64) DEFAULT NULL,
    ADD COLUMN parent_eval_run_id varchar(64) DEFAULT NULL,
    ADD COLUMN generation         tinyint     NOT NULL DEFAULT 1,
    ADD COLUMN is_sota            tinyint     NOT NULL DEFAULT 0,
    ADD COLUMN root_factor_id     varchar(64) DEFAULT NULL,
    ADD INDEX idx_root (root_factor_id),
    ADD INDEX idx_parent (parent_factor_id);
