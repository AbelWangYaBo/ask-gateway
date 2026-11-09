"""安全原语：Key/Token 生成、SHA-256 哈希、PBKDF2 口令校验。"""

from __future__ import annotations

import pytest

from ask_gateway.services.security import (
    KEY_PREFIX,
    generate_api_key,
    generate_token,
    hash_key,
    hash_password,
    hash_token,
    key_prefix_of,
    verify_password,
)


def test_generate_api_key_format_and_uniqueness() -> None:
    key1 = generate_api_key()
    key2 = generate_api_key()
    assert key1.startswith(KEY_PREFIX)
    assert key1 != key2
    # sk-ask- (7) + 32 hex
    assert len(key1) == 39
    assert all(c in "0123456789abcdef" for c in key1.removeprefix(KEY_PREFIX))


def test_key_prefix_is_16_chars() -> None:
    key = generate_api_key()
    prefix = key_prefix_of(key)
    assert len(prefix) == 16
    assert key.startswith(prefix)


def test_hash_key_deterministic_sha256_hex() -> None:
    key = generate_api_key()
    digest = hash_key(key)
    assert len(digest) == 64
    assert int(digest, 16)  # 合法 hex
    assert digest == hash_key(key)
    assert hash_key(key) != hash_key(generate_api_key())


def test_hash_token_deterministic() -> None:
    token = generate_token()
    assert len(token) >= 32
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64


def test_password_pbkdf2_roundtrip() -> None:
    stored = hash_password("s3cret-口令")
    assert stored.startswith("pbkdf2_sha256$200000$")
    assert "s3cret" not in stored
    assert verify_password("s3cret-口令", stored) is True
    assert verify_password("wrong", stored) is False


def test_verify_password_supports_plaintext_env() -> None:
    # ADMIN_PASSWORD 环境变量直接给明文时走常量时间比较
    assert verify_password("env-plain", "env-plain") is True
    assert verify_password("env-plain", "other") is False


def test_verify_password_rejects_empty() -> None:
    assert verify_password("anything", "") is False
    assert verify_password("", "") is False


def test_pbkdf2_salt_makes_hashes_distinct() -> None:
    assert hash_password("same") != hash_password("same")
    # 两个哈希都能验过
    assert verify_password("same", hash_password("same"))


def test_tampered_pbkdf2_string() -> None:
    stored = hash_password("ok")
    # 摘要被改但仍是合法 hex：校验失败而非通过
    assert verify_password("ok", stored + "deadbeef") is False
    # 结构损坏：非法 hex / 段数不对
    with pytest.raises(ValueError):
        verify_password("ok", "pbkdf2_sha256$200000$zz$aa")
    with pytest.raises(ValueError):
        verify_password("ok", "pbkdf2_sha256$200000$onlythreeparts")
