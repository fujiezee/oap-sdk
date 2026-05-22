#!/usr/bin/env python3
"""
OAP 双分身交互 Demo

模拟两个人（阿明 & 小红）的分身通过网络互相发现、握手、加密通信。

运行方式：
    1. 先启动中继服务器：
       python3.11 ../oap-relay/relay_server.py &

    2. 运行本 Demo：
       python3.11 examples/demo_network.py

前提：pip install websockets
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from oap import Avatar
from oap.compute.client import InferenceMode
from oap.network import AvatarNetwork, PeerChannel


RELAY_URL = "ws://localhost:8765"


async def simulate_two_avatars():
    print("\n" + "═" * 60)
    print("  OAP 双分身网络交互 Demo")
    print("═" * 60)

    # ── 创建两个分身 ─────────────────────────────────────
    print("\n▸ 创建阿明和小红的分身...")

    avatar_a, mnemonic_a = await Avatar.create(
        name="阿明",
        base_dir="/tmp/oap_demo_net_a",
        inference_mode=InferenceMode.MOCK,
    )

    avatar_b, mnemonic_b = await Avatar.create(
        name="小红",
        base_dir="/tmp/oap_demo_net_b",
        inference_mode=InferenceMode.MOCK,
    )

    print(f"  ✓ 阿明: {avatar_a.config.avatar_did}")
    print(f"  ✓ 小红: {avatar_b.config.avatar_did}")

    # ── 连接到中继 ───────────────────────────────────────
    print("\n▸ 连接到中继服务器...")

    net_a = AvatarNetwork(avatar_a.key_ring, relay_url=RELAY_URL)
    net_b = AvatarNetwork(avatar_b.key_ring, relay_url=RELAY_URL)

    connected_a = await net_a.connect()
    connected_b = await net_b.connect()

    if not connected_a or not connected_b:
        print("  ⚠ 连接失败！请确保中继服务器已启动：")
        print("    python3.11 ../oap-relay/relay_server.py")
        return

    print("  ✓ 双方已连接到中继")

    # ── 注册 ──────────────────────────────────────────────
    print("\n▸ 注册分身到网络...")

    await net_a.register(display_name="阿明")
    await net_b.register(display_name="小红")

    print("  ✓ 双方已注册")

    # 等待注册信息同步
    await asyncio.sleep(0.5)

    # ── 发现 ──────────────────────────────────────────────
    print("\n▸ 阿明发现网络上的其他分身...")

    peers = await net_a.list_avatars()
    for p in peers:
        print(f"  发现: {p.display_name} ({p.did[:30]}...) 在线={p.online}")

    # ── 握手（建立加密通道）───────────────────────────────
    print(f"\n▸ 阿明向小红发起握手...")

    # 双方都设置消息回调（先设置再握手，确保不丢消息）
    received_by_a = []
    received_by_b = []

    async def on_a_receive(channel: PeerChannel, message: str):
        received_by_a.append(message)
        print(f"  [阿明收到] 来自 {channel.peer_display_name}: {message[:80]}")

    async def on_b_receive(channel: PeerChannel, message: str):
        received_by_b.append(message)
        print(f"  [小红收到] 来自 {channel.peer_display_name}: {message[:80]}")

    net_a.on_message = on_a_receive
    net_b.on_message = on_b_receive

    # 阿明发起握手
    channel_a = await net_a.handshake(avatar_b.config.avatar_did)

    if not channel_a:
        print("  ⚠ 握手失败")
        return

    # 等待握手完成
    await asyncio.sleep(0.5)

    print(f"  ✓ 加密通道已建立！")
    print(f"    阿明 → 小红 (AES-256-GCM 端到端加密)")

    # ── 加密通信 ──────────────────────────────────────────
    print("\n▸ 开始加密通信...\n")

    messages_from_a = [
        "你好小红！我是阿明的数字分身。",
        "阿明最近在考虑换工作，你对职业发展有什么看法？",
        "对了，阿明说下周想请你喝咖啡聊聊。",
    ]

    for msg in messages_from_a:
        await net_a.send(channel_a, msg)
        print(f"  [阿明发送] {msg[:60]}...")
        await asyncio.sleep(0.3)

    # 等待消息到达
    await asyncio.sleep(1.0)

    # 小红回复
    channel_b = net_b.get_channel(avatar_a.config.avatar_did)
    if channel_b:
        replies_from_b = [
            "你好阿明的分身！我是小红，很高兴认识你。",
            "关于换工作，我觉得最重要的是想清楚自己要什么。",
            "咖啡好啊！帮我转告阿明，下周三下午可以。",
        ]
        for reply in replies_from_b:
            await net_b.send(channel_b, reply)
            print(f"  [小红发送] {reply[:60]}...")
            await asyncio.sleep(0.3)

    # 阿明收小红的消息
    await asyncio.sleep(1.0)

    # ── 统计 ──────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  通信统计")

    print(f"\n  阿明:")
    print(f"    加密通道: {len(net_a.active_channels)} 个")
    if net_a.active_channels:
        ch = net_a.active_channels[0]
        print(f"    发送消息: {ch.messages_sent} 条")
        print(f"    接收消息: {ch.messages_received} 条")

    print(f"\n  小红:")
    print(f"    加密通道: {len(net_b.active_channels)} 个")
    if net_b.active_channels:
        ch = net_b.active_channels[0]
        print(f"    发送消息: {ch.messages_sent} 条")
        print(f"    接收消息: {ch.messages_received} 条")

    # ── 清理 ──────────────────────────────────────────────
    await net_a.disconnect()
    await net_b.disconnect()

    print("\n" + "═" * 60)
    print("  Demo 完成！")
    print("  中继只转发了密文，从未读取任何消息内容。")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(simulate_two_avatars())