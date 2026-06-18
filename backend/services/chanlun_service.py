"""缠论分析服务（纯 Python 实现，零第三方依赖）。

算法流程：原始K线 → 包含处理 → 分型识别 → 笔连接 → 中枢识别 → 买卖点判定。

返回的时间字符串与前端 K 线 x 轴类目直接对齐。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from backend.storage.data_service import DataService

log = logging.getLogger(__name__)


# ========== 输出数据结构 ==========

@dataclass
class FxResult:
    dt: str
    mark: str       # "top" | "bottom"
    price: float
    high: float
    low: float


@dataclass
class BiResult:
    sdt: str
    edt: str
    direction: str  # "up" | "down"
    high: float
    low: float


@dataclass
class ZsResult:
    sdt: str
    edt: str
    zg: float
    zd: float
    gg: float
    dd: float


@dataclass
class BspResult:
    dt: str
    bsp_type: str   # "buy1" | "buy2" | "buy3" | "sell1" | "sell2" | "sell3"
    price: float


@dataclass
class ChanlunResult:
    fx_list: list[FxResult] = field(default_factory=list)
    bi_list: list[BiResult] = field(default_factory=list)
    zs_list: list[ZsResult] = field(default_factory=list)
    zs_up_list: list[ZsResult] = field(default_factory=list)  # 中枢扩张合并后的高级别中枢
    bsp_list: list[BspResult] = field(default_factory=list)


# ========== 内部数据结构 ==========

@dataclass
class _Bar:
    """合并后的K线"""
    dt: datetime
    open: float
    close: float
    high: float
    low: float
    vol: float
    index: int      # 在原始序列中的位置


@dataclass
class _FX:
    """分型"""
    dt: datetime
    mark: str       # "top" | "bottom"
    high: float
    low: float
    index: int      # 所在合并K线的 index
    price: float    # 顶分型取 high，底分型取 low


@dataclass
class _BI:
    """笔"""
    fx_a: _FX       # 起始分型
    fx_b: _FX       # 结束分型
    direction: str  # "up"（底→顶）| "down"（顶→底）
    high: float
    low: float


# ========== 核心算法 ==========

def _remove_include(bars: list[_Bar]) -> list[_Bar]:
    """K线包含处理。

    规则：相邻两根K线若存在包含关系（一根的高低完全覆盖另一根），
    则合并为一根。合并方向取决于趋势：
    - 上升趋势（前一根high上升）：取 max(high), max(low)
    - 下降趋势（前一根high下降）：取 min(high), min(low)
    """
    if len(bars) < 2:
        return list(bars)

    result: list[_Bar] = [bars[0]]

    for i in range(1, len(bars)):
        cur = bars[i]
        prev = result[-1]

        has_include = (prev.high >= cur.high and prev.low <= cur.low) or \
                      (cur.high >= prev.high and cur.low <= prev.low)

        if not has_include:
            result.append(cur)
            continue

        # 判断趋势方向：看 result 中前两根的 high 关系
        if len(result) >= 2:
            going_up = result[-1].high >= result[-2].high
        else:
            going_up = cur.high >= prev.high

        if going_up:
            merged = _Bar(
                dt=cur.dt if cur.high >= prev.high else prev.dt,
                open=prev.open,
                close=cur.close,
                high=max(prev.high, cur.high),
                low=max(prev.low, cur.low),
                vol=prev.vol + cur.vol,
                index=cur.index,
            )
        else:
            merged = _Bar(
                dt=cur.dt if cur.low <= prev.low else prev.dt,
                open=prev.open,
                close=cur.close,
                high=min(prev.high, cur.high),
                low=min(prev.low, cur.low),
                vol=prev.vol + cur.vol,
                index=cur.index,
            )
        result[-1] = merged

    return result


def _find_fx(bars: list[_Bar]) -> list[_FX]:
    """从合并后的K线中识别分型。

    顶分型：中间K线的 high 是三根中最高，且 low 也不是最低
    底分型：中间K线的 low 是三根中最低，且 high 也不是最高
    """
    fxs: list[_FX] = []
    for i in range(1, len(bars) - 1):
        p, c, n = bars[i - 1], bars[i], bars[i + 1]

        if c.high > p.high and c.high > n.high and c.low > p.low and c.low > n.low:
            fxs.append(_FX(
                dt=c.dt, mark="top", high=c.high, low=c.low,
                index=c.index, price=c.high,
            ))
        elif c.low < p.low and c.low < n.low and c.high < p.high and c.high < n.high:
            fxs.append(_FX(
                dt=c.dt, mark="bottom", high=c.high, low=c.low,
                index=c.index, price=c.low,
            ))
    return fxs


_MIN_BI_BARS = 4  # 一笔至少跨越的合并K线数


def _connect_bi(fxs: list[_FX]) -> list[_BI]:
    """从分型列表中连接笔。

    规则：
    1. 顶底交替：笔必须从顶到底或从底到顶
    2. 同类分型取极值：连续顶分型取最高者，连续底分型取最低者
    3. 两个分型之间至少有 _MIN_BI_BARS 根合并K线（含端点）
    """
    if len(fxs) < 2:
        return []

    # 第一步：去除不合规的分型，保证顶底交替
    filtered: list[_FX] = [fxs[0]]
    for fx in fxs[1:]:
        last = filtered[-1]
        if fx.mark == last.mark:
            # 同类型：顶取高的，底取低的
            if fx.mark == "top" and fx.price > last.price:
                filtered[-1] = fx
            elif fx.mark == "bottom" and fx.price < last.price:
                filtered[-1] = fx
        else:
            # 不同类型：检查间距
            if abs(fx.index - last.index) >= _MIN_BI_BARS:
                filtered.append(fx)
            else:
                # 间距不够，跳过这个分型（但如果它更极端则替换前一个同类型的）
                pass

    # 第二步：再次确保顶底交替 + 同类去重
    clean: list[_FX] = []
    for fx in filtered:
        if not clean:
            clean.append(fx)
            continue
        if fx.mark == clean[-1].mark:
            if fx.mark == "top" and fx.price > clean[-1].price:
                clean[-1] = fx
            elif fx.mark == "bottom" and fx.price < clean[-1].price:
                clean[-1] = fx
        else:
            clean.append(fx)

    # 第三步：从相邻分型对构建笔
    bis: list[_BI] = []
    for i in range(len(clean) - 1):
        a, b = clean[i], clean[i + 1]
        if a.mark == "bottom" and b.mark == "top":
            bis.append(_BI(fx_a=a, fx_b=b, direction="up",
                           high=b.high, low=a.low))
        elif a.mark == "top" and b.mark == "bottom":
            bis.append(_BI(fx_a=a, fx_b=b, direction="down",
                           high=a.high, low=b.low))
    return bis


def _find_zs(bis: list[_BI], freq: str) -> list[ZsResult]:
    """从笔列表中识别中枢。

    中枢 = 至少连续3笔的价格重叠区间。
    ZG = 区间内各笔 low 的最大值（进入段的高点取 min → 简化为重叠区上沿）
    ZD = 区间内各笔 high 的最小值（进入段的低点取 max → 简化为重叠区下沿）

    标准定义：取第2、3笔（中间笔）的重叠区间作为中枢范围，
    然后向后扩展——后续笔与中枢区间有重叠则纳入。
    """
    if len(bis) < 3:
        return []

    results: list[ZsResult] = []
    i = 0
    while i + 2 < len(bis):
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]
        # 中枢区间 = 第2笔和第3笔的重叠
        zg = min(b2.high, b3.high)
        zd = max(b2.low, b3.low)
        if zg <= zd:
            i += 1
            continue

        # 扩展：后续笔与 [zd, zg] 有重叠则纳入
        end_idx = i + 2
        for j in range(i + 3, len(bis)):
            if bis[j].low <= zg and bis[j].high >= zd:
                end_idx = j
            else:
                break

        included = bis[i:end_idx + 1]
        results.append(ZsResult(
            sdt=_fmt_dt(included[0].fx_a.dt, freq),
            edt=_fmt_dt(included[-1].fx_b.dt, freq),
            zg=round(zg, 4),
            zd=round(zd, 4),
            gg=round(max(b.high for b in included), 4),
            dd=round(min(b.low for b in included), 4),
        ))
        i = end_idx + 1

    return results


def _merge_zs(zs_list: list[ZsResult]) -> list[ZsResult]:
    """中枢扩张：相邻中枢的 [ZD, ZG] 有价格重叠时，合并为高级别中枢。

    合并后的中枢：
    - 时间范围取并集（最早 sdt ~ 最晚 edt）
    - ZG/ZD 取交集（这是两个中枢真正重叠的核心区间）
    - GG/DD 取全局极值
    连续多个中枢两两重叠时会链式合并。
    """
    if len(zs_list) < 2:
        return []

    merged: list[ZsResult] = []
    i = 0
    while i < len(zs_list) - 1:
        a = zs_list[i]
        # 检查 a 是否能与后续中枢链式合并
        group = [a]
        j = i + 1
        while j < len(zs_list):
            b = zs_list[j]
            prev = group[-1]
            overlap_zg = min(prev.zg, b.zg)
            overlap_zd = max(prev.zd, b.zd)
            if overlap_zg > overlap_zd:
                group.append(b)
                j += 1
            else:
                break

        if len(group) >= 2:
            merged.append(ZsResult(
                sdt=group[0].sdt,
                edt=group[-1].edt,
                zg=round(min(z.zg for z in group), 4),
                zd=round(max(z.zd for z in group), 4),
                gg=round(max(z.gg for z in group), 4),
                dd=round(min(z.dd for z in group), 4),
            ))
        i = j

    return merged


def _find_bsp(bis: list[_BI], zs_list: list[ZsResult], freq: str) -> list[BspResult]:
    """基于笔和中枢识别买卖点。

    一买(buy1)：中枢下方最后一笔向下笔的终点（低于 zd）
    二买(buy2)：一买之后第一笔向下笔不创新低
    三买(buy3)：中枢之后第一笔向下笔低点在中枢上方（> zg）

    卖点对称。简化实现，不含 MACD 背驰判断。
    """
    results: list[BspResult] = []
    if not zs_list or len(bis) < 5:
        return results

    for zs in zs_list:
        after_bis = [b for b in bis if _fmt_dt(b.fx_a.dt, freq) >= zs.edt]

        # 三买：中枢后向下笔低点 > zg
        for bi in after_bis:
            if bi.direction == "down" and bi.low > zs.zg:
                results.append(BspResult(
                    dt=_fmt_dt(bi.fx_b.dt, freq), bsp_type="buy3",
                    price=round(bi.low, 4),
                ))
                break

        # 三卖：中枢后向上笔高点 < zd
        for bi in after_bis:
            if bi.direction == "up" and bi.high < zs.zd:
                results.append(BspResult(
                    dt=_fmt_dt(bi.fx_b.dt, freq), bsp_type="sell3",
                    price=round(bi.high, 4),
                ))
                break

        # 一买：中枢内向下笔且低点 < zd
        down_below = [
            b for b in bis
            if b.direction == "down" and b.low < zs.zd
            and _fmt_dt(b.fx_b.dt, freq) <= zs.edt
        ]
        if down_below:
            last_down = down_below[-1]
            results.append(BspResult(
                dt=_fmt_dt(last_down.fx_b.dt, freq), bsp_type="buy1",
                price=round(last_down.low, 4),
            ))
            # 二买
            later = [
                b for b in bis
                if b.direction == "down"
                and _fmt_dt(b.fx_a.dt, freq) > _fmt_dt(last_down.fx_b.dt, freq)
            ]
            if later and later[0].low > last_down.low:
                results.append(BspResult(
                    dt=_fmt_dt(later[0].fx_b.dt, freq), bsp_type="buy2",
                    price=round(later[0].low, 4),
                ))

        # 一卖：中枢内向上笔且高点 > zg
        up_above = [
            b for b in bis
            if b.direction == "up" and b.high > zs.zg
            and _fmt_dt(b.fx_b.dt, freq) <= zs.edt
        ]
        if up_above:
            last_up = up_above[-1]
            results.append(BspResult(
                dt=_fmt_dt(last_up.fx_b.dt, freq), bsp_type="sell1",
                price=round(last_up.high, 4),
            ))
            later = [
                b for b in bis
                if b.direction == "up"
                and _fmt_dt(b.fx_a.dt, freq) > _fmt_dt(last_up.fx_b.dt, freq)
            ]
            if later and later[0].high < last_up.high:
                results.append(BspResult(
                    dt=_fmt_dt(later[0].fx_b.dt, freq), bsp_type="sell2",
                    price=round(later[0].high, 4),
                ))

    seen = set()
    deduped = []
    for bsp in results:
        key = (bsp.dt, bsp.bsp_type)
        if key not in seen:
            seen.add(key)
            deduped.append(bsp)
    return deduped


# ========== 工具函数 ==========

def _fmt_dt(dt: datetime, freq: str) -> str:
    if freq == "1m":
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


# ========== 主入口 ==========

def analyze(
    symbol: str,
    start: date,
    end: date,
    freq: str = "1d",
    adjust: str = "qfq",
) -> ChanlunResult:
    svc = DataService()
    bars_df = _load_bars(svc, symbol, start, end, freq, adjust)
    if bars_df.empty or len(bars_df) < 10:
        return ChanlunResult()

    # 构建内部K线
    raw_bars: list[_Bar] = []
    for i, row in enumerate(bars_df.itertuples()):
        dt_val = row.dt
        if not isinstance(dt_val, datetime):
            dt_val = datetime.combine(dt_val, datetime.min.time())
        raw_bars.append(_Bar(
            dt=dt_val,
            open=float(row.open),
            close=float(row.close),
            high=float(row.high),
            low=float(row.low),
            vol=float(row.volume) if hasattr(row, "volume") else 0.0,
            index=i,
        ))

    # 算法流水线
    merged = _remove_include(raw_bars)
    fxs = _find_fx(merged)
    bis = _connect_bi(fxs)
    zs_list = _find_zs(bis, freq)
    zs_up_list = _merge_zs(zs_list)
    bsp_list = _find_bsp(bis, zs_list, freq)

    # 转输出格式
    fx_out = [
        FxResult(dt=_fmt_dt(fx.dt, freq), mark=fx.mark,
                 price=round(fx.price, 4), high=round(fx.high, 4), low=round(fx.low, 4))
        for fx in fxs
    ]
    bi_out = [
        BiResult(sdt=_fmt_dt(bi.fx_a.dt, freq), edt=_fmt_dt(bi.fx_b.dt, freq),
                 direction=bi.direction, high=round(bi.high, 4), low=round(bi.low, 4))
        for bi in bis
    ]

    return ChanlunResult(
        fx_list=fx_out,
        bi_list=bi_out,
        zs_list=zs_list,
        zs_up_list=zs_up_list,
        bsp_list=bsp_list,
    )


def _load_bars(
    svc: DataService,
    symbol: str,
    start: date,
    end: date,
    freq: str,
    adjust: str,
) -> pd.DataFrame:
    if freq == "1d":
        bars = svc.load_bars([symbol], start, end, freq="1d", adjust=adjust)
        key_map = {k.strip().upper(): k for k in bars}
        normalized = symbol.strip().upper()
        if normalized not in key_map:
            return pd.DataFrame()
        df = bars[key_map[normalized]].reset_index()
        df = df.rename(columns={"trade_date": "dt"})
        return df
    else:
        from backend.storage.clickhouse_client import ch_client
        sid_map = svc.resolver.resolve_many([symbol])
        if not sid_map:
            return pd.DataFrame()
        sid = next(iter(sid_map.values()))
        with ch_client() as ch:
            rows = ch.execute(
                """
                SELECT trade_date, minute_slot, open, high, low, close, volume, amount_k
                FROM quant_data.stock_bar_1m FINAL
                WHERE symbol_id = %(sid)s
                  AND trade_date BETWEEN %(s)s AND %(e)s
                ORDER BY trade_date, minute_slot
                """,
                {"sid": int(sid), "s": start, "e": end},
            )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows,
            columns=["trade_date", "minute_slot", "open", "high", "low", "close", "volume", "amount_k"],
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype("float64")
        df["dt"] = df["trade_date"] + pd.to_timedelta(df["minute_slot"].astype(int), unit="m")
        return df


def to_dict(result: ChanlunResult) -> dict[str, Any]:
    return {
        "fx_list": [
            {"dt": f.dt, "mark": f.mark, "price": f.price, "high": f.high, "low": f.low}
            for f in result.fx_list
        ],
        "bi_list": [
            {"sdt": b.sdt, "edt": b.edt, "direction": b.direction, "high": b.high, "low": b.low}
            for b in result.bi_list
        ],
        "zs_list": [
            {"sdt": z.sdt, "edt": z.edt, "zg": z.zg, "zd": z.zd, "gg": z.gg, "dd": z.dd}
            for z in result.zs_list
        ],
        "zs_up_list": [
            {"sdt": z.sdt, "edt": z.edt, "zg": z.zg, "zd": z.zd, "gg": z.gg, "dd": z.dd}
            for z in result.zs_up_list
        ],
        "bsp_list": [
            {"dt": p.dt, "bsp_type": p.bsp_type, "price": p.price}
            for p in result.bsp_list
        ],
    }
