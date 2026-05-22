"""
OAP Avatar — 数字分身核心类

这是整个 SDK 的顶层入口，汇聚所有子系统：
  KeyRing     ← 密钥管理（身份、加密、签名）
  MemoryStore ← 记忆存储（加密读写、热温冷分级）
  ComputeClient ← 推理执行（本地 / 远程 / 模拟）

生命周期：
  Avatar.create()   ← 全新创建（生成 DID + 初始化存储）
  Avatar.load()     ← 从已有配置文件加载（需要助记词）
  avatar.chat()     ← 对话（推理 + 记忆写入）
  avatar.train()    ← 个人化训练（从记忆中提取训练信号）
  avatar.reflect()  ← 生成反思记忆（对近期对话的元认知）
  avatar.save()     ← 保存配置状态
  avatar.destroy()  ← 安全销毁（擦除所有数据）
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, List

from oap.crypto.keyring import KeyRing, generate_mnemonic
from oap.memory.store import MemoryStore, MemoryEntry, MemoryType
from oap.compute.client import ComputeClient, InferenceMode, InferenceResult


class AvatarState(str, Enum):
    ACTIVE   = "active"
    SLEEPING = "sleeping"   # Owner 主动休眠
    FROZEN   = "frozen"     # 紧急冻结
    ARCHIVED = "archived"   # 归档（不再活跃使用）


@dataclass
class AvatarConfig:
    """分身配置（持久化，不含私钥）"""
    avatar_did: str
    name: str
    created_at: float
    state: AvatarState = AvatarState.ACTIVE
    base_model: str = "meta-llama/Meta-Llama-3-8B"
    inference_mode: str = InferenceMode.MOCK.value
    persona_weight_path: Optional[str] = None
    system_prompt: str = "你是 {name} 的数字分身，请以第一人称回应。"
    data_dir: str = ""
    weights_dir: str = ""
    version: int = 1
    # ── 外部推理配置（仅存储非敏感信息）──
    api_base: str = "https://api.openai.com/v1"
    api_type: str = "openai"   # "openai" | "anthropic" | "custom"
    model: str = "gpt-4o-mini"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AvatarConfig":
        data = dict(data)
        data["state"] = AvatarState(data.get("state", "active"))
        return cls(**data)


class ConversationTurn:
    """对话轮次（内存中，用于当前会话上下文）"""
    def __init__(self, role: str, content: str):
        self.role = role          # "owner" | "avatar"
        self.content = content
        self.timestamp = time.time()
        self.feedback: Optional[str] = None     # "positive" | "negative" | "correction"
        self.correction: Optional[str] = None


class Avatar:
    """
    数字分身

    快速示例：

        # 创建新分身
        avatar, mnemonic = await Avatar.create(name="小明")
        print("请保存助记词:", mnemonic)

        # 对话
        reply = await avatar.chat("今天发生了一件有趣的事...")
        print(reply)

        # 给反馈
        avatar.feedback("positive")      # 好的回复
        avatar.feedback("correction", "我期望的是...")  # 纠正

        # 保存
        await avatar.save()

        # 下次从助记词加载
        avatar = await Avatar.load("~/.oap/my_avatar", mnemonic)
    """

    def __init__(self, config: AvatarConfig, key_ring: KeyRing):
        self.config = config
        self.key_ring = key_ring
        self._memory: Optional[MemoryStore] = None
        self._compute: Optional[ComputeClient] = None
        self._session: List[ConversationTurn] = []   # 当前会话历史
        self._pending_training_signals: list = []
        self._api_key: str = ""   # API Key 只驻留内存，不持久化

    # ── 工厂方法 ──────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        name: str,
        mnemonic: Optional[str] = None,
        base_dir: Optional[str] = None,
        base_model: str = "meta-llama/Meta-Llama-3-8B",
        inference_mode: InferenceMode = InferenceMode.MOCK,
        system_prompt: Optional[str] = None,
        # ── 外部推理参数 ──
        api_base: str = "https://api.openai.com/v1",
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        api_type: str = "openai",
    ) -> tuple["Avatar", str]:
        """
        创建全新分身。

        支持的推理模式：
          MOCK     → 模拟回复（默认，无需任何 API Key）
          EXTERNAL → 调用外部 API（OpenAI / Claude / DeepSeek 等）
          LOCAL    → 本地 transformers 推理（需要 GPU）
          REMOTE   → 去中心化算力市场

        Returns:
            (avatar, mnemonic)  — 助记词必须由 Owner 安全保存！
        """
        if mnemonic is None:
            mnemonic = generate_mnemonic()

        avatar_did = KeyRing.did_from_mnemonic(mnemonic)
        key_ring = KeyRing.from_mnemonic(mnemonic, avatar_did)

        if base_dir is None:
            base_dir = str(Path.home() / ".oap" / "avatars" / avatar_did[-12:])

        data_dir = str(Path(base_dir) / "data")
        weights_dir = str(Path(base_dir) / "weights")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        Path(weights_dir).mkdir(parents=True, exist_ok=True)

        prompt = system_prompt or f"你是 {name} 的数字分身，完全了解 {name} 的思想、价值观和行为方式，请以第一人称回应。"

        # 外部模式时 model 字段存实际模型名
        effective_model = model if inference_mode == InferenceMode.EXTERNAL else base_model

        config = AvatarConfig(
            avatar_did=avatar_did,
            name=name,
            created_at=time.time(),
            base_model=effective_model,
            inference_mode=inference_mode.value,
            system_prompt=prompt,
            data_dir=data_dir,
            weights_dir=weights_dir,
            api_base=api_base,
            api_type=api_type,
            model=model,
        )

        avatar = cls(config, key_ring)
        # 保存 API Key 到实例（不持久化到配置文件）
        avatar._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        await avatar._init_subsystems()
        await avatar.save()

        return avatar, mnemonic

    @classmethod
    async def load(
        cls,
        avatar_dir: str,
        mnemonic: str,
        api_key: str = "",
        # 以下参数可覆盖分身保存的配置（全局 config 优先）
        api_base: str = "",
        api_type: str = "",
        model: str = "",
    ) -> "Avatar":
        """
        从已有分身目录 + 助记词加载分身。

        api_key/api_base/api_type/model 可覆盖分身保存的旧配置。
        这让 oap config set 的值始终生效，无需重建分身。
        """
        avatar_dir = Path(avatar_dir).expanduser()
        config_path = avatar_dir / "config.enc.json"

        if not config_path.exists():
            raise FileNotFoundError(f"找不到分身配置文件: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            blob = json.load(f)

        avatar_did_hint = blob.get("_did_hint", "")
        if not avatar_did_hint:
            avatar_did_hint = KeyRing.did_from_mnemonic(mnemonic)

        key_ring = KeyRing.from_mnemonic(mnemonic, avatar_did_hint)
        config_bytes = key_ring.decrypt(blob["config"])
        config = AvatarConfig.from_dict(json.loads(config_bytes))

        key_ring = KeyRing.from_mnemonic(mnemonic, config.avatar_did)

        avatar = cls(config, key_ring)
        # 注入 API Key（从参数或环境变量）
        avatar._api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")

        # 全局 config 优先覆盖分身保存的旧值（必须在 _init_subsystems 之前！））
        if api_base:
            avatar.config.api_base = api_base
        if api_type:
            avatar.config.api_type = api_type
        if model:
            avatar.config.model = model

        await avatar._init_subsystems()
        return avatar

    # ── 核心功能 ──────────────────────────────────────────

    async def chat(
        self,
        message: str,
        use_memory: bool = True,
        importance: float = 0.5,
    ) -> str:
        """
        与分身对话。

        1. 记录输入到当前会话
        2. 从记忆中召回相关上下文
        3. 执行推理
        4. 将交互写入记忆

        Returns:
            分身的回复文本
        """
        if self.config.state == AvatarState.FROZEN:
            raise RuntimeError("分身已被紧急冻结，无法交互")
        if self.config.state == AvatarState.SLEEPING:
            raise RuntimeError("分身正在休眠，请先唤醒")

        # 记录 owner 输入
        turn = ConversationTurn("owner", message)
        self._session.append(turn)

        # 记忆召回
        context_entries = None
        if use_memory:
            context_entries = self._memory.recent(10)
            keyword_matches = self._memory.search(message[:20], limit=5)
            # 合并去重
            seen = {e.id for e in context_entries}
            for e in keyword_matches:
                if e.id not in seen:
                    context_entries.append(e)
                    seen.add(e.id)

        # 推理
        result: InferenceResult = await self._compute.infer(
            message, context_entries=context_entries
        )

        # 记录分身回复
        avatar_turn = ConversationTurn("avatar", result.text)
        self._session.append(avatar_turn)

        # 写入记忆
        self._memory.add(MemoryEntry(
            content=f"Owner: {message}\nAvatar: {result.text}",
            type=MemoryType.CONVERSATION,
            importance=importance,
        ))

        return result.text

    def feedback(
        self,
        kind: str,
        correction: Optional[str] = None,
        importance_boost: float = 0.2,
    ) -> None:
        """
        对分身最后一条回复给出反馈。

        kind: "positive" | "negative" | "correction"
        correction: 当 kind=="correction" 时，期望的正确回复
        """
        # 找到最后一个 avatar turn
        avatar_turns = [t for t in self._session if t.role == "avatar"]
        if not avatar_turns:
            return
        last_avatar = avatar_turns[-1]
        last_avatar.feedback = kind
        last_avatar.correction = correction

        # 提取训练信号
        owner_turns = [t for t in self._session if t.role == "owner"]
        if not owner_turns:
            return
        prompt = owner_turns[-1].content

        if kind == "positive":
            self._pending_training_signals.append({
                "type": "style_feedback",
                "prompt": prompt,
                "chosen": last_avatar.content,
                "importance": 0.7,
            })
        elif kind == "negative" and correction:
            self._pending_training_signals.append({
                "type": "preference",
                "prompt": prompt,
                "chosen": correction,
                "rejected": last_avatar.content,
                "importance": 0.9,
            })
        elif kind == "correction" and correction:
            self._pending_training_signals.append({
                "type": "correction",
                "prompt": prompt,
                "chosen": correction,
                "importance": 0.95,
            })

    async def train(
        self,
        method: str = "auto",
        epochs: int = 1,
        min_signals: int = 5,
    ) -> Optional[str]:
        """
        从积累的训练信号中训练分身的个人化权重。

        Returns:
            加密权重文件路径，无信号时返回 None
        """
        if len(self._pending_training_signals) < min_signals:
            print(f"[Avatar] 训练信号不足（{len(self._pending_training_signals)}/{min_signals}），跳过训练")
            return None

        try:
            from oap_lora.training.engine import PersonaTrainer, PersonaWeightConfig, TrainingSignal
        except ImportError:
            raise RuntimeError(
                "训练功能需要安装 oap-lora-framework：\n"
                "  pip install -e ../lora-framework"
            )

        config = PersonaWeightConfig(base_model_name=self.config.base_model)
        trainer = PersonaTrainer(config=config, key_ring=self.key_ring)

        signals = [
            TrainingSignal(
                type=s["type"],
                prompt=s["prompt"],
                chosen=s.get("chosen"),
                rejected=s.get("rejected"),
                importance=s.get("importance", 0.5),
            )
            for s in self._pending_training_signals
        ]
        trainer.add_training_signals(signals)

        output_dir = str(Path(self.config.weights_dir) / "persona_weight")
        Path(output_dir).mkdir(exist_ok=True)

        print(f"[Avatar] 开始训练，方法={method}，信号数={len(signals)}")

        dpo_signals = [s for s in signals if s.type == "preference"]
        if method == "dpo" or (method == "auto" and dpo_signals):
            weight_path = trainer.train_dpo(output_dir=output_dir, num_epochs=epochs)
        else:
            weight_path = trainer.train_sft(output_dir=output_dir, num_epochs=epochs)

        if weight_path:
            self.config.persona_weight_path = weight_path
            self._pending_training_signals.clear()
            await self.save()
            print(f"[Avatar] 训练完成，权重: {weight_path}")

        return weight_path

    async def reflect(self) -> str:
        """
        生成反思记忆。

        分析近期对话，生成元认知记录（如：「本周我倾向于用比喻解释技术问题」）
        这些反思记忆对后续对话质量有显著提升。
        """
        recent = self._memory.recent(20, MemoryType.CONVERSATION)
        if not recent:
            return "（没有足够的近期对话用于反思）"

        summary_prompt = (
            "以下是最近的对话记录，请生成一段简短的自我反思，"
            "总结你的思维模式、价值倾向和沟通风格：\n\n"
            + "\n".join(f"- {e.content[:100]}" for e in recent[:10])
        )

        result = await self._compute.infer(summary_prompt)
        reflection = result.text

        # 将反思写入高重要性记忆
        self._memory.add(MemoryEntry(
            content=reflection,
            type=MemoryType.REFLECTION,
            importance=0.85,
            source="avatar-reflection",
        ))

        return reflection

    # ── 状态控制 ──────────────────────────────────────────

    def sleep(self):
        """分身进入休眠（Owner 主动）"""
        self.config.state = AvatarState.SLEEPING
        print(f"[Avatar] {self.config.name} 已进入休眠")

    def wake(self):
        """唤醒休眠的分身"""
        if self.config.state == AvatarState.SLEEPING:
            self.config.state = AvatarState.ACTIVE
            print(f"[Avatar] {self.config.name} 已唤醒")

    def emergency_freeze(self):
        """紧急冻结（所有交互立即停止）"""
        self.config.state = AvatarState.FROZEN
        self._session.clear()
        print(f"[Avatar] ⚠️ {self.config.name} 已紧急冻结")

    async def destroy(self, confirm: bool = False):
        """
        安全销毁分身（不可逆！）

        删除所有记忆、权重、配置文件，随机覆写后删除。
        """
        if not confirm:
            raise ValueError("销毁操作不可逆，请传入 confirm=True 确认")

        data_path = Path(self.config.data_dir)
        weights_path = Path(self.config.weights_dir)
        config_file = data_path.parent / "config.enc.json"

        # 随机覆写所有文件
        for path in [data_path, weights_path]:
            if path.exists():
                for f in path.rglob("*"):
                    if f.is_file():
                        size = f.stat().st_size
                        with open(f, "wb") as fp:
                            fp.write(os.urandom(max(size, 1)))
                        f.unlink()

        if config_file.exists():
            config_file.unlink()

        print(f"[Avatar] {self.config.name} 已安全销毁，所有数据已擦除")

    # ── 持久化 ────────────────────────────────────────────

    async def save(self):
        """将配置加密保存到磁盘（不含私钥，仅配置元数据）"""
        config_data = self.config.to_dict()
        config_bytes = json.dumps(config_data, ensure_ascii=False).encode()
        blob = self.key_ring.encrypt(config_bytes)

        config_file = Path(self.config.data_dir).parent / "config.enc.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({
                "_did_hint": self.config.avatar_did,  # DID 明文存储（公开信息）
                "config": blob,
            }, f, indent=2, ensure_ascii=False)

    # ── 状态查询 ──────────────────────────────────────────

    def status(self) -> dict:
        """返回分身当前状态摘要"""
        mem_stats = self._memory.stats() if self._memory else {}
        return {
            "name": self.config.name,
            "did": self.config.avatar_did,
            "state": self.config.state.value,
            "created": time.strftime("%Y-%m-%d", time.localtime(self.config.created_at)),
            "memory": mem_stats,
            "pendingSignals": len(self._pending_training_signals),
            "sessionTurns": len(self._session),
            "hasPersonaWeight": bool(self.config.persona_weight_path),
            "inferenceMode": self.config.inference_mode,
        }

    def clear_session(self):
        """清除当前会话上下文（不影响记忆存储）"""
        self._session.clear()

    # ── 内部初始化 ────────────────────────────────────────

    async def _init_subsystems(self):
        """初始化记忆存储和推理客户端"""
        self._memory = MemoryStore(self.config.data_dir, self.key_ring)
        self._compute = ComputeClient(
            key_ring=self.key_ring,
            memory_store=self._memory,
            mode=InferenceMode(self.config.inference_mode),
            base_model_name=self.config.base_model,
            persona_weight_path=self.config.persona_weight_path,
            system_prompt=self.config.system_prompt.format(name=self.config.name),
            # 外部推理配置
            api_base=self.config.api_base,
            api_key=self._api_key,
            model=self.config.model,
            api_type=self.config.api_type,
        )

    def __repr__(self) -> str:
        return (
            f"Avatar(name={self.config.name!r}, "
            f"did={self.config.avatar_did[-16:]!r}, "
            f"state={self.config.state.value})"
        )
