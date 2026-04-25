"""Baostock 数据探查脚本（Phase 1 验收前置）。

目的：在真正动手写 Phase 2 的财务 / 指数成分 / 行业 adapter 之前，先把 Baostock
的几个关键字段语义摸清楚，避免字段含义不符预期时大规模返工。

探查清单（对应 Phase 1 敲定的三大盲区）：

1. **退市股票的 ``outDate`` 覆盖率**：
   - 随机抽若干代码，断言 ``query_stock_basic`` 能返回有效的 ``outDate``；
   - 如果覆盖不全，说明 Baostock 对某些早期退市缺记录，Phase 2 需要额外数据源补。

2. **HS300 历史成分回溯能力**：
   - 分别拉 2015-06-30 / 2018-06-30 / 2024-06-30 的 ``query_hs300_stocks``；
   - 成分数应 ≈ 300（±5 以内视为正常）。
   - 如果早期年份不够 300，说明 Baostock 的历史成分回溯不到那个时点。

3. **财务数据 ``pubDate`` 语义**：
   - 对 ``sh.600519``（茅台）拿几个季度的 ``query_profit_data``；
   - 打印 ``statDate``（报告期）与 ``pubDate``（公告日）的差值分布；
   - 验证 ``pubDate`` 是公告日而非报告期，这是防未来函数的关键字段。

用法::

    python -m backend.scripts.probe_baostock

输出直接打到 stdout，方便粘贴给协作者或存档。不写入任何数据库，完全只读探查。
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

# 允许 `python backend/scripts/probe_baostock.py` 直接跑时也能 import backend
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.adapters.baostock.client import baostock_session, check_rs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("probe_baostock")


def _rows(rs) -> list[dict]:
    """把 ResultData 迭代成 list[dict]（小数据集专用，别用在全市场查询上）。"""
    out = []
    fields = rs.fields
    while rs.next():
        out.append(dict(zip(fields, rs.get_row_data())))
    return out


def probe_delist_coverage() -> None:
    """探查 1：退市股票 outDate 覆盖率。"""
    import baostock as bs  # noqa: PLC0415

    print("\n=== [Probe 1] 退市股票 outDate 覆盖率 ===")
    rs = bs.query_stock_basic(code="", code_name="")
    check_rs(rs, "query_stock_basic(all)")

    total = 0
    delisted_total = 0
    delisted_with_out_date = 0
    examples: list[dict] = []
    fields = rs.fields
    while rs.next():
        row = dict(zip(fields, rs.get_row_data()))
        total += 1
        if row.get("status") == "0":
            delisted_total += 1
            out_date = (row.get("outDate") or "").strip()
            if out_date:
                delisted_with_out_date += 1
            if len(examples) < 10:
                examples.append(
                    {
                        "code": row.get("code"),
                        "name": row.get("code_name"),
                        "ipo": row.get("ipoDate"),
                        "out": row.get("outDate"),
                    }
                )

    coverage = (
        delisted_with_out_date / delisted_total if delisted_total else 0.0
    )
    print(f"  全市场总数: {total}")
    print(f"  已退市数量: {delisted_total}")
    print(f"  其中有 outDate: {delisted_with_out_date}")
    print(f"  覆盖率: {coverage:.1%}")
    print("  前 10 条退市样例:")
    for ex in examples:
        print(f"    {ex}")


def probe_hs300_history() -> None:
    """探查 2：HS300 历史成分回溯。"""
    import baostock as bs  # noqa: PLC0415

    print("\n=== [Probe 2] HS300 历史成分回溯 ===")
    for probe_date in ("2015-06-30", "2018-06-30", "2024-06-30"):
        rs = bs.query_hs300_stocks(date=probe_date)
        if rs.error_code != "0":
            print(
                f"  {probe_date}: 失败 code={rs.error_code} msg={rs.error_msg}"
            )
            continue
        rows = _rows(rs)
        print(f"  {probe_date}: 成分数={len(rows)}  sample={rows[:3]}")


def probe_announcement_date() -> None:
    """探查 3：财务数据 pubDate 语义。"""
    import baostock as bs  # noqa: PLC0415

    print("\n=== [Probe 3] 财务数据 pubDate 语义（以 sh.600519 茅台为例） ===")
    # 近几年几个季度的利润表
    quarters = [
        (2022, 4),
        (2023, 1),
        (2023, 2),
        (2023, 3),
        (2023, 4),
        (2024, 1),
    ]
    for year, q in quarters:
        rs = bs.query_profit_data(code="sh.600519", year=year, quarter=q)
        if rs.error_code != "0":
            print(f"  {year}Q{q}: 失败 {rs.error_code} {rs.error_msg}")
            continue
        rows = _rows(rs)
        if not rows:
            print(f"  {year}Q{q}: 无数据")
            continue
        r = rows[0]
        stat = r.get("statDate")
        pub = r.get("pubDate")
        # 算差值
        delta_days = "?"
        try:
            if stat and pub:
                d_stat = datetime.strptime(stat, "%Y-%m-%d").date()
                d_pub = datetime.strptime(pub, "%Y-%m-%d").date()
                delta_days = (d_pub - d_stat).days
        except ValueError:
            pass
        print(
            f"  {year}Q{q}: statDate={stat}  pubDate={pub}  "
            f"delta_days={delta_days}"
        )


def probe_industry_semantics() -> None:
    """探查 4：行业分类接口语义（query_stock_industry）。

    需要确认的关键问题：
    - 该接口返回的是**当前快照**还是带变更时间的历史？
    - ``updateDate`` 字段表示什么？是**最近一次行业归属变更日**还是**接口数据刷新日**？
    - 行业分级粒度（一级 / 二级 / 三级？）。

    决定 ``fr_industry_history`` 的 ``effective_date`` / ``end_date`` 如何灌：
    - 如果接口给的是变更时间 → 直接用作 effective_date；
    - 如果只是刷新时间 → 我们只能从某个时点开始，按月做快照检测变化。
    """
    import baostock as bs  # noqa: PLC0415

    print("\n=== [Probe 4] 行业分类接口语义 ===")

    rs = bs.query_stock_industry()
    if rs.error_code != "0":
        print(f"  全市场查询失败: {rs.error_code} {rs.error_msg}")
        return

    print(f"  fields: {rs.fields}")

    rows: list[dict] = []
    while rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))

    print(f"  全市场行业行数: {len(rows)}")
    if rows:
        print(f"  前 3 条样例: {rows[:3]}")
        update_dates = [r.get("updateDate") for r in rows if r.get("updateDate")]
        if update_dates:
            uniq = sorted(set(update_dates))
            print(
                f"  updateDate 唯一值数: {len(uniq)}, "
                f"min={uniq[0]}, max={uniq[-1]}, sample(头5)={uniq[:5]}"
            )

    # 单股查 — 茅台，看是否能拿到历史多行
    print("\n  -- sh.600519（茅台）单股查询 --")
    rs = bs.query_stock_industry(code="sh.600519")
    if rs.error_code == "0":
        rows = []
        while rs.next():
            rows.append(dict(zip(rs.fields, rs.get_row_data())))
        print(f"  单股返回行数: {len(rows)}, 行: {rows}")


def probe_index_constituent_history() -> None:
    """探查 5：指数成分历史回溯节奏。

    回溯 ZZ500 在 2015 / 2018 / 2024 三个时点的成分数；
    再额外测非月末日期的查询行为，决定快照策略（按月 vs 按交易日）。
    """
    import baostock as bs  # noqa: PLC0415

    print("\n=== [Probe 5] 指数成分历史 ===")

    print("  -- ZZ500 回溯 --")
    for d in ("2015-12-31", "2018-12-31", "2024-12-31"):
        rs = bs.query_zz500_stocks(date=d)
        if rs.error_code != "0":
            print(f"  {d}: 失败 {rs.error_code} {rs.error_msg}")
            continue
        rows = []
        while rs.next():
            rows.append(dict(zip(rs.fields, rs.get_row_data())))
        print(f"  ZZ500 {d}: 成分数={len(rows)} sample={rows[:2]}")

    print("\n  -- 非月末日期能否查询（HS300） --")
    for d in ("2024-06-15", "2024-06-28", "2024-06-30", "2024-07-01"):
        rs = bs.query_hs300_stocks(date=d)
        if rs.error_code != "0":
            print(f"  {d}: 失败 {rs.error_code} {rs.error_msg}")
            continue
        n = 0
        while rs.next():
            n += 1
        print(f"  HS300 {d}: 成分数={n}")


def main() -> None:
    print(f"Baostock probe @ {date.today()}")
    with baostock_session():
        probe_delist_coverage()
        probe_hs300_history()
        probe_announcement_date()
        probe_industry_semantics()
        probe_index_constituent_history()
    print("\nprobe done.")


if __name__ == "__main__":
    main()
