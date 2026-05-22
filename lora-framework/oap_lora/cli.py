"""
OAP LoRA Framework - CLI 命令行工具

使用方式：
    oap-train init            # 初始化分身（生成密钥、设置宪法）
    oap-train train --method sft  # 运行 SFT 训练
    oap-train train --method dpo  # 运行 DPO 训练
    oap-train update          # 增量更新
    oap-train chat            # 与分身对话并收集训练信号
    oap-train export          # 导出加密权重
    oap-train status          # 查看分身状态
"""

import json
import os
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

console = Console()

CONFIG_DIR = Path.home() / ".oap"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> Optional[dict]:
    """加载配置"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_config(config: dict):
    """保存配置"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


@click.group()
def main():
    """OAP LoRA Framework - 你的数字分身个人化训练工具"""
    pass


@main.command()
def init():
    """初始化你的数字分身"""
    console.print(Panel.fit(
        "[bold blue]OAP 数字分身初始化[/bold blue]\n"
        "你的密钥在本地生成，永远不会离开你的设备。",
        border_style="blue"
    ))

    # 生成助记词（简化：实际应使用 BIP-39）
    import secrets
    wordlist = [
        "apple", "brave", "cloud", "dream", "earth", "flame", "grace", "heart",
        "ivory", "jewel", "karma", "light", "magic", "noble", "ocean", "peace",
        "quest", "river", "spirit", "truth", "unity", "valor", "wisdom", "xenon",
    ]
    mnemonic = " ".join(secrets.choice(wordlist) for _ in range(12))

    console.print("\n[bold yellow]你的助记词（请抄写保管，不要截图）：[/bold yellow]")
    console.print(Panel(mnemonic, border_style="yellow"))
    console.print("[red]⚠️  这是恢复你的分身的唯一凭证，请务必保管好！[/red]\n")

    if not Confirm.ask("我已经安全保管了助记词", default=False):
        console.print("[red]初始化中止。请在安全保管助记词后重新运行。[/red]")
        return

    # 设置分身名称
    avatar_name = Prompt.ask("为你的分身取一个名字", default="我的数字分身")

    # 生成 Avatar DID
    import hashlib
    avatar_id = hashlib.sha256(mnemonic.encode()).hexdigest()[:32]
    avatar_did = f"did:oap:ipfs:0x{avatar_id}"

    # 初始化密钥环
    from oap_lora.crypto.keys import AvatarKeyRing
    key_ring = AvatarKeyRing.from_mnemonic(mnemonic, avatar_did)
    public_keys = key_ring.export_public_keys()

    console.print(f"\n[green]✓ 分身 DID:[/green] {avatar_did}")
    console.print(f"[green]✓ 身份公钥:[/green] {public_keys['identityKey'][:32]}...")

    # 保存配置（不保存助记词！只保存 DID 和公钥）
    config = {
        "avatarDID": avatar_did,
        "avatarName": avatar_name,
        "publicKeys": public_keys,
        "created": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "dataDir": str(Path.home() / ".oap" / "data"),
        "weightsDir": str(Path.home() / ".oap" / "weights"),
    }
    save_config(config)

    # 创建必要目录
    Path(config["dataDir"]).mkdir(parents=True, exist_ok=True)
    Path(config["weightsDir"]).mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(
        f"[bold green]✓ 分身 '{avatar_name}' 初始化完成！[/bold green]\n\n"
        f"DID: {avatar_did}\n"
        f"数据目录: {config['dataDir']}\n"
        f"权重目录: {config['weightsDir']}\n\n"
        "下一步：\n"
        "  oap-train chat    # 开始与分身对话\n"
        "  oap-train train   # 训练个人化权重",
        border_style="green"
    ))


@main.command()
@click.option("--method", type=click.Choice(["sft", "dpo", "auto"]), default="auto", help="训练方法")
@click.option("--mnemonic", prompt="输入你的助记词", hide_input=True, help="助记词（本地处理，不上传）")
@click.option("--epochs", default=3, help="训练轮次")
@click.option("--model", default="meta-llama/Meta-Llama-3-8B", help="基础模型")
def train(method: str, mnemonic: str, epochs: int, model: str):
    """训练个人化 LoRA 权重"""
    config = load_config()
    if not config:
        console.print("[red]请先运行 oap-train init 初始化分身[/red]")
        return

    console.print(Panel.fit(
        f"[bold blue]开始训练分身权重[/bold blue]\n"
        f"方法: {method} | 轮次: {epochs} | 基础模型: {model}\n"
        "所有训练在本地进行，数据不上传任何云端。",
        border_style="blue"
    ))

    # 重建密钥环
    from oap_lora.crypto.keys import AvatarKeyRing
    key_ring = AvatarKeyRing.from_mnemonic(mnemonic.strip(), config["avatarDID"])

    # 初始化训练器
    from oap_lora.training.engine import PersonaTrainer, PersonaWeightConfig
    weight_config = PersonaWeightConfig(base_model_name=model)
    trainer = PersonaTrainer(config=weight_config, key_ring=key_ring)

    # 加载数据
    from oap_lora.data.collector import DataCollector
    collector = DataCollector(config["avatarDID"], key_ring, config["dataDir"])
    signals = collector.collect_training_signals()

    if not signals:
        console.print("[yellow]没有找到训练数据。请先运行 oap-train chat 与分身对话。[/yellow]")
        return

    console.print(f"[blue]找到 {len(signals)} 条训练信号[/blue]")
    trainer.add_training_signals(signals)

    # 执行训练
    output_dir = config["weightsDir"]
    if method == "sft" or (method == "auto" and not any(s.type == "preference" for s in signals)):
        encrypted_path = trainer.train_sft(output_dir=output_dir, num_epochs=epochs)
    elif method == "dpo" or (method == "auto" and any(s.type == "preference" for s in signals)):
        encrypted_path = trainer.train_dpo(output_dir=output_dir, num_epochs=epochs)
    else:
        encrypted_path = trainer.train_sft(output_dir=output_dir, num_epochs=epochs)

    if encrypted_path:
        console.print(Panel.fit(
            f"[bold green]✓ 训练完成！[/bold green]\n"
            f"加密权重文件: {encrypted_path}\n"
            "权重已用你的私钥加密，只有你可以使用。",
            border_style="green"
        ))


@main.command()
@click.option("--mnemonic", prompt="输入你的助记词", hide_input=True)
def chat(mnemonic: str):
    """与分身对话并采集训练信号"""
    config = load_config()
    if not config:
        console.print("[red]请先运行 oap-train init 初始化分身[/red]")
        return

    from oap_lora.crypto.keys import AvatarKeyRing
    from oap_lora.data.collector import DataCollector

    key_ring = AvatarKeyRing.from_mnemonic(mnemonic.strip(), config["avatarDID"])
    collector = DataCollector(config["avatarDID"], key_ring, config["dataDir"])

    avatar_name = config.get("avatarName", "分身")
    console.print(Panel.fit(
        f"[bold blue]与 {avatar_name} 对话[/bold blue]\n"
        "输入你的消息，然后对分身回复给出反馈（y=好/n=不好/c=纠正/q=退出）\n"
        "所有对话在本地加密存储，成为训练数据。",
        border_style="blue"
    ))

    session = collector.start_session()
    signal_count = 0

    while True:
        user_input = Prompt.ask(f"\n[bold cyan]你[/bold cyan]")
        if user_input.lower() in ("q", "quit", "exit"):
            break

        collector.chat(user_input)

        # 这里实际应该调用分身推理
        # 简化：直接让 Owner 输入分身的模拟回复（用于数据采集演示）
        avatar_response = Prompt.ask(f"[bold green]{avatar_name}[/bold green] (模拟回复)")
        collector.avatar_reply(avatar_response)

        # 收集反馈
        feedback_choice = Prompt.ask(
            "反馈",
            choices=["y", "n", "c", "s"],
            default="s",
        )
        mapping = {"y": "positive", "n": "negative", "s": None}

        if feedback_choice == "c":
            correction = Prompt.ask("正确的回复应该是")
            collector.give_feedback("correction", correction)
            signal_count += 1
            console.print("[green]✓ 纠正已记录[/green]")
        elif feedback_choice == "n":
            correction = Prompt.ask("你期望的回复（可选，直接回车跳过）", default="")
            collector.give_feedback("negative", correction if correction else None)
            signal_count += 1
            console.print("[yellow]✓ 负面反馈已记录[/yellow]")
        elif feedback_choice == "y":
            collector.give_feedback("positive")
            signal_count += 1
            console.print("[green]✓ 正面反馈已记录[/green]")

    collector.end_session()
    console.print(f"\n[green]✓ 会话结束，采集了 {signal_count} 条训练信号，已加密保存。[/green]")


@main.command()
def status():
    """查看分身状态"""
    config = load_config()
    if not config:
        console.print("[red]请先运行 oap-train init 初始化分身[/red]")
        return

    table = Table(title=f"数字分身状态: {config.get('avatarName', '未命名')}")
    table.add_column("项目", style="cyan")
    table.add_column("状态", style="green")

    table.add_row("DID", config["avatarDID"])
    table.add_row("创建时间", config.get("created", "未知"))

    # 检查权重文件
    weights_dir = Path(config.get("weightsDir", ""))
    weight_file = weights_dir / "persona_weight.oap.json"
    table.add_row("个人化权重", "✓ 已训练" if weight_file.exists() else "✗ 未训练")

    # 检查数据文件
    data_dir = Path(config.get("dataDir", ""))
    data_count = len(list(data_dir.glob("*.oap.enc.json"))) if data_dir.exists() else 0
    table.add_row("训练数据会话", f"{data_count} 个会话")

    console.print(table)
    console.print("\n[dim]提示: 所有数据均在本地加密存储，只有你的助记词可以解密[/dim]")


if __name__ == "__main__":
    main()
