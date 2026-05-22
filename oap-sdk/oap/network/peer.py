"""
OAP AvatarNetwork — 分身网络客户端

核心流程：
  1. connect()   → 连接到中继服务器
  2. register()  → 注册自己的 DID + 公钥
  3. list_avatars() → 发现其他分身
  4. handshake() → 与另一个分身建立加密通道（ECDH）
  5. send()      → 发送端到端加密消息
  6. on_message  → 收到消息回调

安全模型：中继只能看到密文，无法解密。
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Awaitable

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization

from oap.crypto.keyring import KeyRing


@dataclass
class PeerInfo:
    """已发现的远端分身信息"""
    did: str
    display_name: str
    public_key: str = ""
    comm_public_key: str = ""
    endpoint: str = ""
    online: bool = False
    last_seen: float = 0.0


@dataclass
class PeerChannel:
    """与另一个分身的加密通信通道"""
    peer_did: str
    peer_display_name: str
    session_key: bytes
    established_at: float = 0.0
    messages_sent: int = 0
    messages_received: int = 0

    def __post_init__(self):
        if not self.established_at:
            self.established_at = time.time()

    def encrypt(self, plaintext: str) -> dict:
        iv = os.urandom(12)
        ct_tag = AESGCM(self.session_key).encrypt(iv, plaintext.encode(), self.peer_did.encode())
        self.messages_sent += 1
        return {"encrypted": ct_tag[:-16].hex(), "iv": iv.hex(), "tag": ct_tag[-16:].hex()}

    def decrypt(self, blob: dict) -> str:
        iv = bytes.fromhex(blob["iv"])
        ct_tag = bytes.fromhex(blob["encrypted"]) + bytes.fromhex(blob["tag"])
        plain = AESGCM(self.session_key).decrypt(iv, ct_tag, self.peer_did.encode())
        self.messages_received += 1
        return plain.decode()


class AvatarNetwork:
    """分身网络客户端（支持多中继联邦）"""

    def __init__(self, key_ring: KeyRing, relay_urls: list[str] = None,
                 relay_url: str = "ws://localhost:8765"):
        self.key_ring = key_ring
        # 支持多中继（去中心化）：优先用 relay_urls 列表，兼容单个 relay_url
        self.relay_urls = relay_urls or ([relay_url] if relay_url else ["ws://localhost:8765"])
        self.relay_url = self.relay_urls[0]  # 当前使用的中继
        self._ws = None
        self._connected = False
        self._registered = False
        self._channels: dict[str, PeerChannel] = {}
        self._ephemeral_keys: dict[str, X25519PrivateKey] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cache: list[dict] = []
        self.on_message: Optional[Callable[[PeerChannel, str], Awaitable[None]]] = None

    # ── 连接 ──────────────────────────────────────────────

    async def connect(self) -> bool:
        try:
            import websockets
        except ImportError:
            raise RuntimeError("pip install websockets")
        # 尝试连接所有已知中继，用第一个成功的
        for url in self.relay_urls:
            try:
                self._ws = await websockets.connect(url)
                self.relay_url = url
                self._connected = True
                self._listen_task = asyncio.create_task(self._listen_loop())
                self._ping_task = asyncio.create_task(self._ping_loop())
                return True
            except Exception as e:
                print(f"[Network] {url} 连接失败: {e}")
                continue
        print(f"[Network] 所有中继均不可用")
        return False

    async def disconnect(self):
        if self._listen_task: self._listen_task.cancel()
        if self._ping_task: self._ping_task.cancel()
        if self._ws: await self._ws.close()
        self._connected = False

    # ── 消息收发辅助 ──────────────────────────────────────

    async def _next_msg(self, timeout: float = 5.0) -> Optional[dict]:
        """从队列取一条消息"""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _wait_for(self, msg_type: str, match: dict = None, timeout: float = 5.0) -> Optional[dict]:
        """等待特定类型+字段匹配的消息"""
        # 先查缓存
        for i, m in enumerate(self._cache):
            if m.get("type") == msg_type:
                if match and any(m.get(k) != v for k, v in match.items()):
                    continue
                return self._cache.pop(i)
        # 等新消息
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = await self._next_msg(timeout=max(0.5, deadline - time.time()))
            if not msg:
                continue
            if msg.get("type") == msg_type:
                if match and any(msg.get(k) != v for k, v in match.items()):
                    self._cache.append(msg)
                    continue
                return msg
            self._cache.append(msg)
        return None

    # ── 注册 ──────────────────────────────────────────────

    async def register(self, display_name: str = "Anonymous") -> bool:
        if not self._connected: raise RuntimeError("未连接")
        pub = self.key_ring.public_info()
        await self._ws.send(json.dumps({
            "type": "register", "did": self.key_ring.avatar_did,
            "publicKey": pub["identityPubKey"], "commPublicKey": pub["commPubKey"],
            "displayName": display_name, "endpoint": "",
        }))
        resp = await self._wait_for("register_ack", timeout=5.0)
        if resp:
            self._registered = True
            return True
        return False

    # ── 发现 ──────────────────────────────────────────────

    async def discover(self, target_did: str) -> Optional[PeerInfo]:
        if not self._connected: raise RuntimeError("未连接")
        await self._ws.send(json.dumps({"type": "discover", "target_did": target_did}))
        resp = await self._wait_for("discover_response", match={"target_did": target_did}, timeout=5.0)
        if not resp or not resp.get("found"):
            return None
        r = resp["registration"]
        return PeerInfo(did=r["did"], display_name=r.get("displayName", "?"),
                        public_key=r.get("publicKey", ""), comm_public_key=r.get("commPublicKey", ""),
                        endpoint=r.get("endpoint", ""))

    async def list_avatars(self) -> list[PeerInfo]:
        if not self._connected: raise RuntimeError("未连接")
        await self._ws.send(json.dumps({"type": "list"}))
        resp = await self._wait_for("list_response", timeout=5.0)
        if not resp:
            return []
        return [PeerInfo(did=a["did"], display_name=a.get("displayName", "?"),
                         online=a.get("online", False), last_seen=a.get("lastSeen", 0))
                for a in resp.get("avatars", []) if a["did"] != self.key_ring.avatar_did]

    # ── 握手 ──────────────────────────────────────────────

    async def handshake(self, peer_did: str, timeout: float = 30.0) -> Optional[PeerChannel]:
        if not self._connected: raise RuntimeError("未连接")
        peer = await self.discover(peer_did)
        if not peer:
            print(f"[Network] 找不到: {peer_did[:30]}...")
            return None

        eph_priv = X25519PrivateKey.generate()
        eph_pub_bytes = eph_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self._ephemeral_keys[peer_did] = eph_priv

        await self._ws.send(json.dumps({
            "type": "handshake", "from_did": self.key_ring.avatar_did,
            "to_did": peer_did, "ephemeral_public_key": eph_pub_bytes.hex(),
        }))

        resp = await self._wait_for("handshake_response", match={"from_did": peer_did}, timeout=timeout)
        if not resp:
            print(f"[Network] 握手超时: {peer.display_name}")
            return None

        peer_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(resp["ephemeral_public_key"]))
        shared = eph_priv.exchange(peer_pub)
        dids = sorted([self.key_ring.avatar_did, peer_did])
        session_key = HKDF(hashes.SHA256(), 32, b"oap-session-key",
                           f"{dids[0]}:{dids[1]}".encode()).derive(shared)

        ch = PeerChannel(peer_did=peer_did, peer_display_name=peer.display_name, session_key=session_key)
        self._channels[peer_did] = ch
        print(f"[Network] 加密通道已建立: {peer.display_name}")
        return ch

    # ── 发送 ──────────────────────────────────────────────

    async def send(self, channel: PeerChannel, message: str):
        if not self._connected: raise RuntimeError("未连接")
        enc = channel.encrypt(message)
        await self._ws.send(json.dumps({
            "type": "message", "from_did": self.key_ring.avatar_did,
            "to_did": channel.peer_did, **enc,
        }))

    async def send_to(self, peer_did: str, message: str):
        ch = self._channels.get(peer_did)
        if not ch: raise RuntimeError(f"请先 handshake {peer_did[:20]}...")
        await self.send(ch, message)

    def get_channel(self, peer_did: str) -> Optional[PeerChannel]:
        return self._channels.get(peer_did)

    @property
    def is_connected(self) -> bool: return self._connected
    @property
    def active_channels(self) -> list[PeerChannel]: return list(self._channels.values())

    # ── 内部 ──────────────────────────────────────────────

    async def _listen_loop(self):
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type", "")
                if t == "message":
                    await self._handle_msg(msg)
                elif t == "handshake":
                    await self._handle_handshake(msg)
                else:
                    await self._queue.put(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Network] 监听异常: {e}")
            self._connected = False

    async def _handle_msg(self, msg: dict):
        ch = self._channels.get(msg.get("from_did", ""))
        if not ch: return
        try:
            text = ch.decrypt({k: msg.get(k, "") for k in ("encrypted", "iv", "tag")})
        except Exception:
            return
        if self.on_message:
            await self.on_message(ch, text)

    async def _handle_handshake(self, msg: dict):
        from_did = msg.get("from_did", "")
        eph_hex = msg.get("ephemeral_public_key", "")
        if not eph_hex: return

        peer = await self.discover(from_did)
        my_eph = X25519PrivateKey.generate()
        my_pub_bytes = my_eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        peer_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(eph_hex))

        shared = my_eph.exchange(peer_pub)
        dids = sorted([self.key_ring.avatar_did, from_did])
        session_key = HKDF(hashes.SHA256(), 32, b"oap-session-key",
                           f"{dids[0]}:{dids[1]}".encode()).derive(shared)

        ch = PeerChannel(peer_did=from_did,
                         peer_display_name=peer.display_name if peer else "?",
                         session_key=session_key)
        self._channels[from_did] = ch

        await self._ws.send(json.dumps({
            "type": "handshake_response", "from_did": self.key_ring.avatar_did,
            "to_did": from_did, "ephemeral_public_key": my_pub_bytes.hex(),
        }))
        print(f"[Network] 接受握手: {ch.peer_display_name}")

    async def _ping_loop(self):
        try:
            while self._connected:
                await asyncio.sleep(30)
                if self._ws and self._connected:
                    await self._ws.send(json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            pass