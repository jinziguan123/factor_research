"""QMT (国信 iQuant) 本地 ``.DAT`` 文件读取。

来源：从 timing_driven_backtest 的 ``data_manager.py`` 抽取的二进制解析逻辑，
研究端需要同样读 QMT 下载下来的分钟线 .DAT 文件，所以把最小必要部分搬进来。

文件格式（单条记录 56 字节）：
    time(u4) + open(i4) + high(i4) + low(i4) + close(i4) + unused_amt(f4)
    + volume(i4) + unused_res(i4) + amount(i8) + padding(V24)
- 开头 8 字节是文件头，通过 ``offset=8`` 跳过。
- 价格字段以「原始价 × 1000」存为整数，读时除以 1000.0。
- ``time`` 是 UTC+0 秒级 Unix 时间戳；北京时间需要 ``+28800`` 再转 datetime。

目录约定：``<base>/<market>/<period_dir>/<code>.DAT``，其中：
    market ∈ {SH, SZ, BJ}；period_dir 1m=60 / 1d=86400。
"""
from __future__ import annotations

import logging
import mmap
import os
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 单条记录二进制结构（与 iQuant 输出的 1m .DAT 对齐）。
# 字段顺序和字节宽度必须与 iQuant 写出的文件一致，任意改动都会读成错乱数据。
IQUANT_DTYPE = np.dtype(
    [
        ("time", "<u4"),
        ("open", "<i4"),
        ("high", "<i4"),
        ("low", "<i4"),
        ("close", "<i4"),
        ("unused_amt", "<f4"),
        ("volume", "<i4"),
        ("unused_res", "<i4"),
        ("amount", "<i8"),
        ("padding", "V24"),
    ]
)

# 默认目录：直接从环境变量取，生产机 Windows 路径形如 C:\iQuant\...\datadir。
# 研究端在 Mac/Linux 上 **一定** 要设 ``IQUANT_LOCAL_DATA_DIR``，否则读不到任何文件。
DEFAULT_LOCAL_DATA_DIR = os.environ.get(
    "IQUANT_LOCAL_DATA_DIR",
    r"C:\iQuant\国信iQuant策略交易平台\datadir",
)


def _period_dir(period: str) -> str:
    # iQuant 落盘按秒数分目录：日线 86400、分钟线 60。
    if period == "1d":
        return "86400"
    if period == "1m":
        return "60"
    raise ValueError(f"Unsupported period: {period}")


def get_dat_file_path(
    symbol: str,
    period: str = "1m",
    base_dir: str | os.PathLike | None = None,
) -> str:
    """根据 ``000001.SZ`` 这样的 symbol 拼出 ``.DAT`` 绝对路径。"""
    base = str(base_dir) if base_dir else DEFAULT_LOCAL_DATA_DIR
    if "." not in symbol:
        raise ValueError(f"symbol must look like '000001.SZ', got {symbol!r}")
    code, market = symbol.strip().upper().split(".", 1)
    return os.path.join(base, market, _period_dir(period), f"{code}.DAT")


def read_iquant_mmap(
    file_path: str | Path,
    fields: Sequence[str] = ("open", "high", "low", "close", "volume", "amount"),
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> pd.DataFrame:
    """读单只 .DAT 文件为 DataFrame。

    - 走 ``mmap``，对 5000+ 只股票批量扫描时比 ``np.fromfile`` 省内存。
    - 返回 ``DataFrame``：index 为 1 分钟对齐的 ``DatetimeIndex``（北京时间，无 tz），
      columns 为 ``fields`` 中命中的子集。
    - 文件不存在 / 文件过小 / 异常 → 返回空 DataFrame（调用方做存在性判断）。
    - ``start_ts`` / ``end_ts`` 是秒级 Unix 时间戳，用于增量读时裁剪磁盘块上的记录，
      **是文件存的 time 字段（UTC+0 秒）**，不是北京时间；调用方记得 - 28800。
    """
    if not os.path.exists(file_path):
        return pd.DataFrame()

    filtered_data = None
    try:
        with open(file_path, "rb") as f:
            # 经验：空文件 / 只有 header 的文件 mmap 会抛 ValueError，提前 short-circuit。
            f.seek(0, 2)
            if f.tell() < 8:
                return pd.DataFrame()
            f.seek(0, 0)

            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                # offset=8 跳过文件头；frombuffer 只是做视图，不拷贝物理内存。
                data = np.frombuffer(mm, dtype=IQUANT_DTYPE, offset=8)

                valid_mask = data["time"] > 0
                if start_ts is not None:
                    valid_mask &= data["time"] >= int(start_ts)
                if end_ts is not None:
                    valid_mask &= data["time"] <= int(end_ts)

                # Boolean indexing 会触发一次 copy，让我们脱离 mmap 作用域后仍然拿得到数据。
                filtered_data = data[valid_mask].copy()
                del data
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_iquant_mmap failed: path=%s err=%s", file_path, exc)
        return pd.DataFrame()

    if filtered_data is None or filtered_data.size == 0:
        return pd.DataFrame()

    timestamps = filtered_data["time"].astype(np.int64)
    # iQuant 的 time 字段已经是 UTC+0 秒、天然对齐到分钟边界；这里 // 60 * 60 仅为防御性
    # 裁剪（过去遇到过个别脏数据不对齐），不会改变正常样本。+28800 把 UTC 秒转成北京时间。
    timestamps = (timestamps // 60) * 60 + 28800
    index = pd.to_datetime(timestamps, unit="s")

    columns: dict[str, np.ndarray] = {}
    wanted = set(fields)
    if "open" in wanted:
        columns["open"] = (filtered_data["open"] / 1000.0).astype(np.float32)
    if "high" in wanted:
        columns["high"] = (filtered_data["high"] / 1000.0).astype(np.float32)
    if "low" in wanted:
        columns["low"] = (filtered_data["low"] / 1000.0).astype(np.float32)
    if "close" in wanted:
        columns["close"] = (filtered_data["close"] / 1000.0).astype(np.float32)
    if "volume" in wanted:
        columns["volume"] = filtered_data["volume"].astype(np.float32)
    if "amount" in wanted:
        columns["amount"] = filtered_data["amount"].astype(np.float32)

    df = pd.DataFrame(columns, index=index)
    df.index.name = "datetime"
    if not df.index.is_unique:
        df = df[~df.index.duplicated(keep="last")]
    return df
