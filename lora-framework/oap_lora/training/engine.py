"""
OAP 个人化训练引擎

支持在本地设备上对开源 LLM 进行 LoRA 个人化微调，
训练产生的权重加密存储，完全归 Owner 控制。

支持的训练方式：
1. SFT (Supervised Fine-Tuning) - 从对话记录学习说话风格
2. DPO (Direct Preference Optimization) - 从偏好对比学习价值观
3. Incremental Update - 增量更新，持续进化
"""

import os
import json
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Any
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from peft import LoraConfig, get_peft_model, TaskType, PeftModel
from datasets import Dataset

from ..crypto.keys import AvatarKeyRing


@dataclass
class PersonaWeightConfig:
    """个人化权重配置 - 对应 OAP 协议中的 Persona Weight 格式"""

    base_model_name: str = "meta-llama/Meta-Llama-3-8B"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: list[str] = field(
        default_factory=lambda: ["q_proj", "v_proj", "k_proj", "o_proj"]
    )
    training_method: str = "oap-incremental-dpo"

    def to_lora_config(self) -> LoraConfig:
        """转换为 PEFT LoRAConfig"""
        return LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=self.lora_rank,
            lora_alpha=self.lora_alpha,
            lora_dropout=self.lora_dropout,
            target_modules=self.target_modules,
        )

    def to_oap_format(self) -> dict:
        """输出 OAP 协议格式的 Persona Weight 元数据"""
        return {
            "format": "oap-persona-weight-v1",
            "baseModel": {
                "name": self.base_model_name,
                "hash": "",  # 训练后填充
                "source": f"https://huggingface.co/{self.base_model_name}",
            },
            "weights": {
                "method": "lora",
                "rank": self.lora_rank,
                "alpha": self.lora_alpha,
                "targetModules": self.target_modules,
                "encryptionMethod": "aes-256-gcm",
            },
            "training": {
                "trainingMethod": self.training_method,
            },
        }


@dataclass
class TrainingSignal:
    """训练信号 - Owner 与分身交互产生的训练数据"""

    type: str  # "preference" | "correction" | "new_knowledge" | "style_feedback"
    prompt: str
    chosen: Optional[str] = None  # DPO: 偏好的回复
    rejected: Optional[str] = None  # DPO: 不偏好的回复
    context: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5  # 0-1, 用于采样权重

    def to_dpo_pair(self) -> Optional[dict]:
        """转换为 DPO 训练对"""
        if self.chosen and self.rejected:
            return {
                "prompt": self.prompt,
                "chosen": self.chosen,
                "rejected": self.rejected,
            }
        return None

    def to_sft_pair(self) -> Optional[dict]:
        """转换为 SFT 训练对"""
        if self.chosen:
            return {
                "prompt": self.prompt,
                "response": self.chosen,
            }
        return None


class PersonaTrainer:
    """个人化训练器 - 在本地设备上训练分身的 LoRA 权重"""

    def __init__(
        self,
        config: PersonaWeightConfig,
        key_ring: AvatarKeyRing,
        device: str = "auto",
        quantize: bool = True,
    ):
        self.config = config
        self.key_ring = key_ring
        self.device = device
        self.quantize = quantize

        self.model = None
        self.tokenizer = None
        self.training_signals: list[TrainingSignal] = []
        self.total_steps = 0

    def load_base_model(self):
        """加载基础模型"""
        from rich.console import Console
        console = Console()

        console.print(f"[blue]加载基础模型:[/blue] {self.config.base_model_name}")

        # 量化配置（4-bit，适合消费级设备）
        if self.quantize:
            from transformers import BitsAndBytesConfig
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config.base_model_name,
                quantization_config=bnb_config,
                device_map=self.device,
                trust_remote_code=True,
            )
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config.base_model_name,
                device_map=self.device,
                torch_dtype=torch.bfloat16,
                trust_remote_code=True,
            )

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.config.base_model_name,
            trust_remote_code=True,
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        console.print("[green]基础模型加载完成[/green]")

    def load_existing_weights(self, encrypted_weight_path: str):
        """加载已有的加密 LoRA 权重"""
        with open(encrypted_weight_path, "rb") as f:
            encrypted_data = json.load(f)

        # 解密权重
        decrypted_bytes = self.key_ring.decrypt_weight(encrypted_data)

        # 保存临时文件供 PEFT 加载
        temp_path = encrypted_weight_path + ".tmp"
        with open(temp_path, "wb") as f:
            f.write(decrypted_bytes)

        # 应用 LoRA 权重
        self.model = PeftModel.from_pretrained(self.model, temp_path)

        # 删除临时文件
        os.remove(temp_path)

    def add_training_signal(self, signal: TrainingSignal):
        """添加训练信号"""
        self.training_signals.append(signal)

    def add_training_signals(self, signals: list[TrainingSignal]):
        """批量添加训练信号"""
        self.training_signals.extend(signals)

    def train_sft(
        self,
        output_dir: str = "./avatar_weights",
        num_epochs: int = 3,
        batch_size: int = 1,
        gradient_accumulation_steps: int = 8,
        learning_rate: float = 2e-4,
        max_seq_length: int = 512,
    ) -> str:
        """SFT 训练 - 从对话记录学习说话风格

        Returns:
            输出目录路径
        """
        from rich.console import Console
        from trl import SFTTrainer
        console = Console()

        if not self.model:
            self.load_base_model()

        # 准备数据
        sft_data = []
        for signal in self.training_signals:
            pair = signal.to_sft_pair()
            if pair:
                sft_data.append({
                    "text": f"### User:\n{pair['prompt']}\n\n### Assistant:\n{pair['response']}"
                })

        if not sft_data:
            console.print("[red]没有可用的 SFT 训练数据[/red]")
            return ""

        dataset = Dataset.from_list(sft_data)
        console.print(f"[blue]SFT 训练数据:[/blue] {len(dataset)} 条")

        # 配置 LoRA
        lora_config = self.config.to_lora_config()
        self.model = get_peft_model(self.model, lora_config)
        self.model.print_trainable_parameters()

        # 训练参数
        training_args = TrainingArguments(
            output_dir=output_dir,
            num_train_epochs=num_epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            learning_rate=learning_rate,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10,
            save_strategy="epoch",
            optim="paged_adamw_8bit",
            gradient_checkpointing=True,
            report_to="none",  # 不上传到任何云端
        )

        # 训练
        trainer = SFTTrainer(
            model=self.model,
            args=training_args,
            train_dataset=dataset,
            max_seq_length=max_seq_length,
            tokenizer=self.tokenizer,
        )

        console.print("[blue]开始 SFT 训练...[/blue]")
        trainer.train()
        self.total_steps += trainer.state.global_step

        # 保存
        self.model.save_pretrained(output_dir)
        self.tokenizer.save_pretrained(output_dir)
        console.print(f"[green]训练完成，权重保存到: {output_dir}[/green]")

        # 加密并导出
        encrypted_path = self._export_encrypted_weights(output_dir)
        return encrypted_path

    def train_dpo(
        self,
        output_dir: str = "./avatar_weights",
        num_epochs: int = 1,
        batch_size: int = 1,
        gradient_accumulation_steps: int = 8,
        learning_rate: float = 5e-5,
        beta: float = 0.1,
        max_seq_length: int = 512,
    ) -> str:
        """DPO 训练 - 从偏好对比学习价值观

        Returns:
            加密权重文件路径
        """
        from rich.console import Console
        from trl import DPOTrainer, DPOConfig
        console = Console()

        if not self.model:
            self.load_base_model()

        # 准备 DPO 数据
        dpo_data = []
        for signal in self.training_signals:
            pair = signal.to_dpo_pair()
            if pair:
                dpo_data.append(pair)

        if not dpo_data:
            console.print("[red]没有可用的 DPO 训练数据[/red]")
            return ""

        dataset = Dataset.from_list(dpo_data)
        console.print(f"[blue]DPO 训练数据:[/blue] {len(dataset)} 条")

        # 配置 LoRA
        lora_config = self.config.to_lora_config()
        self.model = get_peft_model(self.model, lora_config)
        self.model.print_trainable_parameters()

        # DPO 训练参数
        training_args = DPOConfig(
            output_dir=output_dir,
            num_train_epochs=num_epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            learning_rate=learning_rate,
            beta=beta,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10,
            optim="paged_adamw_8bit",
            gradient_checkpointing=True,
            report_to="none",
        )

        # 训练
        trainer = DPOTrainer(
            model=self.model,
            args=training_args,
            train_dataset=dataset,
            processing_class=self.tokenizer,
        )

        console.print("[blue]开始 DPO 训练...[/blue]")
        trainer.train()
        self.total_steps += trainer.state.global_step

        # 保存
        self.model.save_pretrained(output_dir)
        self.tokenizer.save_pretrained(output_dir)
        console.print(f"[green]训练完成，权重保存到: {output_dir}[/green]")

        # 加密并导出
        encrypted_path = self._export_encrypted_weights(output_dir)
        return encrypted_path

    def _export_encrypted_weights(self, weight_dir: str) -> str:
        """将 LoRA 权重加密导出为 OAP 格式"""
        from rich.console import Console
        from safetensors.torch import load_file
        console = Console()

        # 找到 safetensors 文件
        weight_files = list(Path(weight_dir).glob("*.safetensors"))
        if not weight_files:
            # 尝试 bin 文件
            weight_files = list(Path(weight_dir).glob("*.bin"))

        if not weight_files:
            console.print("[red]找不到权重文件[/red]")
            return ""

        # 读取权重
        if weight_files[0].suffix == ".safetensors":
            weight_data = load_file(str(weight_files[0]))
        else:
            weight_data = torch.load(str(weight_files[0]), map_location="cpu")

        # 序列化权重
        import io
        buffer = io.BytesIO()
        torch.save(weight_data, buffer)
        weight_bytes = buffer.getvalue()

        # 计算哈希
        weight_hash = hashlib.sha256(weight_bytes).hexdigest()

        # 加密
        encrypted = self.key_ring.encrypt_weight(weight_bytes)

        # 构建 OAP 格式输出
        oap_weight = self.config.to_oap_format()
        oap_weight["baseModel"]["hash"] = f"sha256:{weight_hash}"
        oap_weight["weights"]["encryptedData"] = encrypted["encrypted"].hex()
        oap_weight["weights"]["iv"] = encrypted["iv"].hex()
        oap_weight["weights"]["tag"] = encrypted["tag"].hex()
        oap_weight["training"]["totalSteps"] = self.total_steps
        oap_weight["training"]["lastUpdateDate"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 签名
        signature = self.key_ring.sign(json.dumps(oap_weight, sort_keys=True).encode())
        oap_weight["signature"] = signature.hex()

        # 保存
        encrypted_path = os.path.join(weight_dir, "persona_weight.oap.json")
        with open(encrypted_path, "w", encoding="utf-8") as f:
            json.dump(oap_weight, f, indent=2, ensure_ascii=False)

        console.print(f"[green]加密权重已导出: {encrypted_path}[/green]")
        return encrypted_path

    def incremental_update(
        self,
        new_signals: list[TrainingSignal],
        existing_weight_path: Optional[str] = None,
        output_dir: str = "./avatar_weights",
    ) -> str:
        """增量更新 - 从新的训练信号持续进化

        这是分身日常更新的主要方式：
        1. 加载现有权重
        2. 用新信号做轻量训练
        3. 保存新的加密权重
        """
        from rich.console import Console
        console = Console()

        self.add_training_signals(new_signals)

        # 如果有已有权重，先加载
        if existing_weight_path:
            console.print("[blue]加载已有权重进行增量更新...[/blue]")
            self.load_base_model()
            self.load_existing_weights(existing_weight_path)

        # 根据 training_method 选择训练方式
        if self.config.training_method == "oap-incremental-dpo":
            # 优先使用 DPO（偏好学习）
            dpo_signals = [s for s in new_signals if s.type == "preference"]
            sft_signals = [s for s in new_signals if s.type != "preference"]

            if dpo_signals:
                self.training_signals = dpo_signals
                return self.train_dpo(
                    output_dir=output_dir,
                    num_epochs=1,  # 增量更新只用 1 个 epoch
                    learning_rate=1e-5,  # 更小的学习率
                )
            elif sft_signals:
                self.training_signals = sft_signals
                return self.train_sft(
                    output_dir=output_dir,
                    num_epochs=1,
                    learning_rate=1e-5,
                )

        # 默认走 SFT
        return self.train_sft(output_dir=output_dir, num_epochs=1, learning_rate=1e-5)
