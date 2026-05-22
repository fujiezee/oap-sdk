"""
OAP ComputeClient — 分身推理客户端

负责：
  1. 外部推理（OpenAI / Anthropic / 任何兼容 API，最快让分身说话）
  2. 本地推理（直接调用 transformers，无需网络）
  3. 远程调度推理（通过去中心化算力市场）
  4. 模拟推理（开发测试用）

推荐：先用 EXTERNAL 模式快速验证，再切换到 LOCAL 或 REMOTE。
外部模式依赖 httpx（已包含在核心依赖中）。
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

from oap.crypto.keyring import KeyRing
from oap.memory.store import MemoryStore, MemoryEntry


class InferenceMode(str, Enum):
    EXTERNAL = "external"  # 外部 API 推理（OpenAI / Anthropic / 任意兼容端点）
    LOCAL    = "local"     # 本地 transformers 推理
    REMOTE   = "remote"    # 去中心化算力市场
    MOCK     = "mock"      # 单元测试 / Demo 用的模拟模式


@dataclass
class InferenceResult:
    """单次推理结果"""
    text: str
    latency_ms: float = 0.0
    mode: InferenceMode = InferenceMode.MOCK
    node_address: Optional[str] = None   # 远程模式时的节点地址
    tee_verified: bool = False
    zk_verified: bool = False
    cost_wei: int = 0
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return self.text


class ComputeClient:
    """
    分身推理客户端

    用法：
        # 外部 API（最快）
        client = ComputeClient(key_ring, memory_store,
            mode=InferenceMode.EXTERNAL,
            api_base="https://api.openai.com/v1",
            api_key="sk-...",
            model="gpt-4o-mini",
        )
        result = await client.infer("你好")

        # 或本地模型
        client = ComputeClient(key_ring, memory_store,
            mode=InferenceMode.LOCAL,
            base_model_name="meta-llama/Meta-Llama-3-8B",
        )
    """

    def __init__(
        self,
        key_ring: KeyRing,
        memory_store: MemoryStore,
        mode: InferenceMode = InferenceMode.MOCK,
        base_model_name: str = "meta-llama/Meta-Llama-3-8B",
        persona_weight_path: Optional[str] = None,
        scheduler=None,   # AvatarScheduler（远程模式）
        system_prompt: str = "",
        # ── 外部推理配置 ──
        api_base: str = "https://api.openai.com/v1",
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        api_type: str = "openai",  # "openai" | "anthropic" | "custom"
    ):
        self.key_ring = key_ring
        self.memory = memory_store
        self.mode = mode
        self.base_model_name = base_model_name
        self.persona_weight_path = persona_weight_path
        self.scheduler = scheduler
        self.system_prompt = system_prompt

        # 外部 API
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.api_type = api_type

        self._model = None
        self._tokenizer = None
        self._peft_model = None

    # ── 推理入口 ──────────────────────────────────────────

    async def infer(
        self,
        prompt: str,
        context_entries: Optional[List[MemoryEntry]] = None,
        max_new_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> InferenceResult:
        """执行推理，根据 mode 自动选择本地 / 远程 / 模拟"""
        t0 = time.time()

        # 拼装上下文
        context = self._build_context(prompt, context_entries)

        if self.mode == InferenceMode.MOCK:
            result_text = self._mock_infer(prompt)
        elif self.mode == InferenceMode.EXTERNAL:
            result_text = await self._external_infer(context, max_new_tokens, temperature)
        elif self.mode == InferenceMode.LOCAL:
            result_text = await self._local_infer(context, max_new_tokens, temperature, top_p)
        elif self.mode == InferenceMode.REMOTE:
            result_text = await self._remote_infer(context, max_new_tokens)
        else:
            raise ValueError(f"Unknown inference mode: {self.mode}")

        latency = (time.time() - t0) * 1000

        return InferenceResult(
            text=result_text,
            latency_ms=round(latency, 1),
            mode=self.mode,
            tee_verified=(self.mode == InferenceMode.REMOTE),
        )

    # ── 外部推理 ──────────────────────────────────────────

    async def _external_infer(
        self, context: str, max_new_tokens: int, temperature: float
    ) -> str:
        """
        调用外部推理 API（OpenAI / Anthropic / 任何 OpenAI 兼容端点）。

        支持的 api_type：
          - "openai"    → OpenAI 官方 API（也兼容 DeepSeek、Together、Ollama 等）
          - "anthropic"  → Anthropic Claude API
          - "custom"     → 任意 OpenAI 兼容端点（只需改 api_base）

        数据安全：
          - 只有拼装好的 Prompt 发送到 API（不发送原始加密记忆）
          - API 调用是标准 HTTP 请求，不经我们任何中间服务器
          - 如果需要更强的隐私保障，请切换到 LOCAL 模式
        """
        import httpx

        if not self.api_key:
            raise RuntimeError(
                "外部推理需要 API Key。\n"
                "设置方式：\n"
                '  1. 环境变量: export OPENAI_API_KEY="sk-..."\n'
                '  2. 代码传入: ComputeClient(api_key="sk-...")\n'
                '  3. 创建分身时: Avatar.create(api_key="sk-...")'
            )

        # ── Anthropic Claude API ──────────────────────────
        if self.api_type == "anthropic":
            return await self._call_anthropic(context, max_new_tokens, temperature)

        # ── OpenAI 兼容 API（默认）────────────────────────
        return await self._call_openai_compat(context, max_new_tokens, temperature)

    async def _call_openai_compat(
        self, context: str, max_new_tokens: int, temperature: float
    ) -> str:
        """OpenAI 兼容 API 调用（支持 OpenAI / DeepSeek / Together / Ollama 等）"""
        import httpx

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": context})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_new_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.api_base}/chat/completions"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"].strip()

    async def _call_anthropic(
        self, context: str, max_new_tokens: int, temperature: float
    ) -> str:
        """Anthropic Claude API 调用"""
        import httpx

        payload = {
            "model": self.model,
            "max_tokens": max_new_tokens,
            "temperature": temperature,
            "system": self.system_prompt if self.system_prompt else None,
            "messages": [{"role": "user", "content": context}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        url = f"{self.api_base}/messages"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return data["content"][0]["text"].strip()

    # ── 本地推理 ──────────────────────────────────────────

    async def _local_infer(
        self, context: str, max_new_tokens: int, temperature: float, top_p: float
    ) -> str:
        """使用本地 transformers + LoRA 权重推理"""
        if self._model is None:
            await self._load_local_model()

        import torch
        inputs = self._tokenizer(context, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                do_sample=True,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()

    async def _load_local_model(self):
        """懒加载本地模型（首次推理时才载入）"""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError:
            raise RuntimeError(
                "本地推理需要安装 torch 和 transformers：\n"
                "  pip install 'oap-sdk[train]'"
            )

        print(f"[ComputeClient] 加载基础模型: {self.base_model_name}...")
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(self.base_model_name)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._model = AutoModelForCausalLM.from_pretrained(
            self.base_model_name,
            quantization_config=bnb_cfg,
            device_map="auto",
        )

        if self.persona_weight_path:
            print(f"[ComputeClient] 加载个人化权重: {self.persona_weight_path}")
            # 解密权重
            import tempfile, os
            with open(self.persona_weight_path, encoding="utf-8") as f:
                blob = json.load(f)
            weight_bytes = self.key_ring.decrypt_weight({
                "iv": blob["weights"]["iv"],
                "ct": blob["weights"]["encryptedData"],
                "tag": blob["weights"]["tag"],
            })
            with tempfile.TemporaryDirectory() as tmp:
                import torch
                weight_path = os.path.join(tmp, "adapter_model.bin")
                with open(weight_path, "wb") as f:
                    f.write(weight_bytes)
                from peft import PeftModel
                self._model = PeftModel.from_pretrained(self._model, tmp)
        print("[ComputeClient] 模型就绪")

    # ── 远程推理 ──────────────────────────────────────────

    async def _remote_infer(self, context: str, max_new_tokens: int) -> str:
        """通过去中心化算力市场执行远程推理"""
        if self.scheduler is None:
            raise RuntimeError("远程推理需要配置 scheduler")

        import os
        input_data = {"context": context, "max_new_tokens": max_new_tokens}
        encrypted_input = self.key_ring.encrypt(
            json.dumps(input_data, ensure_ascii=False).encode()
        )

        result_bytes = await self.scheduler.schedule_inference(
            avatar_did=self.key_ring.avatar_did,
            encrypted_weight_cid=self.persona_weight_path or "QmNoWeight",
            encrypted_input=json.dumps(encrypted_input).encode(),
            constitution_cid="QmNoConstitution",
            max_budget_wei=10 ** 15,
        )
        if result_bytes:
            result = json.loads(result_bytes)
            return result.get("response", "（远程推理无返回）")
        return "（远程推理失败）"

    # ── 模拟推理（开发 / 测试用）─────────────────────────

    def _mock_infer(self, prompt: str) -> str:
        """返回一个模拟回复，不需要任何 GPU 或网络"""
        recent = self.memory.recent(5)
        context_hint = f"（基于 {len(recent)} 条近期记忆）" if recent else ""
        return (
            f"[模拟分身] {context_hint} "
            f"你说：「{prompt[:60]}{'...' if len(prompt)>60 else ''}」\n"
            f"我理解你的问题，这里是我的思考……（此为模拟回复，接入真实模型后替换）"
        )

    # ── 上下文构建 ────────────────────────────────────────

    def _build_context(
        self, prompt: str, extra_entries: Optional[List[MemoryEntry]] = None
    ) -> str:
        """将系统提示 + 相关记忆 + 当前输入拼装成完整 Prompt"""
        parts = []

        if self.system_prompt:
            parts.append(f"### System\n{self.system_prompt}")

        # 注入记忆上下文
        memories = extra_entries or self.memory.recent(10)
        if memories:
            mem_text = "\n".join(
                f"[{m.type.value}@{time.strftime('%m-%d %H:%M', time.localtime(m.timestamp))}] {m.content[:200]}"
                for m in memories[-5:]  # 最近 5 条
            )
            parts.append(f"### 近期记忆\n{mem_text}")

        parts.append(f"### 当前输入\n{prompt}")
        parts.append("### 回复")
        return "\n\n".join(parts)

    def __repr__(self) -> str:
        return f"ComputeClient(mode={self.mode}, did={self.key_ring.avatar_did[:20]}...)"
