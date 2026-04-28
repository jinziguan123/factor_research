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

    # ---------- live_market worker（嵌入主进程的实盘行情常驻 thread） ----------
    # 默认开启：app startup 时 spawn daemon thread 跑 worker，shutdown 时干净退出。
    # 关闭后可改用 `python -m backend.workers.live_market` 独立进程模式。
    live_market_worker_enabled: bool = Field(
        default=True, alias="FR_LIVE_MARKET_WORKER",
    )
    # archive_1m 默认关闭，避免每个 dev 环境都被强制每天拉 ~120 万行 1m K。
    live_market_archive_1m: bool = Field(
        default=False, alias="FR_LIVE_MARKET_ARCHIVE_1M",
    )
    # 数据落后时是否自动 backfill：True 则 signal_service 检测到 stock_bar_1d 落后
    # 时同步触发 akshare 拉补当前订阅 pool 的缺口（首次会阻塞 ~1-5min 取决于池大小），
    # False 沿用 fail-with-message（要求用户手动跑 backfill 命令）。
    live_market_auto_backfill_daily: bool = Field(
        default=True, alias="FR_LIVE_MARKET_AUTO_BACKFILL_DAILY",
    )

    # ---------- LLM（因子助手用；走 OpenAI 兼容协议，中转 / 官方都行） ----------
    # 为空串时 factor_assistant API 会直接报 503，提醒用户先在 .env 里配好 key。
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    # 中转服务的场景改这里，如 https://api.your-proxy.com/v1；官方即 OpenAI。
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    # LLM 调用超时（秒）；设大一点容忍中转偶发 RTT 抖动。
    openai_timeout_s: float = Field(default=60.0, alias="OPENAI_TIMEOUT_S")
    # 是否在请求里带 response_format={type: json_object}（chat_completions 协议）
    # / text.format={type: json_object}（responses 协议）。默认 True；少数老代理
    # 不兼容时可手动关掉走 prompt 约束。
    openai_response_format_json: bool = Field(
        default=True, alias="OPENAI_RESPONSE_FORMAT_JSON"
    )
    # LLM HTTP 协议选择：
    #   chat_completions —— 老协议，POST /v1/chat/completions，gpt-4o / 4o-mini / 3.5 等 chat 模型用这个；
    #   responses        —— 新协议，POST /v1/responses，o1/o3/gpt-5 家族（reasoning 模型）必须用这个，
    #                       否则走老协议时 message.content 会被吞空。
    openai_api_protocol: str = Field(
        default="chat_completions", alias="OPENAI_API_PROTOCOL"
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
