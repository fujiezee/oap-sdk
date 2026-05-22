# OAP — 开放分身协议

> 每个人类都应该拥有一个数字分身，背后是一个活着的 AI，且分身本身是去中心化的。

OAP（Open Avatar Protocol）是一套去中心化数字分身的完整基础设施：从身份、记忆、训练到网络通信，所有数据归个人所有，任何中心化平台无法控制或窃听。

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    OAP 生态                          │
├────────────┬────────────┬────────────┬──────────────┤
│  协议规范   │  训练框架   │  算力市场   │  权利宣言    │
│  oap-spec  │  lora-     │  compute-  │  rights-     │
│            │  framework │  layer     │  declaration │
├────────────┴────────────┴────────────┴──────────────┤
│                    OAP SDK                           │
│  Avatar · Memory · Crypto · Compute · Network       │
├─────────────────────────────────────────────────────┤
│                 去中心化网络层                         │
│  Federated Relay · DHT · Gossip · E2EE              │
└─────────────────────────────────────────────────────┘
```

## 项目结构

```
center/
├── oap-spec/                    # 协议规范
│   ├── OAP_PROTOCOL.md         # 7 层协议（身份/数据/模型/宪法/交互/加密/可验证推理）
│   └── schemas/                # JSON Schema（memory-v1, constitution-v1）
├── oap-sdk/                    # 可安装 SDK
│   ├── oap/
│   │   ├── avatar/core.py      # Avatar 分身生命周期
│   │   ├── memory/store.py     # 加密记忆存储（热/温/冷）
│   │   ├── crypto/keyring.py   # KeyRing 五类密钥派生
│   │   ├── compute/client.py   # 推理客户端（EXTERNAL/LOCAL/REMOTE/MOCK）
│   │   ├── network/peer.py     # 去中心化网络客户端
│   │   └── cli.py              # 命令行工具
│   ├── examples/
│   │   ├── demo_full.py        # 端到端 Demo（Mock 推理）
│   │   ├── demo_external.py    # 外部推理 Demo（OpenAI/Claude/DeepSeek/Ollama）
│   │   ├── demo_network.py     # 双分身网络通信 Demo
│   │   └── demo_decentralized.py # 去中心化联邦 Demo
│   ├── install.sh              # 一键安装脚本
│   └── pyproject.toml
├── oap-relay/                  # 去中心化中继
│   ├── federated_relay.py      # 联邦中继（Gossip + 路由）
│   ├── relay_server.py         # 单节点中继
│   └── dht.py                  # Kademlia DHT
├── lora-framework/             # 本地 LoRA 训练框架
│   └── oap_lora/
│       ├── training/engine.py  # SFT/DPO 训练引擎
│       ├── data/collector.py   # 数据采集
│       └── crypto/keys.py      # 密钥管理
├── compute-layer/              # 去中心化算力市场
│   ├── contracts/
│   │   ├── AvatarComputeMarket.sol  # 主合约
│   │   └── Mocks.sol
│   ├── tests/                  # 26 个 Hardhat 测试
│   └── scripts/deploy.js
└── rights-declaration/
    └── DIGITAL_AVATAR_RIGHTS.md  # 26 条权利宣言
```

## 快速开始

### 前提条件

- Python 3.10+
- pip 或 pipx

### 安装

**方式一：一键脚本**

```bash
bash install.sh
```

**方式二：pipx（推荐，隔离环境）**

```bash
pipx install .
# 或从 GitHub
pipx install git+https://github.com/oap-org/oap.git
```

**方式三：pip**

```bash
pip install -e .
```

**方式四：npx 风格（一次性运行）**

```bash
pipx run --spec . oap --help
```

### 创建分身

```bash
# Mock 模式（无需 API Key，开箱即用）
oap init --name 你的名字

# 接入 OpenAI
export OPENAI_API_KEY="sk-..."
oap init --name 你的名字 --mode external --model gpt-4o-mini

# 接入 DeepSeek
oap init --name 你的名字 --mode external \
  --api-base https://api.deepseek.com/v1 \
  --model deepseek-chat

# 接入本地 Ollama（完全离线）
oap init --name 你的名字 --mode external \
  --api-base http://localhost:11434/v1 \
  --model llama3

# 接入 Anthropic Claude
oap init --name 你的名字 --mode external \
  --api-type anthropic \
  --model claude-3-5-sonnet-20241022
```

### 对话

```bash
# 交互模式
oap chat

# 单次对话
oap chat -m "帮我分析一下最近的决策"

# 对话中的命令：
#   :q   退出       :s   查看状态
#   :r   反思       :p   正面反馈
#   :f 内容  纠正反馈
```

### 代码中使用

```python
from oap import Avatar
from oap.compute.client import InferenceMode

# 创建分身
avatar, mnemonic = await Avatar.create(
    name="你的名字",
    inference_mode=InferenceMode.EXTERNAL,
    api_key="sk-...",
    model="gpt-4o-mini",
)

# 对话
reply = await avatar.chat("你好")

# 反馈 → 积累训练信号
avatar.feedback("positive")
avatar.feedback("correction", "我更希望你说得简洁一点")

# 反思
reflection = await avatar.reflect()

# 保存 / 加载
await avatar.save()
avatar = await Avatar.load("~/.oap/avatars/xxx", mnemonic)
```

## 去中心化网络

分身可以互相发现、握手、建立端到端加密通信。中继只转发密文，无法解密。

### 启动联邦中继

```bash
# 单节点
python oap-relay/federated_relay.py --port 8765

# 多节点联邦
python oap-relay/federated_relay.py --port 8765 --id relay-alpha
python oap-relay/federated_relay.py --port 8767 --id relay-beta --peers ws://alpha:8765
```

### 分身联网

```python
from oap.network import AvatarNetwork

# 连接中继（支持多个，自动降级）
net = AvatarNetwork(key_ring, relay_urls=[
    "ws://relay1.example.com:8765",
    "ws://relay2.example.com:8765",
])
await net.connect()
await net.register(display_name="阿明")

# 发现其他分身
peers = await net.list_avatars()

# 握手（ECDH 密钥协商 → AES-256-GCM 加密通道）
channel = await net.handshake(peer_did="did:oap:...")

# 加密通信
await net.send(channel, "你好，我是阿明的分身")
```

## 安全设计

| 特性 | 说明 |
|------|------|
| **自主权身份** | DID 从本地助记词派生，无需中心注册 |
| **五类密钥** | 身份/记忆/权重/通信/紧急，从 PBKDF2+HKDF 派生 |
| **端到端加密** | AES-256-GCM，中继零知识 |
| **前向安全** | 每次握手生成临时 ECDH 密钥对 |
| **记忆安全遗忘** | 随机覆写磁盘后删除 |
| **紧急冻结** | 一键冻结所有交互 |
| **API Key 驻留内存** | 不写入任何配置文件 |
| **去中心化网络** | 联邦中继 + DHT，无单点故障 |

## 算力市场

```bash
cd compute-layer
npm install
npx hardhat test        # 26 个测试
npx hardhat node        # 本地测试网
npx hardhat run scripts/deploy.js --network localhost
```

## Demo 运行

```bash
# 1. 基础功能（Mock 推理）
python3 examples/demo_full.py

# 2. 外部推理（需要 API Key）
export OPENAI_API_KEY="sk-..."
python3 examples/demo_external.py

# 3. 双分身网络通信
python3 oap-relay/relay_server.py &    # 先启动中继
python3 examples/demo_network.py

# 4. 去中心化联邦（两个 Relay）
python3 examples/demo_decentralized.py
```

## License

Apache-2.0