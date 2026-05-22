"""
OAP DHT — Kademlia 风格分布式哈希表

用于去中心化地查找"一个 DID 的分身连在哪个 Relay"。

原理：
  - 每个 DID 映射到一个 256-bit key（SHA-256(did)）
  - 每个 Relay 节点有一个 node_id（256-bit）
  - 路由表按 XOR 距离组织（Kademlia buckets）
  - 查找时，逐步逼近目标 key，返回最近的节点信息

存储的值：
  key = SHA-256(did:oap:...)
  value = {"did": "...", "home_relay": "ws://...", "comm_public_key": "...", "display_name": "..."}

注意：这是轻量实现，适合联邦内数十~数百个 Relay 节点。
生产环境可替换为 Kademlia 标准库或 libp2p DHT。
"""

import hashlib
import json
import time
import random
from dataclasses import dataclass, field
from typing import Optional


# ── 常量 ──────────────────────────────────────────────────

K = 8                # 每个 bucket 最多存 K 个节点
ALPHA = 3            # 并行查找数
KEY_BITS = 256       # key 空间位数
BUCKET_COUNT = 256   # bucket 数量


def xor_distance(a: int, b: int) -> int:
    return a ^ b


def bucket_index(distance: int) -> int:
    """XOR 距离对应的 bucket 索引（高位到低位）"""
    if distance == 0:
        return 0
    return KEY_BITS - distance.bit_length()


def did_to_key(did: str) -> int:
    """DID → 256-bit key"""
    return int(hashlib.sha256(did.encode()).hexdigest(), 16)


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class DHTNode:
    """DHT 中的节点（代表一个 Relay）"""
    node_id: int
    relay_url: str
    last_seen: float = 0.0

    def __post_init__(self):
        if not self.last_seen:
            self.last_seen = time.time()


@dataclass
class DHTValue:
    """DHT 中存储的值"""
    key: int
    value: dict       # {"did": "...", "home_relay": "...", ...}
    timestamp: float = 0.0
    ttl: float = 86400.0  # 默认 24 小时

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


# ── Kademlia 路由表 ──────────────────────────────────────

class RoutingTable:
    """Kademlia 风格路由表"""

    def __init__(self, local_node_id: int):
        self.local_id = local_node_id
        self.buckets: list[list[DHTNode]] = [[] for _ in range(BUCKET_COUNT)]

    def add(self, node: DHTNode) -> bool:
        """添加节点到路由表，返回是否新增"""
        if node.node_id == self.local_id:
            return False
        idx = bucket_index(xor_distance(self.local_id, node.node_id))
        bucket = self.buckets[idx]

        # 已存在则更新
        for i, n in enumerate(bucket):
            if n.node_id == node.node_id:
                bucket[i] = node
                return False

        # 满了则丢弃（简单策略，生产环境用 LRU）
        if len(bucket) >= K:
            return False

        bucket.append(node)
        return True

    def find_closest(self, target_key: int, count: int = K) -> list[DHTNode]:
        """查找距离 target_key 最近的 count 个节点"""
        all_nodes = []
        for bucket in self.buckets:
            all_nodes.extend(bucket)

        all_nodes.sort(key=lambda n: xor_distance(n.node_id, target_key))
        return all_nodes[:count]

    def remove(self, node_id: int):
        """移除节点"""
        for bucket in self.buckets:
            for i, n in enumerate(bucket):
                if n.node_id == node_id:
                    bucket.pop(i)
                    return

    @property
    def size(self) -> int:
        return sum(len(b) for b in self.buckets)


# ── DHT 存储 ──────────────────────────────────────────────

class DHTStore:
    """本地 DHT 键值存储"""

    def __init__(self):
        self._data: dict[int, DHTValue] = {}

    def put(self, key: int, value: dict, ttl: float = 86400.0):
        self._data[key] = DHTValue(key=key, value=value, ttl=ttl)

    def get(self, key: int) -> Optional[dict]:
        v = self._data.get(key)
        if v and not v.expired:
            return v.value
        if v and v.expired:
            del self._data[key]
        return None

    def remove(self, key: int):
        self._data.pop(key, None)

    def cleanup(self):
        """清理过期数据"""
        expired = [k for k, v in self._data.items() if v.expired]
        for k in expired:
            del self._data[k]

    @property
    def size(self) -> int:
        return len(self._data)


# ── DHT 节点 ──────────────────────────────────────────────

class DHTNodePeer:
    """
    DHT 节点实例（运行在 Relay 上）

    职责：
      1. 维护路由表（知道其他 Relay 在哪）
      2. 维护本地存储（存了一部分 key-value）
      3. 响应查找请求（FIND_NODE / FIND_VALUE）
      4. 传播新注册的分身信息（STORE）
    """

    def __init__(self, relay_url: str, node_id: Optional[int] = None):
        self.relay_url = relay_url
        self.node_id = node_id or random.getrandbits(KEY_BITS)
        self.routing_table = RoutingTable(self.node_id)
        self.store = DHTStore()

    # ── 对外接口 ──────────────────────────────────────────

    def register_avatar(self, did: str, info: dict, ttl: float = 86400.0):
        """注册一个分身到 DHT"""
        key = did_to_key(did)
        info["did"] = did
        info["registered_at"] = time.time()
        self.store.put(key, info, ttl=ttl)

    def lookup_avatar(self, did: str) -> Optional[dict]:
        """查找一个分身的信息（先查本地，再查路由）"""
        key = did_to_key(did)
        result = self.store.get(key)
        if result:
            return result

        # 返回最近的节点列表，调用方可以去这些节点查询
        closest = self.routing_table.find_closest(key, ALPHA)
        if closest:
            return {
                "_type": "redirect",
                "closest_nodes": [
                    {"node_id": hex(n.node_id), "relay_url": n.relay_url}
                    for n in closest
                ],
            }
        return None

    def add_peer(self, relay_url: str, node_id: Optional[int] = None) -> bool:
        """添加已知节点到路由表"""
        nid = node_id or int(hashlib.sha256(relay_url.encode()).hexdigest(), 16)
        return self.routing_table.add(DHTNode(node_id=nid, relay_url=relay_url))

    def find_closest_peers(self, target_key: int, count: int = K) -> list[dict]:
        """查找距离目标 key 最近的节点"""
        nodes = self.routing_table.find_closest(target_key, count)
        return [{"node_id": hex(n.node_id), "relay_url": n.relay_url} for n in nodes]

    # ── 统计 ──────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "node_id": hex(self.node_id),
            "relay_url": self.relay_url,
            "routing_table_size": self.routing_table.size,
            "store_size": self.store.size,
        }