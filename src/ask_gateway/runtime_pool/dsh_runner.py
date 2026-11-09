"""DshRunner：把官方 deepseek-harness-sdk 包进 Runner 协议。

一个实例对应一个长驻 DSH 子进程；上游 Key 经 env 注入，只存在于子进程环境，
不进审计库、不进应用日志。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from deepseek_harness.errors import (
    JsonRpcError,
    SdkProtocolError,
    TransportClosedError,
)

from ask_gateway.runtime_pool.errors import (
    RunnerBroken,
    RunnerError,
    RunnerTimeout,
)
from ask_gateway.runtime_pool.events import EventProjector, StoredEvent
from ask_gateway.runtime_pool.runner import RunOutcome, build_outcome


@dataclass(frozen=True, slots=True)
class DshRunnerConfig:
    upstream_key_label: str
    upstream_key: str
    dsh_home: Path
    workspace: Path
    profile: str
    provider: str
    model: str
    base_url: str | None
    dsh_bin: str | None
    patches: tuple[str, ...]
    initialize_timeout_seconds: float
    request_timeout_seconds: float | None


class DshRunner:
    def __init__(self, config: DshRunnerConfig) -> None:
        self.upstream_key_label = config.upstream_key_label
        self._config = config
        self._harness: DeepSeekHarness | None = None
        self._broken = False

    def start(self) -> None:
        if self._harness is not None:
            return
        config = self._config
        config.dsh_home.mkdir(parents=True, exist_ok=True)
        config.workspace.mkdir(parents=True, exist_ok=True)
        self._harness = DeepSeekHarness(
            DeepSeekHarnessConfig(
                provider=config.provider,
                model=config.model,
                profile=config.profile,
                dsh_bin=config.dsh_bin,
                patches=config.patches,
                dsh_home=str(config.dsh_home),
                cwd=str(config.workspace),
                runtime_cwd=str(config.workspace),
                env={"DEEPSEEK_API_KEY": config.upstream_key},
                api_key=config.upstream_key,
                base_url=config.base_url,
                initialize_timeout_seconds=config.initialize_timeout_seconds,
                request_timeout_seconds=config.request_timeout_seconds,
            )
        )
        self._harness.start()

    def health_alive(self) -> bool:
        return self._harness is not None and not self._broken

    def run(
        self,
        messages: list[dict],
        *,
        session_id: str | None = None,
        on_event: Callable[[StoredEvent], None] | None = None,
    ) -> RunOutcome:
        if self._broken:
            raise RunnerBroken("runner marked broken; pool must recycle it")
        self.start()
        assert self._harness is not None

        owned_session_id = session_id or f"askgw-{uuid.uuid4().hex}"
        projector = EventProjector()

        def on_notification(notification: object) -> None:
            # Notification 是 slots dataclass（method/payload），鸭子取值即可
            method = getattr(notification, "method", None)
            if method != "session.event":
                return
            payload = getattr(notification, "payload", {})
            if payload.get("sessionId") != owned_session_id:
                return
            event = payload.get("event")
            if isinstance(event, dict):
                for stored in projector.ingest_raw(event):
                    if on_event is not None:
                        on_event(stored)

        try:
            result = self._harness.run(
                messages,
                session_id=owned_session_id,
                on_notification=on_notification,
            )
        except TimeoutError as exc:  # SDK 请求超时抛内置 TimeoutError
            raise RunnerTimeout(str(exc)) from exc
        except TransportClosedError as exc:
            self._broken = True
            raise RunnerBroken(f"runtime transport closed: {exc}") from exc
        except SdkProtocolError as exc:
            self._broken = True
            raise RunnerBroken(f"runtime protocol error: {exc}") from exc
        except JsonRpcError as exc:
            # 进程与协议仍健康，句柄可复用
            raise RunnerError(f"upstream json-rpc error: {exc.message}") from exc

        return build_outcome(
            session_id=result.session_id,
            raw_events=result.events,
            final_response=result.final_response,
            finish_reason=result.finish_reason,
            projector=projector,
        )

    def close(self) -> None:
        harness = self._harness
        self._harness = None
        if harness is not None:
            harness.close()
