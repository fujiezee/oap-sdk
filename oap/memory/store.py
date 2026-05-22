"""
OAP MemoryStore — 分身记忆存储

负责分身记忆条目的：
  - 加密写入（AES-256-GCM）
  - 按时间 / 类型 / 重要性检索
  - 本地文件系统持久化（冷热分级）
  - 记忆遗忘（安全擦除）

记忆热分级：
  hot  ← 最近 7 天，驻留内存索引，低延迟
  warm ← 7-90 天，仅索引驻留内存，内容按需加载
  cold ← 90 天以上，全量磁盘，按需解密
"""

import json
import os
import time
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, List

from oap.crypto.keyring import KeyRing


class MemoryType(str, Enum):
    CONVERSATION = "conversation"
    ACTION       = "action"
    OBSERVATION  = "observation"
    REFLECTION   = "reflection"
    KNOWLEDGE    = "knowledge"


class MemoryTier(str, Enum):
    HOT  = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass
class MemoryEntry:
    """单条记忆条目（明文，仅在内存中存在）"""
    content: str
    type: MemoryType = MemoryType.CONVERSATION
    importance: float = 0.5        # 0-1，越高越重要，影响遗忘策略
    emotional_valence: float = 0.0 # -1 到 1
    source: str = "owner-input"
    participants: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            raw = f"{self.content[:32]}:{self.timestamp}"
            self.id = "mem_" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    @property
    def tier(self) -> MemoryTier:
        age_days = (time.time() - self.timestamp) / 86400
        if age_days < 7:
            return MemoryTier.HOT
        if age_days < 90:
            return MemoryTier.WARM
        return MemoryTier.COLD

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


class MemoryStore:
    """
    分身记忆存储

    目录结构：
        {data_dir}/
          memories/
            index.enc.json        ← 加密索引（所有条目的元数据）
            hot/  {id}.enc.json   ← 热记忆（明文已在 self._hot_cache）
            warm/ {id}.enc.json   ← 温记忆
            cold/ {id}.enc.json   ← 冷记忆
    """

    def __init__(self, data_dir: str, key_ring: KeyRing):
        self._root = Path(data_dir) / "memories"
        self._kr = key_ring
        self._hot_cache: dict[str, MemoryEntry] = {}   # id → MemoryEntry
        self._index: dict[str, dict] = {}              # id → metadata

        for tier in MemoryTier:
            (self._root / tier.value).mkdir(parents=True, exist_ok=True)

        self._load_index()

    # ── 写入 ──────────────────────────────────────────────

    def add(self, entry: MemoryEntry) -> str:
        """写入一条记忆，返回记忆 ID"""
        # 更新热缓存
        self._hot_cache[entry.id] = entry

        # 持久化加密
        self._write_entry(entry)

        # 更新索引
        self._index[entry.id] = {
            "id": entry.id,
            "type": entry.type.value,
            "timestamp": entry.timestamp,
            "importance": entry.importance,
            "source": entry.source,
            "tier": entry.tier.value,
        }
        self._save_index()

        return entry.id

    def add_conversation(
        self,
        content: str,
        importance: float = 0.5,
        emotional_valence: float = 0.0,
    ) -> str:
        """快捷方式：写入一条对话记忆"""
        return self.add(MemoryEntry(
            content=content,
            type=MemoryType.CONVERSATION,
            importance=importance,
            emotional_valence=emotional_valence,
        ))

    # ── 检索 ──────────────────────────────────────────────

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """按 ID 获取记忆条目（先查热缓存，再磁盘解密）"""
        if memory_id in self._hot_cache:
            return self._hot_cache[memory_id]
        return self._load_entry(memory_id)

    def recent(self, n: int = 20, memory_type: Optional[MemoryType] = None) -> List[MemoryEntry]:
        """获取最近 n 条记忆（优先从热缓存，按时间倒序）"""
        candidates = list(self._index.values())
        if memory_type:
            candidates = [m for m in candidates if m["type"] == memory_type.value]
        candidates.sort(key=lambda m: m["timestamp"], reverse=True)

        results = []
        for meta in candidates[:n]:
            entry = self.get(meta["id"])
            if entry:
                results.append(entry)
        return results

    def important(self, min_importance: float = 0.7, limit: int = 50) -> List[MemoryEntry]:
        """获取高重要性记忆（用于生成反思/长期知识）"""
        candidates = [m for m in self._index.values() if m["importance"] >= min_importance]
        candidates.sort(key=lambda m: m["importance"], reverse=True)
        results = []
        for meta in candidates[:limit]:
            entry = self.get(meta["id"])
            if entry:
                results.append(entry)
        return results

    def search(self, keyword: str, limit: int = 10) -> List[MemoryEntry]:
        """简单关键词检索（明文匹配热缓存，冷记忆暂不搜索）"""
        kw = keyword.lower()
        results = [
            e for e in self._hot_cache.values()
            if kw in e.content.lower()
        ]
        results.sort(key=lambda e: e.importance, reverse=True)
        return results[:limit]

    def stats(self) -> dict:
        """返回记忆统计信息"""
        total = len(self._index)
        by_tier = {t.value: 0 for t in MemoryTier}
        by_type = {t.value: 0 for t in MemoryType}
        for meta in self._index.values():
            by_tier[meta.get("tier", "cold")] += 1
            by_type[meta.get("type", "conversation")] += 1
        return {"total": total, "by_tier": by_tier, "by_type": by_type}

    # ── 遗忘 ──────────────────────────────────────────────

    def forget(self, memory_id: str) -> bool:
        """
        安全遗忘：从索引移除，磁盘文件随机覆写后删除。
        Owner 主动触发，不可逆。
        """
        if memory_id not in self._index:
            return False

        tier = self._index[memory_id].get("tier", "cold")
        fpath = self._root / tier / f"{memory_id}.enc.json"

        if fpath.exists():
            # 随机覆写（防止文件系统恢复）
            size = fpath.stat().st_size
            with open(fpath, "wb") as f:
                f.write(os.urandom(size))
            fpath.unlink()

        self._hot_cache.pop(memory_id, None)
        del self._index[memory_id]
        self._save_index()
        return True

    def forget_before(self, timestamp: float) -> int:
        """批量遗忘某时间点之前的记忆，返回遗忘数量"""
        targets = [mid for mid, meta in self._index.items()
                   if meta["timestamp"] < timestamp]
        for mid in targets:
            self.forget(mid)
        return len(targets)

    # ── 内部 I/O ──────────────────────────────────────────

    def _write_entry(self, entry: MemoryEntry):
        plaintext = json.dumps(entry.to_dict(), ensure_ascii=False).encode()
        blob = self._kr.encrypt(plaintext)
        fpath = self._root / entry.tier.value / f"{entry.id}.enc.json"
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(blob, f)

    def _load_entry(self, memory_id: str) -> Optional[MemoryEntry]:
        tier = self._index.get(memory_id, {}).get("tier", "cold")
        fpath = self._root / tier / f"{memory_id}.enc.json"
        if not fpath.exists():
            return None
        with open(fpath, encoding="utf-8") as f:
            blob = json.load(f)
        plaintext = self._kr.decrypt(blob)
        data = json.loads(plaintext)
        # 反序列化枚举字段
        if "type" in data and isinstance(data["type"], str):
            data["type"] = MemoryType(data["type"])
        return MemoryEntry(**data)

    def _load_index(self):
        idx_path = self._root / "index.enc.json"
        if not idx_path.exists():
            self._index = {}
            return
        with open(idx_path, encoding="utf-8") as f:
            blob = json.load(f)
        plaintext = self._kr.decrypt(blob)
        self._index = json.loads(plaintext)

        # 预热热缓存
        hot_ids = [mid for mid, m in self._index.items() if m.get("tier") == "hot"]
        for mid in hot_ids:
            entry = self._load_entry(mid)
            if entry:
                self._hot_cache[mid] = entry

    def _save_index(self):
        plaintext = json.dumps(self._index, ensure_ascii=False).encode()
        blob = self._kr.encrypt(plaintext)
        idx_path = self._root / "index.enc.json"
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(blob, f)

    def __len__(self) -> int:
        return len(self._index)

    def __repr__(self) -> str:
        return f"MemoryStore(entries={len(self)}, hot={len(self._hot_cache)})"
