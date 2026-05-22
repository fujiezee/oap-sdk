"""
OAP CLI — 数字分身命令行工具

用法：
    oap init                    创建新分身
    oap chat                    与分身对话（交互模式）
    oap chat -m "你好"          单次对话
    oap status                  查看分身状态
    oap reflect                 让分身生成反思
    oap forget --before 30d     遗忘 30 天前的记忆
    oap freeze                  紧急冻结
    oap list                    列出所有分身

外部推理：
    oap init --mode external --model gpt-4o-mini
    oap init --mode external --api-type anthropic --model claude-3-5-sonnet-20241022
    oap init --mode external --api-base http://localhost:11434/v1 --model llama3

    # 或设置环境变量
    export OPENAI_API_KEY="sk-..."
    oap chat
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.markdown import Markdown

console = Console()

# ── 常量 ──────────────────────────────────────────────────

OAP_HOME = Path.home() / ".oap"
AVATARS_DIR = OAP_HOME / "avatars"
ACTIVE_FILE = OAP_HOME / "active.json"


# ── 工具函数 ──────────────────────────────────────────────

def _ensure_dirs():
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)


def _get_active_avatar() -> dict:
    """获取当前激活的分身信息"""
    if not ACTIVE_FILE.exists():
        return None
    with open(ACTIVE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _set_active_avatar(info: dict):
    """设置当前激活的分身"""
    _ensure_dirs()
    with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)


def _list_avatars() -> list[dict]:
    """列出所有已创建的分身"""
    if not AVATARS_DIR.exists():
        return []
    avatars = []
    for d in AVATARS_DIR.iterdir():
        config_file = d / "config.enc.json"
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            avatars.append({
                "dir": str(d),
                "did_hint": data.get("_did_hint", "unknown"),
            })
    return avatars


def _resolve_mode(mode_str: str):
    """从字符串解析 InferenceMode"""
    from oap.compute.client import InferenceMode
    mapping = {
        "mock": InferenceMode.MOCK,
        "external": InferenceMode.EXTERNAL,
        "local": InferenceMode.LOCAL,
        "remote": InferenceMode.REMOTE,
    }
    return mapping.get(mode_str.lower(), InferenceMode.MOCK)


# ── CLI 主入口 ────────────────────────────────────────────

@click.group()
@click.version_option(version="0.1.0", prog_name="oap")
def main():
    """OAP — 数字分身命令行工具"""
    pass


# ── init ──────────────────────────────────────────────────

@main.command()
@click.option("--name", "-n", help="分身名称")
@click.option("--mode", "-m", default="mock",
              type=click.Choice(["mock", "external", "local", "remote"]),
              help="推理模式")
@click.option("--model", default="gpt-4o-mini", help="模型名称（external 模式）")
@click.option("--api-base", default=None, help="API 端点 URL")
@click.option("--api-type", default="openai", type=click.Choice(["openai", "anthropic", "custom"]),
              help="API 类型")
@click.option("--api-key", default=None, help="API Key（建议用环境变量）")
@click.option("--system-prompt", "-s", default=None, help="系统提示词")
def init(name, mode, model, api_base, api_type, api_key, system_prompt):
    """创建新的数字分身"""

    console.print(Panel.fit(
        "[bold blue]OAP 数字分身初始化[/bold blue]\n"
        "你的密钥在本地生成，永远不会离开你的设备。",
        border_style="blue"
    ))

    if not name:
        name = Prompt.ask("  为你的分身取个名字", default="我的分身")

    infer_mode = _resolve_mode(mode)

    # 如果是 external 模式，检查 API Key
    if infer_mode.value == "external":
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key and api_type != "openai":
            console.print("[yellow]⚠ 外部推理需要 API Key[/yellow]")
            api_key = Prompt.ask("  请输入 API Key", password=True)
        if not api_base:
            if api_type == "anthropic":
                api_base = "https://api.anthropic.com/v1"
            else:
                api_base = "https://api.openai.com/v1"

    console.print(f"\n  名称: {name}")
    console.print(f"  模式: {mode}")
    if infer_mode.value == "external":
        console.print(f"  模型: {model}")
        console.print(f"  API:  {api_base}")

    from oap import Avatar

    async def _create():
        kwargs = dict(
            name=name,
            inference_mode=infer_mode,
            system_prompt=system_prompt,
        )
        if infer_mode.value == "external":
            kwargs.update(api_base=api_base or "https://api.openai.com/v1",
                         api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
                         model=model, api_type=api_type)

        avatar, mnemonic = await Avatar.create(**kwargs)
        return avatar, mnemonic

    with console.status("[bold green]正在创建分身...[/bold green]"):
        avatar, mnemonic = asyncio.run(_create())

    # 保存为激活分身
    _set_active_avatar({
        "name": avatar.config.name,
        "did": avatar.config.avatar_did,
        "dir": str(Path(avatar.config.data_dir).parent),
        "mode": mode,
        "created": time.strftime("%Y-%m-%d %H:%M"),
    })

    console.print(Panel.fit(
        f"[bold green]✓ 分身 '{avatar.config.name}' 创建成功！[/bold green]\n\n"
        f"  DID: {avatar.config.avatar_did}\n"
        f"  模式: {mode}\n\n"
        f"[bold yellow]⚠ 助记词（请安全保存，不要截图）：[/bold yellow]\n"
        f"  {mnemonic}\n\n"
        f"下一步：\n"
        f"  [cyan]oap chat[/cyan]     开始对话\n"
        f"  [cyan]oap status[/cyan]   查看状态",
        border_style="green"
    ))


# ── chat ──────────────────────────────────────────────────

@main.command()
@click.option("--message", "-m", default=None, help="单次对话（非交互模式）")
@click.option("--avatar-dir", "-d", default=None, help="分身目录（默认使用激活分身）")
@click.option("--mnemonic", prompt=True, hide_input=True,
              confirmation_prompt=False, help="助记词")
def chat(message, avatar_dir, mnemonic):
    """与分身对话"""

    # 找到分身目录
    if avatar_dir:
        target_dir = avatar_dir
    else:
        active = _get_active_avatar()
        if active:
            target_dir = active["dir"]
        else:
            console.print("[red]没有激活的分身，请先运行 oap init[/red]")
            return

    from oap import Avatar

    async def _load():
        return await Avatar.load(target_dir, mnemonic.strip())

    with console.status("[bold green]加载分身...[/bold green]"):
        avatar = asyncio.run(_load())

    console.print(Panel.fit(
        f"[bold green]{avatar.config.name}[/bold green] 已上线\n"
        f"  DID: {avatar.config.avatar_did[-20:]}...\n"
        f"  记忆: {len(avatar._memory)} 条 | 模式: {avatar.config.inference_mode}",
        border_style="green"
    ))

    # 单次对话模式
    if message:
        async def _single():
            return await avatar.chat(message)
        reply = asyncio.run(_single())
        console.print(f"\n[bold]{avatar.config.name}:[/bold] {reply}")
        asyncio.run(avatar.save())
        return

    # ── 交互对话模式 ──────────────────────────────────────
    console.print("\n[dim]命令：直接输入消息对话 | :q 退出 | :f 纠正反馈 | :p 正面反馈 | :r 反思 | :s 状态[/dim]")
    console.print("─" * 60)

    while True:
        try:
            user_input = Prompt.ask(f"[bold cyan]你[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # 内置命令
        if user_input == ":q":
            break
        elif user_input == ":s":
            _print_status(avatar)
            continue
        elif user_input == ":r":
            with console.status("[bold]反思中...[/bold]"):
                reflection = asyncio.run(avatar.reflect())
            console.print(f"\n[bold yellow]反思:[/bold yellow] {reflection}\n")
            continue
        elif user_input.startswith(":f "):
            correction = user_input[3:]
            avatar.feedback("correction", correction)
            console.print("[green]✓ 纠正反馈已记录[/green]")
            continue
        elif user_input == ":p":
            avatar.feedback("positive")
            console.print("[green]✓ 正面反馈已记录[/green]")
            continue
        elif user_input.startswith(":"):
            console.print("[dim]未知命令。可用: :q :s :r :f :p[/dim]")
            continue

        # 正常对话
        try:
            with console.status(f"[bold]{avatar.config.name} 思考中...[/bold]"):
                reply = asyncio.run(avatar.chat(user_input))
            console.print(f"\n[bold green]{avatar.config.name}:[/bold green] {reply}\n")
        except RuntimeError as e:
            if "冻结" in str(e):
                console.print(f"[red]⚠ {e}[/red]")
            else:
                console.print(f"[red]推理失败: {e}[/red]")
        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")

    # 退出时保存
    asyncio.run(avatar.save())
    console.print(f"\n[dim]会话结束，状态已保存。[/dim]")


# ── status ────────────────────────────────────────────────

@main.command()
@click.option("--avatar-dir", "-d", default=None, help="分身目录")
def status(avatar_dir):
    """查看分身状态"""

    if avatar_dir:
        target_dir = avatar_dir
    else:
        active = _get_active_avatar()
        if active:
            target_dir = active["dir"]
        else:
            # 列出所有分身
            avatars = _list_avatars()
            if not avatars:
                console.print("[yellow]还没有创建任何分身，运行 oap init 开始[/yellow]")
            else:
                table = Table(title="所有分身")
                table.add_column("名称", style="cyan")
                table.add_column("DID", style="dim")
                table.add_column("目录", style="dim")
                for a in avatars:
                    table.add_row(a.get("name", "?"), a["did_hint"][:30] + "...", a["dir"])
                console.print(table)
            return

    # 需要助记词才能加载，所以只读取公开的配置信息
    config_file = Path(target_dir) / "config.enc.json"
    if not config_file.exists():
        console.print(f"[red]找不到分身配置: {config_file}[/red]")
        return

    active = _get_active_avatar()
    if active:
        console.print(Panel.fit(
            f"[bold]当前激活分身[/bold]\n"
            f"  名称: {active.get('name', '?')}\n"
            f"  DID: {active.get('did', '?')}\n"
            f"  创建: {active.get('created', '?')}\n"
            f"  目录: {active.get('dir', '?')}",
            border_style="cyan"
        ))
    else:
        console.print("[yellow]没有激活的分身[/yellow]")


# ── reflect ───────────────────────────────────────────────

@main.command()
@click.option("--avatar-dir", "-d", default=None, help="分身目录")
@click.option("--mnemonic", prompt=True, hide_input=True, help="助记词")
def reflect(avatar_dir, mnemonic):
    """让分身对近期对话生成反思"""

    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return

    from oap import Avatar

    async def _run():
        avatar = await Avatar.load(target_dir, mnemonic.strip())
        with console.status("[bold]反思中...[/bold]"):
            result = await avatar.reflect()
        await avatar.save()
        return avatar.config.name, result

    name, reflection = asyncio.run(_run())
    console.print(Panel.fit(
        f"[bold]{name} 的反思[/bold]\n\n{reflection}",
        border_style="yellow"
    ))


# ── forget ────────────────────────────────────────────────

@main.command()
@click.option("--avatar-dir", "-d", default=None, help="分身目录")
@click.option("--mnemonic", prompt=True, hide_input=True, help="助记词")
@click.option("--before", default=None, help="遗忘此时间之前的记忆（如 30d, 90d）")
@click.option("--memory-id", default=None, help="遗忘指定 ID 的记忆")
def forget(avatar_dir, mnemonic, before, memory_id):
    """遗忘记忆（不可逆）"""

    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return

    from oap import Avatar

    async def _run():
        avatar = await Avatar.load(target_dir, mnemonic.strip())
        return avatar

    avatar = asyncio.run(_run())

    if not Confirm.ask("[bold red]⚠ 遗忘操作不可逆，确认？[/bold red]", default=False):
        console.print("[dim]已取消[/dim]")
        return

    if memory_id:
        ok = avatar._memory.forget(memory_id)
        if ok:
            console.print(f"[green]✓ 已遗忘: {memory_id[:20]}...[/green]")
        else:
            console.print(f"[red]未找到: {memory_id}[/red]")

    elif before:
        # 解析时间（如 30d → 30天前的时间戳）
        days = int(before.rstrip("d"))
        ts = time.time() - days * 86400
        count = avatar._memory.forget_before(ts)
        console.print(f"[green]✓ 已遗忘 {count} 条 {days} 天前的记忆[/green]")

    else:
        console.print("[yellow]请指定 --before 或 --memory-id[/yellow]")

    asyncio.run(avatar.save())


# ── freeze ────────────────────────────────────────────────

@main.command()
@click.option("--avatar-dir", "-d", default=None, help="分身目录")
@click.option("--mnemonic", prompt=True, hide_input=True, help="助记词")
def freeze(avatar_dir, mnemonic):
    """紧急冻结分身（停止所有交互）"""

    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return

    from oap import Avatar

    async def _run():
        avatar = await Avatar.load(target_dir, mnemonic.strip())
        avatar.emergency_freeze()
        await avatar.save()

    asyncio.run(_run())
    console.print("[bold red]⚠ 分身已紧急冻结[/bold red]")


# ── list ──────────────────────────────────────────────────

@main.command("list")
def list_avatars():
    """列出所有分身"""
    avatars = _list_avatars()
    if not avatars:
        console.print("[yellow]还没有创建任何分身，运行 oap init 开始[/yellow]")
        return

    table = Table(title="数字分身列表")
    table.add_column("#", style="dim", width=4)
    table.add_column("名称", style="cyan")
    table.add_column("DID", style="dim")
    table.add_column("目录", style="dim")

    for i, a in enumerate(avatars, 1):
        table.add_row(str(i), a.get("name", "?"), a["did_hint"][:30] + "...", a["dir"])

    console.print(table)


# ── 内部函数 ──────────────────────────────────────────────

def _print_status(avatar):
    """打印分身状态"""
    s = avatar.status()
    table = Table(title=f"分身状态: {s['name']}", show_header=False)
    table.add_column("项目", style="cyan", width=14)
    table.add_column("值")
    table.add_row("DID", s["did"])
    table.add_row("状态", s["state"])
    table.add_row("创建日期", s["created"])
    table.add_row("推理模式", s["inferenceMode"])
    table.add_row("记忆总数", str(s["memory"]["total"]))
    table.add_row("训练信号", str(s["pendingSignals"]))
    table.add_row("个人化权重", "✓" if s["hasPersonaWeight"] else "✗")
    console.print(table)


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    main()