"""门户对话服务：持久化、追加、从任意点分叉。

对话是线性消息序列；「从 c 点继续」= fork（复制截断点之前的消息成
新对话，标记来源），可对同一点反复分叉出 c-d / c-e / c-f 多条平行分支。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ask_gateway.models.api_key import ApiKey
from ask_gateway.models.conversation import Conversation, ConversationMessage
from ask_gateway.services.errors import ServiceError


class ConversationNotFoundError(ServiceError):
    http_status = 404

    def __init__(self) -> None:
        super().__init__("conversation_not_found", "对话不存在")


def _get_owned(session: Session, api_key: ApiKey, conversation_id: uuid.UUID) -> Conversation:
    row = session.get(Conversation, conversation_id)
    if row is None or row.holder_name != api_key.holder_name:
        raise ConversationNotFoundError()
    return row


def list_conversations(session: Session, api_key: ApiKey) -> list[Conversation]:
    return list(
        session.scalars(
            select(Conversation)
            .where(Conversation.holder_name == api_key.holder_name)
            .order_by(Conversation.updated_at.desc())
        )
    )


def create_conversation(
    session: Session, api_key: ApiKey, title: str | None = None
) -> Conversation:
    row = Conversation(holder_name=api_key.holder_name, title=(title or "新对话")[:200])
    session.add(row)
    session.flush()
    return row


def get_messages(
    session: Session, api_key: ApiKey, conversation_id: uuid.UUID
) -> tuple[Conversation, list[ConversationMessage]]:
    row = _get_owned(session, api_key, conversation_id)
    return row, list(row.messages)



def append_messages(
    session: Session,
    conversation: Conversation,
    *,
    user_content: str,
    assistant_content: str,
    assistant_reasoning: str | None,
    request_id: uuid.UUID | None,
) -> list[ConversationMessage]:
    """一次对话轮次追加 user + assistant 两条消息；首条消息时以提问摘要为标题。"""
    next_seq = (conversation.messages[-1].seq + 1) if conversation.messages else 1
    user_msg = ConversationMessage(
        conversation_id=conversation.id, seq=next_seq, role="user", content=user_content
    )
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        seq=next_seq + 1,
        role="assistant",
        content=assistant_content,
        reasoning=assistant_reasoning,
        request_id=request_id,
    )
    session.add_all([user_msg, assistant_msg])
    if conversation.title == "新对话" and user_content.strip():
        conversation.title = user_content.strip().replace("\n", " ")[:50]
    session.flush()
    return [user_msg, assistant_msg]


def fork_conversation(
    session: Session,
    api_key: ApiKey,
    conversation_id: uuid.UUID,
    at_seq: int | None = None,
) -> Conversation:
    """从 at_seq（含）之前截断复制出新对话；缺省取末条消息之后（整体复制）。

    a-b-c 对话 fork 两次即得「基于 c 点」的两条平行分支。
    """
    source, messages = get_messages(session, api_key, conversation_id)
    if at_seq is None:
        kept = messages
    else:
        kept = [m for m in messages if m.seq <= at_seq]
        if not kept:
            raise ServiceError(400, "invalid_fork_point", "分叉点之前没有任何消息")
    forked = Conversation(
        holder_name=api_key.holder_name,
        title=f"{source.title} · 分叉",
        forked_from_id=source.id,
        fork_point_seq=kept[-1].seq if kept else None,
    )
    session.add(forked)
    session.flush()
    for index, message in enumerate(kept, start=1):
        session.add(
            ConversationMessage(
                conversation_id=forked.id,
                seq=index,
                role=message.role,
                content=message.content,
                reasoning=message.reasoning,
                request_id=message.request_id,
            )
        )
    session.flush()
    return forked


def attach_request(
    session: Session,
    *,
    holder_name: str,
    messages: list[dict],
    assistant_content: str,
    assistant_reasoning: str | None,
    request_id: uuid.UUID,
) -> Conversation | None:
    """把一次完成的请求归入对话体系（API 与 chatbot 共用同一路径）。

    归并规则：请求 messages 的 user/assistant 前缀与某对话现有消息序列
    完全一致 → 续写该对话；否则新建对话（以末条 user 消息开头）。
    """
    qa = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    user_inputs = [m for m in qa if m["role"] == "user"]
    if not user_inputs:
        return None
    new_user_text = str(user_inputs[-1]["content"])

    expected = [(m["role"], str(m["content"])) for m in qa[:-1]]
    candidates = list(
        session.scalars(
            select(Conversation)
            .where(Conversation.holder_name == holder_name)
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        )
    )
    for conversation in candidates:
        existing = [(m.role, m.content) for m in conversation.messages]
        if existing == expected:
            append_messages(
                session,
                conversation,
                user_content=new_user_text,
                assistant_content=assistant_content,
                assistant_reasoning=assistant_reasoning,
                request_id=request_id,
            )
            return conversation

    conversation = Conversation(holder_name=holder_name)
    session.add(conversation)
    session.flush()
    append_messages(
        session,
        conversation,
        user_content=new_user_text,
        assistant_content=assistant_content,
        assistant_reasoning=assistant_reasoning,
        request_id=request_id,
    )
    return conversation
