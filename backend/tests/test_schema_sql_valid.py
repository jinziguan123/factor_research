"""Schema 初始化 SQL 的静态校验。

该测试只做纯文本扫描，不连接任何数据库，目的是：
1. 防止误删 factor_research 专属表的 DDL；
2. 防止手滑把生产业务表（stock_symbol / stock_pool / backtest_runs 等）
   写进本项目的初始化脚本里——这些表由 timing_driven 维护，本项目只读/隔离使用。
"""
from __future__ import annotations

from pathlib import Path

# 测试文件位于 backend/tests/，SQL 位于 backend/scripts/。
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_mysql_sql_exists() -> None:
    sql_path = SCRIPTS / "init_mysql.sql"
    assert sql_path.exists(), f"缺少文件 {sql_path}"
    sql = sql_path.read_text(encoding="utf-8")

    # 1) factor_research 专属的 7 张表必须齐全
    required_tables = [
        "fr_qfq_factor",
        "fr_factor_meta",
        "fr_factor_eval_runs",
        "fr_factor_eval_metrics",
        "fr_backtest_runs",
        "fr_backtest_metrics",
        "fr_backtest_artifacts",
    ]
    for table in required_tables:
        assert table in sql, f"init_mysql.sql 缺少表 {table}"

    # 2) 所有 CREATE TABLE 必须带 IF NOT EXISTS（幂等）
    #    粗略检查：文本里不能出现裸 "CREATE TABLE `fr_" 而无 IF NOT EXISTS。
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS `{table}`" in sql, (
            f"表 {table} 的 DDL 必须是 CREATE TABLE IF NOT EXISTS 以保证幂等"
        )

    # 3) 严禁包含生产业务表的 DDL / DML，这些表由 timing_driven 维护
    forbidden_snippets = [
        "CREATE TABLE `stock_symbol`",
        "CREATE TABLE stock_symbol",
        "CREATE TABLE `stock_pool`",
        "CREATE TABLE stock_pool",
        "CREATE TABLE `stock_pool_symbol`",
        "CREATE TABLE `stock_bar_import_job`",
        "CREATE TABLE `backtest_runs`",
        "CREATE TABLE backtest_runs",
        "ALTER TABLE backtest_runs",
    ]
    for forbidden in forbidden_snippets:
        assert forbidden not in sql, (
            f"init_mysql.sql 不应包含 {forbidden}（生产业务表，由 timing_driven 维护）"
        )


def test_clickhouse_sql_exists() -> None:
    sql_path = SCRIPTS / "init_clickhouse.sql"
    assert sql_path.exists(), f"缺少文件 {sql_path}"
    sql = sql_path.read_text(encoding="utf-8")

    # 库要幂等创建
    assert "CREATE DATABASE IF NOT EXISTS quant_data" in sql

    # 两张新建表
    assert "stock_bar_1d" in sql
    assert "factor_value_1d" in sql

    # 全部 CREATE TABLE 必须是 IF NOT EXISTS
    assert "CREATE TABLE IF NOT EXISTS quant_data.stock_bar_1d" in sql
    assert "CREATE TABLE IF NOT EXISTS quant_data.factor_value_1d" in sql

    # 引擎必须是 ReplacingMergeTree
    assert "ReplacingMergeTree" in sql

    # 关键字段类型约束（见设计文档 §3.2 的单位说明）
    # volume 必须升级为 UInt64 以容纳日级累加量
    assert "`volume` UInt64" in sql or "`volume`     UInt64" in sql, (
        "stock_bar_1d.volume 必须是 UInt64（设计文档 §3.2）"
    )
    # factor_value_1d.value 必须是 Float64（避免精度问题）
    assert "`value` Float64" in sql or "`value`          Float64" in sql, (
        "factor_value_1d.value 必须是 Float64（设计文档 §3.2）"
    )


def test_run_init_script_exists() -> None:
    """run_init.py 必须存在且可以被 import（不能有语法错误）。"""
    import importlib

    mod = importlib.import_module("backend.scripts.run_init")
    # 必需的顶层符号
    for name in ("main", "_run_mysql", "_run_clickhouse", "_safety_check", "_split_sql"):
        assert hasattr(mod, name), f"backend.scripts.run_init 缺少 {name}"


def test_split_sql_basic() -> None:
    """_split_sql 对分号拆分 + 引号内不拆分的基本行为。"""
    from backend.scripts.run_init import _split_sql

    sql = "SELECT 1; SELECT 'a;b'; CREATE TABLE t (c int);"
    stmts = [s.strip() for s in _split_sql(sql) if s.strip()]
    assert stmts == ["SELECT 1", "SELECT 'a;b'", "CREATE TABLE t (c int)"]


def test_safety_check_blocks_production_host(monkeypatch) -> None:
    """把 mysql_host 设为生产 IP 时，_safety_check 必须抛 RuntimeError。

    历史上这里是 sys.exit(1)，但若 _safety_check 被误挂在 ASGI BackgroundTask
    里会让 response hook 直接崩栈。改抛异常后：CLI 的 main() 自己兜 try/except
    转 sys.exit；线程/import 层面的调用由各自 try/except 捕获。
    """
    import pytest

    from backend.scripts import run_init
    from backend.config import settings

    monkeypatch.setattr(settings, "mysql_host", "172.30.26.12")
    monkeypatch.setattr(settings, "clickhouse_host", "127.0.0.1")
    monkeypatch.delenv("FR_ALLOW_PRODUCTION_INIT", raising=False)

    with pytest.raises(RuntimeError, match="MySQL"):
        run_init._safety_check()


def test_safety_check_allows_override(monkeypatch) -> None:
    """FR_ALLOW_PRODUCTION_INIT=1 时允许放行，方便运维真的要跑的场景。"""
    from backend.scripts import run_init
    from backend.config import settings

    monkeypatch.setattr(settings, "mysql_host", "172.30.26.12")
    monkeypatch.setattr(settings, "clickhouse_host", "172.30.26.12")
    monkeypatch.setenv("FR_ALLOW_PRODUCTION_INIT", "1")

    # 不应抛异常
    run_init._safety_check()


def test_safety_check_passes_for_localhost(monkeypatch) -> None:
    """默认本地/容器名 host 都是安全的，不应退出。"""
    from backend.scripts import run_init
    from backend.config import settings

    for host in ("127.0.0.1", "localhost", "mysql", "clickhouse"):
        monkeypatch.setattr(settings, "mysql_host", host)
        monkeypatch.setattr(settings, "clickhouse_host", host)
        run_init._safety_check()
