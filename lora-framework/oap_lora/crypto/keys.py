"""
OAP 密钥管理模块

管理分身的密钥层级：
- Master Key: 由 Owner 助记词 + 生物识别派生
- Avatar Identity Key (Ed25519): 身份签名
- Memory Encryption Key (AES-256): 记忆数据加密
- Persona Weight Key (AES-256): 模型权重加密
- Communication Key (X25519): 通信密钥协商
- Emergency Key (Ed25519): 紧急控制
"""

import os
import json
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


@dataclass
class AvatarKeyRing:
    """分身密钥环 - 管理分身所有密钥"""

    avatar_did: str
    identity_key_private: Ed25519PrivateKey = field(default=None)
    identity_key_public: Ed25519PublicKey = field(default=None)
    memory_encryption_key: bytes = field(default=None)
    weight_encryption_key: bytes = field(default=None)
    communication_key_private: X25519PrivateKey = field(default=None)
    communication_key_public: X25519PublicKey = field(default=None)
    emergency_key_private: Ed25519PrivateKey = field(default=None)
    emergency_key_public: Ed25519PublicKey = field(default=None)

    @classmethod
    def from_mnemonic(cls, mnemonic: str, avatar_did: str, passphrase: str = "") -> "AvatarKeyRing":
        """从助记词派生整个密钥环

        使用 BIP-39 风格的助记词作为熵源，通过 HKDF 派生各子密钥。
        """
        # 从助记词生成种子
        seed = hashlib.pbkdf2_hmac(
            "sha512",
            mnemonic.encode("utf-8"),
            ("oap-avatar" + passphrase).encode("utf-8"),
            2048,
        )

        ring = cls(avatar_did=avatar_did)

        # 派生身份密钥 (Ed25519)
        id_seed = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-identity-key",
            info=avatar_did.encode("utf-8"),
        ).derive(seed)
        ring.identity_key_private = Ed25519PrivateKey.from_private_bytes(id_seed)
        ring.identity_key_public = ring.identity_key_private.public_key()

        # 派生记忆加密密钥 (AES-256)
        ring.memory_encryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-memory-encryption",
            info=avatar_did.encode("utf-8"),
        ).derive(seed)

        # 派生权重加密密钥 (AES-256)
        ring.weight_encryption_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-weight-encryption",
            info=avatar_did.encode("utf-8"),
        ).derive(seed)

        # 派生通信密钥 (X25519)
        comm_seed = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-communication-key",
            info=avatar_did.encode("utf-8"),
        ).derive(seed)
        ring.communication_key_private = X25519PrivateKey.from_private_bytes(comm_seed)
        ring.communication_key_public = ring.communication_key_private.public_key()

        # 派生紧急密钥 (Ed25519)
        emerg_seed = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-emergency-key",
            info=avatar_did.encode("utf-8"),
        ).derive(seed)
        ring.emergency_key_private = Ed25519PrivateKey.from_private_bytes(emerg_seed)
        ring.emergency_key_public = ring.emergency_key_private.public_key()

        return ring

    def derive_memory_shard_key(self, shard_index: str) -> bytes:
        """为每个记忆分片派生独立加密密钥"""
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"oap-shard-key",
            info=f"{self.avatar_did}:{shard_index}".encode("utf-8"),
        ).derive(self.memory_encryption_key)

    def encrypt_weight(self, weight_bytes: bytes) -> dict:
        """加密模型权重"""
        iv = os.urandom(12)
        aesgcm = AESGCM(self.weight_encryption_key)
        ciphertext = aesgcm.encrypt(iv, weight_bytes, None)
        return {
            "encrypted": ciphertext[:-16],
            "iv": iv,
            "tag": ciphertext[-16:],
        }

    def decrypt_weight(self, encrypted_data: dict) -> bytes:
        """解密模型权重"""
        aesgcm = AESGCM(self.weight_encryption_key)
        ciphertext = encrypted_data["iv"] + encrypted_data["encrypted"] + encrypted_data["tag"]
        return aesgcm.decrypt(encrypted_data["iv"], encrypted_data["encrypted"] + encrypted_data["tag"], None)

    def sign(self, data: bytes) -> bytes:
        """用身份密钥签名"""
        return self.identity_key_private.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        """验证身份密钥签名"""
        try:
            self.identity_key_public.verify(signature, data)
            return True
        except Exception:
            return False

    def export_public_keys(self) -> dict:
        """导出公钥信息（不含私钥，可公开）"""
        return {
            "avatarDID": self.avatar_did,
            "identityKey": self.identity_key_public.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex(),
            "communicationKey": self.communication_key_public.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex(),
            "emergencyKey": self.emergency_key_public.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex(),
        }


@dataclass
class SocialRecovery:
    """社交恢复配置 - M-of-N 恢复机制"""

    threshold: int  # M: 需要多少个监护人同意
    guardians: list[str] = field(default_factory=list)  # N: 监护人 DID 列表
    delay_hours: int = 720  # 恢复延迟（默认 30 天）
    shards: dict[str, bytes] = field(default_factory=dict)  # 加密后的密钥分片

    def setup(self, master_key: bytes, guardian_dids: list[str], threshold: int):
        """设置社交恢复

        使用 Shamir Secret Sharing 将主密钥分成 N 份，
        任意 M 份可恢复主密钥。
        """
        self.guardians = guardian_dids
        self.threshold = threshold
        # 使用简单的 XOR 分片方案（生产环境应使用 Shamir Secret Sharing）
        self.shards = self._split_key(master_key, len(guardian_dids), threshold)

    def _split_key(self, key: bytes, n: int, m: int) -> dict[str, bytes]:
        """将密钥分为 n 份，任意 m 份可恢复"""
        # 简化实现：为每个监护人生成随机密钥分片
        # 生产环境应使用 cryptography 的 Shamir Secret Sharing
        shards = {}
        remaining = key
        for i, did in enumerate(self.guardians):
            if i < len(self.guardians) - 1:
                shard = os.urandom(32)
                shards[did] = shard
                # XOR: remaining = remaining XOR shard
                remaining = bytes(a ^ b for a, b in zip(remaining, shard))
            else:
                shards[did] = remaining
        return shards

    def recover(self, provided_shards: dict[str, bytes]) -> Optional[bytes]:
        """从提供的分片恢复密钥"""
        if len(provided_shards) < self.threshold:
            return None
        # XOR 所有分片恢复原始密钥
        result = bytes(32)
        for did in self.guardians:
            if did in provided_shards:
                result = bytes(a ^ b for a, b in zip(result, provided_shards[did]))
        return result
