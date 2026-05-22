"""
OAP KeyRing — 分身密钥环

统一封装分身所有密钥的派生、使用与序列化。
从一个助记词出发，派生五类子密钥：
  identity   → Ed25519   身份签名
  memory     → AES-256   记忆数据加密
  weight     → AES-256   模型权重加密
  comm       → X25519    通信密钥协商
  emergency  → Ed25519   紧急控制
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization


# ── 助记词生成 ─────────────────────────────────────────────

_WORDLIST = [
    "apple", "brave", "cloud", "dream", "earth", "flame", "grace", "heart",
    "ivory", "jewel", "karma", "light", "magic", "noble", "ocean", "peace",
    "quest", "river", "solar", "trust", "unity", "valor", "water", "xenon",
    "yield", "zephyr", "amber", "blaze", "coral", "dawn", "ember", "frost",
]


def generate_mnemonic(word_count: int = 12) -> str:
    """生成随机助记词（使用 secrets 模块保证密码学安全）"""
    import secrets
    return " ".join(secrets.choice(_WORDLIST) for _ in range(word_count))


# ── 核心密钥环 ─────────────────────────────────────────────

class KeyRing:
    """
    分身密钥环

    所有密钥在内存中驻留，不持久化到磁盘（助记词才是持久化的凭证）。
    建议在需要时从助记词重建，用完即弃，减少内存暴露时间。
    """

    def __init__(
        self,
        avatar_did: str,
        identity_priv: Ed25519PrivateKey,
        memory_key: bytes,
        weight_key: bytes,
        comm_priv: X25519PrivateKey,
        emergency_priv: Ed25519PrivateKey,
    ):
        self.avatar_did = avatar_did
        self._identity_priv = identity_priv
        self._memory_key = memory_key
        self._weight_key = weight_key
        self._comm_priv = comm_priv
        self._emergency_priv = emergency_priv

        # 缓存公钥
        self.identity_pub = identity_priv.public_key()
        self.comm_pub = comm_priv.public_key()
        self.emergency_pub = emergency_priv.public_key()

    # ── 工厂方法 ──────────────────────────────────────────

    @classmethod
    def from_mnemonic(cls, mnemonic: str, avatar_did: str, passphrase: str = "") -> "KeyRing":
        """从助记词派生完整密钥环（确定性，相同输入总是产生相同输出）"""
        seed = hashlib.pbkdf2_hmac(
            "sha512",
            mnemonic.strip().encode(),
            f"oap:{passphrase}".encode(),
            iterations=210_000,  # OWASP 2023 推荐值
        )
        info = avatar_did.encode()

        def _derive(label: str, length: int = 32) -> bytes:
            return HKDF(hashes.SHA256(), length, label.encode(), info).derive(seed)

        return cls(
            avatar_did=avatar_did,
            identity_priv=Ed25519PrivateKey.from_private_bytes(_derive("oap-identity")),
            memory_key=_derive("oap-memory"),
            weight_key=_derive("oap-weight"),
            comm_priv=X25519PrivateKey.from_private_bytes(_derive("oap-comm")),
            emergency_priv=Ed25519PrivateKey.from_private_bytes(_derive("oap-emergency")),
        )

    @staticmethod
    def did_from_mnemonic(mnemonic: str) -> str:
        """从助记词生成确定性的 Avatar DID"""
        seed = hashlib.pbkdf2_hmac("sha512", mnemonic.strip().encode(), b"oap:did", 100_000)
        avatar_id = hashlib.sha256(seed).hexdigest()
        return f"did:oap:local:0x{avatar_id[:40]}"

    # ── 加解密 ────────────────────────────────────────────

    def encrypt(self, plaintext: bytes, key: Optional[bytes] = None) -> dict:
        """AES-256-GCM 加密，默认使用记忆加密密钥"""
        k = key or self._memory_key
        iv = os.urandom(12)
        ciphertext_with_tag = AESGCM(k).encrypt(iv, plaintext, None)
        return {
            "iv": iv.hex(),
            "ct": ciphertext_with_tag[:-16].hex(),
            "tag": ciphertext_with_tag[-16:].hex(),
        }

    def decrypt(self, blob: dict, key: Optional[bytes] = None) -> bytes:
        """AES-256-GCM 解密"""
        k = key or self._memory_key
        iv = bytes.fromhex(blob["iv"])
        ct_tag = bytes.fromhex(blob["ct"]) + bytes.fromhex(blob["tag"])
        return AESGCM(k).decrypt(iv, ct_tag, None)

    def encrypt_weight(self, weight_bytes: bytes) -> dict:
        """加密模型权重（使用专用权重密钥）"""
        return self.encrypt(weight_bytes, self._weight_key)

    def decrypt_weight(self, blob: dict) -> bytes:
        """解密模型权重"""
        return self.decrypt(blob, self._weight_key)

    def derive_shard_key(self, shard_id: str) -> bytes:
        """为每个记忆分片派生独立密钥"""
        return HKDF(hashes.SHA256(), 32, b"oap-shard",
                    f"{self.avatar_did}:{shard_id}".encode()).derive(self._memory_key)

    # ── 签名 / 验签 ───────────────────────────────────────

    def sign(self, data: bytes) -> str:
        """用身份私钥签名，返回十六进制字符串"""
        return self._identity_priv.sign(data).hex()

    def verify(self, data: bytes, sig_hex: str) -> bool:
        """验证身份签名"""
        try:
            self.identity_pub.verify(bytes.fromhex(sig_hex), data)
            return True
        except Exception:
            return False

    # ── 公钥导出 ──────────────────────────────────────────

    def public_info(self) -> dict:
        """导出可公开的公钥信息"""
        def _raw(key) -> str:
            return key.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ).hex()

        return {
            "avatarDID": self.avatar_did,
            "identityPubKey": _raw(self.identity_pub),
            "commPubKey": _raw(self.comm_pub),
            "emergencyPubKey": _raw(self.emergency_pub),
        }

    def __repr__(self) -> str:
        return f"KeyRing(did={self.avatar_did})"
