"""
OAP 算力节点运行时

算力节点在 TEE 内运行分身推理任务的核心逻辑：
1. 接受加密任务（加密权重 + 加密输入）
2. 在 TEE 内解密执行
3. 生成 TEE 认证报告 + ZK 证明
4. 返回加密结果

关键安全保证：
- 节点操作员无法读取分身内容
- 推理结果可被第三方验证
- 执行过程审计可追溯
"""

import asyncio
import json
import os
import time
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TEEType(Enum):
    SGX = "sgx"
    SEV = "sev"
    NITRO = "nitro"
    SIMULATED = "simulated"  # 用于开发测试


@dataclass
class NodeConfig:
    """节点配置"""
    node_address: str
    tee_type: TEEType = TEEType.SIMULATED
    mr_enclave: str = ""        # 可信镜像哈希
    max_concurrent_tasks: int = 4
    stake_amount_wei: int = 0
    endpoint: str = "http://localhost:8080"


@dataclass
class TaskExecution:
    """任务执行记录"""
    task_id: str
    avatar_did: str
    started_at: float
    completed_at: Optional[float] = None
    success: bool = False
    error: Optional[str] = None
    result_cid: Optional[str] = None


class TEEEnclave:
    """
    TEE 可信执行环境模拟

    在真实部署中，这里应该：
    - 在 Intel SGX Enclave 内运行
    - 使用 Gramine / Occlum 等 LibOS 框架
    - 内存自动加密，外部不可读取
    - 生成真实的 Remote Attestation Report
    """

    def __init__(self, tee_type: TEEType, mr_enclave: str):
        self.tee_type = tee_type
        self.mr_enclave = mr_enclave
        self._initialized = False

    async def initialize(self):
        """初始化 TEE 环境"""
        print(f"[TEE] 初始化 {self.tee_type.value} 环境...")
        await asyncio.sleep(0.1)
        self._initialized = True
        print(f"[TEE] 环境就绪 | mrEnclave: {self.mr_enclave[:16]}...")

    async def execute_inference(
        self,
        encrypted_weight_data: bytes,
        encrypted_input_data: bytes,
        constitution_data: bytes,
        weight_decryption_key: bytes,  # 由 Owner 提供，在 TEE 内使用
        input_decryption_key: bytes,
    ) -> tuple[bytes, bytes, bytes]:
        """
        在 TEE 内执行推理

        Returns:
            (encrypted_result, tee_attestation, zk_proof)
        """
        if not self._initialized:
            raise RuntimeError("TEE 未初始化")

        print("[TEE] 开始在可信环境内执行推理...")

        # Step 1: 在 TEE 内解密权重（外部不可见）
        print("[TEE] 解密分身权重...")
        # weight_plaintext = decrypt(encrypted_weight_data, weight_decryption_key)

        # Step 2: 在 TEE 内解密输入（外部不可见）
        print("[TEE] 解密推理输入...")
        # input_plaintext = decrypt(encrypted_input_data, input_decryption_key)

        # Step 3: 检查宪法规则
        print("[TEE] 检查宪法规则...")
        # constitution_check = check_constitution(input_plaintext, constitution_data)

        # Step 4: 执行推理（在 TEE 内，外部不可见）
        print("[TEE] 执行模型推理...")
        await asyncio.sleep(0.5)  # 模拟推理时间
        # result_plaintext = model.generate(input_plaintext)

        # Step 5: 加密结果
        print("[TEE] 加密推理结果...")
        mock_result = json.dumps({"response": "TEE 内的推理结果（模拟）"}).encode()
        encrypted_result = self._encrypt_for_owner(mock_result, input_decryption_key)

        # Step 6: 生成 TEE 认证报告
        print("[TEE] 生成 TEE 认证报告...")
        attestation = self._generate_attestation(hashlib.sha256(encrypted_result).digest())

        # Step 7: 生成 ZK 证明（证明推理确实由正确模型执行）
        print("[TEE] 生成 ZK 证明...")
        zk_proof = self._generate_zk_proof(encrypted_result, attestation)

        print("[TEE] 执行完成")
        return encrypted_result, attestation, zk_proof

    def _encrypt_for_owner(self, data: bytes, key: bytes) -> bytes:
        """加密结果供 Owner 解密"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        iv = os.urandom(12)
        aesgcm = AESGCM(key[:32])
        ciphertext = aesgcm.encrypt(iv, data, None)
        result = {"iv": iv.hex(), "ciphertext": ciphertext.hex()}
        return json.dumps(result).encode()

    def _generate_attestation(self, result_hash: bytes) -> bytes:
        """
        生成 TEE 远程认证报告

        真实实现中：
        - SGX: 调用 sgx_create_report(), sgx_get_quote()
        - SEV: 调用 SEV-SNP attestation API
        - Nitro: 调用 nsm_get_attestation_doc()
        """
        if self.tee_type == TEEType.SIMULATED:
            # 模拟认证报告
            attestation = {
                "teeType": self.tee_type.value,
                "mrEnclave": self.mr_enclave,
                "resultHash": result_hash.hex(),
                "timestamp": time.time(),
                "signature": hashlib.sha256(result_hash + self.mr_enclave.encode()).hexdigest(),
            }
            return json.dumps(attestation).encode()

        raise NotImplementedError(f"TEE type {self.tee_type} not implemented in this version")

    def _generate_zk_proof(self, encrypted_result: bytes, attestation: bytes) -> bytes:
        """
        生成 ZK 证明

        真实实现中应使用：
        - circom + snarkjs（Groth16）
        - Halo2
        - SP1 / RISC0（zkVM）

        证明内容：
        "我知道某个 LoRA 权重 W，使得模型 M+W 对输入 X 产生了输出 Y"
        同时不泄露 W 的内容。
        """
        mock_proof = {
            "type": "mock-groth16",
            "publicInputs": {
                "resultHash": hashlib.sha256(encrypted_result).hexdigest(),
                "modelHash": hashlib.sha256(attestation).hexdigest(),
            },
            "proof": "0x" + os.urandom(256).hex(),
        }
        return json.dumps(mock_proof).encode()


class ComputeNode:
    """OAP 算力节点"""

    def __init__(self, config: NodeConfig):
        self.config = config
        self.enclave = TEEEnclave(config.tee_type, config.mr_enclave)
        self.execution_history: list[TaskExecution] = []
        self._semaphore = None

    async def start(self):
        """启动节点"""
        print(f"\n{'='*50}")
        print(f"OAP 算力节点启动")
        print(f"  地址: {self.config.node_address}")
        print(f"  TEE: {self.config.tee_type.value}")
        print(f"  并发任务: {self.config.max_concurrent_tasks}")
        print(f"  端点: {self.config.endpoint}")
        print(f"{'='*50}\n")

        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)
        await self.enclave.initialize()
        print("[Node] 节点就绪，等待任务...")

    async def handle_task(
        self,
        task_id: str,
        avatar_did: str,
        encrypted_weight_cid: str,
        encrypted_input_cid: str,
        constitution_cid: str,
        weight_decryption_key: bytes,
        input_decryption_key: bytes,
    ) -> Optional[dict]:
        """
        处理单个推理任务

        Args:
            weight_decryption_key: Owner 提供的权重解密密钥
                （在真实实现中，这通过 ECDH 密钥协商建立安全通道传输）
            input_decryption_key: 输入解密密钥

        Returns:
            结果字典：{encryptedResultCID, teeAttestation, zkProof}
        """
        execution = TaskExecution(
            task_id=task_id,
            avatar_did=avatar_did,
            started_at=time.time(),
        )

        async with self._semaphore:
            print(f"\n[Node] 接受任务: {task_id[:16]}... | 分身: {avatar_did[:30]}...")

            try:
                # 从 IPFS 获取加密数据
                print(f"[Node] 从 IPFS 获取加密权重: {encrypted_weight_cid[:20]}...")
                encrypted_weight = await self._fetch_from_ipfs(encrypted_weight_cid)

                print(f"[Node] 从 IPFS 获取加密输入: {encrypted_input_cid[:20]}...")
                encrypted_input = await self._fetch_from_ipfs(encrypted_input_cid)

                print(f"[Node] 从 IPFS 获取宪法规则: {constitution_cid[:20]}...")
                constitution = await self._fetch_from_ipfs(constitution_cid)

                # 在 TEE 内执行推理
                encrypted_result, attestation, zk_proof = await self.enclave.execute_inference(
                    encrypted_weight_data=encrypted_weight,
                    encrypted_input_data=encrypted_input,
                    constitution_data=constitution,
                    weight_decryption_key=weight_decryption_key,
                    input_decryption_key=input_decryption_key,
                )

                # 上传加密结果到 IPFS
                result_cid = await self._upload_to_ipfs(encrypted_result)

                execution.completed_at = time.time()
                execution.success = True
                execution.result_cid = result_cid

                elapsed = execution.completed_at - execution.started_at
                print(f"[Node] 任务完成: {task_id[:16]}... | 耗时: {elapsed:.2f}s")

                return {
                    "encryptedResultCID": result_cid,
                    "teeAttestation": attestation.decode(),
                    "zkProof": zk_proof.decode(),
                    "elapsedSeconds": elapsed,
                }

            except Exception as e:
                execution.error = str(e)
                execution.completed_at = time.time()
                print(f"[Node] 任务失败: {task_id[:16]}... | 错误: {e}")
                return None

            finally:
                self.execution_history.append(execution)

    async def _fetch_from_ipfs(self, cid: str) -> bytes:
        """从 IPFS 获取数据（模拟）"""
        await asyncio.sleep(0.05)
        return f"encrypted_data_for_{cid}".encode()

    async def _upload_to_ipfs(self, data: bytes) -> str:
        """上传数据到 IPFS（模拟）"""
        await asyncio.sleep(0.05)
        return "QmResult" + hashlib.sha256(data).hexdigest()[:40]

    def get_stats(self) -> dict:
        """获取节点统计信息"""
        completed = [e for e in self.execution_history if e.success]
        failed = [e for e in self.execution_history if not e.success]
        avg_time = (
            sum((e.completed_at - e.started_at) for e in completed) / len(completed)
            if completed else 0
        )
        return {
            "nodeAddress": self.config.node_address,
            "totalTasks": len(self.execution_history),
            "completedTasks": len(completed),
            "failedTasks": len(failed),
            "successRate": len(completed) / max(len(self.execution_history), 1),
            "avgExecutionTime": round(avg_time, 2),
        }
