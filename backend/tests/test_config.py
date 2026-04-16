"""配置加载层的单元测试：验证 Settings 能正确从环境变量读取字段。"""
import os

import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_settings_reads_env(monkeypatch):
    """验证 Settings 能通过环境变量别名读取 MySQL / ClickHouse 主机地址，且 task_workers 有合理默认值。"""
    monkeypatch.setenv("MYSQL_HOST", "1.2.3.4")
    monkeypatch.setenv("CLICKHOUSE_HOST", "5.6.7.8")
    s = Settings(_env_file=None)
    assert s.mysql_host == "1.2.3.4"
    assert s.clickhouse_host == "5.6.7.8"
    assert s.task_workers >= 1


def test_default_values_locked():
    """锁住关键默认值，防止被无意改动。

    - ``owner_key`` 必须是 ``factor_research``，区分本平台写入的因子。
    - ``mysql_host`` / ``clickhouse_host`` 默认值必须是 ``127.0.0.1``（开发态安全），
      绝不能悄悄回退成任何生产 IP。
    - ``factors_dir`` / ``qfq_factor_path`` 必须为绝对路径，避免 ProcessPool 子进程
      cwd 不一致时解析错误。
    """
    s = Settings(_env_file=None)
    assert s.owner_key == "factor_research"
    assert s.task_workers >= 1
    assert s.mysql_host == "127.0.0.1"
    assert s.clickhouse_host == "127.0.0.1"
    # 绝对路径校验：默认值应以 `/` 开头（macOS/Linux 约定；Windows 暂不考虑）。
    assert os.path.isabs(s.factors_dir), f"factors_dir 应为绝对路径，实际={s.factors_dir}"
    assert os.path.isabs(s.qfq_factor_path), f"qfq_factor_path 应为绝对路径，实际={s.qfq_factor_path}"
    # factors_dir 应落在 backend/ 目录下；qfq_factor_path 应落在项目根 data/ 下。
    assert s.factors_dir.endswith("/backend/factors")
    assert s.qfq_factor_path.endswith("/data/merged_adjust_factors.parquet")
    # artifact_dir 默认落在项目根 data/artifacts 下，Docker 卷挂载点在此命中。
    assert os.path.isabs(s.artifact_dir), f"artifact_dir 应为绝对路径，实际={s.artifact_dir}"
    assert s.artifact_dir.endswith("/data/artifacts")


def test_task_workers_ge_1(monkeypatch):
    """``task_workers`` 必须 >=1，配成 0 应直接被 pydantic 约束拦截。"""
    monkeypatch.setenv("FR_TASK_WORKERS", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
