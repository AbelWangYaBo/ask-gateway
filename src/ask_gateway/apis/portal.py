"""用户门户 REST：API Key 即凭证，持有人只读视图。

与 /v1 共用 Bearer sk-ask- 鉴权（停用/到期/吊销即时生效），
与 /admin 共用查询核心；数据范围强制收窄到持有人本人。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ask_gateway.db.session import session_scope
from ask_gateway.models.api_key import ApiKey
from ask_gateway.schemas.admin import (
    RequestDetailOut,
    RequestListItem,
    RequestPage,
    UsageChart,
    UsageChartSeries,
    UsageSummaryOut,
    UsageSummaryRow,
)
from ask_gateway.schemas.conversation import (
    ConversationChatRequest,
    ConversationCreateRequest,
    ConversationDetailOut,
    ConversationForkRequest,
    ConversationMessageOut,
    ConversationOut,
)
from ask_gateway.schemas.portal import PortalDashboardOut, PortalProfileOut
from ask_gateway.services import admin_service, conversation_service, portal_service
from ask_gateway.services.chat_service import ChatRuntime, sse_chat
from ask_gateway.services.errors import AuthError
from ask_gateway.services.key_service import authenticate

router = APIRouter(prefix="/api/portal")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def require_holder(
    authorization: str | None = Header(default=None),
    session: Session = Depends(session_scope),
) -> ApiKey:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    api_key = authenticate(session, token) if token else None
    if api_key is None:
        raise AuthError("unauthorized", "API Key missing, disabled or expired")
    return api_key


@router.get("/profile", response_model=PortalProfileOut)
def get_profile(
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> PortalProfileOut:
    return PortalProfileOut.model_validate(portal_service.profile(session, api_key))


@router.get("/dashboard", response_model=PortalDashboardOut)
def get_dashboard(
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> PortalDashboardOut:
    return PortalDashboardOut.model_validate(
        portal_service.my_dashboard(session, api_key)
    )


@router.get("/requests", response_model=RequestPage)
def list_my_requests(
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> RequestPage:
    rows, total = portal_service.my_requests(
        session,
        api_key,
        page=page,
        page_size=page_size,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return RequestPage(
        items=[RequestListItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/requests/{request_id}", response_model=RequestDetailOut)
def get_my_request(
    request_id: UUID,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> RequestDetailOut:
    row = portal_service.my_request_row(session, api_key, request_id)
    detail = RequestDetailOut.model_validate(row)
    detail.thinking = admin_service.thinking_text(row)
    return detail


@router.get("/usage/summary", response_model=UsageSummaryOut)
def my_usage(
    group_by: Literal["key", "model", "day"] = "day",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> UsageSummaryOut:
    rows = portal_service.my_usage_summary(
        session, api_key, group_by, date_from, date_to
    )
    return UsageSummaryOut(
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
        rows=[
            UsageSummaryRow(
                group=r.group,
                group_label=r.group_label,
                request_count=r.request_count,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                search_count=r.search_count,
                cost_cents=r.cost_cents,
            )
            for r in rows
        ],
        chart=UsageChart(
            categories=[r.group_label for r in rows],
            series=[
                UsageChartSeries(
                    name="费用(分)",
                    data=[float(r.cost_cents) for r in rows],
                ),
                UsageChartSeries(
                    name="请求数",
                    data=[r.request_count for r in rows],
                ),
            ],
        ),
    )


# ---- 对话（持久化 + 分叉） ----


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> list[ConversationOut]:
    return [
        ConversationOut.model_validate(r)
        for r in conversation_service.list_conversations(session, api_key)
    ]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    payload: ConversationCreateRequest | None = None,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> ConversationOut:
    return ConversationOut.model_validate(
        conversation_service.create_conversation(
            session, api_key, payload.title if payload else None
        )
    )


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationDetailOut
)
def get_conversation(
    conversation_id: UUID,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> ConversationDetailOut:
    row, messages = conversation_service.get_messages(session, api_key, conversation_id)
    return ConversationDetailOut(
        conversation=ConversationOut.model_validate(row),
        messages=[ConversationMessageOut.model_validate(m) for m in messages],
    )


@router.post(
    "/conversations/{conversation_id}/fork",
    response_model=ConversationOut,
    status_code=201,
)
def fork_conversation(
    conversation_id: UUID,
    payload: ConversationForkRequest | None = None,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> ConversationOut:
    forked = conversation_service.fork_conversation(
        session, api_key, conversation_id, payload.at_seq if payload else None
    )
    return ConversationOut.model_validate(forked)


@router.post("/conversations/{conversation_id}/chat")
async def conversation_chat(
    conversation_id: UUID,
    payload: ConversationChatRequest,
    request: Request,
    api_key: ApiKey = Depends(require_holder),
    session: Session = Depends(session_scope),
) -> Response:
    """对话内流式聊天：服务端拼历史 + 新输入，结束后自动追加两条消息。"""
    from ask_gateway.schemas.chat import ChatCompletionRequest

    runtime: ChatRuntime = request.app.state.chat_runtime
    conversation, history = conversation_service.get_messages(
        session, api_key, conversation_id
    )
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": payload.content})
    chat_payload = ChatCompletionRequest(
        model=payload.model,
        messages=messages,  # type: ignore[arg-type]
        stream=True,
        enable_web_search=payload.enable_web_search,
    )
    request_id = uuid.uuid4()
    inner = sse_chat(
        runtime,
        api_key=api_key,
        payload=chat_payload,
        request_id=request_id,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        is_disconnected=request.is_disconnected,
    )

    async def wrapper():
        # 对话记录统一由 execute_chat 收尾挂载（API 与 chatbot 同一路径），
        # 断连兜底也在那里完成
        async for frame in inner:
            yield frame

    return EventSourceResponse(wrapper(), headers={"X-Request-Id": str(request_id)})

