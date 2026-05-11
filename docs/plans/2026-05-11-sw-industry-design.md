# 申万行业分类接入设计

## 背景

`fr_industry_current.industry_l1` 存的是证监会行业分类（来自 Baostock），不是申万行业。
因子中性化、行业对比等场景需要申万分类（一级 31 个、二级 131 个、三级 336 个）。

## 方案

在 `fr_industry_current` 表新增 `sw_l1` / `sw_l2` / `sw_l3` 三个字段，数据源为 Akshare 申万行业接口。

## Schema 变更

```sql
ALTER TABLE fr_industry_current
  ADD COLUMN sw_l1 varchar(32) NOT NULL DEFAULT '' AFTER industry_classification,
  ADD COLUMN sw_l2 varchar(32) NOT NULL DEFAULT '' AFTER sw_l1,
  ADD COLUMN sw_l3 varchar(64) NOT NULL DEFAULT '' AFTER sw_l2,
  ADD KEY idx_sw_l1 (sw_l1);
```

## 数据采集策略

1. `sw_index_second_info()` → 建 L2_name→L1_name 映射
2. `sw_index_third_info()` → 建 L3_name→L2_name 映射
3. 遍历 336 个 L3 行业 `index_component_sw(code)` → stock→L3 映射，通过层级反推 L1/L2
4. 兜底：遍历 31 个 L1 行业，对 L3 未覆盖的股票填充 sw_l1

每次调用间隔 0.5s 防限流，总耗时 ~8-10 分钟，作为后台任务运行。

## 代码结构

- `backend/adapters/akshare_industry.py` — 申万行业适配器（fetch + upsert）
- `backend/scripts/migrations/007_add_sw_industry_fields.sql` — 迁移脚本
- `backend/api/routers/admin.py` — 新增 `POST /api/admin/industry:sync_sw` 端点

## 写入逻辑

- 已有 symbol：UPDATE sw_l1/sw_l2/sw_l3
- 新 symbol（Baostock 未覆盖但申万有）：INSERT 完整行
