"""应用入口：Settings → 引擎/会话工厂/配额/DSH 池/FastAPI 组装。

lifespan 只承载常驻职责：建目录、池预热与 reaper、优雅关停。
一次性任务（迁移、定价种子）绝不放这里，分别走 alembic 与 scripts/seed_pricing.py。
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from ask_gateway.config import Settings, get_settings
from ask_gateway.db.session import get_engine, get_session_factory
from ask_gateway.logging_conf import configure_logging, get_logger
from ask_gateway.runtime_pool.dsh_runner import DshRunner, DshRunnerConfig
from ask_gateway.runtime_pool.pool import RuntimePool
from ask_gateway.runtime_pool.runner import Runner
from ask_gateway.server.app import create_app
from ask_gateway.services import upstream_key_service
from ask_gateway.services.chat_service import ChatRuntime
from ask_gateway.services.quota_service import QuotaService
from ask_gateway.services.secret_box import SecretBox, build_secret_box

log = get_logger(component="bootstrap")

_STATIC_DIST = Path(__file__).parent / "server" / "static" / "dist"


class SPAMount(StaticFiles):
    """SPA 兜底：静态资源 404 时回退 index.html，交给前端路由处理。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


@dataclasses.dataclass(frozen=True, slots=True)
class UpstreamControl:
    """管理端调整上游 Key 后重建池分组所需的全部句柄。"""

    settings: Settings
    session_factory: sessionmaker
    box: SecretBox

    def active_labels(self) -> list[str]:
        with self.session_factory() as session:
            return upstream_key_service.active_pool_labels(session)

    def reconcile_pool(self, pool: RuntimePool) -> list[str]:
        labels = self.active_labels()
        pool.reconfigure(labels)
        return labels

    def runner_factory(self, label: str) -> Runner:
        settings = self.settings
        with self.session_factory() as session:
            upstream_key = upstream_key_service.plaintext_for_label(session, self.box, label)
        if upstream_key is None:
            raise RuntimeError(f"上游 Key 不可用（label={label}）：未配置或已停用")
        return DshRunner(
            DshRunnerConfig(
                upstream_key_label=label,
                upstream_key=upstream_key,
                dsh_home=settings.dsh_home_root / label,
                workspace=settings.dsh_workspace_root / label,
                profile=settings.dsh_profile,
                provider=settings.upstream_provider,
                model=settings.upstream_model,
                base_url=settings.upstream_base_url,
                dsh_bin=settings.dsh_bin,
                patches=(),
                initialize_timeout_seconds=settings.dsh_initialize_timeout_seconds,
                request_timeout_seconds=settings.request_timeout_seconds,
            )
        )


def bootstrap_upstream_keys(
    settings: Settings, session_factory: sessionmaker, box: SecretBox
) -> list[str]:
    """启动时确定池分组：env UPSTREAM_KEYS 仅作首次引导，此后以 DB 为准。"""
    with session_factory() as session:
        if settings.upstream_keys:
            seeded = upstream_key_service.seed_from_env_once(session, box, settings.upstream_keys)
            if seeded:
                log.info("seeded upstream keys from env (one-time)", count=seeded)
            session.commit()
        labels = upstream_key_service.active_pool_labels(session)
    if not labels:
        log.warning("上游 Key 未配置：管理面可用，问答请求将在 runtime 启动时失败")
    return labels


def create_application(settings: Settings) -> FastAPI:
    engine = get_engine()
    session_factory = get_session_factory()
    box = build_secret_box(settings)

    control = UpstreamControl(settings=settings, session_factory=session_factory, box=box)
    labels = bootstrap_upstream_keys(settings, session_factory, box)
    pool = RuntimePool(
        runner_factory=control.runner_factory,
        labels=labels or ["upstream-unconfigured"],
        # 未配置上游 Key 时不预热线程，避免启动即拉起无效子进程
        min_size=settings.pool_min_size if labels else 0,
        max_size=settings.pool_max_size,
        queue_maxsize=settings.pool_queue_maxsize,
        acquire_timeout=settings.pool_acquire_timeout_seconds,
        idle_ttl_seconds=settings.pool_idle_ttl_seconds,
        reaper_interval_seconds=settings.pool_reaper_interval_seconds,
        circuit_break_seconds=settings.upstream_circuit_break_seconds,
    )
    quota = QuotaService(
        default_rpm=settings.default_rpm,
        default_daily_budget_cents=settings.default_daily_budget_cents,
        default_concurrency=settings.default_concurrency,
    )
    runtime = ChatRuntime(
        session_factory=session_factory,
        quota=quota,
        pool=pool,
        allowed_models=tuple(settings.allowed_models),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        log.info(
            "starting runtime pool",
            labels=labels,
            min_size=settings.pool_min_size if labels else 0,
            max_size=settings.pool_max_size,
        )
        pool.start()
        try:
            yield
        finally:
            log.info("shutting down runtime pool", grace=settings.shutdown_grace_seconds)
            pool.shutdown(grace_seconds=settings.shutdown_grace_seconds)
            engine.dispose()

    app = create_app(
        chat_runtime=runtime,
        admin_password=settings.admin_password,
        admin_session_ttl_hours=settings.admin_session_ttl_hours,
        lifespan=lifespan,
        upstream_control=control,
        pool=pool,
    )
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    # 路由按注册顺序匹配：/v1、/admin、/healthz、/docs 已先注册，不会被静态挂载吞掉
    if not _STATIC_DIST.is_dir():
        log.warning("前端构建产物缺失，SPA 未挂载", dist=str(_STATIC_DIST))
        return
    app.mount("/", SPAMount(directory=str(_STATIC_DIST), html=True), name="spa")
    log.info("SPA mounted", dist=str(_STATIC_DIST))


def main() -> None:
    settings = get_settings()
    configure_logging(log_json=settings.log_json, level=settings.log_level)
    uvicorn.run(app, host=settings.host, port=settings.port)


# 供 `uvicorn ask_gateway.main:app` 直接引用；池构造不启动子进程，start 只在 lifespan 发生
app = create_application(get_settings())
