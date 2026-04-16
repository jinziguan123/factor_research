"""配置加载层的单元测试：验证 Settings 能正确从环境变量读取字段。"""
from backend.config import Settings


def test_settings_reads_env(monkeypatch):
    """验证 Settings 能通过环境变量别名读取 MySQL / ClickHouse 主机地址，且 task_workers 有合理默认值。"""
    monkeypatch.setenv("MYSQL_HOST", "1.2.3.4")
    monkeypatch.setenv("CLICKHOUSE_HOST", "5.6.7.8")
    s = Settings()
    assert s.mysql_host == "1.2.3.4"
    assert s.clickhouse_host == "5.6.7.8"
    assert s.task_workers >= 1
