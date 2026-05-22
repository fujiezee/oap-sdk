"""
OAP 分身任务调度器

负责在去中心化算力市场中调度分身推理任务：
1. 构建任务请求（加密输入，发布到链上）
2. 监听投标（等待算力节点投标）
3. 自动选标（根据价格 × 信誉 综合评分）
4. 监控执行（跟踪任务状态）
5. 验证结果（解密并验证结果完整性）
6. 处理异常（超时重发、争议提交）
"""

import asyncio
import json
import os
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable
from pathlib import Path
from enum import Enum

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SchedulerState(Enum):
    IDLE = "idle"
    PUBLISHING = "publishing"
    WAITING_BIDS = "waiting_bids"
    ASSIGNED = "assigned"
    WAITING_RESULT = "waiting_result"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ComputeBid:
    """算力节点投标"""
    node_address: str
    price_wei: int
    estimated_seconds: int
    endpoint: str
    reputation: int
    composite_score: float = 0.0

    def calculate_score(self, price_weight: float = 0.6, reputation_weight: float = 0.4) -> float:
        """
        计算综合评分（越高越好）
        - 价格权重：60%（价格越低分数越高）
        - 信誉权重：40%（信誉越高分数越高）
        """
        # 价格分：反比（归一化到 0-1）
        max_price = 10**18  # 1 ETH 作为基准
        price_score = 1.0 - min(self.price_wei / max_price, 1.0)

        # 信誉分：正比（0-10000 归一化到 0-1）
        reputation_score = self.reputation / 10000.0

        self.composite_score = (
            price_weight * price_score + reputation_weight * reputation_score
        )
        return self.composite_score


@dataclass
class ScheduledTask:
    """已调度的分身任务"""
    task_id: str
    avatar_did: str
    encrypted_weight_cid: str
    encrypted_input_cid: str
    constitution_cid: str
    max_budget_wei: int
    deadline: int
    required_tee_level: int = 1
    state: SchedulerState = SchedulerState.IDLE
    bids: list[ComputeBid] = field(default_factory=list)
    assigned_bid: Optional[ComputeBid] = None
    encrypted_result_cid: Optional[str] = None
    result_plaintext: Optional[bytes] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class AvatarScheduler:
    """
    分身任务调度器

    实际生产中应连接到以太坊节点（或 L2）与 AvatarComputeMarket 合约交互。
    此实现为本地模拟版本，便于开发和测试。
    """

    def __init__(
        self,
        owner_address: str,
        communication_key: bytes,  # X25519 私钥，用于解密结果
        bid_wait_seconds: int = 300,  # 等待投标时间（5分钟）
        min_bids: int = 1,            # 最少需要几个投标才选标
        price_weight: float = 0.6,
        reputation_weight: float = 0.4,
        on_state_change: Optional[Callable] = None,
    ):
        self.owner_address = owner_address
        self.communication_key = communication_key
        self.bid_wait_seconds = bid_wait_seconds
        self.min_bids = min_bids
        self.price_weight = price_weight
        self.reputation_weight = reputation_weight
        self.on_state_change = on_state_change

        self.active_tasks: dict[str, ScheduledTask] = {}
        self._running = False

    def _log(self, task_id: str, message: str, level: str = "INFO"):
        """结构化日志"""
        print(f"[{level}] [{time.strftime('%H:%M:%S')}] [{task_id[:8]}...] {message}")

    async def schedule_inference(
        self,
        avatar_did: str,
        encrypted_weight_cid: str,
        encrypted_input: bytes,
        constitution_cid: str,
        max_budget_wei: int,
        timeout_seconds: int = 600,
        required_tee_level: int = 1,
    ) -> Optional[bytes]:
        """
        完整的分身推理调度流程

        Args:
            avatar_did: 分身 DID
            encrypted_weight_cid: 加密权重的 IPFS CID
            encrypted_input: 加密后的推理输入（字节）
            constitution_cid: 宪法规则 IPFS CID
            max_budget_wei: 最大预算（wei）
            timeout_seconds: 总超时时间
            required_tee_level: 所需 TEE 安全级别

        Returns:
            推理结果（明文字节），失败返回 None
        """
        task_id = self._generate_task_id(avatar_did)

        # Step 1: 上传加密输入到去中心化存储
        self._log(task_id, "上传加密输入到去中心化存储...")
        encrypted_input_cid = await self._upload_to_ipfs(encrypted_input)

        # Step 2: 在链上发布任务
        deadline = int(time.time()) + timeout_seconds
        task = ScheduledTask(
            task_id=task_id,
            avatar_did=avatar_did,
            encrypted_weight_cid=encrypted_weight_cid,
            encrypted_input_cid=encrypted_input_cid,
            constitution_cid=constitution_cid,
            max_budget_wei=max_budget_wei,
            deadline=deadline,
            required_tee_level=required_tee_level,
        )
        self.active_tasks[task_id] = task
        await self._change_state(task, SchedulerState.PUBLISHING)

        success = await self._publish_task_onchain(task)
        if not success:
            await self._change_state(task, SchedulerState.FAILED)
            return None

        # Step 3: 等待算力节点投标
        await self._change_state(task, SchedulerState.WAITING_BIDS)
        self._log(task_id, f"等待算力节点投标（{self.bid_wait_seconds}秒）...")
        await self._wait_for_bids(task)

        if not task.bids:
            self._log(task_id, "没有收到任何投标，任务失败", "WARN")
            await self._change_state(task, SchedulerState.FAILED)
            return None

        # Step 4: 选择最优投标
        best_bid = self._select_best_bid(task)
        task.assigned_bid = best_bid
        self._log(
            task_id,
            f"选中节点: {best_bid.node_address[:10]}... "
            f"| 报价: {best_bid.price_wei} wei "
            f"| 信誉: {best_bid.reputation} "
            f"| 评分: {best_bid.composite_score:.3f}"
        )

        await self._assign_task_onchain(task, best_bid)
        await self._change_state(task, SchedulerState.ASSIGNED)

        # Step 5: 等待执行结果
        await self._change_state(task, SchedulerState.WAITING_RESULT)
        result_received = await self._wait_for_result(task, timeout_seconds - self.bid_wait_seconds)

        if not result_received:
            self._log(task_id, "等待结果超时，触发超时处理...", "WARN")
            await self._handle_timeout(task)
            await self._change_state(task, SchedulerState.FAILED)
            return None

        # Step 6: 验证结果
        await self._change_state(task, SchedulerState.VERIFYING)
        verified = await self._verify_result(task)

        if not verified:
            self._log(task_id, "结果验证失败，提交争议...", "ERROR")
            await self._dispute_task(task, "verification_failed")
            await self._change_state(task, SchedulerState.FAILED)
            return None

        # Step 7: 解密结果
        plaintext = await self._decrypt_result(task)
        task.result_plaintext = plaintext
        task.completed_at = time.time()

        await self._change_state(task, SchedulerState.COMPLETED)
        elapsed = task.completed_at - task.created_at
        self._log(task_id, f"推理完成 | 耗时: {elapsed:.1f}s | 节点: {best_bid.node_address[:10]}...")

        return plaintext

    def _generate_task_id(self, avatar_did: str) -> str:
        """生成唯一任务 ID"""
        salt = os.urandom(8).hex()
        return hashlib.sha256(f"{avatar_did}:{salt}:{time.time()}".encode()).hexdigest()

    async def _upload_to_ipfs(self, data: bytes) -> str:
        """上传数据到 IPFS（生产环境中应调用真实 IPFS API）"""
        # 模拟 IPFS 上传：返回数据的哈希作为 CID
        await asyncio.sleep(0.1)  # 模拟网络延迟
        fake_cid = "QmFake" + hashlib.sha256(data).hexdigest()[:40]
        return fake_cid

    async def _publish_task_onchain(self, task: ScheduledTask) -> bool:
        """在链上发布任务（生产环境中应调用以太坊合约）"""
        self._log(task.task_id, "发布任务到链上合约...")
        await asyncio.sleep(0.2)  # 模拟区块确认时间
        return True

    async def _wait_for_bids(self, task: ScheduledTask):
        """等待算力节点投标"""
        # 模拟接收投标
        await asyncio.sleep(min(self.bid_wait_seconds, 1))  # 在测试中缩短等待时间

        # 模拟 3 个节点投标
        mock_bids = [
            ComputeBid(
                node_address="0x" + "a" * 40,
                price_wei=1000000000000000,   # 0.001 ETH
                estimated_seconds=30,
                endpoint="https://node-a.oap.dev",
                reputation=7500,
            ),
            ComputeBid(
                node_address="0x" + "b" * 40,
                price_wei=800000000000000,    # 0.0008 ETH（更便宜）
                estimated_seconds=45,
                endpoint="https://node-b.oap.dev",
                reputation=9000,
            ),
            ComputeBid(
                node_address="0x" + "c" * 40,
                price_wei=1200000000000000,   # 0.0012 ETH（更贵）
                estimated_seconds=20,
                endpoint="https://node-c.oap.dev",
                reputation=6000,
            ),
        ]
        task.bids = mock_bids

    def _select_best_bid(self, task: ScheduledTask) -> ComputeBid:
        """选择最优投标（综合价格 + 信誉）"""
        for bid in task.bids:
            bid.calculate_score(self.price_weight, self.reputation_weight)

        # 按综合评分排序，取最高
        best = max(task.bids, key=lambda b: b.composite_score)
        return best

    async def _assign_task_onchain(self, task: ScheduledTask, bid: ComputeBid):
        """在链上确认任务分配"""
        self._log(task.task_id, f"在链上确认任务分配给节点 {bid.node_address[:10]}...")
        await asyncio.sleep(0.1)

    async def _wait_for_result(self, task: ScheduledTask, timeout: float) -> bool:
        """等待算力节点返回结果"""
        # 模拟节点执行并返回加密结果
        execution_time = min(task.assigned_bid.estimated_seconds if task.assigned_bid else 30, 2)
        await asyncio.sleep(execution_time * 0.01)  # 测试中缩短

        # 模拟加密结果
        task.encrypted_result_cid = "QmResult" + hashlib.sha256(task.task_id.encode()).hexdigest()[:40]
        return True

    async def _verify_result(self, task: ScheduledTask) -> bool:
        """验证结果（TEE 认证 + ZK 证明）"""
        self._log(task.task_id, "验证 TEE 认证 + ZK 证明...")
        await asyncio.sleep(0.1)
        # 模拟验证通过
        return True

    async def _decrypt_result(self, task: ScheduledTask) -> bytes:
        """解密结果"""
        # 模拟解密：实际中从 IPFS 获取加密结果，用 Owner 的通信密钥解密
        mock_result = json.dumps({
            "response": "这是分身的推理结果（示例）",
            "avatarDID": task.avatar_did,
            "taskId": task.task_id,
        }).encode("utf-8")
        return mock_result

    async def _handle_timeout(self, task: ScheduledTask):
        """处理任务超时"""
        self._log(task.task_id, "处理超时：退还 Owner 预算...", "WARN")
        # 链上调用：退款并惩罚节点信誉

    async def _dispute_task(self, task: ScheduledTask, reason: str):
        """提交争议"""
        self._log(task.task_id, f"提交争议: {reason}", "ERROR")
        # 链上调用：disputeTask

    async def _change_state(self, task: ScheduledTask, new_state: SchedulerState):
        """状态转换"""
        old_state = task.state
        task.state = new_state
        self._log(task.task_id, f"状态: {old_state.value} → {new_state.value}")
        if self.on_state_change:
            await self.on_state_change(task, old_state, new_state)

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """查询任务状态"""
        task = self.active_tasks.get(task_id)
        if not task:
            return None
        return {
            "taskId": task.task_id,
            "avatarDID": task.avatar_did,
            "state": task.state.value,
            "bidsCount": len(task.bids),
            "assignedNode": task.assigned_bid.node_address if task.assigned_bid else None,
            "createdAt": task.created_at,
            "completedAt": task.completed_at,
        }


class AvatarComputeClient:
    """
    分身算力客户端

    提供高层接口，封装调度器的使用：
    - 加密输入数据
    - 提交推理请求
    - 解密并返回结果
    """

    def __init__(
        self,
        avatar_did: str,
        encrypted_weight_cid: str,
        constitution_cid: str,
        communication_key: bytes,
        scheduler: AvatarScheduler,
    ):
        self.avatar_did = avatar_did
        self.encrypted_weight_cid = encrypted_weight_cid
        self.constitution_cid = constitution_cid
        self.communication_key = communication_key
        self.scheduler = scheduler

    def _encrypt_input(self, input_data: dict) -> bytes:
        """加密推理输入（用 Owner 与节点的共享密钥）"""
        plaintext = json.dumps(input_data, ensure_ascii=False).encode("utf-8")
        iv = os.urandom(12)
        aesgcm = AESGCM(self.communication_key[:32])
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        result = {
            "iv": iv.hex(),
            "ciphertext": ciphertext.hex(),
        }
        return json.dumps(result).encode("utf-8")

    async def infer(
        self,
        prompt: str,
        context: Optional[dict] = None,
        max_budget_wei: int = 10**15,  # 0.001 ETH
        timeout_seconds: int = 600,
    ) -> Optional[str]:
        """
        执行分身推理

        Args:
            prompt: 输入提示
            context: 额外上下文（记忆索引、对话历史等）
            max_budget_wei: 最大预算
            timeout_seconds: 超时时间

        Returns:
            分身的回复文本
        """
        input_data = {
            "prompt": prompt,
            "context": context or {},
            "timestamp": time.time(),
        }
        encrypted_input = self._encrypt_input(input_data)

        result_bytes = await self.scheduler.schedule_inference(
            avatar_did=self.avatar_did,
            encrypted_weight_cid=self.encrypted_weight_cid,
            encrypted_input=encrypted_input,
            constitution_cid=self.constitution_cid,
            max_budget_wei=max_budget_wei,
            timeout_seconds=timeout_seconds,
        )

        if result_bytes:
            result = json.loads(result_bytes.decode("utf-8"))
            return result.get("response")
        return None


# ─── 演示运行 ─────────────────────────────────────────────

async def demo():
    """演示分身调度层运行"""
    print("=" * 60)
    print("OAP 分身算力调度层 - 演示")
    print("=" * 60)

    # 初始化调度器
    scheduler = AvatarScheduler(
        owner_address="0x" + "f" * 40,
        communication_key=os.urandom(32),
        bid_wait_seconds=5,
        min_bids=1,
    )

    # 初始化客户端
    client = AvatarComputeClient(
        avatar_did="did:oap:ipfs:0x4a2b3c4d5e6f...",
        encrypted_weight_cid="QmPersonaWeight...",
        constitution_cid="QmConstitution...",
        communication_key=os.urandom(32),
        scheduler=scheduler,
    )

    # 执行推理
    print("\n发送推理请求...")
    response = await client.infer(
        prompt="你好，今天我有一个重要的决定需要你帮我分析",
        context={"memoryShards": ["QmShard1", "QmShard2"]},
    )

    print(f"\n分身回复: {response}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(demo())
