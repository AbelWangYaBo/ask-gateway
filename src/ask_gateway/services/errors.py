"""服务层统一异常，路由层据此映射 HTTP 状态码。"""

from __future__ import annotations


class ServiceError(Exception):
    """所有服务异常基类，携带稳定错误码与 HTTP 状态。"""

    http_status: int = 400

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class KeyNotFoundError(ServiceError):
    http_status = 404

    def __init__(self, message: str = "api key not found") -> None:
        super().__init__("key_not_found", message)


class QuotaError(ServiceError):
    """配额拒绝。code 取值：rpm_exceeded / concurrency_exceeded / daily_budget_exceeded。"""

    http_status = 429

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)


class AuthError(ServiceError):
    http_status = 401

    def __init__(self, code: str = "invalid_credentials", message: str = "invalid credentials") -> None:
        super().__init__(code, message)


class PricingConflictError(ServiceError):
    http_status = 409

    def __init__(self, message: str) -> None:
        super().__init__("pricing_conflict", message)
