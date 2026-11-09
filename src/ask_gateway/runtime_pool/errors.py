"""runtime 池与 runner 异常。路由层据此映射 429/502/504。"""

from __future__ import annotations


class PoolError(Exception):
    """池层异常基类。"""


class PoolTimeout(PoolError):
    """队列等待超时或队列已满 → HTTP 429。"""


class PoolClosing(PoolError):
    """优雅关停中，拒绝新请求 → HTTP 503。"""


class RunnerError(PoolError):
    """一次 run 失败，runner 仍可复用 → HTTP 502。"""


class RunnerBroken(PoolError):
    """子进程崩溃/协议断裂，句柄必须销毁 → HTTP 502。"""


class RunnerTimeout(PoolError):
    """run 超过请求超时 → HTTP 504。"""
