"""8 张表的插入回查、关系级联与约束冒烟测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ask_gateway.models import (
    AdminAuditLog,
    AdminSession,
    ApiKey,
    EventRecord,
    PricingRule,
    RequestRecord,
    UpstreamKey,
    UsageRecord,
)
from ask_gateway.models.request import STATUS_QUEUED


def _make_api_key(session: Session, **kwargs: object) -> ApiKey:
    key = ApiKey(
        key_prefix="sk-ask-ab12",
        key_hash="a" * 64,
        holder_name="user-a",
        label="个人 Key",
        **kwargs,
    )
    session.add(key)
    session.flush()
    return key


def _make_request(session: Session, api_key: ApiKey, **kwargs: object) -> RequestRecord:
    now = datetime.now(UTC)
    req = RequestRecord(
        api_key_id=api_key.id,
        holder_name=api_key.holder_name,
        key_label=api_key.label,
        model="deepseek-flash",
        stream=False,
        prompt_messages=[{"role": "user", "content": "今天有什么新闻？"}],
        queued_at=now,
        **kwargs,
    )
    session.add(req)
    session.flush()
    return req


def test_api_key_defaults_and_persistence(db_session: Session) -> None:
    key = _make_api_key(db_session)
    db_session.commit()

    fetched = db_session.get(ApiKey, key.id)
    assert fetched is not None
    assert fetched.status == "active"
    assert fetched.rpm_limit is None
    assert fetched.last_used_at is None
    assert fetched.created_at is not None
    assert fetched.key_hash == "a" * 64


def test_api_key_hash_unique(db_session: Session) -> None:
    _make_api_key(db_session)
    db_session.add(
        ApiKey(
            key_prefix="sk-ask-ab34",
            key_hash="a" * 64,
            holder_name="user-b",
            label="重复 hash",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_upstream_key_fingerprint_unique(db_session: Session) -> None:
    from ask_gateway.services.secret_box import SecretBox

    box = SecretBox(b"m" * 32)
    plain = "sk-model-test-upstream"
    row = UpstreamKey(
        label="主 Key",
        key_ciphertext=box.encrypt(plain),
        key_fingerprint=SecretBox.fingerprint(plain),
        key_preview=SecretBox.preview(plain),
    )
    db_session.add(row)
    db_session.flush()
    dup = UpstreamKey(
        label="重复",
        key_ciphertext=box.encrypt(plain),
        key_fingerprint=SecretBox.fingerprint(plain),
        key_preview=SecretBox.preview(plain),
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_request_defaults_and_json_roundtrip(db_session: Session) -> None:
    key = _make_api_key(db_session)
    req = _make_request(db_session, key)
    db_session.commit()

    fetched = db_session.scalar(select(RequestRecord).where(RequestRecord.id == req.id))
    assert fetched is not None
    assert fetched.status == STATUS_QUEUED
    assert fetched.search_count == 0
    assert fetched.total_cost_cents == Decimal("0.000")
    assert fetched.enable_web_search is True
    assert fetched.prompt_messages == [
        {"role": "user", "content": "今天有什么新闻？"}
    ]
    # 冗余快照与关系双向可达
    assert fetched.holder_name == "user-a"
    assert fetched.api_key.id == key.id


def test_events_ordering_and_seq_unique(db_session: Session) -> None:
    key = _make_api_key(db_session)
    req = _make_request(db_session, key)
    db_session.add_all(
        [
            EventRecord(request_id=req.id, seq=2, event_type="text", payload={"text": "后"}),
            EventRecord(request_id=req.id, seq=1, event_type="thinking", payload={"text": "先"}),
        ]
    )
    db_session.flush()

    assert [e.seq for e in req.events] == [1, 2]
    assert req.events[0].payload == {"text": "先"}

    db_session.add(
        EventRecord(request_id=req.id, seq=1, event_type="text", payload={"text": "撞 seq"})
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_events_cascade_delete_with_request(db_session: Session) -> None:
    key = _make_api_key(db_session)
    req = _make_request(db_session, key)
    db_session.add(EventRecord(request_id=req.id, seq=1, event_type="turn/start", payload={}))
    db_session.flush()
    event_id = req.events[0].id

    db_session.delete(req)
    db_session.commit()
    assert db_session.get(EventRecord, event_id) is None


def test_usage_records_and_cost(db_session: Session) -> None:
    key = _make_api_key(db_session)
    req = _make_request(db_session, key)
    now = datetime.now(UTC)
    db_session.add(
        UsageRecord(
            request_id=req.id,
            step=1,
            input_tokens=100,
            output_tokens=80,
            cache_read_tokens=10,
            cache_write_tokens=None,
            reasoning_tokens=20,
            complete=True,
            search_count=2,
            pricing_snapshot={"model": "deepseek-flash", "rule_name": "全时段"},
            cost_cents=Decimal("1.234"),
            created_at=now,
        )
    )
    db_session.commit()

    fetched = db_session.scalar(select(UsageRecord).where(UsageRecord.request_id == req.id))
    assert fetched is not None
    assert fetched.pricing_snapshot == {"model": "deepseek-flash", "rule_name": "全时段"}
    assert fetched.cost_cents == Decimal("1.234")
    assert fetched.complete is True
    assert req.usage_records[0].step == 1


def test_usage_records_cascade_delete(db_session: Session) -> None:
    key = _make_api_key(db_session)
    req = _make_request(db_session, key)
    db_session.add(
        UsageRecord(
            request_id=req.id,
            step=0,
            complete=False,
            pricing_snapshot={},
            created_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    usage_id = req.usage_records[0].id

    db_session.delete(req)
    db_session.commit()
    assert db_session.get(UsageRecord, usage_id) is None


def test_pricing_rule_nullable_window_and_decimals(db_session: Session) -> None:
    rule = PricingRule(
        model="deepseek-flash",
        rule_name="全时段默认",
        weekdays_only=False,
        is_active=True,
        effective_from=datetime.now(UTC),
        input_price_per_mtok=Decimal("200.0000"),
        output_price_per_mtok=Decimal("800.0000"),
        search_price_per_k=Decimal("50.0000"),
    )
    db_session.add(rule)
    db_session.commit()

    fetched = db_session.scalar(select(PricingRule).where(PricingRule.id == rule.id))
    assert fetched is not None
    assert fetched.peak_start_hour is None
    assert fetched.peak_end_hour is None
    assert fetched.input_price_per_mtok == Decimal("200.0000")
    assert fetched.cache_read_price_per_mtok is None


def test_admin_session_and_audit_log(db_session: Session) -> None:
    expires = datetime(2030, 1, 1, tzinfo=UTC)
    session_row = AdminSession(
        token_hash="b" * 64,
        expires_at=expires,
        client_ip="127.0.0.1",
        user_agent="pytest",
    )
    log = AdminAuditLog(
        action="key.create",
        target_type="api_key",
        target_id="dummy-id",
        detail={"holder_name": "user-a"},
        client_ip="127.0.0.1",
    )
    db_session.add_all([session_row, log])
    db_session.commit()

    fetched_session = db_session.scalar(select(AdminSession))
    fetched_log = db_session.scalar(select(AdminAuditLog))
    assert fetched_session is not None and fetched_session.expires_at == expires
    assert fetched_session.created_at is not None
    assert fetched_log is not None and fetched_log.detail == {"holder_name": "user-a"}
    assert fetched_log.created_at is not None


def test_admin_session_token_hash_unique(db_session: Session) -> None:
    for _ in range(2):
        db_session.add(
            AdminSession(
                token_hash="c" * 64,
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()
