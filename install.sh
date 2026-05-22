#!/usr/bin/env bash
#
# OAP SDK 一键安装脚本
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/oap/oap-sdk/main/install.sh | bash
#   或本地运行：
#   bash install.sh
#
# 功能：
#   1. 检测 Python 3.10+ 是否可用
#   2. 安装 pipx（如果没有）
#   3. 用 pipx 安装 oap-sdk（隔离环境，不污染系统 Python）
#   4. 验证 oap 命令可用
#
set -euo pipefail

# ── 颜色 ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "  ${CYAN}▸${RESET} $*"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${RESET} $*"; }
error() { echo -e "  ${RED}✗${RESET} $*"; exit 1; }

# ── 横幅 ──────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  OAP SDK — 数字分身命令行工具 安装器${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo ""

# ── Step 1: 检测 Python ──────────────────────────────────

info "检测 Python 环境..."

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)

        if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; }; then
            PYTHON="$cmd"
            ok "找到 Python $version ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "需要 Python 3.10+，请先安装：https://www.python.org/downloads/"
fi

# ── Step 2: 检测/安装 pipx ───────────────────────────────

info "检测 pipx..."

if ! command -v pipx &>/dev/null; then
    warn "pipx 未安装，正在安装..."

    # 优先用 brew（macOS）
    if command -v brew &>/dev/null; then
        brew install pipx
        pipx ensurepath
    else
        $PYTHON -m pip install --user pipx
        $PYTHON -m pipx ensurepath
    fi

    # 刷新 PATH
    export PATH="$HOME/.local/bin:$PATH"

    if command -v pipx &>/dev/null; then
        ok "pipx 安装成功"
    else
        error "pipx 安装失败，请手动安装: https://pipx.pypa.io"
    fi
else
    ok "pipx 已就绪"
fi

# ── Step 3: 安装 oap-sdk ─────────────────────────────────

info "安装 oap-sdk..."

# 判断是本地安装还是远程安装
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SDK="$SCRIPT_DIR"

if [ -f "$LOCAL_SDK/pyproject.toml" ] && grep -q "oap-sdk" "$LOCAL_SDK/pyproject.toml" 2>/dev/null; then
    # 本地源码安装
    info "从本地源码安装: $LOCAL_SDK"
    pipx install --force "$LOCAL_SDK"
else
    # 从 PyPI 安装（当包发布后）
    pipx install oap-sdk --force 2>/dev/null || {
        # PyPI 还没有，尝试从 GitHub 安装
        warn "PyPI 上暂无 oap-sdk，尝试从 GitHub 安装..."
        pipx install --force "git+https://github.com/fujiezee/oap-sdk.git" 2>/dev/null || {
            error "安装失败。请手动安装：\n  pipx install git+https://github.com/fujiezee/oap-sdk.git"
        }
    }
fi

# ── Step 4: 验证 ─────────────────────────────────────────

info "验证安装..."

if command -v oap &>/dev/null; then
    VERSION=$(oap --version 2>/dev/null || echo "unknown")
    ok "oap $VERSION 安装成功！"
else
    # pipx 安装后可能需要刷新 PATH
    export PATH="$HOME/.local/bin:$PATH"
    if command -v oap &>/dev/null; then
        VERSION=$(oap --version 2>/dev/null || echo "unknown")
        ok "oap $VERSION 安装成功！"
    else
        error "oap 命令不可用，请检查 pipx ensurepath 是否执行成功"
    fi
fi

# ── 完成 ──────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  安装完成！${RESET}"
echo ""
echo -e "  快速开始："
echo ""
echo -e "  ${CYAN}oap init --name 你的名字${RESET}              # 创建分身（Mock 模式）"
echo -e "  ${CYAN}oap chat${RESET}                              # 开始对话"
echo ""
echo -e "  接入外部推理："
echo ""
echo -e "  ${DIM}export OPENAI_API_KEY=\"sk-...\"${RESET}"
echo -e "  ${CYAN}oap init --name 你的名字 --mode external${RESET}  # 接入 GPT"
echo -e "  ${CYAN}oap chat${RESET}                              # 和真实 LLM 对话"
echo ""
echo -e "  接入本地 Ollama（完全离线）："
echo ""
echo -e "  ${DIM}ollama serve${RESET}"
echo -e "  ${CYAN}oap init --mode external --api-base http://localhost:11434/v1 --model llama3${RESET}"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo ""