"""全局配置加载层。

基于 pydantic-settings 从进程环境变量 / `.env` 读取配置，字段名统一使用 snake_case，
并通过 ``Field(alias=...)`` 把历史/部署脚本里的大写环境变量（如 ``MYSQL_HOST``）映射进来，
避免上层代码到处散落 ``os.getenv``。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ``config.py`` 所在目录即 backend 根；再向上一级是 factor_research 项目根。
# 用绝对路径作为默认值，避免 ProcessPool 子进程因 cwd 不一致导致相对路径解析错误。
_BACKEND_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_ROOT.parent


class Settings(BaseSettings):
    """整个后端的运行期配置。

    所有字段都对应一个环境变量别名；没有设置时使用下方默认值，默认值取自
    ``backend/.env.example`` 中的开发态参数，便于本地直接启动。
    """

    # ---------- ClickHouse（行情 / 因子值存储） ----------
    clickhouse_host: str = Field(default="127.0.0.1", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=9000, alias="CLICKHOUSE_PORT")
    clickhouse_database: str = Field(default="quant_data", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")

    # ---------- MySQL（元数据 / 任务 / 指标） ----------
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="myuser", alias="MYSQL_USER")
    mysql_password: str = Field(default="mypassword", alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="quant_data", alias="MYSQL_DATABASE")

    # ---------- 复权因子 Parquet 路径 ----------
    qfq_factor_path: str = Field(
        default=str(_PROJECT_ROOT / "data" / "merged_adjust_factors.parquet"),
        alias="QFQ_FACTOR_PATH",
    )
    # 回测产物（equity / orders / trades parquet）根目录；运行时按 <run_id> 建子目录。
    # 绝对路径默认值与 qfq_factor_path 风格一致，避免 ProcessPool 子进程 cwd 漂移。
    artifact_dir: str = Field(
        default=str(_PROJECT_ROOT / "data" / "artifacts"),
        alias="FR_ARTIFACT_DIR",
    )

    # ---------- 因子研究平台自身参数 ----------
    # 任务进程池大小；设为 >=1 以保证至少串行执行一个任务。
    task_workers: int = Field(default=2, ge=1, alias="FR_TASK_WORKERS")
    log_level: str = Field(default="INFO", alias="FR_LOG_LEVEL")
    # 是否开启因子热加载（watchdog 监听 factors 目录）。
    hot_reload: bool = Field(default=True, alias="FR_HOT_RELOAD")
    # factor_meta.owner 的默认归属，区分本平台与外部系统写入的因子。
    owner_key: str = Field(default="factor_research", alias="FR_OWNER_KEY")
    factors_dir: str = Field(
        default=str(_BACKEND_ROOT / "factors"),
        alias="FR_FACTORS_DIR",
    )

    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        # 允许环境变量使用字段别名（大写）命名。
        populate_by_name=True,
        # 忽略 .env 中未在此声明的冗余键，避免启动期因多余字段报错。
        extra="ignore",
        case_sensitive=False,
    )


# 模块级单例：其他模块通过 ``from backend.config import settings`` 使用。
settings = Settings()
