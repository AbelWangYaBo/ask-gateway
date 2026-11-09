"""安全原语：Key/Token 生成、SHA-256 哈希、PBKDF2 口令校验。

本平台 Key 明文只在创建响应里出现一次，库里只存 SHA-256 hex。
管理员口令来自环境变量，支持明文（常量时间比较）或 pbkdf2 哈希两种写法。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "sk-ask-"
_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 200_000


def generate_api_key() -> str:
    """生成新平台 Key：sk-ask- + 32 个 hex 字符。"""
    return f"{KEY_PREFIX}{secrets.token_hex(16)}"


def key_prefix_of(plaintext: str) -> str:
    """库里保存的展示前缀（VARCHAR(16)），用于控制台辨认而不暴露明文。"""
    return plaintext[:16]


def hash_key(plaintext: str) -> str:
    """平台 Key 的 SHA-256 hex，作为唯一查找键。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """管理员登录会话 token（明文只下发一次，库存哈希）。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希，格式 pbkdf2_sha256$iterations$salt_hex$digest_hex。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。stored 为 pbkdf2 串走 PBKDF2，否则按环境变量明文常量时间比较。"""
    if not stored:
        return False
    if stored.startswith(f"{_PBKDF2_ALGO}$"):
        parts = stored.split("$")
        if len(parts) != 4:
            raise ValueError("malformed pbkdf2 password string")
        _, iterations_str, salt_hex, digest_hex = parts
        try:
            iterations = int(iterations_str)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
        except ValueError as exc:
            raise ValueError("malformed pbkdf2 password string") from exc
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    return hmac.compare_digest(password, stored)
