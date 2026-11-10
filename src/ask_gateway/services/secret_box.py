"""上游 Key 加密存储原语：AES-GCM 加解密与明文指纹。

主密钥解析顺序：
1. 环境变量 SECRETS_MASTER_KEY（base64 的 32 字节）；
2. 缺省时自动生成并落盘 data/secrets/master_key（0600），下次启动复用。
密文格式 base64(nonce(12) + ciphertext)，指纹为 SHA-256 前 16 hex，
preview 为前 6 后 4 的展示形态——三者配合实现「密文可存、明文可比对」。
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets as pysecrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ask_gateway.config import Settings

MASTER_KEY_BYTES = 32
NONCE_BYTES = 12


class SecretBoxError(RuntimeError):
    """主密钥缺失/格式错误或密文损坏。"""


def _load_or_create_master_key(settings: Settings) -> bytes:
    env_value = os.environ.get("SECRETS_MASTER_KEY", "").strip()
    if env_value:
        try:
            key = base64.b64decode(env_value, validate=True)
        except Exception as exc:  # noqa: BLE001 - 统一翻译为配置错误
            raise SecretBoxError("SECRETS_MASTER_KEY 不是合法 base64") from exc
        if len(key) != MASTER_KEY_BYTES:
            raise SecretBoxError(
                f"SECRETS_MASTER_KEY 必须是 {MASTER_KEY_BYTES} 字节（base64 编码后 44 字符）"
            )
        return key

    key_file = settings.data_dir / "secrets" / "master_key"
    if key_file.is_file():
        key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
        if len(key) != MASTER_KEY_BYTES:
            raise SecretBoxError(f"主密钥文件损坏：{key_file}")
        return key

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = pysecrets.token_bytes(MASTER_KEY_BYTES)
    key_file.write_text(base64.b64encode(key).decode("ascii"), encoding="utf-8")
    key_file.chmod(0o600)
    return key


class SecretBox:
    """进程内持有一份主密钥；加解密均为纯函数式无状态调用。"""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != MASTER_KEY_BYTES:
            raise SecretBoxError("master key 长度非法")
        self._aes = AESGCM(master_key)

    def encrypt(self, plaintext: str) -> str:
        nonce = pysecrets.token_bytes(NONCE_BYTES)
        sealed = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, encoded: str) -> str:
        try:
            raw = base64.b64decode(encoded, validate=True)
            nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
            return self._aes.decrypt(nonce, sealed, None).decode("utf-8")
        except SecretBoxError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretBoxError("密文损坏或主密钥不匹配") from exc

    @staticmethod
    def fingerprint(plaintext: str) -> str:
        """SHA-256 前 16 hex：与明文一一对应的比对指纹（非可逆）。"""
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def preview(plaintext: str) -> str:
        """人眼比对形态：前 6 后 4。"""
        if len(plaintext) <= 10:
            return plaintext[:2] + "…" if len(plaintext) > 2 else plaintext
        return f"{plaintext[:6]}…{plaintext[-4:]}"


def build_secret_box(settings: Settings) -> SecretBox:
    return SecretBox(_load_or_create_master_key(settings))
