"""pytest 公共 fixture。

目前的职责：
1. 单测默认隔离 .env / 公共环境变量，防止本地开发机上的真实配置污染测试结果。
   - 清理所有 Settings 关心的公共环境变量（monkeypatch 作用于单用例，结束自动恢复）。
   - 新增单测在实例化 ``Settings`` 时请显式传 ``_env_file=None`` 以彻底跳过 .env 文件读取。
     历史用例只要未显式设置 env var，也能借助本 fixture 得到确定性默认值。

后续 Task 会在此继续追加数据库 / 临时目录等 fixture。
"""
from __future__ import annotations

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
