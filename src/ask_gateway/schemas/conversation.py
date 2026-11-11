"""门户对话 schema。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    forked_from_id: UUID | None
    fork_point_seq: int | None
    created_at: datetime
    updated_at: datetime


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationForkRequest(BaseModel):
    # 含该序号之前的消息被复制进新对话；缺省 = 全量（末点分叉）
    at_seq: int | None = Field(default=None, ge=1)


class ConversationMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    role: str
    content: str
    reasoning: str | None
    request_id: UUID | None
    created_at: datetime


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[ConversationMessageOut]


class ConversationChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32_000)
    model: str = Field(default="deepseek-flash", max_length=64)
    enable_web_search: bool = True
