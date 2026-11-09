"""应用配置：全部来自环境变量，pydantic-settings 解析。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 基础 ----
    host: str = Field(default="127.0.0.1", description="绑定地址；默认仅本机，暴露局域网需显式覆盖")
    port: int = Field(default=8000)
    data_dir: Path = Field(default=Path("data"), description="运行数据根目录")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True, description="生产 JSON 日志，本地开发可关")

    # ---- 数据库 ----
    # 默认 sqlite 便于本地无 PG 开发；生产由 .env 覆盖为 postgresql+psycopg://...
    database_url: str = Field(
        default="sqlite:///data/ask_gateway.db",
    )

    # ---- 管理员 ----
    admin_password: str = Field(default="", description="控制台登录口令；生产必须通过环境变量注入")
    admin_session_ttl_hours: int = Field(default=12)

    # ---- 上游 DeepSeek Key 池（明文只走环境变量，不入库不入日志）----
    # 环境变量写法：UPSTREAM_KEYS="key1,key2"（NoDecode 阻止 env 源抢先 JSON 解析）
    upstream_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    upstream_provider: str = Field(default="deepseek-official")
    upstream_base_url: str | None = Field(default=None)
    upstream_model: str = Field(default="deepseek-flash")

    # ---- DSH runtime ----
    dsh_profile: str = Field(default="ask-gateway")
    dsh_bin: str | None = Field(default=None, description="默认用 SDK 捆绑 runtime")
    pool_min_size: int = Field(default=2)
    pool_max_size: int = Field(default=12)
    pool_queue_maxsize: int = Field(default=32)
    pool_acquire_timeout_seconds: float = Field(default=60.0)
    pool_idle_ttl_seconds: float = Field(default=600.0)
    pool_reaper_interval_seconds: float = Field(default=60.0)
    upstream_circuit_break_seconds: float = Field(default=300.0)
    shutdown_grace_seconds: float = Field(default=45.0)
    request_timeout_seconds: float | None = Field(default=None)
    dsh_initialize_timeout_seconds: float = Field(
        default=60.0, description="DSH 子进程冷启动与握手上限"
    )

    # ---- 本平台 Key 的默认配额（NULL 时落到这里）----
    default_rpm: int = Field(default=30)
    default_daily_budget_cents: int = Field(default=10000)
    default_concurrency: int = Field(default=4)

    # ---- 模型白名单 ----
    allowed_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["deepseek-flash", "deepseek-v4-pro"]
    )

    @field_validator("upstream_keys", "allowed_models", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """环境变量里的逗号分隔字符串转 list。"""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return value  # 让 pydantic 按 JSON 解析
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @property
    def dsh_home_root(self) -> Path:
        return self.data_dir / "dsh" / "homes"

    @property
    def dsh_workspace_root(self) -> Path:
        return self.data_dir / "dsh" / "workspaces"

    @property
    def dsh_session_root(self) -> Path:
        return self.data_dir / "dsh" / "sessions"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.dsh_home_root,
            self.dsh_workspace_root,
            self.dsh_session_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
