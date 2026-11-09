"""FastAPI 应用工厂：依赖装配、统一错误信封。

lifespan（池预热、优雅关停）由入口 main.py 注入；SPA 静态托管也在入口挂载，
本工厂只承载对外 API、管理面与统一错误信封。
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ask_gateway.apis.admin import AdminConfig
from ask_gateway.apis.admin import router as admin_router
from ask_gateway.apis.v1 import router as v1_router
from ask_gateway.services.chat_service import ChatRuntime
from ask_gateway.services.errors import ServiceError

# 本机裁剪版 CPython 的 collections.abc 无 AsyncContextManager，与 Starlette 一样走 contextlib
Lifespan = Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]


def create_app(
    *,
    chat_runtime: ChatRuntime,
    admin_password: str = "",
    admin_session_ttl_hours: int = 12,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    app = FastAPI(title="ask-gateway", version="0.1.0", lifespan=lifespan)
    app.state.chat_runtime = chat_runtime
    app.state.admin_config = AdminConfig(
        admin_password=admin_password,
        session_ttl_hours=admin_session_ttl_hours,
    )
    app.include_router(v1_router)
    app.include_router(admin_router)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_envelope(exc.code, exc.message, request),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_error_envelope("invalid_request", str(exc.errors()), request),
        )

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_envelope("internal_error", "internal server error", request),
        )

    return app


def _error_envelope(code: str, message: str, request: Request) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
        }
    }


def _request_id(request: Request) -> str:
    # 路由内生成的 request_id 挂在 state 上；鉴权/校验阶段还没有则现场生成
    raw = getattr(request.state, "request_id", None)
    return str(raw) if raw is not None else str(uuid.uuid4())
