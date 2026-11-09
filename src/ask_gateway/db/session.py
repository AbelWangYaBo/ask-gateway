"""引擎与会话工厂。同步会话：DSH SDK 本身是阻塞式 JSON-RPC，全链路同步更简单可靠。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ask_gateway.config import get_settings
from ask_gateway.db.base import json_dumps_utf8

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # sqlite 需要 check_same_thread=False 才能跨线程池使用
        return {
            "connect_args": {"check_same_thread": False},
            "future": True,
            "json_serializer": json_dumps_utf8,
        }
    # pg 连接池默认 QueuePool；池大小与 runtime 池解耦，给管理面留余量
    return {
        "pool_size": 10,
        "max_overflow": 20,
        "future": True,
        "json_serializer": json_dumps_utf8,
    }


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def session_scope() -> Iterator[Session]:
    """FastAPI 依赖：每请求一个会话，异常回滚。"""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
