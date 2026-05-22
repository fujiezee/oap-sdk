"""
OAP Federated Relay — 去中心化中继联邦

每个 Relay 是对等的，通过 Gossip 协议互相同步：
  - 注册信息：新分身注册时，广播到所有联邦节点
  - 消息路由：发送方连接的 Relay 自动把消息转发到目标所在的 Relay
  - 心跳检测：定期交换节点列表，自动发现新节点

任何人可以运行自己的 Relay，加入联邦即自动同步。
没有主节点，任何 Relay 宕机不影响通信。

联邦协议：
  relay://join       — 新 Relay 加入联邦
  relay://sync       — 全量注册表同步
  relay://gossip     — 增量更新广播（新注册/离线）
  relay://route      — 跨 Relay 消息路由

运行方式：
    python federated_relay.py                        # 单节点模式
    python federated_relay.py --peers ws://other:8765  # 加入联邦
    python federated_relay.py --port 8766              # 指定端口
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
    print("pip install websockets")
    raise


@dataclass
class AvatarRegistration:
    did: str
    public_key: str = ""
    comm_public_key: str = ""
    display_name: str = "Anonymous"
    endpoint: str = ""
    home_relay: str = ""       # 分身连接的 Relay ID
    registered_at: float = 0.0
    last_seen: float = 0.0

    def __post_init__(self):
        if not self.registered_at:
            self.registered_at = time.time()
        self.last_seen = time.time()


@dataclass
class PeerRelay:
    """联邦中的对等 Relay"""
    relay_id: str
    ws_url: str
    connected: bool = False
    last_sync: float = 0.0


class FederatedRelay:
    """
    去中心化中继联邦节点

    每个 Relay 维护一份全量注册表（CRDT 合并），
    通过 Gossip 增量同步，通过路由转发跨节点消息。
    """

    def __init__(self, relay_id: str = "", port: int = 8765, peer_urls: list[str] = None):
        self.relay_id = relay_id or f"relay-{hashlib.sha256(str(time.time()).encode()).hex()[:12]}"
        self.port = port
        self.peer_urls = peer_urls or []

        # 本地状态
        self.registrations: dict[str, AvatarRegistration] = {}  # did → reg
        self.local_connections: dict[str, any] = {}  # did → websocket
        self.peer_relays: dict[str, PeerRelay] = {}  # relay_id → PeerRelay
        self.peer_connections: dict[str, any] = {}   # relay_id → websocket (到 peer)
        self.message_count = 0
        self.gossip_seq = 0  # Gossip 序列号，用于去重

        # 已见 gossip 消息 ID（去重）
        self._seen_gossip: set[str] = set()
        self._seen_gossip_max = 10000

    # ── 处理分身连接 ──────────────────────────────────────

    async def avatar_handler(self, websocket, first_msg: dict = None):
        """处理分身（Avatar）的 WebSocket 连接"""
        connected_did = None

        # 处理第一条消息
        if first_msg:
            connected_did = await self._process_avatar_msg(first_msg, websocket)

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue
                connected_did = await self._process_avatar_msg(msg, websocket)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if connected_did:
                self.local_connections.pop(connected_did, None)
                if connected_did in self.registrations:
                    self.registrations[connected_did].last_seen = 0
                await self._gossip({"action": "offline", "did": connected_did, "relay_id": self.relay_id})
                print(f"[offline] {connected_did[:30]}...")

    async def _process_avatar_msg(self, msg: dict, websocket) -> Optional[str]:
        """处理单条 Avatar 消息，返回 connected_did"""
        msg_type = msg.get("type", "")
        connected_did = None

        if msg_type == "register":
            connected_did = await self._handle_register(msg, websocket)
        elif msg_type == "discover":
            await self._handle_discover(msg, websocket)
        elif msg_type == "list":
            await self._handle_list(msg, websocket)
        elif msg_type in ("handshake", "handshake_response", "message"):
            await self._handle_cross_relay_msg(msg, websocket, msg_type)
        elif msg_type == "ping":
            await websocket.send(json.dumps({"type": "pong"}))
        elif msg_type == "relay_info":
            await websocket.send(json.dumps({
                "type": "relay_info_response", "relay_id": self.relay_id,
                "peers": list(self.peer_relays.keys()),
                "avatars_registered": len(self.registrations),
                "messages_routed": self.message_count,
            }))
        return connected_did

    # ── 处理注册 ──────────────────────────────────────────

    async def _handle_register(self, msg: dict, websocket) -> Optional[str]:
        did = msg.get("did", "")
        if not did.startswith("did:oap:"):
            await websocket.send(json.dumps({"type": "error", "message": "Invalid DID"}))
            return None

        reg = AvatarRegistration(
            did=did,
            public_key=msg.get("publicKey", ""),
            comm_public_key=msg.get("commPublicKey", ""),
            display_name=msg.get("displayName", "Anonymous"),
            endpoint=msg.get("endpoint", ""),
            home_relay=self.relay_id,
        )
        self.registrations[did] = reg
        self.local_connections[did] = websocket

        await websocket.send(json.dumps({
            "type": "register_ack", "did": did, "relay_id": self.relay_id,
            "timestamp": time.time(),
        }))

        # 广播到联邦
        await self._gossip({
            "action": "register",
            "registration": asdict(reg),
        })

        print(f"[register] {reg.display_name} ({did[:30]}...) @ {self.relay_id}")
        return did

    # ── 处理发现 ──────────────────────────────────────────

    async def _handle_discover(self, msg: dict, websocket):
        target_did = msg.get("target_did", "")
        reg = self.registrations.get(target_did)
        await websocket.send(json.dumps({
            "type": "discover_response",
            "target_did": target_did,
            "found": reg is not None,
            "registration": asdict(reg) if reg else None,
        }))

    # ── 处理列表 ──────────────────────────────────────────

    async def _handle_list(self, msg: dict, websocket):
        avatars = []
        for did, reg in self.registrations.items():
            avatars.append({
                "did": did,
                "displayName": reg.display_name,
                "online": did in self.local_connections,
                "homeRelay": reg.home_relay,
                "lastSeen": reg.last_seen,
            })
        await websocket.send(json.dumps({
            "type": "list_response",
            "avatars": avatars,
            "total": len(avatars),
            "relay_id": self.relay_id,
        }))

    # ── 跨 Relay 消息路由 ─────────────────────────────────

    async def _handle_cross_relay_msg(self, msg: dict, websocket, label: str):
        """路由消息：如果目标在本地则直接转发，否则路由到目标所在的 Relay"""
        to_did = msg.get("to_did", "")
        from_did = msg.get("from_did", "")

        # 目标在本地
        if to_did in self.local_connections:
            await self.local_connections[to_did].send(json.dumps(msg))
            self.message_count += 1
            print(f"[{label}] {from_did[:20]}... → {to_did[:20]}... (local)")
            return

        # 目标在其他 Relay
        target_reg = self.registrations.get(to_did)
        if target_reg and target_reg.home_relay:
            target_relay_id = target_reg.home_relay
            peer_ws = self.peer_connections.get(target_relay_id)
            if peer_ws:
                # 转发到目标 Relay（包装为 relay_route 消息）
                route_msg = {
                    "type": "relay_route",
                    "original_type": label,
                    "payload": msg,
                    "target_relay": target_relay_id,
                }
                await peer_ws.send(json.dumps(route_msg))
                self.message_count += 1
                print(f"[{label}] {from_did[:20]}... → {to_did[:20]}... (via {target_relay_id})")
            else:
                # 没有直连，广播到所有 peer
                await self._route_broadcast(msg, label, from_did, to_did)
        else:
            await websocket.send(json.dumps({
                "type": "error", "message": f"Avatar offline", "code": "OFFLINE",
            }))

    async def _route_broadcast(self, msg: dict, label: str, from_did: str, to_did: str):
        """向所有联邦节点广播路由消息"""
        route_msg = {
            "type": "relay_route",
            "original_type": label,
            "payload": msg,
            "target_did": to_did,
        }
        for relay_id, ws in self.peer_connections.items():
            try:
                await ws.send(json.dumps(route_msg))
            except Exception:
                pass
        self.message_count += 1
        print(f"[{label}] {from_did[:20]}... → {to_did[:20]}... (broadcast)")

    # ── Gossip 协议 ───────────────────────────────────────

    async def _gossip(self, data: dict):
        """向所有联邦节点广播增量更新"""
        self.gossip_seq += 1
        gossip_id = f"{self.relay_id}:{self.gossip_seq}"

        # 去重
        self._seen_gossip.add(gossip_id)
        if len(self._seen_gossip) > self._seen_gossip_max:
            # 简单清理：保留最近一半
            self._seen_gossip = set(list(self._seen_gossip)[self._seen_gossip_max // 2:])

        gossip_msg = {
            "type": "relay_gossip",
            "gossip_id": gossip_id,
            "source_relay": self.relay_id,
            "data": data,
            "timestamp": time.time(),
        }

        for relay_id, ws in self.peer_connections.items():
            try:
                await ws.send(json.dumps(gossip_msg))
            except Exception:
                pass

    async def _handle_gossip(self, msg: dict):
        """处理收到的 Gossip 消息"""
        gossip_id = msg.get("gossip_id", "")
        if gossip_id in self._seen_gossip:
            return  # 去重
        self._seen_gossip.add(gossip_id)

        data = msg.get("data", {})
        action = data.get("action", "")

        if action == "register":
            reg_data = data.get("registration", {})
            did = reg_data.get("did", "")
            if did and did not in self.registrations:
                reg = AvatarRegistration(**reg_data)
                self.registrations[did] = reg
                print(f"[gossip:register] {reg.display_name} ({did[:20]}...) from {msg.get('source_relay', '?')}")

        elif action == "offline":
            did = data.get("did", "")
            if did in self.registrations:
                self.registrations[did].last_seen = 0

        # 继续传播（Gossip 风格）
        await self._gossip(data)

    # ── 处理来自其他 Relay 的连接 ─────────────────────────

    async def relay_handler(self, websocket, first_msg: dict = None):
        """处理来自其他 Relay 的连接"""
        if first_msg:
            await self._process_relay_msg(first_msg, websocket)
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._process_relay_msg(msg, websocket)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _process_relay_msg(self, msg: dict, websocket):
        """处理单条 Relay 消息"""
        msg_type = msg.get("type", "")
        if msg_type == "relay_join":
            await self._handle_relay_join(msg, websocket)
        elif msg_type == "relay_gossip":
            await self._handle_gossip(msg)
        elif msg_type == "relay_route":
            await self._handle_relay_route(msg)
        elif msg_type == "relay_sync_request":
            await self._handle_sync_request(msg, websocket)

    async def _handle_relay_join(self, msg: dict, websocket):
        """其他 Relay 加入联邦"""
        peer_id = msg.get("relay_id", "")
        peer_url = msg.get("ws_url", "")

        peer = PeerRelay(relay_id=peer_id, ws_url=peer_url, connected=True, last_sync=time.time())
        self.peer_relays[peer_id] = peer
        self.peer_connections[peer_id] = websocket

        # 回复：我自己的信息 + 全量注册表
        await websocket.send(json.dumps({
            "type": "relay_join_ack",
            "relay_id": self.relay_id,
            "known_peers": {rid: {"ws_url": p.ws_url} for rid, p in self.peer_relays.items() if rid != peer_id},
            "registrations": {did: asdict(reg) for did, reg in self.registrations.items()},
        }))

        print(f"[federation] Peer joined: {peer_id} ({peer_url})")

    async def _handle_relay_route(self, msg: dict):
        """处理路由来的消息——在本地查找目标分身并投递"""
        payload = msg.get("payload", {})
        to_did = msg.get("target_did", "") or payload.get("to_did", "")
        original_type = msg.get("original_type", "")

        if to_did in self.local_connections:
            await self.local_connections[to_did].send(json.dumps(payload))
            self.message_count += 1
            print(f"[routed:{original_type}] → {to_did[:20]}... (delivered)")
        else:
            # 继续转发
            if to_did and to_did not in self.local_connections:
                # 尝试转发到目标可能所在的 Relay
                target_reg = self.registrations.get(to_did)
                if target_reg and target_reg.home_relay and target_reg.home_relay in self.peer_connections:
                    try:
                        await self.peer_connections[target_reg.home_relay].send(json.dumps(msg))
                        print(f"[routed:{original_type}] → {to_did[:20]}... (forwarded to {target_reg.home_relay})")
                    except Exception:
                        pass

    async def _handle_sync_request(self, msg: dict, websocket):
        """处理全量同步请求"""
        await websocket.send(json.dumps({
            "type": "relay_sync_response",
            "relay_id": self.relay_id,
            "registrations": {did: asdict(reg) for did, reg in self.registrations.items()},
            "known_peers": {rid: {"ws_url": p.ws_url} for rid, p in self.peer_relays.items()},
        }))

    # ── 连接到其他 Relay（出站）───────────────────────────

    async def _connect_to_peers(self):
        """主动连接到已知的 Relay 节点"""
        for url in self.peer_urls:
            try:
                ws = await websockets.connect(f"{url}/relay")
                await ws.send(json.dumps({
                    "type": "relay_join",
                    "relay_id": self.relay_id,
                    "ws_url": f"ws://localhost:{self.port}",
                }))

                # 接收 join_ack
                resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(resp)

                if data.get("type") == "relay_join_ack":
                    peer_id = data.get("relay_id", "")
                    peer = PeerRelay(relay_id=peer_id, ws_url=url, connected=True, last_sync=time.time())
                    self.peer_relays[peer_id] = peer
                    self.peer_connections[peer_id] = ws

                    # 同步注册表
                    for did, reg_data in data.get("registrations", {}).items():
                        if did not in self.registrations:
                            self.registrations[did] = AvatarRegistration(**reg_data)

                    # 发现更多 Peer
                    for rid, info in data.get("known_peers", {}).items():
                        if rid not in self.peer_relays and info.get("ws_url"):
                            self.peer_urls.append(info["ws_url"])

                    print(f"[federation] Connected to {peer_id} ({url}), synced {len(data.get('registrations', {}))} avatars")

                    # 启动对等监听
                    asyncio.create_task(self._listen_peer(ws, peer_id))

            except Exception as e:
                print(f"[federation] Failed to connect {url}: {e}")

    async def _listen_peer(self, ws, peer_id: str):
        """监听来自对等 Relay 的消息"""
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = msg.get("type", "")
                if msg_type == "relay_gossip":
                    await self._handle_gossip(msg)
                elif msg_type == "relay_route":
                    await self._handle_relay_route(msg)
        except websockets.exceptions.ConnectionClosed:
            print(f"[federation] Peer disconnected: {peer_id}")
            self.peer_connections.pop(peer_id, None)
            if peer_id in self.peer_relays:
                self.peer_relays[peer_id].connected = False

    # ── 启动 ──────────────────────────────────────────────

    async def start(self, host: str = "0.0.0.0", port: int = 8765):
        self.port = port

        print(f"\n{'═' * 55}")
        print(f"  OAP Federated Relay — 去中心化中继")
        print(f"  ID: {self.relay_id}")
        print(f"  地址: ws://{host}:{port}")
        if self.peer_urls:
            print(f"  联邦节点: {', '.join(self.peer_urls)}")
        print(f"{'═' * 55}\n")

        async with serve(self._router, host, port):
            # 连接到已知 Peer
            if self.peer_urls:
                await self._connect_to_peers()
            await asyncio.Future()

    async def _router(self, websocket):
        """自动判断连接类型：第一条消息决定走 Avatar 还是 Relay 通道"""
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
            msg = json.loads(raw)
        except (json.JSONDecodeError, asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            return

        msg_type = msg.get("type", "")

        # Relay 联邦消息以 relay_ 开头
        if msg_type.startswith("relay_"):
            # 把第一条消息也交给 relay_handler
            await self.relay_handler(websocket, first_msg=msg)
        else:
            # Avatar 消息，把第一条消息也交给 avatar_handler
            await self.avatar_handler(websocket, first_msg=msg)


def main():
    parser = argparse.ArgumentParser(description="OAP 去中心化中继联邦")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--id", default="", help="Relay ID（默认自动生成）")
    parser.add_argument("--peers", nargs="*", default=[], help="联邦节点 URL 列表")
    args = parser.parse_args()

    relay = FederatedRelay(
        relay_id=args.id or "",
        port=args.port,
        peer_urls=args.peers,
    )

    try:
        asyncio.run(relay.start(args.host, args.port))
    except KeyboardInterrupt:
        print("\nRelay 已停止")


if __name__ == "__main__":
    main()