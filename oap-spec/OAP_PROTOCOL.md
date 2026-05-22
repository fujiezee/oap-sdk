# Open Avatar Protocol (OAP) — 开放分身协议规范

> 版本: 0.1.0-draft
> 状态: 草案
> 日期: 2026-05-21

## 0. 愿景

每一个人类在数据世界中拥有一个数字分身（Digital Avatar）。该分身的数据、模型权重、行为规则完全归属其本人，不受任何中心化平台控制。OAP 定义分身之间、分身与外部系统之间的互操作标准，确保分身可迁移、可验证、可审计，同时保证隐私不可侵犯。

## 1. 核心设计原则

| 原则 | 含义 |
|------|------|
| **数据主权** | 分身的所有数据（记忆、偏好、交互记录）只有本人私钥可解密 |
| **模型自主** | 分身的模型权重（LoRA 等）只有本人可以更新、导出、迁移 |
| **意志不可篡改** | 分身的行为规则（宪法层）由本人设定，运行时不可被第三方绕过 |
| **可迁移性** | 分身可在不同算力网络、不同运行时环境间无损迁移 |
| **可验证性** | 分身的推理结果可通过零知识证明被验证，无需暴露内部状态 |
| **互操作性** | 任何符合 OAP 的分身可与其他分身或外部系统安全交互 |

## 2. 术语定义

| 术语 | 定义 |
|------|------|
| Avatar | 数字分身，一个人的数字延伸智能体 |
| Owner | 分身的拥有者，即对应的自然人 |
| DID | Decentralized Identifier，去中心化身份标识 |
| Memory Shard | 加密记忆分片，存储在去中心化存储网络中 |
| Persona Weight | 个人化模型权重（如 LoRA），编码分身的性格与知识 |
| Constitution | 分身宪法，由 Owner 设定的不可违反行为规则 |
| TEE Enclave | 可信执行环境，保证分身运行时状态不可被窥视 |
| Avatar DID | 分身的去中心化身份，与 Owner DID 绑定 |

## 3. 身份层 (Identity Layer)

### 3.1 Avatar DID 方法

OAP 定义 `did:oap` 方法：

```
did:oap:<network>:<avatar-id>
```

- `network`: 分身所在的去中心化网络标识（如 `ipfs`, `arweave`, `filecoin`）
- `avatar-id`: 由 Owner 的公钥派生的唯一标识符

示例：
```
did:oap:ipfs:0x4a2b...8f3e
```

### 3.2 身份绑定

- 每个 Avatar DID 必须与一个 Owner DID 绑定
- 绑定关系写入 DID 文档，由 Owner 签名
- 一个 Owner 只能有一个主分身（Primary Avatar），可有多个辅助分身
- 分身之间的委托关系由 Owner 签名授权

### 3.3 DID 文档结构

```json
{
  "@context": ["https://www.w3.org/ns/did/v1", "https://oap.dev/ns/did/v1"],
  "id": "did:oap:ipfs:0x4a2b...8f3e",
  "controller": "did:key:0xOwnerPubKey...",
  "verificationMethod": [
    {
      "id": "did:oap:ipfs:0x4a2b...8f3e#key-1",
      "type": "Ed25519VerificationKey2020",
      "controller": "did:oap:ipfs:0x4a2b...8f3e",
      "publicKeyMultibase": "z6Mk..."
    }
  ],
  "service": [
    {
      "id": "did:oap:ipfs:0x4a2b...8f3e#messaging",
      "type": "OAPMessaging",
      "serviceEndpoint": "https://relay.oap.dev/did:oap:ipfs:0x4a2b...8f3e"
    },
    {
      "id": "did:oap:ipfs:0x4a2b...8f3e#compute",
      "type": "OAPCompute",
      "serviceEndpoint": "https://compute.oap.dev/did:oap:ipfs:0x4a2b...8f3e"
    }
  ],
  "oap:avatarMetadata": {
    "created": "2026-01-15T00:00:00Z",
    "baseModel": "llama-3-8b",
    "personaWeightRef": "ipfs://QmPersonaWeight...",
    "constitutionRef": "ipfs://QmConstitution...",
    "memoryShardRefs": ["ipfs://QmShard1...", "ipfs://QmShard2..."]
  }
}
```

## 4. 数据层 (Data Layer)

### 4.1 记忆数据格式

分身的记忆以时间线索引的条目存储，每条记忆包含：

```json
{
  "id": "mem_0xHash...",
  "timestamp": "2026-05-21T14:30:00Z",
  "type": "conversation | action | observation | reflection | dream",
  "modality": "text | image | audio | video | structured",
  "content": {
    "encrypted": "base64-encoded-aes-256-gcm-ciphertext",
    "iv": "base64-encoded-initialization-vector",
    "tag": "base64-encoded-auth-tag"
  },
  "metadata": {
    "source": "owner-input | avatar-observation | avatar-reflection | external-interaction",
    "participants": ["did:oap:ipfs:0xOther..."],
    "emotionalValence": 0.7,
    "importance": 0.9
  },
  "accessControl": {
    "level": "private | shared-with-consent | public-anonymized",
    "consentTokens": []
  }
}
```

### 4.2 记忆分片存储

- 记忆条目按时间窗口分片（默认每 24 小时一个分片）
- 每个分片使用 AES-256-GCM 独立加密
- 加密密钥由 Owner 的主密钥通过 HKDF 派生
- 分片以 Merkle DAG 结构组织，支持增量追加
- 存储到去中心化网络（IPFS / Arweave），CID 作为引用

### 4.3 记忆索引

加密索引存储在链上或 DHT 中，支持 Owner 按时间范围、类型、参与者查询：

```json
{
  "shardIndex": [
    {
      "timeRange": ["2026-05-20T00:00:00Z", "2026-05-21T00:00:00Z"],
      "cid": "QmShard20260520...",
      "entryCount": 47,
      "merkleRoot": "0xMerkleRoot..."
    }
  ],
  "encryptedIndexKey": "base64-encoded-encrypted-index-key"
}
```

### 4.4 记忆生命周期

| 阶段 | 说明 |
|------|------|
| 热记忆 (Hot) | 最近 7 天，驻留在 TEE 内存中，低延迟访问 |
| 温记忆 (Warm) | 7-90 天，加密缓存在本地设备 |
| 冷记忆 (Cold) | 90 天以上，仅存储在去中心化网络 |
| 归档 (Archived) | Owner 主动归档，压缩存储，低优先级 |
| 遗忘 (Forgotten) | Owner 主动删除，分片引用移除，数据最终被 GC |

## 5. 模型层 (Model Layer)

### 5.1 基础模型要求

- 必须是开源权重模型（OSI 兼容许可证）
- 推荐基础模型：LLaMA-3-8B, Mistral-7B, Phi-3-mini 等
- 基础模型哈希必须记录在 DID 文档中，确保可验证

### 5.2 个人化权重格式 (Persona Weight)

个人化权重使用 OAP 定义的统一格式：

```json
{
  "format": "oap-persona-weight-v1",
  "baseModel": {
    "name": "llama-3-8b",
    "hash": "sha256:abc123...",
    "source": "https://huggingface.co/meta-llama/Meta-Llama-3-8B"
  },
  "weights": {
    "method": "lora",
    "rank": 16,
    "alpha": 32,
    "targetModules": ["q_proj", "v_proj", "k_proj", "o_proj"],
    "encryptedData": "base64-encoded-encrypted-safetensors",
    "encryptionMethod": "aes-256-gcm",
    "iv": "base64-encoded-iv",
    "tag": "base64-encoded-tag"
  },
  "training": {
    "startDate": "2026-01-15T00:00:00Z",
    "lastUpdateDate": "2026-05-21T00:00:00Z",
    "totalSteps": 15000,
    "datasetHash": "sha256:def456...",
    "trainingMethod": "oap-incremental-dpo"
  },
  "attestation": {
    "teeQuote": "base64-encoded-tee-attestation",
    "verifierPubKey": "0xVerifierPubKey..."
  }
}
```

### 5.3 增量更新协议

分身的个人化权重通过增量方式持续更新：

1. Owner 与分身交互产生训练信号（偏好反馈、纠正、新知识）
2. 训练信号加密存储为 `training-delta` 条目
3. 累积足够 delta 后，在 TEE 中执行增量训练
4. 新权重经 TEE 认证（Remote Attestation）后发布
5. 权重更新哈希写入 DID 文档，形成不可篡改的版本链

### 5.4 模型迁移

Owner 可将分身从一个基础模型迁移到另一个：

1. 在 TEE 中加载当前 Persona Weight
2. 通过知识蒸馏或权重投影生成新模型的 Persona Weight
3. Owner 验证迁移后分身的行为一致性
4. 确认后更新 DID 文档

## 6. 宪法层 (Constitution Layer)

### 6.1 宪法格式

宪法是 Owner 为分身设定的不可违反规则：

```json
{
  "format": "oap-constitution-v1",
  "avatarDID": "did:oap:ipfs:0x4a2b...8f3e",
  "version": 3,
  "created": "2026-01-15T00:00:00Z",
  "lastModified": "2026-05-01T00:00:00Z",
  "rules": [
    {
      "id": "rule-001",
      "priority": 100,
      "type": "prohibition",
      "scope": "all",
      "condition": "action.category == 'financial' AND action.amount > 100",
      "enforcement": "require-owner-confirmation",
      "description": "任何超过100元的金融操作必须获得本人确认"
    },
    {
      "id": "rule-002",
      "priority": 200,
      "type": "prohibition",
      "scope": "all",
      "condition": "action.involves('personal-health-data')",
      "enforcement": "block",
      "description": "绝不可对外分享个人健康数据"
    },
    {
      "id": "rule-003",
      "priority": 50,
      "type": "obligation",
      "scope": "interaction-with-other-avatars",
      "condition": "interaction.duration > 5min",
      "enforcement": "log-and-report",
      "description": "与其他分身交互超过5分钟须记录并向本人报告"
    }
  ],
  "overrides": [],
  "emergencyStop": {
    "enabled": true,
    "trigger": "owner-biometric-panic",
    "action": "freeze-all-interactions-and-notify-owner"
  }
}
```

### 6.2 宪法执行

- 宪法规则在 TEE 内的规则引擎中执行
- 每个动作在执行前必须通过宪法检查
- 宪法检查的结果生成审计日志（加密存储）
- 任何绕过宪法检查的尝试被视为安全违规，触发紧急停止

### 6.3 宪法修改

- 修改宪法需要 Owner 的**强认证**（多因素 + 生物识别）
- 修改历史不可删除，形成完整的版本链
- 放松限制的修改需 24 小时冷却期
- 紧急收紧可立即生效

## 7. 交互层 (Interaction Layer)

### 7.1 分身间通信协议 (Avatar-to-Avatar)

分身之间的通信遵循以下协议：

```
Avatar A                          Avatar B
   │                                 │
   │─── Discovery (DID Resolution) ──│
   │                                 │
   │─── Handshake ───────────────────│
   │    {did, capabilities,          │
   │     consentRequirements}        │
   │                                 │
   │◄── Handshake Response ──────────│
   │    {did, capabilities,          │
   │     consentProvided}            │
   │                                 │
   │─── Consent Verification ────────│
   │    (双方 Owner 授权确认)         │
   │                                 │
   │═══ Encrypted Channel (MLS) ═════│
   │    (端到端加密通信)              │
   │                                 │
   │─── Interaction ─────────────────│
   │◄── Response ────────────────────│
   │    ...                          │
   │                                 │
   │─── Interaction End ─────────────│
   │    {auditLog, digest}           │
```

### 7.2 消息格式

```json
{
  "protocol": "oap-interaction-v1",
  "version": 1,
  "messageId": "msg_0xHash...",
  "timestamp": "2026-05-21T14:30:00Z",
  "from": "did:oap:ipfs:0x4a2b...8f3e",
  "to": "did:oap:ipfs:0xOtherAvatar...",
  "type": "handshake | consent-request | interaction | interaction-end | error",
  "payload": {
    "encrypted": "base64-encoded-mls-ciphertext",
    "contentType": "application/json | text/plain | audio/opus | ..."
  },
  "metadata": {
    "threadId": "thread_0xHash...",
    "inReplyTo": "msg_0xPreviousHash...",
    "consentToken": "0xConsentToken..." 
  }
}
```

### 7.3 外部系统交互 (Avatar-to-External)

分身与外部系统（如网站、API、IoT 设备）的交互：

```json
{
  "protocol": "oap-external-v1",
  "avatarDID": "did:oap:ipfs:0x4a2b...8f3e",
  "request": {
    "target": "https://api.example.com/v1/resource",
    "method": "GET",
    "headers": {},
    "body": null
  },
  "delegation": {
    "scope": "read-only",
    "expiresAt": "2026-05-21T15:30:00Z",
    "ownerSignature": "0xSignature...",
    "constraints": {
      "maxRequests": 10,
      "allowedEndpoints": ["/v1/resource/*"]
    }
  },
  "auditTrail": {
    "constitutionCheck": "pass",
    "consentVerified": true,
    "teeAttestation": "base64-encoded-quote"
  }
}
```

### 7.4 Owner 与分身交互 (Owner-to-Avatar)

Owner 与自己分身的直接交互接口：

- **实时对话**：WebSocket / QUIC 端到端加密通道
- **异步指令**：通过去中心化消息队列（如 Waku）发送
- **紧急控制**：生物识别触发紧急停止 / 冻结 / 擦除
- **状态查询**：分身当前状态、正在执行的任务、交互历史摘要

## 8. 加密标准 (Cryptographic Standards)

### 8.1 密钥层级

```
Master Key (Owner 生物识别 + 助记词派生)
  │
  ├── Avatar Identity Key (Ed25519) — 签名、身份证明
  ├── Memory Encryption Key (AES-256) — 记忆数据加密
  │     └── Per-Shard Key (HKDF派生)
  ├── Persona Weight Key (AES-256) — 模型权重加密
  ├── Communication Key (X25519) — ECDH 密钥协商
  └── Emergency Key (Ed25519) — 紧急控制
```

### 8.2 加密算法

| 用途 | 算法 | 说明 |
|------|------|------|
| 对称加密 | AES-256-GCM | 数据加密（记忆、权重、通信） |
| 非对称签名 | Ed25519 | 身份验证、文档签名 |
| 密钥协商 | X25519 | ECDH，建立共享密钥 |
| 群组加密 | MLS (RFC 9420) | 多分身通信端到端加密 |
| 密钥派生 | HKDF-SHA256 | 从主密钥派生子密钥 |
| 哈希 | SHA-256 | 数据完整性校验 |
| 零知识证明 | Groth16 / PLONK | 推理结果可验证性 |

### 8.3 密钥恢复

- 社交恢复：指定 N 个受信任联系人，M-of-N 可恢复（默认 3-of-5）
- 时间锁恢复：设定延迟期（如 30 天），期间 Owner 可取消
- 硬件绑定：可选绑定到 Owner 的安全硬件（YubiKey / 手机 SE）

## 9. 可验证推理 (Verifiable Inference)

### 9.1 问题

当分身在远程算力节点上运行时，如何确保推理结果确实由正确的模型产生，而非被篡改？

### 9.2 方案：TEE 认证 + ZK 证明

1. 算力节点在 TEE 中加载分身（加密权重 + 基础模型）
2. TEE 生成 Remote Attestation Quote，证明运行环境可信
3. 推理完成后，TEE 内生成推理摘要的 ZK 证明
4. 验证者可检查 ZK 证明，确认推理确实由声明的模型在声明的环境中执行

### 9.3 验证流程

```
Owner 发布推理请求
  → 算力节点投标
  → Owner 选择节点
  → 节点在 TEE 中加载分身
  → 节点提供 TEE Attestation
  → Owner 验证 Attestation
  → 推理执行
  → 节点返回: {result, zkProof, teeAttestation}
  → Owner / 验证网络验证结果
```

## 10. 审计与合规 (Audit & Compliance)

### 10.1 审计日志

所有分身行为生成审计日志，加密存储：

```json
{
  "format": "oap-audit-v1",
  "avatarDID": "did:oap:ipfs:0x4a2b...8f3e",
  "entries": [
    {
      "id": "audit_0xHash...",
      "timestamp": "2026-05-21T14:30:00Z",
      "action": "interaction-with-avatar",
      "target": "did:oap:ipfs:0xOther...",
      "constitutionCheck": "pass",
      "consentProvided": true,
      "resultDigest": "sha256:abc...",
      "teeQuote": "base64..."
    }
  ],
  "merkleRoot": "0xRoot..."
}
```

### 10.2 审计权限

| 谁 | 能看什么 |
|------|---------|
| Owner | 一切 |
| 分身自身 | 仅自己的交互日志摘要 |
| 第三方审计者 | 仅 Owner 授权的脱敏摘要 |
| 法律机关 | 需 Owner 解密授权或法律令状 |

## 11. 分身生命周期 (Avatar Lifecycle)

```
创建 → 成长 → 活跃 → 休眠 → 唤醒 → 归档 → 传承/删除

创建: Owner 生成 DID，初始化基础模型和空 Persona Weight
成长: 持续交互，增量训练，权重演化
活跃: 正常运行，自主交互，代理任务
休眠: Owner 主动暂停，分身冻结状态
唤醒: Owner 重新激活，从冻结状态恢复
归档: Owner 不再活跃使用，压缩存储
传承: Owner 去世，按遗嘱转移或封存
删除: Owner 主动销毁，所有数据彻底擦除
```

## 12. 共识与治理 (Governance)

### 12.1 协议升级

- OAP 采用语义版本号 (SemVer)
- 主版本升级需社区投票（OAP Token 持有者）
- 次版本升级由技术委员会批准
- 修补版本由维护者直接发布

### 12.2 分身权利仲裁

- 去中心化仲裁法庭（如 Kleros 集成）
- 分身间纠纷由仲裁法庭裁决
- Owner 可为分身指定法律代理

## 附录 A: 数据类型注册表

| Type URI | 描述 | 格式引用 |
|----------|------|---------|
| `oap:memory:conversation` | 对话记忆 | Section 4.1 |
| `oap:memory:action` | 行动记忆 | Section 4.1 |
| `oap:memory:observation` | 观察记忆 | Section 4.1 |
| `oap:memory:reflection` | 反思维记忆 | Section 4.1 |
| `oap:personaweight:lora` | LoRA 个人化权重 | Section 5.2 |
| `oap:constitution:rule` | 宪法规则 | Section 6.1 |
| `oap:audit:entry` | 审计条目 | Section 10.1 |

## 附录 B: 推荐实现技术栈

| 组件 | 推荐技术 |
|------|---------|
| 去中心化身份 | W3C DID Core + did:oap 方法 |
| 去中心化存储 | IPFS / Arweave / Filecoin |
| 可信执行环境 | Intel SGX / AMD SEV / AWS Nitro Enclaves |
| 群组加密 | MLS (RFC 9420) |
| 零知识证明 | circom + snarkjs / Halo2 |
| 去中心化消息 | Waku / Libp2p |
| 基础模型 | LLaMA-3 / Mistral / Phi-3 (开源权重) |
| 个人化训练 | OAP LoRA Framework (见 lora-framework/) |

---

*本协议为草案，欢迎社区讨论与贡献。*

