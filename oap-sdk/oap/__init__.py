"""
OAP SDK — Open Avatar Protocol 开发工具包

快速开始：

    from oap import Avatar

    # 创建你的数字分身
    avatar = await Avatar.create(
        name="我的分身",
        mnemonic="your twelve word mnemonic phrase here",
    )

    # 对话
    reply = await avatar.chat("你好，帮我分析一下这个决策...")
    print(reply)

    # 训练（从对话中持续进化）
    await avatar.train()

    # 保存状态
    await avatar.save()
"""

from oap.avatar.core import Avatar
from oap.memory.store import MemoryStore
from oap.crypto.keyring import KeyRing
from oap.compute.client import ComputeClient

__version__ = "0.1.0"
__all__ = ["Avatar", "MemoryStore", "KeyRing", "ComputeClient"]
