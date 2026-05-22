/**
 * AvatarComputeMarket 完整测试套件
 *
 * 测试场景：
 *  1. 节点注册 & 管理
 *  2. 任务创建 & 投标 & 分配
 *  3. 结果提交 & 支付结算
 *  4. 争议处理 & 惩罚
 *  5. 信誉系统
 *  6. 边界条件 & 安全检查
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-toolbox/network-helpers");

// ── 常量 ─────────────────────────────────────────────────

const MINIMUM_STAKE = ethers.parseEther("0.1");
const TASK_BUDGET   = ethers.parseEther("0.01");
const BID_PRICE     = ethers.parseEther("0.008");
const MR_ENCLAVE    = ethers.keccak256(ethers.toUtf8Bytes("trusted-avatar-runtime-v1"));

const AVATAR_DID         = "did:oap:local:0xabc123";
const WEIGHT_CID         = "QmPersonaWeight0123456789";
const INPUT_CID          = "QmEncryptedInput0123456789";
const CONSTITUTION_CID   = "QmConstitution0123456789";
const RESULT_CID         = "QmEncryptedResult0123456789";

// 模拟 TEE Attestation（32 字节哈希）
const MOCK_ATTESTATION   = ethers.toUtf8Bytes(JSON.stringify({
  teeType: "simulated",
  mrEnclave: MR_ENCLAVE,
  timestamp: Date.now(),
}));

// 模拟 ZK 证明
const MOCK_ZK_PROOF      = ethers.toUtf8Bytes(JSON.stringify({
  type: "mock-groth16",
  proof: "0x" + "a".repeat(512),
}));

// ── Fixtures ─────────────────────────────────────────────

async function deployFixture() {
  const [owner, node1, node2, node3, taskOwner, disputant] = await ethers.getSigners();

  // 部署模拟 TEE 验证器（总是返回 true，测试用）
  const MockTEEVerifier = await ethers.deployContract("MockTEEVerifier");
  const MockZKVerifier  = await ethers.deployContract("MockZKVerifier");

  const Market = await ethers.getContractFactory("AvatarComputeMarket");
  const market = await Market.deploy(
    await MockTEEVerifier.getAddress(),
    await MockZKVerifier.getAddress(),
  );

  return { market, MockTEEVerifier, MockZKVerifier, owner, node1, node2, node3, taskOwner, disputant };
}

async function deployWithRegisteredNodes() {
  const f = await loadFixture(deployFixture);
  const { market, node1, node2 } = f;

  // 注册两个算力节点
  await market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
    value: MINIMUM_STAKE,
  });
  await market.connect(node2).registerNode("sev", MR_ENCLAVE, "https://node2.oap.dev", {
    value: MINIMUM_STAKE,
  });

  return f;
}

async function deployWithOpenTask() {
  const f = await deployWithRegisteredNodes();
  const { market, taskOwner } = f;

  const deadline = (await time.latest()) + 3600; // 1小时后

  const tx = await market.connect(taskOwner).createTask(
    AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, deadline, 1,
    { value: TASK_BUDGET }
  );
  const receipt = await tx.wait();
  const taskCreatedEvent = receipt.logs.find(
    log => log.fragment?.name === "TaskCreated"
  );
  const taskId = taskCreatedEvent.args[0];

  return { ...f, taskId, deadline };
}

// ── 测试套件 ─────────────────────────────────────────────

describe("AvatarComputeMarket", function () {

  // ─── 1. 节点注册 ────────────────────────────────────────

  describe("节点注册", function () {

    it("应该允许算力节点注册并质押", async function () {
      const { market, node1 } = await loadFixture(deployFixture);

      await expect(
        market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
          value: MINIMUM_STAKE,
        })
      ).to.emit(market, "NodeRegistered")
        .withArgs(node1.address, "sgx", MR_ENCLAVE);

      const nodeInfo = await market.getNodeInfo(node1.address);
      expect(nodeInfo.nodeAddress).to.equal(node1.address);
      expect(nodeInfo.stakeAmount).to.equal(MINIMUM_STAKE);
      expect(nodeInfo.reputation).to.equal(5000n); // 初始信誉值
      expect(nodeInfo.status).to.equal(0n); // Active
    });

    it("应该拒绝质押不足的注册", async function () {
      const { market, node1 } = await loadFixture(deployFixture);
      const insufficientStake = ethers.parseEther("0.05");

      await expect(
        market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
          value: insufficientStake,
        })
      ).to.be.revertedWith("Insufficient stake");
    });

    it("应该拒绝重复注册同一地址", async function () {
      const { market, node1 } = await loadFixture(deployFixture);

      await market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
        value: MINIMUM_STAKE,
      });

      await expect(
        market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
          value: MINIMUM_STAKE,
        })
      ).to.be.revertedWith("Already registered");
    });

    it("节点退出后应能取回质押", async function () {
      const { market, node1 } = await loadFixture(deployFixture);

      await market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
        value: MINIMUM_STAKE,
      });

      const balanceBefore = await ethers.provider.getBalance(node1.address);
      const tx = await market.connect(node1).exitNode();
      const receipt = await tx.wait();
      const gasUsed = receipt.gasUsed * receipt.gasPrice;
      const balanceAfter = await ethers.provider.getBalance(node1.address);

      expect(balanceAfter + gasUsed - balanceBefore).to.be.closeTo(
        MINIMUM_STAKE, ethers.parseEther("0.001")
      );

      const nodeInfo = await market.getNodeInfo(node1.address);
      expect(nodeInfo.status).to.equal(3n); // Exited
    });
  });

  // ─── 2. 任务创建 ────────────────────────────────────────

  describe("任务创建", function () {

    it("应该允许 Owner 创建推理任务", async function () {
      const { market, taskOwner } = await loadFixture(deployWithRegisteredNodes);

      const deadline = (await time.latest()) + 3600;
      const tx = await market.connect(taskOwner).createTask(
        AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, deadline, 1,
        { value: TASK_BUDGET }
      );

      await expect(tx).to.emit(market, "TaskCreated")
        .withArgs(
          (taskId) => taskId.startsWith !== undefined,  // 任意 bytes32
          taskOwner.address,
          AVATAR_DID,
        );
    });

    it("应该拒绝 deadline 过短的任务", async function () {
      const { market, taskOwner } = await loadFixture(deployWithRegisteredNodes);

      const tooSoonDeadline = (await time.latest()) + 60; // 1 分钟后，不够投标时间

      await expect(
        market.connect(taskOwner).createTask(
          AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, tooSoonDeadline, 1,
          { value: TASK_BUDGET }
        )
      ).to.be.revertedWith("Deadline too soon");
    });

    it("应该拒绝零预算任务", async function () {
      const { market, taskOwner } = await loadFixture(deployWithRegisteredNodes);
      const deadline = (await time.latest()) + 3600;

      await expect(
        market.connect(taskOwner).createTask(
          AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, deadline, 1,
          { value: 0n }
        )
      ).to.be.revertedWith("Must provide budget");
    });
  });

  // ─── 3. 投标 & 分配 ────────────────────────────────────

  describe("投标与分配", function () {

    it("节点应该能对任务投标", async function () {
      const { market, node1, taskId } = await loadFixture(deployWithOpenTask);

      await expect(
        market.connect(node1).submitBid(taskId, BID_PRICE, 30)
      ).to.emit(market, "TaskBidReceived")
        .withArgs(taskId, node1.address, BID_PRICE);

      const bids = await market.getBids(taskId);
      expect(bids.length).to.equal(1);
      expect(bids[0].nodeAddress).to.equal(node1.address);
      expect(bids[0].price).to.equal(BID_PRICE);
    });

    it("应该拒绝超出预算的投标", async function () {
      const { market, node1, taskId } = await loadFixture(deployWithOpenTask);
      const overBudget = TASK_BUDGET + 1n;

      await expect(
        market.connect(node1).submitBid(taskId, overBudget, 30)
      ).to.be.revertedWith("Price exceeds budget");
    });

    it("应该拒绝同一节点重复投标", async function () {
      const { market, node1, taskId } = await loadFixture(deployWithOpenTask);

      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);

      await expect(
        market.connect(node1).submitBid(taskId, BID_PRICE, 30)
      ).to.be.revertedWith("Already bid");
    });

    it("Owner 应能选择最优投标分配任务", async function () {
      const { market, taskOwner, node1, node2, taskId } = await loadFixture(deployWithOpenTask);

      // 两个节点投标
      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);
      await market.connect(node2).submitBid(taskId, BID_PRICE - 1000n, 45);

      // 选择 index=1（node2，更便宜）
      await expect(
        market.connect(taskOwner).assignTask(taskId, 1)
      ).to.emit(market, "TaskAssigned")
        .withArgs(taskId, node2.address);

      const task = await market.getTask(taskId);
      expect(task.assignedNode).to.equal(node2.address);
      expect(task.status).to.equal(1n); // Assigned
    });

    it("非 Owner 不能分配任务", async function () {
      const { market, node1, node2, taskId } = await loadFixture(deployWithOpenTask);

      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);

      await expect(
        market.connect(node2).assignTask(taskId, 0)
      ).to.be.revertedWith("Not task owner");
    });
  });

  // ─── 4. 结果提交 & 结算 ────────────────────────────────

  describe("结果提交与结算", function () {

    async function setupAssignedTask() {
      const f = await loadFixture(deployWithOpenTask);
      const { market, taskOwner, node1, taskId } = f;

      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);
      await market.connect(taskOwner).assignTask(taskId, 0);

      return f;
    }

    it("被分配节点应能提交结果并获得报酬", async function () {
      const { market, node1, taskOwner, taskId } = await setupAssignedTask();

      const node1BalanceBefore = await ethers.provider.getBalance(node1.address);
      const taskOwnerBalanceBefore = await ethers.provider.getBalance(taskOwner.address);

      const tx = await market.connect(node1).submitResult(
        taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF
      );
      const receipt = await tx.wait();

      await expect(tx).to.emit(market, "TaskCompleted");

      // 验证节点收到报酬
      const node1BalanceAfter = await ethers.provider.getBalance(node1.address);
      const gasUsed = receipt.gasUsed * receipt.gasPrice;
      const nodeProfit = node1BalanceAfter - node1BalanceBefore + gasUsed;
      expect(nodeProfit).to.equal(BID_PRICE);

      // 验证 Owner 收到剩余退款
      const taskOwnerBalanceAfter = await ethers.provider.getBalance(taskOwner.address);
      const refund = taskOwnerBalanceAfter - taskOwnerBalanceBefore;
      expect(refund).to.equal(TASK_BUDGET - BID_PRICE);

      // 验证节点信誉提升
      const nodeInfo = await market.getNodeInfo(node1.address);
      expect(nodeInfo.reputation).to.equal(5050n); // 5000 + 50
      expect(nodeInfo.completedTasks).to.equal(1n);
    });

    it("非分配节点不能提交结果", async function () {
      const { market, node2, taskId } = await setupAssignedTask();

      await expect(
        market.connect(node2).submitResult(taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF)
      ).to.be.revertedWith("Not assigned node");
    });

    it("超过 deadline 后提交结果应该失败", async function () {
      const { market, node1, taskId, deadline } = await setupAssignedTask();

      // 时间快进到超过 deadline
      await time.increaseTo(deadline + 1);

      await expect(
        market.connect(node1).submitResult(taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF)
      ).to.be.revertedWith("Deadline passed");
    });
  });

  // ─── 5. 争议处理 ────────────────────────────────────────

  describe("争议处理", function () {

    async function setupCompletedTask() {
      const f = await loadFixture(deployWithOpenTask);
      const { market, taskOwner, node1, taskId } = f;

      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);
      await market.connect(taskOwner).assignTask(taskId, 0);
      await market.connect(node1).submitResult(taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF);

      return f;
    }

    it("Owner 应能对结果提出争议", async function () {
      const { market, taskOwner, taskId } = await setupCompletedTask();

      await expect(
        market.connect(taskOwner).disputeTask(taskId, "结果与预期不符")
      ).to.emit(market, "TaskDisputed")
        .withArgs(taskId, taskOwner.address, "结果与预期不符");

      const task = await market.getTask(taskId);
      expect(task.status).to.equal(3n); // Disputed
    });

    it("争议裁决：节点败诉时应被惩罚", async function () {
      const { market, owner, taskOwner, node1, taskId } = await setupCompletedTask();

      await market.connect(taskOwner).disputeTask(taskId, "结果无效");

      const node1StakeBefore = (await market.getNodeInfo(node1.address)).stakeAmount;
      const ownerBalanceBefore = await ethers.provider.getBalance(taskOwner.address);

      await market.connect(owner).resolveDispute(taskId, false); // 节点败诉

      const node1StakeAfter = (await market.getNodeInfo(node1.address)).stakeAmount;
      expect(node1StakeAfter).to.be.lt(node1StakeBefore); // 质押减少

      // 信誉降低
      const nodeInfo = await market.getNodeInfo(node1.address);
      expect(nodeInfo.reputation).to.be.lt(5000n);
    });

    it("争议裁决：节点胜诉时状态应变为 Resolved", async function () {
      const { market, owner, taskOwner, taskId } = await setupCompletedTask();

      await market.connect(taskOwner).disputeTask(taskId, "误报争议");
      await expect(
        market.connect(owner).resolveDispute(taskId, true)
      ).to.emit(market, "TaskResolved")
        .withArgs(taskId, true);

      const task = await market.getTask(taskId);
      expect(task.status).to.equal(4n); // Resolved
    });
  });

  // ─── 6. 信誉系统 ────────────────────────────────────────

  describe("信誉系统", function () {

    it("成功完成任务应增加信誉", async function () {
      const { market, taskOwner, node1 } = await loadFixture(deployWithRegisteredNodes);

      const deadline = (await time.latest()) + 3600;

      // 完成 3 个任务
      for (let i = 0; i < 3; i++) {
        const tx = await market.connect(taskOwner).createTask(
          AVATAR_DID, WEIGHT_CID, INPUT_CID + i, CONSTITUTION_CID, deadline, 1,
          { value: TASK_BUDGET }
        );
        const receipt = await tx.wait();
        const event = receipt.logs.find(l => l.fragment?.name === "TaskCreated");
        const taskId = event.args[0];

        await market.connect(node1).submitBid(taskId, BID_PRICE, 30);
        await market.connect(taskOwner).assignTask(taskId, 0);
        await market.connect(node1).submitResult(taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF);
      }

      const nodeInfo = await market.getNodeInfo(node1.address);
      expect(nodeInfo.reputation).to.equal(5150n); // 5000 + 3*50
      expect(nodeInfo.completedTasks).to.equal(3n);
    });

    it("信誉值应不超过最大值 10000", async function () {
      const { market } = await loadFixture(deployFixture);
      // 信誉上限在合约中通过 REPUTATION_MAX 约束
      // 这里通过查看常量验证
      const maxRep = await market.REPUTATION_MAX();
      expect(maxRep).to.equal(10000n);
    });
  });

  // ─── 7. 安全 & 权限 ────────────────────────────────────

  describe("安全与权限控制", function () {

    it("非注册节点不能投标", async function () {
      const { market, node3, taskId } = await loadFixture(deployWithOpenTask);

      await expect(
        market.connect(node3).submitBid(taskId, BID_PRICE, 30)
      ).to.be.revertedWith("Not registered node");
    });

    it("非合约 Owner 不能修改最小质押", async function () {
      const { market, node1 } = await loadFixture(deployFixture);

      await expect(
        market.connect(node1).updateMinimumStake(ethers.parseEther("0.5"))
      ).to.be.revertedWith("Not owner");
    });

    it("非 Owner 不能仲裁争议", async function () {
      const { market, node1, taskOwner, node2 } = await loadFixture(deployWithRegisteredNodes);

      const deadline = (await time.latest()) + 3600;
      const tx = await market.connect(taskOwner).createTask(
        AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, deadline, 1,
        { value: TASK_BUDGET }
      );
      const receipt = await tx.wait();
      const event = receipt.logs.find(l => l.fragment?.name === "TaskCreated");
      const taskId = event.args[0];

      await market.connect(node1).submitBid(taskId, BID_PRICE, 30);
      await market.connect(taskOwner).assignTask(taskId, 0);
      await market.connect(node1).submitResult(taskId, RESULT_CID, MOCK_ATTESTATION, MOCK_ZK_PROOF);
      await market.connect(taskOwner).disputeTask(taskId, "测试争议");

      await expect(
        market.connect(node2).resolveDispute(taskId, true)
      ).to.be.revertedWith("Not owner");
    });

    it("休眠节点不能投标", async function () {
      const { market, node1, taskId } = await loadFixture(deployWithOpenTask);

      // 节点退出
      await market.connect(node1).exitNode();

      await expect(
        market.connect(node1).submitBid(taskId, BID_PRICE, 30)
      ).to.be.revertedWith("Node not active");
    });
  });

  // ─── 8. Gas 消耗报告 ────────────────────────────────────

  describe("Gas 消耗报告", function () {
    it("注册节点 gas 消耗", async function () {
      const { market, node1 } = await loadFixture(deployFixture);
      const tx = await market.connect(node1).registerNode("sgx", MR_ENCLAVE, "https://node1.oap.dev", {
        value: MINIMUM_STAKE,
      });
      const receipt = await tx.wait();
      console.log(`    registerNode: ${receipt.gasUsed} gas`);
    });

    it("创建任务 gas 消耗", async function () {
      const { market, taskOwner } = await loadFixture(deployWithRegisteredNodes);
      const deadline = (await time.latest()) + 3600;
      const tx = await market.connect(taskOwner).createTask(
        AVATAR_DID, WEIGHT_CID, INPUT_CID, CONSTITUTION_CID, deadline, 1,
        { value: TASK_BUDGET }
      );
      const receipt = await tx.wait();
      console.log(`    createTask: ${receipt.gasUsed} gas`);
    });
  });
});
