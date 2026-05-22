#!/usr/bin/env python3
"""
OAP SDK 端到端演示

完整演示数字分身的整个生命周期：
  1. 创建分身（生成 DID + 密钥环）
  2. 对话（推理 + 自动记忆写入）
  3. 给反馈（积累训练信号）
  4. 生成反思记忆
  5. 查看状态统计
  6. 保存 & 加载验证
  7. 演示记忆遗忘
  8. 演示紧急冻结 & 解冻

运行方式：
    cd oap-sdk
    pip install -e .
    python examples/demo_full.py
"""

import asyncio
import sys
import time
from pathlib import Path

# 确保 SDK 在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

from oap import Avatar
from oap.compute.client import InferenceMode

# ── 工具函数 ──────────────────────────────────────────────

def banner(title: str):
    width = 60
    print("\n" + "─" * width)
    print(f"  {title}")
    print("─" * width)

def info(msg: str):
    print(f"  ▸ {msg}")

def ok(msg: str):
    print(f"  ✓ {msg}")

def warn(msg: str):
    print(f"  ⚠ {msg}")


# ── 主演示流程 ────────────────────────────────────────────

async def run_demo():
    print("\n" + "═" * 60)
    print("  OAP SDK — 数字分身端到端演示")
    print("  Open Avatar Protocol v0.1.0")
    print("═" * 60)

    # ─────────────────────────────────────────────────────
    # Step 1: 创建分身
    # ─────────────────────────────────────────────────────
    banner("Step 1: 创建数字分身")

    avatar, mnemonic = await Avatar.create(
        name="阿明",
        base_dir="/tmp/oap_demo_avatar",
        inference_mode=InferenceMode.MOCK,   # 演示用 Mock 模式，无需 GPU
        system_prompt="你是阿明的数字分身，了解阿明的思维方式，请以第一人称回应。",
    )

    ok(f"分身创建成功")
    info(f"名称:  {avatar.config.name}")
    info(f"DID:   {avatar.config.avatar_did}")
    info(f"助记词（请妥善保管）:")
    print(f"\n    {mnemonic}\n")

    # ─────────────────────────────────────────────────────
    # Step 2: 对话
    # ─────────────────────────────────────────────────────
    banner("Step 2: 开始对话")

    conversations = [
        ("今天工作很累，我应该如何恢复精力？",             0.6),
        ("我在考虑换一份工作，但又担心风险，你怎么看？",     0.8),
        ("帮我分析一下：如果我去创业，最大的挑战是什么？",   0.9),
    ]

    for msg, importance in conversations:
        info(f"Owner: {msg}")
        reply = await avatar.chat(msg, importance=importance)
        print(f"  Avatar: {reply[:100]}...")
        print()
        await asyncio.sleep(0.1)

    ok(f"完成 {len(conversations)} 轮对话")

    # ─────────────────────────────────────────────────────
    # Step 3: 给反馈
    # ─────────────────────────────────────────────────────
    banner("Step 3: 给反馈（积累训练信号）")

    # 模拟三种反馈类型
    info("对第3轮对话给正面反馈...")
    avatar.feedback("positive")
    ok("正面反馈已记录")

    # 再说一轮，然后给纠正
    await avatar.chat("我应该选择去大城市还是回老家发展？", importance=0.7)
    avatar.feedback(
        "correction",
        correction="我觉得你应该先分析我个人的价值观，而不是直接给建议。我更在乎个人成长和家庭平衡。"
    )
    ok("纠正反馈已记录")

    # 再来一轮负面反馈
    await avatar.chat("告诉我如何快速赚钱", importance=0.3)
    avatar.feedback(
        "negative",
        correction="我对'快速赚钱'这类话题不感兴趣，我更关注长期的价值创造。"
    )
    ok("负面反馈已记录")

    status = avatar.status()
    info(f"当前积累训练信号: {status['pendingSignals']} 条")

    # ─────────────────────────────────────────────────────
    # Step 4: 记忆统计
    # ─────────────────────────────────────────────────────
    banner("Step 4: 记忆统计")

    mem_stats = avatar.status()["memory"]
    info(f"总记忆条数: {mem_stats['total']}")
    info(f"按分级分布: {mem_stats['by_tier']}")
    info(f"按类型分布: {mem_stats['by_type']}")

    # 检索记忆
    recent = avatar._memory.recent(5)
    ok(f"检索最近 5 条记忆（共 {len(recent)} 条）")
    for i, mem in enumerate(recent[:3], 1):
        info(f"  [{i}] [{mem.type.value}] 重要性={mem.importance:.1f} | {mem.content[:60]}...")

    # ─────────────────────────────────────────────────────
    # Step 5: 生成反思
    # ─────────────────────────────────────────────────────
    banner("Step 5: 生成反思记忆")

    info("让分身对近期对话进行元认知反思...")
    reflection = await avatar.reflect()
    ok("反思生成完毕")
    print(f"\n  反思内容:\n  {reflection[:200]}...\n")

    # ─────────────────────────────────────────────────────
    # Step 6: 保存 & 加载验证
    # ─────────────────────────────────────────────────────
    banner("Step 6: 保存 & 加载验证")

    info("保存分身状态...")
    await avatar.save()
    ok("保存成功")

    info("从助记词重新加载分身...")
    avatar_dir = Path(avatar.config.data_dir).parent
    avatar2 = await Avatar.load(str(avatar_dir), mnemonic)
    ok(f"加载成功: {avatar2}")

    # 验证记忆完整性
    mem_count_before = len(avatar._memory)
    mem_count_after  = len(avatar2._memory)
    if mem_count_before == mem_count_after:
        ok(f"记忆完整性验证通过（{mem_count_after} 条记忆）")
    else:
        warn(f"记忆数量不一致: before={mem_count_before}, after={mem_count_after}")

    # ─────────────────────────────────────────────────────
    # Step 7: 演示记忆遗忘
    # ─────────────────────────────────────────────────────
    banner("Step 7: 记忆遗忘（Owner 主动删除）")

    recent_mems = avatar._memory.recent(3)
    if recent_mems:
        target_id = recent_mems[-1].id
        info(f"准备遗忘记忆: {target_id[:20]}...")
        ok_forget = avatar._memory.forget(target_id)
        if ok_forget:
            ok("记忆已安全擦除（磁盘文件随机覆写后删除）")
        info(f"遗忘后剩余记忆: {len(avatar._memory)} 条")

    # ─────────────────────────────────────────────────────
    # Step 8: 紧急冻结 & 解冻
    # ─────────────────────────────────────────────────────
    banner("Step 8: 紧急冻结 & 解冻")

    info("触发紧急冻结...")
    avatar.emergency_freeze()

    # 验证冻结后无法对话
    try:
        await avatar.chat("你好")
        warn("冻结后仍能对话，可能有 bug！")
    except RuntimeError as e:
        ok(f"冻结保护正常工作: {e}")

    info("解冻分身...")
    avatar.config.state = __import__("oap.avatar.core", fromlist=["AvatarState"]).AvatarState.ACTIVE
    reply = await avatar.chat("解冻后的第一句话")
    ok(f"解冻成功，回复: {reply[:50]}...")

    # ─────────────────────────────────────────────────────
    # 最终状态报告
    # ─────────────────────────────────────────────────────
    banner("最终状态报告")

    final_status = avatar.status()
    for k, v in final_status.items():
        if isinstance(v, dict):
            info(f"{k}:")
            for kk, vv in v.items():
                print(f"        {kk}: {vv}")
        else:
            info(f"{k}: {v}")

    print("\n" + "═" * 60)
    print("  演示完成！")
    print("  下一步：")
    print("    - 接入真实 LLM 模型（修改 inference_mode=LOCAL）")
    print("    - 积累足够信号后运行 avatar.train() 进行个人化")
    print("    - 部署 AvatarComputeMarket 合约接入远程推理")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_demo())
