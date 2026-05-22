// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title AvatarComputeMarket
 * @dev 去中心化算力市场 - 分身专用调度合约
 *
 * 本合约管理分身推理任务的去中心化算力调度：
 * 1. 算力节点注册与信誉管理
 * 2. 任务投标与分配
 * 3. 结果验证与支付
 * 4. 违规惩罚（Slashing）
 *
 * 关键设计：算力节点只能访问加密的分身权重，
 * 在 TEE 内执行，无法获知分身内部内容。
 */

interface ITEEVerifier {
    /// @notice 验证 TEE 远程认证报告
    function verifyAttestation(
        bytes32 taskId,
        bytes calldata attestationReport,
        bytes32 expectedMrEnclave
    ) external view returns (bool);
}

interface IZKVerifier {
    /// @notice 验证推理结果的 ZK 证明
    function verifyProof(
        bytes32 taskId,
        bytes32 resultHash,
        bytes calldata proof
    ) external view returns (bool);
}

contract AvatarComputeMarket {
    // ─── 数据结构 ─────────────────────────────────────────────

    enum NodeStatus { Active, Suspended, Slashed, Exited }
    enum TaskStatus { Open, Assigned, Completed, Disputed, Resolved }

    struct ComputeNode {
        address nodeAddress;
        string teeType;              // "sgx" | "sev" | "nitro"
        bytes32 mrEnclave;           // 可信镜像哈希
        uint256 stakeAmount;         // 质押金额（防止作恶）
        uint256 completedTasks;      // 完成任务数
        uint256 reputation;          // 信誉分 (0-10000)
        NodeStatus status;
        string endpoint;             // 节点接入端点
    }

    struct ComputeTask {
        bytes32 taskId;
        address owner;               // Avatar Owner 地址
        string avatarDID;            // 分身 DID
        string encryptedWeightCID;   // IPFS CID（加密权重）
        string encryptedInputCID;    // IPFS CID（加密输入）
        string constitutionCID;      // IPFS CID（宪法规则）
        uint256 maxBudget;           // 最大预算（wei）
        uint256 deadline;            // 任务截止时间
        uint8 requiredTeeLevel;      // 1=Intel SGX, 2=AMD SEV, 3=任何 TEE
        address assignedNode;        // 分配的算力节点
        string encryptedResultCID;   // 加密结果 CID
        bytes teeAttestation;        // TEE 认证报告
        bytes zkProof;               // ZK 证明
        TaskStatus status;
        uint256 reward;              // 实际支付报酬
    }

    struct Bid {
        address nodeAddress;
        uint256 price;               // 报价（wei）
        uint256 estimatedTime;       // 预计完成时间（秒）
        string endpoint;             // 节点端点
        uint256 reputation;          // 投标时的信誉分
        bool selected;
    }

    // ─── 状态变量 ─────────────────────────────────────────────

    mapping(address => ComputeNode) public nodes;
    mapping(bytes32 => ComputeTask) public tasks;
    mapping(bytes32 => Bid[]) public bids;
    mapping(bytes32 => mapping(address => bool)) public hasSubmittedBid;

    address public owner;
    address public teeVerifier;
    address public zkVerifier;

    uint256 public minimumStake = 0.1 ether;
    uint256 public bidPeriod = 5 minutes;
    uint256 public verificationPeriod = 2 minutes;

    // 信誉参数
    uint256 public constant REPUTATION_INITIAL = 5000;
    uint256 public constant REPUTATION_MAX = 10000;
    uint256 public constant REPUTATION_REWARD = 50;   // 成功完成任务 +50
    uint256 public constant REPUTATION_PENALTY = 500; // 失败 -500
    uint256 public constant REPUTATION_SLASH_THRESHOLD = 1000; // 低于此值被踢出

    uint256 private _taskCounter;

    // ─── 事件 ─────────────────────────────────────────────────

    event NodeRegistered(address indexed nodeAddress, string teeType, bytes32 mrEnclave);
    event NodeSlashed(address indexed nodeAddress, uint256 slashedAmount, string reason);
    event TaskCreated(bytes32 indexed taskId, address indexed owner, string avatarDID);
    event TaskBidReceived(bytes32 indexed taskId, address indexed node, uint256 price);
    event TaskAssigned(bytes32 indexed taskId, address indexed node);
    event TaskCompleted(bytes32 indexed taskId, address indexed node, bytes32 resultHash);
    event TaskDisputed(bytes32 indexed taskId, address indexed disputant, string reason);
    event TaskResolved(bytes32 indexed taskId, bool nodeWon);

    // ─── 修饰器 ───────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier onlyActiveNode() {
        require(nodes[msg.sender].nodeAddress != address(0), "Not registered node");
        require(nodes[msg.sender].status == NodeStatus.Active, "Node not active");
        _;
    }

    constructor(address _teeVerifier, address _zkVerifier) {
        owner = msg.sender;
        teeVerifier = _teeVerifier;
        zkVerifier = _zkVerifier;
    }

    // ─── 节点管理 ─────────────────────────────────────────────

    /**
     * @notice 算力节点注册
     * @param teeType TEE 类型（"sgx" / "sev" / "nitro"）
     * @param mrEnclave 可信镜像哈希（只有经过认证的分身运行时镜像才能通过验证）
     * @param endpoint 节点访问端点
     */
    function registerNode(
        string calldata teeType,
        bytes32 mrEnclave,
        string calldata endpoint
    ) external payable {
        require(msg.value >= minimumStake, "Insufficient stake");
        require(nodes[msg.sender].nodeAddress == address(0), "Already registered");

        nodes[msg.sender] = ComputeNode({
            nodeAddress: msg.sender,
            teeType: teeType,
            mrEnclave: mrEnclave,
            stakeAmount: msg.value,
            completedTasks: 0,
            reputation: REPUTATION_INITIAL,
            status: NodeStatus.Active,
            endpoint: endpoint
        });

        emit NodeRegistered(msg.sender, teeType, mrEnclave);
    }

    /**
     * @notice 节点退出并取回质押
     */
    function exitNode() external onlyActiveNode {
        nodes[msg.sender].status = NodeStatus.Exited;
        uint256 stake = nodes[msg.sender].stakeAmount;
        nodes[msg.sender].stakeAmount = 0;
        payable(msg.sender).transfer(stake);
    }

    // ─── 任务管理 ─────────────────────────────────────────────

    /**
     * @notice Owner 创建推理任务
     * @param avatarDID 分身 DID
     * @param encryptedWeightCID 加密权重的 IPFS CID
     * @param encryptedInputCID 加密输入的 IPFS CID
     * @param constitutionCID 宪法规则的 IPFS CID
     * @param deadline 任务截止时间
     * @param requiredTeeLevel 要求的 TEE 安全级别
     */
    function createTask(
        string calldata avatarDID,
        string calldata encryptedWeightCID,
        string calldata encryptedInputCID,
        string calldata constitutionCID,
        uint256 deadline,
        uint8 requiredTeeLevel
    ) external payable returns (bytes32 taskId) {
        require(msg.value > 0, "Must provide budget");
        require(deadline > block.timestamp + bidPeriod, "Deadline too soon");

        _taskCounter++;
        taskId = keccak256(abi.encodePacked(msg.sender, avatarDID, _taskCounter, block.timestamp));

        tasks[taskId] = ComputeTask({
            taskId: taskId,
            owner: msg.sender,
            avatarDID: avatarDID,
            encryptedWeightCID: encryptedWeightCID,
            encryptedInputCID: encryptedInputCID,
            constitutionCID: constitutionCID,
            maxBudget: msg.value,
            deadline: deadline,
            requiredTeeLevel: requiredTeeLevel,
            assignedNode: address(0),
            encryptedResultCID: "",
            teeAttestation: "",
            zkProof: "",
            status: TaskStatus.Open,
            reward: 0
        });

        emit TaskCreated(taskId, msg.sender, avatarDID);
    }

    /**
     * @notice 算力节点提交投标
     * @param taskId 任务 ID
     * @param price 报价（wei）
     * @param estimatedTime 预计完成时间（秒）
     */
    function submitBid(
        bytes32 taskId,
        uint256 price,
        uint256 estimatedTime
    ) external onlyActiveNode {
        ComputeTask storage task = tasks[taskId];
        require(task.status == TaskStatus.Open, "Task not open");
        require(price <= task.maxBudget, "Price exceeds budget");
        require(block.timestamp < task.deadline - verificationPeriod, "Bid period ended");
        require(!hasSubmittedBid[taskId][msg.sender], "Already bid");

        hasSubmittedBid[taskId][msg.sender] = true;
        bids[taskId].push(Bid({
            nodeAddress: msg.sender,
            price: price,
            estimatedTime: estimatedTime,
            endpoint: nodes[msg.sender].endpoint,
            reputation: nodes[msg.sender].reputation,
            selected: false
        }));

        emit TaskBidReceived(taskId, msg.sender, price);
    }

    /**
     * @notice Owner 选择最优投标并分配任务
     * @param taskId 任务 ID
     * @param bidIndex 选择的投标索引
     */
    function assignTask(bytes32 taskId, uint256 bidIndex) external {
        ComputeTask storage task = tasks[taskId];
        require(task.owner == msg.sender, "Not task owner");
        require(task.status == TaskStatus.Open, "Task not open");
        require(bidIndex < bids[taskId].length, "Invalid bid index");

        Bid storage selectedBid = bids[taskId][bidIndex];
        selectedBid.selected = true;

        task.status = TaskStatus.Assigned;
        task.assignedNode = selectedBid.nodeAddress;
        task.reward = selectedBid.price;

        emit TaskAssigned(taskId, selectedBid.nodeAddress);
    }

    /**
     * @notice 算力节点提交完成结果
     * @param taskId 任务 ID
     * @param encryptedResultCID 加密结果的 IPFS CID
     * @param teeAttestation TEE 远程认证报告
     * @param zkProof ZK 推理证明
     */
    function submitResult(
        bytes32 taskId,
        string calldata encryptedResultCID,
        bytes calldata teeAttestation,
        bytes calldata zkProof
    ) external {
        ComputeTask storage task = tasks[taskId];
        require(task.assignedNode == msg.sender, "Not assigned node");
        require(task.status == TaskStatus.Assigned, "Task not assigned");
        require(block.timestamp <= task.deadline, "Deadline passed");

        // 验证 TEE 认证
        bool teeValid = ITEEVerifier(teeVerifier).verifyAttestation(
            taskId,
            teeAttestation,
            nodes[msg.sender].mrEnclave
        );
        require(teeValid, "TEE attestation invalid");

        task.encryptedResultCID = encryptedResultCID;
        task.teeAttestation = teeAttestation;
        task.zkProof = zkProof;
        task.status = TaskStatus.Completed;

        // 更新节点信誉
        _updateReputation(msg.sender, true);
        nodes[msg.sender].completedTasks++;

        // 支付报酬
        uint256 reward = task.reward;
        uint256 refund = task.maxBudget - reward;
        task.reward = 0;

        payable(msg.sender).transfer(reward);
        if (refund > 0) {
            payable(task.owner).transfer(refund);
        }

        emit TaskCompleted(taskId, msg.sender, keccak256(bytes(encryptedResultCID)));
    }

    /**
     * @notice Owner 对结果提出争议
     * @param taskId 任务 ID
     * @param reason 争议原因
     */
    function disputeTask(bytes32 taskId, string calldata reason) external {
        ComputeTask storage task = tasks[taskId];
        require(task.owner == msg.sender, "Not task owner");
        require(task.status == TaskStatus.Completed, "Task not completed");

        task.status = TaskStatus.Disputed;
        emit TaskDisputed(taskId, msg.sender, reason);
    }

    /**
     * @notice 合约所有者仲裁争议
     * @param taskId 任务 ID
     * @param nodeWon 节点是否胜诉
     */
    function resolveDispute(bytes32 taskId, bool nodeWon) external onlyOwner {
        ComputeTask storage task = tasks[taskId];
        require(task.status == TaskStatus.Disputed, "Task not disputed");

        task.status = TaskStatus.Resolved;

        if (!nodeWon) {
            // 惩罚恶意节点：扣除部分质押，退还 Owner
            address node = task.assignedNode;
            uint256 slashAmount = task.maxBudget;
            if (nodes[node].stakeAmount >= slashAmount) {
                nodes[node].stakeAmount -= slashAmount;
                payable(task.owner).transfer(slashAmount);
            }
            _updateReputation(node, false);
            _checkSlash(node);
            emit NodeSlashed(node, slashAmount, "dispute_lost");
        }

        emit TaskResolved(taskId, nodeWon);
    }

    // ─── 内部函数 ─────────────────────────────────────────────

    function _updateReputation(address nodeAddress, bool positive) internal {
        ComputeNode storage node = nodes[nodeAddress];
        if (positive) {
            uint256 newRep = node.reputation + REPUTATION_REWARD;
            node.reputation = newRep > REPUTATION_MAX ? REPUTATION_MAX : newRep;
        } else {
            if (node.reputation >= REPUTATION_PENALTY) {
                node.reputation -= REPUTATION_PENALTY;
            } else {
                node.reputation = 0;
            }
        }
    }

    function _checkSlash(address nodeAddress) internal {
        if (nodes[nodeAddress].reputation < REPUTATION_SLASH_THRESHOLD) {
            nodes[nodeAddress].status = NodeStatus.Slashed;
            // 没收全部质押到惩罚池（可通过治理分配）
        }
    }

    // ─── 查询函数 ─────────────────────────────────────────────

    function getBids(bytes32 taskId) external view returns (Bid[] memory) {
        return bids[taskId];
    }

    function getNodeInfo(address nodeAddress) external view returns (ComputeNode memory) {
        return nodes[nodeAddress];
    }

    function getTask(bytes32 taskId) external view returns (ComputeTask memory) {
        return tasks[taskId];
    }

    function updateMinimumStake(uint256 newStake) external onlyOwner {
        minimumStake = newStake;
    }

    receive() external payable {}
}
