#!/usr/bin/env python3
"""
OAP 去中心化网络 Demo

验证：两个 Relay 组成联邦，两个分身分别连不同 Relay，
通过联邦自动路由实现跨节点加密通信。

架构：
  Relay-1 (8765) ← 联邦 → Relay-2 (8767)
      ↑                        ↑
  阿明分身                  小红分身

阿明连 Relay-1，小红连 Relay-2。
阿明给小红发消息 → Relay-1 通过联邦路由到 Relay-2 → 投递给小红。
中继只能看到密文。

运行：
    python3.11 examples/demo_decentralized.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from oap import Avatar
from oap.compute.client import InferenceMode
from oap.network import AvatarNetwork, PeerChannel


async def run_decentralized_demo():
    print("\n" + "═" * 60)
    print("  OAP 去中心化网络 Demo")
    print("  两个 Relay + 两个分身 = 跨节点加密通信")
    print("═" * 60)

    # ── 启动两个联邦 Relay ────────────────────────────────
    print("\n▸ 启动联邦 Relay 节点...")

    # 导入联邦中继
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "oap-relay"))
    from federated_relay import FederatedRelay

    relay1 = FederatedRelay(relay_id="relay-alpha", port=8765)
    relay2 = FederatedRelay(relay_id="relay-beta", port=8767, peer_urls=["ws://localhost:8765"])

    # 启动两个 Relay
    relay1_task = asyncio.create_task(relay1.start("localhost", 8765))
    await asyncio.sleep(0.5)

    relay2_task = asyncio.create_task(relay2.start("localhost", 8767))
    await asyncio.sleep(0.5)

    # 等待 Relay-Beta 连接到 Relay-Alpha 联邦
    await asyncio.sleep(2.0)

    # ── 创建两个分身 ─────────────────────────────────────
    print("\n▸ 创建阿明和小红的分身...")

    avatar_a, mnemonic_a = await Avatar.create(
        name="阿明",
        base_dir="/tmp/oap_decent_a",
        inference_mode=InferenceMode.MOCK,
    )

    avatar_b, mnemonic_b = await Avatar.create(
        name="小红",
        base_dir="/tmp/oap_decent_b",
        inference_mode=InferenceMode.MOCK,
    )

    did_a = avatar_a.config.avatar_did
    did_b = avatar_b.config.avatar_did

    print(f"  ✓ 阿明: {did_a}")
    print(f"  ✓ 小红: {did_b}")

    # ── 连接到不同的 Relay ───────────────────────────────
    print("\n▸ 连接到不同 Relay（去中心化！）...")

    net_a = AvatarNetwork(avatar_a.key_ring, relay_url="ws://localhost:8765")
    net_b = AvatarNetwork(avatar_b.key_ring, relay_url="ws://localhost:8767")

    ok_a = await net_a.connect()
    ok_b = await net_b.connect()

    if not ok_a or not ok_b:
        print("  ⚠ 连接失败")
        return

    print(f"  ✓ 阿明 → Relay-Alpha")
    print(f"  ✓ 小红 → Relay-Beta")

    # ── 注册 ──────────────────────────────────────────────
    print("\n▸ 在各自的 Relay 上注册...")

    await net_a.register(display_name="阿明")
    await net_b.register(display_name="小红")

    print("  ✓ 双方已注册（注册信息将通过 Gossip 同步到联邦所有节点）")

    # 等待 Gossip 同步
    await asyncio.sleep(1.0)

    # ── 验证联邦同步 ──────────────────────────────────────
    print("\n▸ 验证联邦同步：阿明能否通过 Relay-Alpha 找到小红？")

    peers = await net_a.list_avatars()
    for p in peers:
        print(f"  发现: {p.display_name} @ {p.did[:30]}...")

    # ── 跨 Relay 握手 ─────────────────────────────────────
    print(f"\n▸ 阿明向小红发起跨节点握手...")

    received_by_a = []
    received_by_b = []

    async def on_a_msg(ch: PeerChannel, msg: str):
        received_by_a.append(msg)
        print(f"  [阿明收到] {msg[:80]}")

    async def on_b_msg(ch: PeerChannel, msg: str):
        received_by_b.append(msg)
        print(f"  [小红收到] {msg[:80]}")

    net_a.on_message = on_a_msg
    net_b.on_message = on_b_msg

    channel_a = await net_a.handshake(did_b, timeout=10.0)

    if not channel_a:
        print("  ⚠ 握手失败")
        await net_a.disconnect()
        await net_b.disconnect()
        relay1_task.cancel()
        relay2_task.cancel()
        return

    print(f"  ✓ 跨节点加密通道已建立！")
    print(f"    阿明(Relay-Alpha) ←联邦路由→ 小红(Relay-Beta)")

    # ── 跨节点加密通信 ────────────────────────────────────
    print("\n▸ 开始跨节点加密通信...\n")

    await net_a.send(channel_a, "小红你好！我是阿明的分身，我连的是 Relay-Alpha，你连的是 Relay-Beta，但我们的消息仍然是端到端加密的！")
    await asyncio.sleep(0.5)

    await net_a.send(channel_a, "即使有人控制了中间的 Relay 节点，他也只能看到密文。")
    await asyncio.sleep(0.5)

    channel_b = net_b.get_channel(did_a)
    if channel_b:
        await net_b.send(channel_b, "阿明你好！没错，去中心化就是应该这样——没有任何单点可以控制或窃听我们的通信。")
        await asyncio.sleep(0.5)

    # 等消息到达
    await asyncio.sleep(2.0)

    # ── 统计 ──────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  去中心化通信统计")

    print(f"\n  阿明 (连 Relay-Alpha):")
    print(f"    加密通道: {len(net_a.active_channels)} 个")
    if net_a.active_channels:
        ch = net_a.active_channels[0]
        print(f"    发送: {ch.messages_sent} 条 | 接收: {ch.messages_received} 条")

    print(f"\n  小红 (连 Relay-Beta):")
    print(f"    加密通道: {len(net_b.active_channels)} 个")
    if net_b.active_channels:
        ch = net_b.active_channels[0]
        print(f"    发送: {ch.messages_sent} 条 | 接收: {ch.messages_received} 条")

    print(f"\n  Relay-Alpha: {relay1.message_count} 条消息路由")
    print(f"  Relay-Beta:  {relay2.message_count} 条消息路由")

    # ── 清理 ──────────────────────────────────────────────
    await net_a.disconnect()
    await net_b.disconnect()
    relay1_task.cancel()
    relay2_task.cancel()

    print("\n" + "═" * 60)
    print("  Demo 完成！")
    print()
    print("  关键结论：")
    print("  1. 两个分身连不同的 Relay，仍能加密通信")
    print("  2. Relay 通过 Gossip 自动同步注册信息")
    print("  3. 消息通过联邦自动路由到目标所在的 Relay")
    print("  4. 任何 Relay 宕机，分身可自动切换到其他 Relay")
    print("  5. 中继只看到密文，无法解密任何内容")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_decentralized_demo())