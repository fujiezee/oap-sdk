"""
OAP 数据采集模块

从 Owner 的日常交互中采集训练信号，
所有数据在本地处理，加密存储，不上传任何云端。

数据来源：
1. 对话记录 - Owner 与分身的对话
2. 偏好反馈 - Owner 对分身回复的点赞/纠正
3. 行为观察 - Owner 在设备上的操作模式
4. 外部导入 - 从已有数据源导入（聊天记录、日记等）
"""

import os
import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

from ..training.engine import TrainingSignal
from ..crypto.keys import AvatarKeyRing


@dataclass
class ConversationTurn:
    """对话轮次"""
    role: str  # "owner" | "avatar"
    content: str
    timestamp: float = field(default_factory=time.time)
    feedback: Optional[str] = None  # "positive" | "negative" | "correction" | None
    correction_content: Optional[str] = None  # 如果是纠正，正确的回复


@dataclass
class ConversationSession:
    """对话会话"""
    id: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    def __post_init__(self):
        if not self.id:
            self.id = f"conv_{hashlib.sha256(str(self.start_time).encode()).hexdigest()[:16]}"

    def add_turn(self, role: str, content: str, feedback: str = None, correction: str = None):
        """添加一轮对话"""
        turn = ConversationTurn(
            role=role,
            content=content,
            feedback=feedback,
            correction_content=correction,
        )
        self.turns.append(turn)
        return turn

    def close(self):
        """结束会话"""
        self.end_time = time.time()

    def to_training_signals(self) -> list[TrainingSignal]:
        """将会话转换为训练信号"""
        signals = []

        for i, turn in enumerate(self.turns):
            # 从对话中提取 prompt-response 对
            if turn.role == "avatar" and i > 0 and self.turns[i - 1].role == "owner":
                prompt = self.turns[i - 1].content
                response = turn.content

                # 正面反馈 → SFT 数据
                if turn.feedback == "positive":
                    signals.append(TrainingSignal(
                        type="style_feedback",
                        prompt=prompt,
                        chosen=response,
                        importance=0.7,
                    ))

                # 负面反馈 + 纠正 → DPO 数据
                if turn.feedback == "negative" and turn.correction_content:
                    signals.append(TrainingSignal(
                        type="preference",
                        prompt=prompt,
                        chosen=turn.correction_content,
                        rejected=response,
                        importance=0.9,
                    ))

                # 纠正 → 高权重 SFT 数据
                if turn.feedback == "correction" and turn.correction_content:
                    signals.append(TrainingSignal(
                        type="correction",
                        prompt=prompt,
                        chosen=turn.correction_content,
                        importance=0.95,
                    ))

                # 无反馈 → 低权重 SFT 数据
                if turn.feedback is None:
                    signals.append(TrainingSignal(
                        type="style_feedback",
                        prompt=prompt,
                        chosen=response,
                        importance=0.3,
                    ))

        return signals


class DataCollector:
    """数据采集器 - 本地采集并加密存储训练数据"""

    def __init__(self, avatar_did: str, key_ring: AvatarKeyRing, data_dir: str = "./avatar_data"):
        self.avatar_did = avatar_did
        self.key_ring = key_ring
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.current_session: Optional[ConversationSession] = None

    def start_session(self) -> ConversationSession:
        """开始新的对话会话"""
        if self.current_session and not self.current_session.end_time:
            self.current_session.close()
            self._save_session(self.current_session)

        self.current_session = ConversationSession()
        return self.current_session

    def chat(self, owner_message: str) -> ConversationTurn:
        """Owner 发送消息"""
        if not self.current_session:
            self.start_session()
        return self.current_session.add_turn("owner", owner_message)

    def avatar_reply(self, content: str, feedback: str = None, correction: str = None) -> ConversationTurn:
        """分身回复"""
        if not self.current_session:
            raise ValueError("没有活跃的会话")
        return self.current_session.add_turn("avatar", content, feedback, correction)

    def give_feedback(self, feedback: str, correction: str = None):
        """对分身上一条回复给予反馈"""
        if not self.current_session:
            raise ValueError("没有活跃的会话")
        # 找到最后一轮分身回复
        for turn in reversed(self.current_session.turns):
            if turn.role == "avatar":
                turn.feedback = feedback
                turn.correction_content = correction
                break

    def end_session(self):
        """结束当前会话"""
        if self.current_session:
            self.current_session.close()
            self._save_session(self.current_session)
            self.current_session = None

    def _save_session(self, session: ConversationSession):
        """加密保存会话"""
        session_data = {
            "id": session.id,
            "turns": [asdict(t) for t in session.turns],
            "startTime": session.start_time,
            "endTime": session.end_time,
        }
        plaintext = json.dumps(session_data, ensure_ascii=False).encode("utf-8")

        # 加密
        iv = os.urandom(12)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(self.key_ring.memory_encryption_key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        encrypted_data = {
            "id": session.id,
            "encrypted": ciphertext[:-16].hex(),
            "iv": iv.hex(),
            "tag": ciphertext[-16:].hex(),
        }

        file_path = self.data_dir / f"{session.id}.oap.enc.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(encrypted_data, f, indent=2, ensure_ascii=False)

    def collect_training_signals(self) -> list[TrainingSignal]:
        """从所有会话中采集训练信号"""
        # 在实际使用中，这里需要解密并读取所有会话
        # 简化实现：从当前会话采集
        signals = []
        if self.current_session:
            signals.extend(self.current_session.to_training_signals())
        return signals

    def import_chat_history(self, file_path: str, format: str = "json") -> list[TrainingSignal]:
        """从外部聊天记录导入训练数据

        支持格式：
        - json: [{"role": "user/assistant", "content": "..."}, ...]
        - csv: prompt,response,feedback
        """
        signals = []

        if format == "json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 转换为对话会话
            session = ConversationSession()
            for msg in data:
                role = "owner" if msg.get("role") == "user" else "avatar"
                session.add_turn(role, msg.get("content", ""))

            signals.extend(session.to_training_signals())

        elif format == "csv":
            import csv
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    signal = TrainingSignal(
                        type="style_feedback",
                        prompt=row.get("prompt", ""),
                        chosen=row.get("response", ""),
                        importance=0.5,
                    )
                    signals.append(signal)

        return signals
