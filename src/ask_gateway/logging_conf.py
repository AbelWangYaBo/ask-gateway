"""structlog 配置：JSON/console 双 renderer，contextvar 链路字段，密钥脱敏。"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars
from structlog.typing import EventDict, Processor

# sk-ask- 开头的 Key、Bearer 令牌、疑似 API Key 的值
_SECRET_PATTERNS = (
    re.compile(r"sk-ask-[A-Za-z0-9_\-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
)
_MASK = "***REDACTED***"


def _redact_value(value: Any) -> Any:
    """递归脱敏字符串里的密钥形态。"""
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(_MASK, redacted)
        return redacted
    if isinstance(value, dict):
        return {key: _redact_secret_kv(key, val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _redact_secret_kv(key: str, value: Any) -> Any:
    """键名像密钥时整个值打码，否则按值脱敏。"""
    if any(token in key.lower() for token in ("key", "token", "password", "secret", "authorization")):
        return _MASK
    return _redact_value(value)


def redact_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    return {key: _redact_secret_kv(key, val) for key, val in event_dict.items()}


def configure_logging(*, log_json: bool = True, level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())

    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_values: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger().bind(**initial_values)
