"""Unit tests for _secrets.py credential management."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from ego_knowledge._secrets import (
    get_github_token,
    get_siliconflow_api_key,
    init_secrets_file,
    load_secrets,
)
from ego_knowledge.errors import ValidationError


class TestLoadSecrets:
    def test_load_secrets_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_path = tmp_path / "nonexistent" / "secrets.toml"
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        assert load_secrets() == {}

    def test_load_secrets_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text('[github]\ntoken = "example-github-token"\n', encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        result = load_secrets()
        assert result == {"github": {"token": "example-github-token"}}

    def test_load_secrets_invalid_toml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text("not valid toml {{{", encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        with pytest.raises(ValidationError, match="格式异常"):
            load_secrets()


class TestGetGitHubToken:
    def test_get_github_token_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text('[github]\ntoken = "example-github-token"\n', encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        assert get_github_token() == "example-github-token"

    def test_get_github_token_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text('[other]\nkey = "value"\n', encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        assert get_github_token() is None


class TestGetSiliconFlowApiKey:
    def test_get_siliconflow_api_key_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text(
            '[siliconflow]\napi_key = "example-siliconflow-key"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        assert get_siliconflow_api_key() == "example-siliconflow-key"

    def test_get_siliconflow_api_key_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text("[github]\ntoken = \"example-github-token\"\n", encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        monkeypatch.setenv("SILICONFLOW_API_KEY", "example-env-siliconflow-key")

        assert get_siliconflow_api_key() == "example-env-siliconflow-key"

    def test_get_siliconflow_api_key_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        fake_path.write_text('[github]\ntoken = "example-github-token"\n', encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        assert get_siliconflow_api_key() is None


class TestInitSecretsFile:
    def test_init_secrets_file_creates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "config" / "ego-knowledge" / "secrets.toml"
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        init_secrets_file()
        assert fake_path.exists()
        content = fake_path.read_text(encoding="utf-8")
        assert "[github]" in content
        assert "[siliconflow]" in content
        # Check permissions (0600)
        mode = stat.S_IMODE(fake_path.stat().st_mode)
        assert mode == 0o600

    def test_init_secrets_file_skip_existing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_path = tmp_path / "secrets.toml"
        original_content = "[github]\ntoken = 'existing'\n"
        fake_path.write_text(original_content, encoding="utf-8")
        monkeypatch.setattr("ego_knowledge._secrets.SECRETS_PATH", fake_path)
        init_secrets_file()
        assert fake_path.read_text(encoding="utf-8") == original_content
