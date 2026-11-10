#!/usr/bin/env python3
"""ask-gateway 最小调用示例（只用标准库，无需装 SDK）。

用法：
    export ASK_API_KEY=sk-ask-...          # 平台签发的 Key（必填）
    export ASK_BASE_URL=http://127.0.0.1:8000   # 可选，默认本机 8000

    python examples/call_api.py "你好"
    python examples/call_api.py --stream "今天有什么科技新闻？"
    python examples/call_api.py --no-web "只回答两个字：收到"
    python examples/call_api.py --model deepseek-v4-pro "解释一下什么是 SSE"

平台扩展参数（OpenAI 形状之外）：
    enable_web_search  bool   默认 true，审计库记录该请求意图
    thinking_effort    str    可选，审计库记录该请求意图
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get("ASK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _post(path: str, api_key: str, body: dict[str, object]) -> urllib.request.addinfourl:
    # 走本机网关，显式绕过系统/环境代理（macOS 上常配了 Clash 之类）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"{DEFAULT_BASE_URL}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if body.get("stream") else "application/json",
        },
        method="POST",
    )
    return opener.open(request, timeout=600)


def call_once(api_key: str, body: dict[str, object]) -> None:
    with _post("/v1/chat/completions", api_key, body) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    message = payload["choices"][0]["message"]
    if message.get("reasoning_content"):
        print("[thinking]")
        print(message["reasoning_content"].strip())
        print("[/thinking]")
    print(message["content"])
    print(f"\n--- model={payload['model']} request_id={resp.headers.get('X-Request-Id')}")
    print(f"--- usage={json.dumps(payload['usage'], ensure_ascii=False)}")


def call_stream(api_key: str, body: dict[str, object]) -> None:
    """逐帧解析 SSE：data-only 帧是 OpenAI chunk，event: x-dsh-event 是 DSH 原始事件。"""
    body["stream"] = True
    thinking_open = False
    with _post("/v1/chat/completions", api_key, body) as resp:
        print(f"--- request_id={resp.headers.get('X-Request-Id')}")
        event = ""
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                event = ""
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            if event:  # x-request-id / x-dsh-event 等网关事件，业务侧可忽略
                continue
            chunk = json.loads(data)
            if not chunk.get("choices"):
                usage = chunk.get("usage")
                if usage:
                    print(f"\n--- usage={json.dumps(usage, ensure_ascii=False)}")
                continue
            delta = chunk["choices"][0].get("delta") or {}
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            if reasoning:
                if not thinking_open:
                    print("[thinking] ", end="", flush=True)
                    thinking_open = True
                print(reasoning, end="", flush=True)
            if content:
                if thinking_open:
                    print("\n[/thinking]\n", end="")
                    thinking_open = False
                print(content, end="", flush=True)
    if thinking_open:
        print("\n[/thinking]")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="ask-gateway /v1/chat/completions 调用示例")
    parser.add_argument("prompt", help="用户提问")
    parser.add_argument("--stream", action="store_true", help="使用 SSE 流式返回")
    parser.add_argument("--model", default="deepseek-flash", help="deepseek-flash / deepseek-v4-pro")
    parser.add_argument("--no-web", action="store_true", help="enable_web_search=false")
    parser.add_argument("--effort", default=None, help="thinking_effort，如 low / high")
    args = parser.parse_args()

    api_key = os.environ.get("ASK_API_KEY", "").strip()
    if not api_key:
        print("请先 export ASK_API_KEY=sk-ask-...", file=sys.stderr)
        return 2

    body: dict[str, object] = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "enable_web_search": not args.no_web,
    }
    if args.effort:
        body["thinking_effort"] = args.effort

    try:
        call_stream(api_key, body) if args.stream else call_once(api_key, body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"连接失败（服务是否在 {DEFAULT_BASE_URL} 运行？）：{exc.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
