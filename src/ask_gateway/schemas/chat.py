"""OpenAI 兼容 Chat Completion 的请求/响应 schema。

请求在 OpenAI 形状上扩展两个字段：enable_web_search、thinking_effort。
顶层未知字段静默忽略（兼容 OpenAI 客户端常带的 temperature 等参数）；
message 未知字段保留，审计库按原样存储。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    # 平台扩展字段
    enable_web_search: bool = True
    thinking_effort: str | None = None


class ResponseMessage(BaseModel):
    role: str = "assistant"
    content: str
    reasoning_content: str | None = None


class Choice(BaseModel):
    index: int = 0
    message: ResponseMessage
    finish_reason: str | None = None


class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    search_count: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "ask-gateway"


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody
