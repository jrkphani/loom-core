"""Configuration loader for Loom Core.

Loads from a TOML file at `~/Library/Application Support/Loom/config.toml` by
default. The path is overridable via the `LOOM_CONFIG_PATH` env var.

The Anthropic API key is NOT in config — it loads from the env var
`ANTHROPIC_API_KEY` (set by the launchd plist or the user's shell).

System design reference: `../loom-meta/docs/loom-system-design-v1.md` § 8.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "Loom" / "config.toml"


class CoreSettings(BaseModel):
    """`[core]` section of config.toml."""

    http_host: str = "127.0.0.1"
    http_port: int = 9100
    db_path: Path = Field(
        default_factory=lambda: Path.home() / "Library/Application Support/Loom/db/loom.sqlite"
    )
    vault_path: Path = Field(default_factory=lambda: Path.home() / "Documents/Loom")
    log_level: str = "info"


class CronSettings(BaseModel):
    """`[core.cron]` section."""

    inbox_sweep_minutes: int = 5
    state_inference_time: str = "06:30"
    brief_engagement_time: str = "07:00"
    brief_arena_day: str = "sunday"
    brief_arena_time: str = "06:00"
    kg_reconcile_time: str = "02:00"


class AppleAISettings(BaseModel):
    """`[apple_ai]` section."""

    http_host: str = "127.0.0.1"
    http_port: int = 9101
    enabled: bool = True
    fallback_to_claude_on_error: bool = True


class ClaudeSettings(BaseModel):
    """`[claude]` section. API key from env, not config."""

    model_default: str = "claude-opus-4-7"
    model_extraction: str = "claude-sonnet-4-6"
    extraction_max_tokens: int = 4096
    max_retries: int = 3
    timeout_seconds: int = 60


class Settings(BaseModel):
    """Top-level config."""

    core: CoreSettings = Field(default_factory=CoreSettings)
    cron: CronSettings = Field(default_factory=CronSettings)
    apple_ai: AppleAISettings = Field(default_factory=AppleAISettings)
    claude: ClaudeSettings = Field(default_factory=ClaudeSettings)
    anthropic_api_key: str | None = None


def load_settings(path: Path | None = None) -> Settings:
    """Load Loom Core settings from TOML.

    Args:
        path: Override the config path. Defaults to `LOOM_CONFIG_PATH` env var,
            then to `DEFAULT_CONFIG_PATH`.

    Returns:
        Validated `Settings` object. If the file is missing, returns defaults
        (useful for tests).
    """
    config_path = path or Path(os.environ.get("LOOM_CONFIG_PATH", DEFAULT_CONFIG_PATH))

    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)

    cron = raw.get("core", {}).pop("cron", {}) if "core" in raw else {}
    settings = Settings(
        core=CoreSettings(**raw.get("core", {})),
        cron=CronSettings(**cron),
        apple_ai=AppleAISettings(**raw.get("apple_ai", {})),
        claude=ClaudeSettings(**raw.get("claude", {})),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    return settings
