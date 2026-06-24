"""Credential management for EgoKnowledge external integrations.

Manages ``~/.config/ego-knowledge/secrets.toml`` for GitHub tokens and
future SiliconFlow API keys (Phase 7).

File permissions: 0600 (owner read/write only).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .errors import ValidationError

SECRETS_PATH = Path.home() / ".config" / "ego-knowledge" / "secrets.toml"


def load_secrets() -> dict[str, object]:
    """Read secrets.toml; returns empty dict if file does not exist."""
    if not SECRETS_PATH.exists():
        return {}
    try:
        with SECRETS_PATH.open("rb") as f:
            return tomllib.load(f)
    except Exception as exc:
        raise ValidationError(f"secrets.toml 格式异常: {exc}") from exc


def get_github_token() -> str | None:
    """Return the GitHub personal access token, or None if not configured."""
    secrets = load_secrets()
    github_section = secrets.get("github")
    if not isinstance(github_section, dict):
        return None
    token = github_section.get("token")
    return token if isinstance(token, str) else None


def get_siliconflow_api_key() -> str | None:
    """Return the SiliconFlow API key, or None if not configured."""
    secrets = load_secrets()
    siliconflow_section = secrets.get("siliconflow")
    if isinstance(siliconflow_section, dict):
        api_key = siliconflow_section.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            return api_key
    env_api_key = os.environ.get("SILICONFLOW_API_KEY")
    return env_api_key if isinstance(env_api_key, str) and env_api_key.strip() else None


def init_secrets_file() -> None:
    """Bootstrap secrets.toml with commented template; skip if exists."""
    if SECRETS_PATH.exists():
        return
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(
        "# EgoKnowledge 凭证文件（不进仓库）\n"
        "# [github]\n"
        '# token = "<your-github-token>"\n'
        "#\n"
        "# [siliconflow]\n"
        '# api_key = "<your-siliconflow-api-key>"\n',
        encoding="utf-8",
    )
    SECRETS_PATH.chmod(0o600)
