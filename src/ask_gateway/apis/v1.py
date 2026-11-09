"""对外 OpenAI 兼容路由：薄适配层，编排全部在 services/chat_service。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ask_gateway.db.session import session_scope
from ask_gateway.models.api_key import ApiKey
from ask_gateway.schemas.chat import (
    ChatCompletionRequest,
    ModelInfo,
    ModelList,
)
from ask_gateway.services.chat_service import (
    ChatRuntime,
    build_completion_response,
    execute_chat,
    sse_chat,
)
from ask_gateway.services.errors import AuthError
from ask_gateway.services.key_service import authenticate

router = APIRouter()


def get_api_key(
    authorization: str | None = Header(default=None),
    session: Session = Depends(session_scope),
) -> ApiKey:
    token = _bearer_token(authorization)
    api_key = authenticate(session, token) if token else None
    if api_key is None:
        raise AuthError()
    return api_key


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return ""
    return authorization.removeprefix("Bearer ").strip()


def _runtime(request: Request) -> ChatRuntime:
    return request.app.state.chat_runtime  # type: ignore[no-any-return]


@router.post("/v1/chat/completions")
def chat_completions(
    request: Request,
    response: Response,
    payload: ChatCompletionRequest,
    api_key: ApiKey = Depends(get_api_key),
) -> Any:
    runtime = _runtime(request)
    request_id = uuid.uuid4()
    request.state.request_id = request_id
    request_id_header = {"X-Request-Id": str(request_id)}
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    if payload.stream:
        frames = sse_chat(
            runtime,
            api_key=api_key,
            payload=payload,
            request_id=request_id,
            client_ip=client_ip,
            user_agent=user_agent,
            is_disconnected=request.is_disconnected,
        )
        return EventSourceResponse(frames, headers=request_id_header)

    result = execute_chat(
        runtime,
        api_key=api_key,
        payload=payload,
        request_id=request_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    response.headers["X-Request-Id"] = str(request_id)
    return build_completion_response(result)


@router.get("/v1/models")
def list_models(request: Request) -> ModelList:
    runtime = _runtime(request)
    return ModelList(data=[ModelInfo(id=model) for model in runtime.allowed_models])


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    session = runtime.session_factory()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()
    return JSONResponse({"status": "ok"})
