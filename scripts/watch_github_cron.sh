#!/usr/bin/env bash
# cron wrapper 示例：封装 `ek watch-github` 的工作目录与日志输出
# 处理 cd / 输出重定向（script handler 不经 shell，这些都不能放 command）
set -euo pipefail

# cron 环境 PATH 不含 ~/.local/bin，uv 找不到会直接失败
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

EK_REPO="${EK_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${LOG_DIR:-$HOME/.ego-knowledge/logs/watch}"

mkdir -p "$LOG_DIR"
cd "$EK_REPO"

# 用 uv run 而非 .venv/bin/ek，与项目其他 cron 调用风格统一；
# uv 自动从 pyproject.toml 解析环境，绕过 venv path 漂移
exec uv run ek watch-github >> "$LOG_DIR/cron.log" 2>> "$LOG_DIR/cron.err.log"
