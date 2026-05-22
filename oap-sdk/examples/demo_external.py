#!/usr/bin/env python3
"""
OAP SDK — 外部推理 Demo

用真实的 LLM API 让数字分身"说话"。

支持的 API：
  - OpenAI (GPT-4o-mini, GPT-4o 等)
  - Anthropic (Claude 3.5 Sonnet 等)
  - DeepSeek (deepseek-chat)
  - 本地 Ollama (http://localhost:11434/v1)
  - 任何 OpenAI 兼容端点

运行方式：
    # OpenAI（默认）
    export OPENAI_API_KEY="sk-..."
    python3.11 examples/demo_external.py

    # DeepSeek
    python3.11 examples/demo_external.py --api-type openai --api-base https://api.deepseek.com/v1 --model deepseek-chat

    # 本地 Ollama
    python3.11 examples/demo_external.py --api-type openai --api-base http://localhost:11434/v1 --model llama3

    # Anthropic Claude
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3.11 examples/demo_external.py --api-type anthropic --api-base https://api.anthropic.com/v1 --model claude-3-5-sonnet-20241022
"""

import asyncio
import argparse
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from oap import Avatar
from oap.compute.client import InferenceMode


async def run_demo(api_type: str, api_base: str, api_key: str, model: str):
    print("\n" + "═" * 60)
    print("  OAP SDK — 外部推理 Demo")
    print(f"  API: {api_type} | 端点: {api_base} | 模型: {model}")
    print("═" * 60)

    # ── 创建分身 ─────────────────────────────────────────
    print("\n▸ 创建数字分身（接入外部推理）...")

    avatar, mnemonic = await Avatar.create(
        name="阿明",
        base_dir="/tmp/oap_demo_external",
        inference_mode=InferenceMode.EXTERNAL,
        api_base=api_base,
        api_key=api_key,
        api_type=api_type,
        model=model,
        system_prompt=(
            "你是阿明的数字分身，名字叫「小明」。你完全了解阿明的思维方式、价值观和习惯。"
            "你说话自然、真诚，像一个真正的朋友。"
            "阿明是一个注重个人成长和家庭平衡的人，喜欢思考技术和社会问题。"
            "请用中文回应，以第一人称的方式，像朋友一样对话。"
        ),
    )

    print(f"  ✓ 分身创建成功")
    print(f"  DID: {avatar.config.avatar_did}")
    print(f"  模式: EXTERNAL ({model})")

    # ── 对话 ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  开始对话（输入 q 退出，输入 f 给反馈）")
    print("─" * 60)

    conversations = [
        "你好！作为我的数字分身，你了解我什么？",
        "我最近在犹豫要不要换工作，你怎么看？",
        "帮我分析一下，如果我去创业最大的风险是什么？",
        "你觉得我最大的优势是什么？",
    ]

    for msg in conversations:
        print(f"\n  阿明: {msg}")
        try:
            reply = await avatar.chat(msg, importance=0.6)
            print(f"  小明: {reply}")
        except Exception as e:
            print(f"  ⚠ 推理失败: {e}")
            print("  请检查 API Key 和网络连接")
            break
        await asyncio.sleep(0.3)

    # ── 交互模式 ─────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  进入交互模式（输入消息对话，q 退出，f+纠正内容 给反馈）")
    print("─" * 60)

    while True:
        try:
            user_input = input("\n  阿明> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit"):
            break

        # 反馈模式
        if user_input.startswith("f "):
            correction = user_input[2:]
            avatar.feedback("correction", correction)
            print(f"  ✓ 纠正反馈已记录: {correction[:50]}...")
            continue

        try:
            reply = await avatar.chat(user_input)
            print(f"  小明: {reply}")
        except Exception as e:
            print(f"  ⚠ 推理失败: {e}")

    # ── 保存 & 统计 ──────────────────────────────────────
    await avatar.save()
    status = avatar.status()
    print(f"\n  记忆条数: {status['memory']['total']}")
    print(f"  训练信号: {status['pendingSignals']}")
    print(f"  助记词: {mnemonic}")
    print("\n  ✓ 状态已保存，下次可用助记词加载继续对话")
    print("═" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="OAP 外部推理 Demo")
    parser.add_argument("--api-type", default="openai", choices=["openai", "anthropic", "custom"],
                        help="API 类型")
    parser.add_argument("--api-base", default=None,
                        help="API 端点 URL（默认: OpenAI）")
    parser.add_argument("--api-key", default=None,
                        help="API Key（也可通过环境变量设置）")
    parser.add_argument("--model", default=None,
                        help="模型名称（默认: gpt-4o-mini）")

    args = parser.parse_args()

    # 根据类型设置默认值
    if args.api_type == "anthropic":
        api_base = args.api_base or "https://api.anthropic.com/v1"
        api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        model = args.model or "claude-3-5-sonnet-20241022"
    elif args.api_type == "openai":
        api_base = args.api_base or "https://api.openai.com/v1"
        api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
        model = args.model or "gpt-4o-mini"
    else:
        api_base = args.api_base or "https://api.openai.com/v1"
        api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
        model = args.model or "gpt-4o-mini"

    if not api_key:
        print("⚠ 需要 API Key！")
        print("\n设置方式：")
        print('  export OPENAI_API_KEY="sk-..."        # OpenAI')
        print('  export ANTHROPIC_API_KEY="sk-ant-..."  # Anthropic')
        print('  或运行时传入: --api-key "sk-..."')
        sys.exit(1)

    asyncio.run(run_demo(api_type=args.api_type, api_base=api_base, api_key=api_key, model=model))


if __name__ == "__main__":
    main()
