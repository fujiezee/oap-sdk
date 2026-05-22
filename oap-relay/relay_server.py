"""
OAP Relay Server — 分身网络中继服务器

轻量 WebSocket 服务器，负责：
  1. 分身注册（DID + 公钥 + 端点）
  2. DID 发现（查询其他分身的端点）
  3. 消息转发（只转发密文，不解密）

安全保证：
  - 所有消息都是端到端加密的，中继只能看到密文
  - 中继不存储任何消息内容
  - 支持多个中继组成网络（未来可扩展为 P2P）

运行方式：
    python relay_server.py                     # 默认 localhost:8765
    python relay_server.py --host 0.0.0.0 --port 8765
"""

import asyncio
import json
import time
import hashlib
import argparse
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("需要安装 websockets: pip install websockets")
    raise


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class AvatarRegistration:
    """分身注册信息"""
    did: str
    public_key: str           # Ed25519 公钥（十六进制）
    comm_public_key: str      # X25519 通信公钥
    display_name: str
    endpoint: str             # WebSocket 端点（如果有直连）
    registered_at: float = 0.0
    last_seen: float = 0.0

    def __post_init__(self):
        if not self.registered_at:
            self.registered_at = time.time()
        self.last_seen = time.time()


# ── 中继服务器 ────────────────────────────────────────────

class OAPRelayServer:
    """
    OAP 分身网络中继服务器

    协议消息格式（JSON）：

    注册：
        { "type": "register", "did": "...", "publicKey": "...",
          "commPublicKey": "...", "displayName": "...", "endpoint": "..." }

    发现：
        { "type": "discover", "target_did": "..." }

    发现响应：
        { "type": "discover_response", "target_did": "...",
          "found": true, "registration": {...} }

    握手请求：
        { "type": "handshake", "from_did": "...", "to_did": "...",
          "ephemeral_public_key": "...", "payload_encrypted": "..." }

    握手响应：
        { "type": "handshake_response", "from_did": "...", "to_did": "...",
          "ephemeral_public_key": "...", "payload_encrypted": "..." }

    加密消息：
        { "type": "message", "from_did": "...", "to_did": "...",
          "encrypted": "...", "iv": "...", "tag": "..." }

    在线列表：
        { "type": "list" }

    心跳：
        { "type": "ping" } → { "type": "pong" }
    """

    def __init__(self):
        self.registrations: dict[str, AvatarRegistration] = {}  # did → Registration
        self.connections: dict[str, any] = {}  # did → websocket
        self.message_count = 0

    async def handler(self, websocket):
        """处理单个 WebSocket 连接"""
        connected_did = None

        try:
            async for raw_message in websocket:
                try:
                    msg = json.loads(raw_message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue

                msg_type = msg.get("type", "")

                # ── 注册 ────────────────────────────────
                if msg_type == "register":
                    did = msg.get("did", "")
                    if not did.startswith("did:oap:"):
                        await websocket.send(json.dumps({"type": "error", "message": "Invalid DID"}))
                        continue

                    reg = AvatarRegistration(
                        did=did,
                        public_key=msg.get("publicKey", ""),
                        comm_public_key=msg.get("commPublicKey", ""),
                        display_name=msg.get("displayName", "Anonymous"),
                        endpoint=msg.get("endpoint", ""),
                    )
                    self.registrations[did] = reg
                    self.connections[did] = websocket
                    connected_did = did

                    await websocket.send(json.dumps({
                        "type": "register_ack",
                        "did": did,
                        "timestamp": time.time(),
                    }))
                    print(f"[register] {reg.display_name} ({did[:30]}...)")

                # ── 发现 ────────────────────────────────
                elif msg_type == "discover":
                    target_did = msg.get("target_did", "")
                    reg = self.registrations.get(target_did)
                    await websocket.send(json.dumps({
                        "type": "discover_response",
                        "target_did": target_did,
                        "found": reg is not None,
                        "registration": asdict(reg) if reg else None,
                    }))

                # ── 在线列表 ─────────────────────────────
                elif msg_type == "list":
                    avatars = []
                    for did, reg in self.registrations.items():
                        avatars.append({
                            "did": did,
                            "displayName": reg.display_name,
                            "online": did in self.connections,
                            "lastSeen": reg.last_seen,
                        })
                    await websocket.send(json.dumps({
                        "type": "list_response",
                        "avatars": avatars,
                        "total": len(avatars),
                    }))

                # ── 握手 ────────────────────────────────
                elif msg_type == "handshake":
                    to_did = msg.get("to_did", "")
                    if to_did in self.connections:
                        await self.connections[to_did].send(raw_message)
                        self.message_count += 1
                        print(f"[handshake] {msg.get('from_did', '?')[:20]}... → {to_did[:20]}...")
                    else:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": f"Avatar {to_did[:20]}... is not online",
                        }))

                # ── 握手响应 ─────────────────────────────
                elif msg_type == "handshake_response":
                    to_did = msg.get("to_did", "")
                    if to_did in self.connections:
                        await self.connections[to_did].send(raw_message)
                        self.message_count += 1
                        print(f"[handshake_ok] {msg.get('from_did', '?')[:20]}... ↔ {to_did[:20]}...")

                # ── 加密消息转发 ─────────────────────────
                elif msg_type == "message":
                    to_did = msg.get("to_did", "")
                    if to_did in self.connections:
                        await self.connections[to_did].send(raw_message)
                        self.message_count += 1
                        print(f"[msg] {msg.get('from_did', '?')[:20]}... → {to_did[:20]}... (encrypted)")
                    else:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": f"Avatar {to_did[:20]}... is not online",
                            "code": "OFFLINE",
                        }))

                # ── 心跳 ────────────────────────────────
                elif msg_type == "ping":
                    if connected_did and connected_did in self.registrations:
                        self.registrations[connected_did].last_seen = time.time()
                    await websocket.send(json.dumps({"type": "pong"}))

                else:
                    await websocket.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type}"}))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if connected_did:
                self.connections.pop(connected_did, None)
                print(f"[disconnect] {connected_did[:30]}...")

    async def start(self, host: str = "localhost", port: int = 8765):
        """启动中继服务器"""
        print(f"\n{'═' * 55}")
        print(f"  OAP Relay Server — 分身网络中继")
        print(f"  地址: ws://{host}:{port}")
        print(f"{'═' * 55}\n")

        async with serve(self.handler, host, port):
            await asyncio.Future()  # 永不退出


# ── 启动 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OAP 分身网络中继服务器")
    parser.add_argument("--host", default="localhost", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    args = parser.parse_args()

    server = OAPRelayServer()
    try:
        asyncio.run(server.start(args.host, args.port))
    except KeyboardInterrupt:
        print("\n中继服务器已停止")


if __name__ == "__main__":
    main()