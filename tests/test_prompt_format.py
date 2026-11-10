"""OpenAI messages → DSH prompt 文本转换。"""

from __future__ import annotations

import pytest

from ask_gateway.runtime_pool.dsh_runner import to_prompt_text
from ask_gateway.runtime_pool.errors import RunnerError


def test_single_user_message_appends_assistant_hint() -> None:
    out = to_prompt_text([{"role": "user", "content": "今天有什么科技新闻？"}])
    assert out == "User: 今天有什么科技新闻？\n\nAssistant:"


def test_system_and_history_transcript() -> None:
    out = to_prompt_text(
        [
            {"role": "system", "content": "你是问答助手"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]
    )
    assert out == (
        "[system instructions]\n你是问答助手"
        "\n\nUser: 第一问"
        "\n\nAssistant: 第一答"
        "\n\nUser: 第二问"
        "\n\nAssistant:"
    )


def test_multipart_content_takes_text_parts_only() -> None:
    out = to_prompt_text(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看这张图"},
                    {"type": "image_url", "image_url": {"url": "http://x"}},
                ],
            }
        ]
    )
    assert out == "User: 看这张图\n\nAssistant:"


def test_empty_prompt_raises() -> None:
    with pytest.raises(RunnerError, match="empty prompt"):
        to_prompt_text([{"role": "user", "content": ""}])
    with pytest.raises(RunnerError, match="empty prompt"):
        to_prompt_text([])
