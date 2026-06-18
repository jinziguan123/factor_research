"""缠论分析服务。

基于 czsc 库，对单只股票的 OHLCV 数据做缠论结构识别：
分型 → 笔 → 中枢 → 买卖点。

返回的所有时间均为 "YYYY-MM-DD" 字符串（日线）或 "YYYY-MM-DD HH:MM"（分钟线），
与前端 K 线 x 轴类目直接对齐。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from backend.storage.data_service import DataService

log = logging.getLogger(__name__)


# ---------- 输出数据结构 ----------

@dataclass
class FxResult:
    """分型"""
    dt: str
    mark: str  # "top" | "bottom"
    price: float
    high: float
    low: float


@dataclass
class BiResult:
    """笔"""
    sdt: str
    edt: str
    direction: str  # "up" | "down"
    high: float
    low: float


@dataclass
class ZsResult:
    """中枢"""
    sdt: str
    edt: str
    zg: float   # 中枢上沿
    zd: float   # 中枢下沿
    gg: float   # 中枢区间最高
    dd: float   # 中枢区间最低


@dataclass
class BspResult:
    """买卖点"""
    dt: str
    bsp_type: str  # "buy1" | "buy2" | "buy3" | "sell1" | "sell2" | "sell3"
    price: float


@dataclass
class ChanlunResult:
    fx_list: list[FxResult] = field(default_factory=list)
    bi_list: list[BiResult] = field(default_factory=list)
    zs_list: list[ZsResult] = field(default_factory=list)
    bsp_list: list[BspResult] = field(default_factory=list)


# ---------- 分析逻辑 ----------

def _fmt_dt(dt: datetime, freq: str) -> str:
    if freq == "1m":
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


def _build_zs_list(bi_list: list, freq: str) -> list[ZsResult]:
    """从笔列表中识别中枢。

    中枢定义：至少连续 3 笔存在价格重叠区间。
    具体做法：滑动窗口，每 3 笔检查是否构成中枢（ZG > ZD），
    然后尝试向右扩展（后续笔仍与中枢区间重叠则纳入）。
    """
    from czsc import ZS as CzscZS

    results: list[ZsResult] = []
    n = len(bi_list)
    used = set()
    i = 0
    while i + 2 < n:
        if i in used:
            i += 1
            continue
        try:
            zs = CzscZS(bis=bi_list[i:i + 3])
        except Exception:
            i += 1
            continue
        if not zs.is_valid:
            i += 1
            continue
        # 尝试扩展
        end_idx = i + 2
        zg, zd = zs.zg, zs.zd
        for j in range(i + 3, n):
            bi = bi_list[j]
            if bi.low <= zg and bi.high >= zd:
                end_idx = j
            else:
                break
        for k in range(i, end_idx + 1):
            used.add(k)
        results.append(ZsResult(
            sdt=_fmt_dt(bi_list[i].sdt, freq),
            edt=_fmt_dt(bi_list[end_idx].edt, freq),
            zg=round(zg, 4),
            zd=round(zd, 4),
            gg=round(max(bi.high for bi in bi_list[i:end_idx + 1]), 4),
            dd=round(min(bi.low for bi in bi_list[i:end_idx + 1]), 4),
        ))
        i = end_idx + 1
    return results


def _find_bsp(bi_list: list, zs_list_raw: list[ZsResult], freq: str) -> list[BspResult]:
    """基于笔和中枢识别买卖点。

    三类买点：
    - 一买(buy1)：向下离开中枢的笔出现底分型，且力度背驰
    - 二买(buy2)：一买后向上回拉再向下的笔不创新低
    - 三买(buy3)：向上离开中枢后回踩不进入中枢

    三类卖点对称。

    简化实现：用中枢边界和笔的位置关系判断，不做 MACD 背驰（那需要额外指标）。
    """
    results: list[BspResult] = []
    if not zs_list_raw or len(bi_list) < 5:
        return results

    for zs in zs_list_raw:
        zs_zg = zs.zg
        zs_zd = zs.zd
        zs_sdt = zs.sdt
        zs_edt = zs.edt

        # 找中枢之后的笔
        after_bis = [b for b in bi_list if _fmt_dt(b.sdt, freq) >= zs_edt]
        # 找中枢之前的笔
        before_bis = [b for b in bi_list if _fmt_dt(b.edt, freq) <= zs_sdt]

        # --- 三买 buy3：中枢之后，向下笔的低点不进入中枢（低点 > zg） ---
        for bi in after_bis:
            if str(bi.direction) == "向下" and bi.low > zs_zg:
                results.append(BspResult(
                    dt=_fmt_dt(bi.edt, freq),
                    bsp_type="buy3",
                    price=round(bi.low, 4),
                ))
                break

        # --- 三卖 sell3：中枢之后，向上笔的高点不进入中枢（高点 < zd） ---
        for bi in after_bis:
            if str(bi.direction) == "向上" and bi.high < zs_zd:
                results.append(BspResult(
                    dt=_fmt_dt(bi.edt, freq),
                    bsp_type="sell3",
                    price=round(bi.high, 4),
                ))
                break

        # --- 一买 buy1：向下进入中枢下方的笔（低点 < zd），取最后一笔 ---
        down_below = [
            b for b in bi_list
            if str(b.direction) == "向下" and b.low < zs_zd
            and _fmt_dt(b.edt, freq) <= zs_edt
        ]
        if down_below:
            last_down = down_below[-1]
            results.append(BspResult(
                dt=_fmt_dt(last_down.edt, freq),
                bsp_type="buy1",
                price=round(last_down.low, 4),
            ))
            # --- 二买 buy2：一买之后的向下笔不创新低 ---
            after_buy1 = [
                b for b in bi_list
                if str(b.direction) == "向下"
                and _fmt_dt(b.sdt, freq) > _fmt_dt(last_down.edt, freq)
            ]
            if after_buy1 and after_buy1[0].low > last_down.low:
                results.append(BspResult(
                    dt=_fmt_dt(after_buy1[0].edt, freq),
                    bsp_type="buy2",
                    price=round(after_buy1[0].low, 4),
                ))

        # --- 一卖 sell1：向上进入中枢上方的笔（高点 > zg），取最后一笔 ---
        up_above = [
            b for b in bi_list
            if str(b.direction) == "向上" and b.high > zs_zg
            and _fmt_dt(b.edt, freq) <= zs_edt
        ]
        if up_above:
            last_up = up_above[-1]
            results.append(BspResult(
                dt=_fmt_dt(last_up.edt, freq),
                bsp_type="sell1",
                price=round(last_up.high, 4),
            ))
            after_sell1 = [
                b for b in bi_list
                if str(b.direction) == "向上"
                and _fmt_dt(b.sdt, freq) > _fmt_dt(last_up.edt, freq)
            ]
            if after_sell1 and after_sell1[0].high < last_up.high:
                results.append(BspResult(
                    dt=_fmt_dt(after_sell1[0].edt, freq),
                    bsp_type="sell2",
                    price=round(after_sell1[0].high, 4),
                ))

    # 去重（同一时间同一类型只保留一个）
    seen = set()
    deduped = []
    for bsp in results:
        key = (bsp.dt, bsp.bsp_type)
        if key not in seen:
            seen.add(key)
            deduped.append(bsp)
    return deduped


def analyze(
    symbol: str,
    start: date,
    end: date,
    freq: str = "1d",
    adjust: str = "qfq",
) -> ChanlunResult:
    """对指定股票做缠论分析。"""
    from czsc import CZSC, Freq, RawBar

    svc = DataService()
    bars_df = _load_bars(svc, symbol, start, end, freq, adjust)
    if bars_df.empty:
        return ChanlunResult()

    freq_map = {"1d": Freq.D, "1m": Freq.F1}
    czsc_freq = freq_map.get(freq, Freq.D)

    raw_bars = []
    for i, row in enumerate(bars_df.itertuples()):
        raw_bars.append(RawBar(
            symbol=symbol,
            id=i,
            freq=czsc_freq,
            dt=row.dt if isinstance(row.dt, datetime) else datetime.combine(row.dt, datetime.min.time()),
            open=float(row.open),
            close=float(row.close),
            high=float(row.high),
            low=float(row.low),
            vol=float(row.volume) if hasattr(row, "volume") else 0.0,
            amount=float(row.amount_k) * 1000 if hasattr(row, "amount_k") else 0.0,
        ))

    if len(raw_bars) < 10:
        return ChanlunResult()

    czsc_obj = CZSC(raw_bars)

    # 分型
    fx_list = [
        FxResult(
            dt=_fmt_dt(fx.dt, freq),
            mark="top" if "顶" in str(fx.mark) else "bottom",
            price=round(fx.fx, 4),
            high=round(fx.high, 4),
            low=round(fx.low, 4),
        )
        for fx in czsc_obj.fx_list
    ]

    # 笔
    bi_list = [
        BiResult(
            sdt=_fmt_dt(bi.sdt, freq),
            edt=_fmt_dt(bi.edt, freq),
            direction="up" if "上" in str(bi.direction) else "down",
            high=round(bi.high, 4),
            low=round(bi.low, 4),
        )
        for bi in czsc_obj.bi_list
    ]

    # 中枢
    zs_list = _build_zs_list(czsc_obj.bi_list, freq)

    # 买卖点
    bsp_list = _find_bsp(czsc_obj.bi_list, zs_list, freq)

    return ChanlunResult(
        fx_list=fx_list,
        bi_list=bi_list,
        zs_list=zs_list,
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
    """从 DataService 加载 K 线数据，返回统一格式的 DataFrame。"""
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
        hh = (df["minute_slot"] // 60).astype(int).map(lambda v: f"{v:02d}")
        mm = (df["minute_slot"] % 60).astype(int).map(lambda v: f"{v:02d}")
        df["dt"] = df["trade_date"] + pd.to_timedelta(df["minute_slot"].astype(int), unit="m")
        return df


def to_dict(result: ChanlunResult) -> dict[str, Any]:
    """序列化为 JSON-friendly dict。"""
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
        "bsp_list": [
            {"dt": p.dt, "bsp_type": p.bsp_type, "price": p.price}
            for p in result.bsp_list
        ],
    }
