"""
OAP CLI — 数字分身命令行工具

最简用法：
    oap config set api_key sk-...        # 一次设置
    oap init --name 追风                 # 自动检测 API Key → external 模式
    oap chat                             # 直接对话，无需重复输入助记词

手动模式：
    oap init --mode mock --name 追风     # Mock 模式
    oap init --mode external --model deepseek-chat --api-base https://api.deepseek.com/v1
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
from rich.prompt import Prompt, Confirm

console = Console()

# ── 常量 ──────────────────────────────────────────────────

OAP_HOME = Path.home() / ".oap"
AVATARS_DIR = OAP_HOME / "avatars"
ACTIVE_FILE = OAP_HOME / "active.json"
CONFIG_FILE = OAP_HOME / "config.json"
SESSION_FILE = OAP_HOME / "session.json"  # 加密的助记词缓存


# ── 配置管理 ──────────────────────────────────────────────

def _load_config() -> dict:
    """加载全局配置"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_config(cfg: dict):
    """保存全局配置"""
    OAP_HOME.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _detect_api_key() -> tuple[str, str]:
    """自动检测可用的 API Key，返回 (api_key, api_type)"""
    # 优先用 config 里的
    cfg = _load_config()
    if cfg.get("api_key"):
        return cfg["api_key"], cfg.get("api_type", "openai")

    # 再从环境变量
    for env_var, api_type in [
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("DEEPSEEK_API_KEY", "openai"),
    ]:
        val = os.environ.get(env_var, "")
        if val:
            return val, api_type

    return "", ""


def _detect_mode(requested_mode: str) -> str:
    """自动选择推理模式：有 API Key 就用 external，没有就 mock"""
    if requested_mode != "auto":
        return requested_mode
    api_key, _ = _detect_api_key()
    return "external" if api_key else "mock"


def _get_active_avatar() -> dict:
    if not ACTIVE_FILE.exists():
        return None
    with open(ACTIVE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _set_active_avatar(info: dict):
    OAP_HOME.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)


def _save_session(avatar_dir: str, mnemonic: str):
    """保存会话（助记词加密缓存，下次 chat 免输入）"""
    OAP_HOME.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"avatar_dir": avatar_dir, "mnemonic": mnemonic}, f, ensure_ascii=False)


def _load_session() -> dict:
    """加载会话"""
    if SESSION_FILE.exists():
        with open(SESSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _resolve_mode(mode_str: str):
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


# ── config ────────────────────────────────────────────────

@main.group()
def config():
    """管理全局配置"""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value", required=False)
def config_set(key, value):
    """设置配置项

    常用：
        oap config set api_key sk-...           # OpenAI
        oap config set api_key sk-ant-...       # Anthropic
        oap config set api_type anthropic        # 切换到 Claude
        oap config set model gpt-4o-mini         # 默认模型
        oap config set api_base https://api.deepseek.com/v1  # DeepSeek
    """
    # 如果 value 没提供，安全输入（适合 API Key）
    if value is None:
        value = console.input(f"  请输入 {key}: ", password=True)

    cfg = _load_config()

    # api_key 特殊处理：检测类型
    if key == "api_key" and not cfg.get("api_type"):
        if value.startswith("sk-ant-"):
            cfg["api_type"] = "anthropic"
            console.print("  [dim]自动检测: Anthropic Claude[/dim]")
        else:
            cfg["api_type"] = "openai"
            console.print("  [dim]自动检测: OpenAI 兼容[/dim]")

    cfg[key] = value
    _save_config(cfg)
    console.print(f"  [green]✓[/green] {key} 已保存")


@config.command("get")
@click.argument("key", required=False)
def config_get(key):
    """查看配置"""
    cfg = _load_config()
    if not cfg:
        console.print("[dim]暂无配置。运行 oap config set api_key sk-... 开始[/dim]")
        return
    if key:
        val = cfg.get(key, "[未设置]")
        # 隐藏 API Key 中间部分
        if key == "api_key" and isinstance(val, str) and len(val) > 10:
            val = val[:6] + "..." + val[-4:]
        console.print(f"  {key}: {val}")
    else:
        table = Table(title="OAP 配置", show_header=False)
        table.add_column("键", style="cyan", width=14)
        table.add_column("值")
        for k, v in cfg.items():
            display_v = v
            if k == "api_key" and isinstance(v, str) and len(v) > 10:
                display_v = v[:6] + "..." + v[-4:]
            table.add_row(k, display_v)
        console.print(table)


@config.command("reset")
def config_reset():
    """重置所有配置"""
    if Confirm.ask("[bold red]确认重置所有配置？[/bold red]", default=False):
        CONFIG_FILE.unlink(missing_ok=True)
        console.print("[green]✓ 配置已重置[/green]")


# ── init ──────────────────────────────────────────────────

@main.command()
@click.option("--name", "-n", help="分身名称")
@click.option("--mode", "-m", default="auto",
              type=click.Choice(["auto", "mock", "external", "local", "remote"]),
              help="推理模式（auto=自动检测API Key）")
@click.option("--model", default=None, help="模型名称")
@click.option("--api-base", default=None, help="API 端点 URL")
@click.option("--api-type", default=None, type=click.Choice(["openai", "anthropic", "custom"]),
              help="API 类型")
@click.option("--api-key", default=None, help="API Key（优先用 oap config set）")
@click.option("--system-prompt", "-s", default=None, help="系统提示词")
def init(name, mode, model, api_base, api_type, api_key, system_prompt):
    """创建新的数字分身"""
    console.print(Panel.fit(
        "[bold blue]OAP 数字分身初始化[/bold blue]",
        border_style="blue"
    ))

    if not name:
        name = Prompt.ask("  为你的分身取个名字", default="我的分身")

    # 自动模式：检测 API Key
    resolved_mode = _detect_mode(mode)

    if resolved_mode == "external":
        # 优先级：命令行 > config > 环境变量
        cfg = _load_config()
        final_api_key = api_key or cfg.get("api_key", "") or _detect_api_key()[0]
        final_api_type = api_type or cfg.get("api_type", "openai")
        final_api_base = api_base or cfg.get("api_base", "")
        final_model = model or cfg.get("model", "gpt-4o-mini")

        if not final_api_base:
            final_api_base = "https://api.anthropic.com/v1" if final_api_type == "anthropic" else "https://api.openai.com/v1"

        if not final_api_key:
            console.print("[yellow]⚠ 未找到 API Key[/yellow]")
            final_api_key = console.input("  请输入 API Key: ", password=True)
            if not final_api_key:
                console.print("[dim]切换到 Mock 模式[/dim]")
                resolved_mode = "mock"

        console.print(f"\n  名称: {name}")
        console.print(f"  模式: external")
        console.print(f"  模型: {final_model}")
        console.print(f"  API:  {final_api_base}")
    else:
        final_api_key = ""
        final_api_type = "openai"
        final_api_base = ""
        final_model = model or "gpt-4o-mini"
        console.print(f"\n  名称: {name}")
        console.print(f"  模式: mock（模拟回复，设置 API Key 后可用真实模型）")

    from oap import Avatar

    async def _create():
        kwargs = dict(
            name=name,
            inference_mode=_resolve_mode(resolved_mode),
            system_prompt=system_prompt,
        )
        if resolved_mode == "external":
            kwargs.update(
                api_base=final_api_base,
                api_key=final_api_key,
                model=final_model,
                api_type=final_api_type,
            )
        return await Avatar.create(**kwargs)

    with console.status("[bold green]正在创建分身...[/bold green]"):
        avatar, mnemonic = asyncio.run(_create())

    # 保存为激活分身 + 会话
    avatar_dir = str(Path(avatar.config.data_dir).parent)
    _set_active_avatar({
        "name": avatar.config.name,
        "did": avatar.config.avatar_did,
        "dir": avatar_dir,
        "mode": resolved_mode,
    })
    _save_session(avatar_dir, mnemonic)

    console.print(Panel.fit(
        f"[bold green]✓ 分身 '{avatar.config.name}' 创建成功！[/bold green]\n\n"
        f"  DID: {avatar.config.avatar_did}\n"
        f"  模式: {resolved_mode}\n"
        + (f"  模型: {final_model}\n" if resolved_mode == "external" else "")
        + f"\n[bold yellow]⚠ 助记词（请安全保存）：[/bold yellow]\n"
        f"  {mnemonic}\n\n"
        f"下一步: [cyan]oap chat[/cyan]",
        border_style="green"
    ))


# ── chat ──────────────────────────────────────────────────

@main.command()
@click.option("--message", "-m", default=None, help="单次对话")
@click.option("--avatar-dir", "-d", default=None, help="分身目录")
@click.option("--mnemonic", default=None, help="助记词（已缓存则无需输入）")
def chat(message, avatar_dir, mnemonic):
    """与分身对话"""

    # 找分身目录
    if avatar_dir:
        target_dir = avatar_dir
    else:
        active = _get_active_avatar()
        target_dir = active["dir"] if active else None

    if not target_dir:
        console.print("[red]没有激活的分身，请先运行 oap init[/red]")
        return

    # 加载助记词：优先命令行 → 会话缓存 → 手动输入
    if not mnemonic:
        session = _load_session()
        if session.get("avatar_dir") == target_dir:
            mnemonic = session.get("mnemonic", "")

    if not mnemonic:
        console.print("[dim]首次对话需要输入助记词解锁（之后自动缓存）[/dim]")
        mnemonic = console.input("  助记词: ", password=True)

    from oap import Avatar

    with console.status("[bold green]加载分身...[/bold green]"):
        try:
            avatar = asyncio.run(Avatar.load(target_dir, mnemonic.strip()))
        except Exception as e:
            console.print(f"[red]加载失败: {e}[/red]")
            return

    # 缓存助记词到会话
    _save_session(target_dir, mnemonic.strip())

    mode_label = avatar.config.inference_mode
    console.print(Panel.fit(
        f"[bold green]{avatar.config.name}[/bold green] 已上线  "
        f"模式: {mode_label}  记忆: {len(avatar._memory)} 条",
        border_style="green"
    ))

    # 单次对话
    if message:
        reply = asyncio.run(avatar.chat(message))
        console.print(f"\n[bold]{avatar.config.name}:[/bold] {reply}")
        asyncio.run(avatar.save())
        return

    # 交互模式
    console.print("\n[dim]直接输入消息对话 | :q 退出 | :f 纠正 | :p 点赞 | :r 反思 | :s 状态[/dim]")
    console.print("─" * 50)

    while True:
        try:
            user_input = Prompt.ask(f"[bold cyan]你[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input == ":q":
            break
        elif user_input == ":s":
            s = avatar.status()
            console.print(f"  记忆: {s['memory']['total']} 条 | 信号: {s['pendingSignals']} | 模式: {s['inferenceMode']}")
            continue
        elif user_input == ":r":
            with console.status("[bold]反思中...[/bold]"):
                r = asyncio.run(avatar.reflect())
            console.print(f"\n[bold yellow]反思:[/bold yellow] {r}\n")
            continue
        elif user_input.startswith(":f "):
            avatar.feedback("correction", user_input[3:])
            console.print("[green]✓ 纠正已记录[/green]")
            continue
        elif user_input == ":p":
            avatar.feedback("positive")
            console.print("[green]✓ 点赞[/green]")
            continue

        try:
            with console.status(f"[bold]{avatar.config.name} 思考中...[/bold]"):
                reply = asyncio.run(avatar.chat(user_input))
            console.print(f"\n[bold green]{avatar.config.name}:[/bold green] {reply}\n")
        except RuntimeError as e:
            console.print(f"[red]⚠ {e}[/red]")
        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")

    asyncio.run(avatar.save())
    console.print("[dim]会话已保存[/dim]")


# ── status / list / reflect / forget / freeze ─────────────

@main.command()
@click.option("--avatar-dir", "-d", default=None)
def status(avatar_dir):
    """查看分身状态"""
    active = _get_active_avatar()
    if active:
        console.print(Panel.fit(
            f"  名称: {active.get('name', '?')}\n"
            f"  DID: {active.get('did', '?')}\n"
            f"  模式: {active.get('mode', '?')}\n"
            f"  目录: {active.get('dir', '?')}",
            border_style="cyan", title="当前分身"
        ))
    else:
        console.print("[yellow]没有激活的分身[/yellow]")


@main.command("list")
def list_avatars():
    """列出所有分身"""
    if not AVATARS_DIR.exists() or not list(AVATARS_DIR.iterdir()):
        console.print("[yellow]还没有分身，运行 oap init 开始[/yellow]")
        return
    active = _get_active_avatar()
    active_dir = active.get("dir", "") if active else ""
    table = Table(title="分身列表")
    table.add_column("#", width=3)
    table.add_column("名称", style="cyan")
    table.add_column("DID", style="dim")
    table.add_column("模式")
    table.add_column("状态")
    for i, d in enumerate(sorted(AVATARS_DIR.iterdir()), 1):
        cfg_file = d / "config.enc.json"
        if cfg_file.exists():
            with open(cfg_file, encoding="utf-8") as f:
                data = json.load(f)
            is_active = "🔵" if str(d) == active_dir else ""
            table.add_row(str(i), data.get("name", "?"),
                         data.get("_did_hint", "?")[:20] + "...",
                         data.get("inference_mode", "?"), is_active)
    console.print(table)


@main.command()
@click.option("--avatar-dir", "-d", default=None)
@click.option("--mnemonic", default=None)
def reflect(avatar_dir, mnemonic):
    """生成反思"""
    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return
    if not mnemonic:
        session = _load_session()
        mnemonic = session.get("mnemonic", "")
    if not mnemonic:
        mnemonic = console.input("  助记词: ", password=True)

    from oap import Avatar
    avatar = asyncio.run(Avatar.load(target_dir, mnemonic.strip()))
    with console.status("[bold]反思中...[/bold]"):
        result = asyncio.run(avatar.reflect())
    asyncio.run(avatar.save())
    console.print(Panel.fit(f"[bold]反思[/bold]\n\n{result}", border_style="yellow"))


@main.command()
@click.option("--avatar-dir", "-d", default=None)
@click.option("--mnemonic", default=None)
@click.option("--before", default=None, help="遗忘此时间之前（如 30d）")
@click.option("--memory-id", default=None, help="指定记忆 ID")
def forget(avatar_dir, mnemonic, before, memory_id):
    """遗忘记忆"""
    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return
    if not mnemonic:
        session = _load_session()
        mnemonic = session.get("mnemonic", "")
    if not mnemonic:
        mnemonic = console.input("  助记词: ", password=True)

    from oap import Avatar
    avatar = asyncio.run(Avatar.load(target_dir, mnemonic.strip()))

    if not Confirm.ask("[bold red]⚠ 遗忘不可逆，确认？[/bold red]", default=False):
        return

    if memory_id:
        ok = avatar._memory.forget(memory_id)
        console.print(f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'} {memory_id[:20]}...")
    elif before:
        days = int(before.rstrip("d"))
        ts = time.time() - days * 86400
        count = avatar._memory.forget_before(ts)
        console.print(f"[green]✓ 遗忘 {count} 条[/green]")
    else:
        console.print("[yellow]请指定 --before 或 --memory-id[/yellow]")
    asyncio.run(avatar.save())


@main.command()
@click.option("--avatar-dir", "-d", default=None)
@click.option("--mnemonic", default=None)
def freeze(avatar_dir, mnemonic):
    """紧急冻结"""
    target_dir = avatar_dir or (_get_active_avatar() or {}).get("dir")
    if not target_dir:
        console.print("[red]没有激活的分身[/red]")
        return
    if not mnemonic:
        session = _load_session()
        mnemonic = session.get("mnemonic", "")
    if not mnemonic:
        mnemonic = console.input("  助记词: ", password=True)

    from oap import Avatar
    avatar = asyncio.run(Avatar.load(target_dir, mnemonic.strip()))
    avatar.emergency_freeze()
    asyncio.run(avatar.save())
    console.print("[bold red]⚠ 分身已冻结[/bold red]")


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    main()