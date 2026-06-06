# 图形相似度检索 设计文档

日期：2026-06-06
分支：`feature/pattern-search`

## 一、需求与第一性原理

用户提出两个需求：

1. **截图找相似股票**：用户收集了一些喜欢的股票走势截图（外部任意来源），希望 AI 提取其图形特征（可给提示词辅助），并自动在市场中找到走势类似的股票。
2. **个股历史自相似**：给定某只股票，找到它历史上出现过的类似图形。

**核心判断**：两个需求本质是**同一个引擎**——「对归一化后的价格曲线做形状相似度检索」。
- 需求2是纯数值问题（查询对象是真实价格窗口，无需 AI）。
- 需求1只是多了一个「图像 → 形状向量」的前端入口，之后走的检索逻辑与需求2完全一致。

因此先建一个隔离的形状检索引擎，再接两种查询入口。需求2先落地（低风险、可用真实数据验证），需求1叠加其上。

## 二、关键决策（经讨论确认）

| 维度 | 结论 |
|---|---|
| 相似定义 | 整体走势形状（非固定技术形态库、非含成交量）→ 归一化曲线匹配 |
| 需求2范围 | 同一只股票，自身历史滑窗 |
| 需求1范围 | 外部任意截图 → 视觉LLM提取近似曲线 + 提示词纠偏 → **股票池 × 最近窗口** |
| 时间尺度 | 多尺度自动扫（30/60/90/120 交易日），取每只股最佳尺度 |
| 算法 | **相关系数粗筛 + DTW 精排**（廉价过滤 + 精确重排） |
| 归一化 | z-score（去绝对价位与涨幅，只留形状）+ 重采样到固定 128 点 |

### 为何不先训练模型（决策记录）
- 对**纯形状检索**：DTW/相关系数本质就是形状相似度的定义，无 ground truth 可超越；自训模型缺标签、易与 DTW 抓的不变性重合，精度提升边际。
- 对**预测任务**（像的图→未来涨跌）模型有上限优势，但金融时序信噪比低、易过拟合、不可解释、工程周级。
- 本场景核心价值是**可解释 + 立即可验证**，DTW 是性价比与精度都更优的起点。
- 引擎隔离为 `shape_search(query, candidates)` 单一接口，未来可无痛替换为学习型 embedding（TS2Vec/对比学习），上层 API 与前端不变。

## 三、架构

```
查询入口                     引擎(纯数值)                数据
需求2: 框选窗口  ─┐
                 ├─▶ shape_search(query_curve, candidates, scales, top_k)
需求1: 截图       │     ① z-score + 重采样到128         ─▶ DataService
  →视觉LLM提取   ─┘     ② 相关系数粗筛 Top-K               (ClickHouse 行情)
                       ③ DTW 精排
                       → [{symbol, score, scale, 日期段, 缩略曲线}]
```

## 四、后端组件

### 1. `backend/services/pattern_search.py`（引擎，无 LLM、无 IO）
- `normalize_curve(prices: np.ndarray, target_len=128) -> np.ndarray`：z-score → `np.interp` 重采样到定长。
- `correlation_prefilter(query, candidates_matrix) -> np.ndarray`：numpy 矩阵化批量相关系数。
- `dtw_distance(query, cand, band) -> float`：numba + Sakoe-Chiba 带约束（项目已用 numba）。距离转相似度分。
- `shape_search(query_curve, candidates, scales, top_k, prefilter_k=50) -> list[Match]`：多尺度取窗 → 粗筛 Top-K → DTW 精排 → 排序返回。
- `Match` 结构：`{label/symbol, score(0-1), scale, start_date, end_date, curve(下采样缩略)}`。

### 2. `by_stock` 服务（需求2，无 LLM）
- 输入：`symbol`、查询窗口 `window?`（缺省取最近窗口）、`scales?`、`step≈5`、`top_k?`。
- 取该股全历史 close（`DataService.load_bars`），按各尺度滑窗生成候选，排除与查询窗重叠的窗口，跑 `shape_search`。
- 返回带日期段 + 缩略曲线的匹配列表。

### 3. `by_image` 服务（需求1）
- 输入：`image`(dataURI)、`hint?`、`pool_id`、`scales?`、`top_k?`。
- 复用 `factor_assistant._call_openai_compatible` 调视觉模型，prompt 要求输出**归一化折线点 JSON**（截图→曲线）+ 趋势描述，提示词用于纠偏。
- 折线 → `query_curve`（重采样到128）。
- `resolve_pool(pool_id)` → 取池内每只股每个尺度的**最近窗口** → 跑 `shape_search`。
- 返回 `query_curve`（供前端回显核对）+ 匹配列表。

## 五、API（沿用 `{code:0, data}` 契约）

- `POST /api/pattern_search/by_stock` — `{symbol, window?, scales?, top_k?}`
- `POST /api/pattern_search/by_image` — `{image(dataURI), hint?, pool_id, scales?, top_k?}`
- 两者响应均含 `query_curve` + `matches[]`。
- 新建 `backend/api/routers/pattern_search.py`，在 `backend/api/main.py` 注册。

## 六、前端

- **需求2 入口**：复用 `CandlestickChart` 已有框选（现用于 Volume Profile），框选区域加「找相似」按钮，拿选中日期段调 `by_stock`。
- **需求1 入口**：新页 `frontend/src/pages/pattern/PatternSearch.vue`——上传截图 + 提示词 + 选股票池 → 运行；顶部回显提取出的查询曲线供核对。
- **结果组件** `MatchResultList.vue`：每条 = mini ECharts 缩略图 + 相似度分 + 代码/名称 + 日期段 + 「在 K 线中打开」跳转。
- `frontend/src/api/patternSearch.ts`（TanStack Query mutation）+ 路由/导航入口。

## 七、测试

- 引擎单测：相同曲线→1.0；相位平移→DTW 高分而相关系数低；倒置→低分；构造植入已知相似窗的候选集验证排序。
- `by_image` 按项目现有风格 monkeypatch 掉 LLM（不真调），测「折线→曲线→检索」链路。
- 需求2 用真实数据在 1-2 只已知股票上人工核对。

## 八、性能

- 需求1 仅比「池 × 最近窗口」；相关系数矩阵化 + DTW 仅跑 Top-K(~50)，秒级。
- 需求2 滑窗用 step 控制候选数量。

## 九、落地顺序

- **Phase 1**：引擎 + 需求2（无 LLM，真实数据完全可验证）。
- **Phase 2**：需求1 截图提取叠加。
