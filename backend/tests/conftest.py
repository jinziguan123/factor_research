"""pytest 公共 fixture。

职责：
1. 单测默认隔离 .env / 公共环境变量，防止本地开发机上的真实配置污染测试结果。
   - 清理所有 Settings 关心的公共环境变量（monkeypatch 作用于单用例，结束自动恢复）。
   - 新增单测在实例化 ``Settings`` 时请显式传 ``_env_file=None`` 以彻底跳过 .env 文件读取。
     历史用例只要未显式设置 env var，也能借助本 fixture 得到确定性默认值。
2. 集成测试（带 ``@pytest.mark.integration``）需要连接真实本地测试库，
   通过 ``_integration_db_settings`` 把测试库凭据直接灌进模块级 ``settings`` 单例。
3. 提供 ClickHouse / MySQL 的 clean / seed fixture，供 DataService 集成测试使用。

后续 Task 会在此继续追加任务 / 临时目录等 fixture。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

# Settings 关心的所有环境变量别名；autouse fixture 会在每个用例开始前清理它们，
# 避免宿主机实际配置（如部署机上的 CLICKHOUSE_HOST=172.x）影响单测断言。
_SETTINGS_ENV_VARS = (
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DATABASE",
    "QFQ_FACTOR_PATH",
    "FR_TASK_WORKERS",
    "FR_LOG_LEVEL",
    "FR_HOT_RELOAD",
    "FR_OWNER_KEY",
    "FR_FACTORS_DIR",
)

# 本地 docker-compose 测试库凭据（见 docker-compose-test/*/docker-compose.yaml）。
# 硬编码在这里是有意为之：集成测试的前提就是“在本机起过 docker-compose-test”，
# 任何环境差异都应通过启动正确的容器来解决，而不是通过覆盖这组常量。
_INTEGRATION_MYSQL = {
    "mysql_host": "127.0.0.1",
    "mysql_port": 3306,
    "mysql_user": "myuser",
    "mysql_password": "mypassword",
    "mysql_database": "quant_data",
}
_INTEGRATION_CLICKHOUSE = {
    "clickhouse_host": "127.0.0.1",
    "clickhouse_port": 9000,
    "clickhouse_user": "quant",
    "clickhouse_password": "Jinziguan123",
    "clickhouse_database": "quant_data",
}

# 集成 fixture 的 symbol_id 白名单（对应 stock_symbol seed 1..5）。
_TEST_SYMBOL_IDS = (1, 2, 3, 4, 5)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    """默认清理 Settings 相关公共环境变量，保证每个用例起始状态一致。

    若用例需要验证特定环境变量行为，直接用 ``monkeypatch.setenv`` 覆盖即可。
    若需要读取实际 ``.env`` 的集成测试，可在用例里自行实例化 ``Settings()``
    （即不传 ``_env_file=None``）并对宿主环境负责。
    """
    for var in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture(autouse=True)
def _integration_db_settings(request, monkeypatch):
    """为带 ``@pytest.mark.integration`` 的用例把 settings 指向本地测试库。

    为什么是"直接 monkeypatch settings 单例属性"而不是 setenv：
    - ``backend.config.settings`` 是模块级单例，导入时已经固化；
      setenv 对已加载单例无效；
    - ``Settings(_env_file=None)`` 重建实例只能影响局部引用，
      storage 层早已 ``from backend.config import settings`` 拿到旧对象。
    monkeypatch.setattr 在用例结束后会自动回滚，不会污染后续用例。

    NOTE（跨进程测试注意事项）：
    本 fixture 只影响**当前测试进程**的 settings 单例。如果将来 Task 8 的
    ProcessPool 集成测试从子进程发起 DB 访问，子进程会重新 ``import backend.config``
    并拿到 ``Settings()`` 的默认值——默认 host/port/user 恰好也是 127.0.0.1 / 3306
    / myuser，因此短期内"能跑"；但生产环境凭据走 ``.env`` 路径，完全绕过本
    fixture。跨进程集成测试应通过：(a) 父进程先 ``monkeypatch.setenv`` 再 spawn
    让子进程 import 时读到测试配置；或 (b) 在 ProcessPool initializer 里显式注入
    settings；**不能依赖本 fixture 的 attr 覆盖**。
    """
    if request.node.get_closest_marker("integration") is None:
        yield
        return

    from backend.config import settings

    for key, value in {**_INTEGRATION_MYSQL, **_INTEGRATION_CLICKHOUSE}.items():
        monkeypatch.setattr(settings, key, value, raising=True)
    yield


# --------------------- 集成测试 fixture：数据库数据准备 ---------------------


@pytest.fixture
def clean_stock_bar_1d():
    """清空 ClickHouse ``stock_bar_1d`` 中测试 symbol_id 的数据（进入和退出时各一次）。"""
    from backend.storage.clickhouse_client import ch_client

    sid_list = list(_TEST_SYMBOL_IDS)
    with ch_client() as ch:
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1d DELETE WHERE symbol_id IN %(sids)s",
            {"sids": sid_list},
        )
    yield
    with ch_client() as ch:
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1d DELETE WHERE symbol_id IN %(sids)s",
            {"sids": sid_list},
        )


@pytest.fixture
def seed_bar_1d(clean_stock_bar_1d):
    """给 stock_bar_1d 灌 5 只股票 × 30 个自然日（跳过周末）的合成数据。

    价格构造原则：每只股票 base_price = 10 + symbol_id，open=base, high=base+0.5,
    low=base-0.3, close=base+0.1；volume/amount_k 取常量；trade_date 从 2024-01-02 起
    逐日递增，周末跳过。version 用 Unix 纳秒 + symbol_id + 序号，保证单调且唯一。
    """
    from backend.storage.clickhouse_client import ch_client

    base = date(2024, 1, 2)
    rows: list[tuple] = []
    # 以递增 version 插入，避免 ReplacingMergeTree 合并时挑错数据版本。
    # 用一个基准 nanosec 再 + 序号，保证 (version) 严格单调。
    base_version = 1_700_000_000_000_000_000
    seq = 0
    for sid in _TEST_SYMBOL_IDS:
        for i in range(30):
            d = base + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            bp = 10.0 + sid
            rows.append(
                (
                    sid,
                    d,
                    float(bp),  # open
                    float(bp + 0.5),  # high
                    float(bp - 0.3),  # low
                    float(bp + 0.1),  # close
                    1_000_000,  # volume
                    10_000,  # amount_k (千元)
                    base_version + seq,
                )
            )
            seq += 1

    with ch_client() as ch:
        # clickhouse-driver 在 use_numpy=True 时要求 INSERT 走列式，且每列是
        # ndarray / DatetimeIndex / ExtensionArray。这里把行式转列式、并按
        # 表的物理类型选 dtype：整型列用 int64（driver 会收口到 UInt 类型），
        # 日期列用 object dtype（保留 datetime.date），浮点列用 float64。
        cols = list(zip(*rows))
        columns_np = [
            np.asarray(cols[0], dtype=np.uint32),  # symbol_id
            np.asarray(cols[1], dtype=object),  # trade_date (datetime.date)
            np.asarray(cols[2], dtype=np.float32),  # open
            np.asarray(cols[3], dtype=np.float32),  # high
            np.asarray(cols[4], dtype=np.float32),  # low
            np.asarray(cols[5], dtype=np.float32),  # close
            np.asarray(cols[6], dtype=np.uint64),  # volume
            np.asarray(cols[7], dtype=np.uint32),  # amount_k
            np.asarray(cols[8], dtype=np.uint64),  # version
        ]
        ch.execute(
            "INSERT INTO quant_data.stock_bar_1d "
            "(symbol_id, trade_date, open, high, low, close, volume, amount_k, version) "
            "VALUES",
            columns_np,
            columnar=True,
        )
        # OPTIMIZE FINAL：让 ReplacingMergeTree 立刻合并掉重复版本，
        # 后续 SELECT ... FINAL 读到的是稳定状态。
        ch.execute("OPTIMIZE TABLE quant_data.stock_bar_1d FINAL")
    return rows


@pytest.fixture
def clean_qfq_factor():
    """清空 MySQL ``fr_qfq_factor`` 中测试 symbol_id 的数据（进入 + 退出）。"""
    from backend.storage.mysql_client import mysql_conn

    sid_list = list(_TEST_SYMBOL_IDS)
    placeholders = ",".join(["%s"] * len(sid_list))
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"DELETE FROM fr_qfq_factor WHERE symbol_id IN ({placeholders})",
                sid_list,
            )
        c.commit()
    yield
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"DELETE FROM fr_qfq_factor WHERE symbol_id IN ({placeholders})",
                sid_list,
            )
        c.commit()


@pytest.fixture
def seed_qfq_factor(clean_qfq_factor):
    """制造一次除权事件用于 qfq 回归测试：

    - sid=1 在 ``2024-01-15`` 之前因子=1.0，之后=0.5（除权一次）；
    - 其它 symbol_id 因子始终=1.0；
    - 与 ``seed_bar_1d`` 的日期范围保持一致。
    """
    from backend.storage.mysql_client import mysql_conn

    base = date(2024, 1, 2)
    rows: list[tuple] = []
    for sid in _TEST_SYMBOL_IDS:
        for i in range(30):
            d = base + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            factor = 0.5 if (sid == 1 and d >= date(2024, 1, 15)) else 1.0
            rows.append((sid, d, factor, 1_700_000_000))

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO fr_qfq_factor "
                "(symbol_id, trade_date, factor, source_file_mtime) "
                "VALUES (%s, %s, %s, %s)",
                rows,
            )
        c.commit()
    return rows
