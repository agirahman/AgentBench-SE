"""Config I/O for AgentBench-SE.

Loads/saves ``~/.agentbench/config.yaml`` (or a custom path for testing).
Provides validation, dot-notation get/set, and reset helpers.

Config schema (see PRD Appendix B)::

    researcher:   {name, institution, email}
    provider:     {name, api_key, model}
    experiment:   {temperature, max_retries, rate_limit, usd_idr_rate}
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = "~/.agentbench"


class ConfigError(ValueError):
    """Raised for missing/corrupted/invalid configuration."""


def default_config_dir() -> Path:
    """Return the config directory (override via AGENTBENCH_CONFIG_DIR env)."""
    env_dir = os.environ.get("AGENTBENCH_CONFIG_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path(DEFAULT_CONFIG_DIR).expanduser()


class ConfigManager:
    """Manages the persistent ``~/.agentbench/config.yaml`` file."""

    def __init__(self, config_path: str | Path | None = None):
        base = default_config_dir()
        self.config_dir = base
        self.config_path = Path(config_path or base / "config.yaml").expanduser()

    # ------------------------------------------------------------------ #
    # Path helpers
    # ------------------------------------------------------------------ #
    @property
    def cache_dir(self) -> Path:
        return self.config_dir / "cache"

    @property
    def dataset_cache_dir(self) -> Path:
        return self.cache_dir / "swe-bench-lite"

    def config_exists(self) -> bool:
        return self.config_path.is_file()

    # ------------------------------------------------------------------ #
    # Load / save
    # ------------------------------------------------------------------ #
    def load(self) -> dict:
        if not self.config_exists():
            raise ConfigError(
                "No configuration found. Run 'agentbench setup' first."
            )
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigError(
                "Configuration file is corrupted (invalid YAML). "
                f"Delete {self.config_path} and re-run 'agentbench setup'."
            ) from e
        if not isinstance(data, dict):
            raise ConfigError(
                "Configuration file is malformed (expected a YAML mapping). "
                f"Delete {self.config_path} and re-run 'agentbench setup'."
            )
        return data

    def save(self, config: dict) -> Path:
        validated = self.validate(config)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.safe_dump(validated, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return self.config_path

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self, config: dict, require_api_key: bool = False) -> dict:
        if not isinstance(config, dict):
            raise ConfigError("Config must be a mapping.")

        researcher = config.get("researcher", {})
        provider = config.get("provider", {})
        experiment = config.get("experiment", {})

        if not isinstance(researcher, dict) or not researcher.get("name"):
            raise ConfigError("researcher.name is required")

        if not isinstance(provider, dict) or not provider.get("name"):
            raise ConfigError("provider.name is required (openrouter|gemini|groq|opencode)")
        if provider.get("name") not in ("openrouter", "gemini", "groq", "opencode"):
            raise ConfigError(
                f"provider.name must be one of "
                f"(openrouter|gemini|groq|opencode), got '{provider.get('name')}'"
            )
        if not provider.get("model"):
            raise ConfigError("provider.model is required")
        if require_api_key and not provider.get("api_key"):
            raise ConfigError("provider.api_key is required")

        if not isinstance(experiment, dict):
            experiment = {}
        temp = experiment.get("temperature", 0.2)
        if not (0.0 <= float(temp) <= 1.0):
            raise ConfigError("experiment.temperature must be in 0.0-1.0")

        return {
            "researcher": {
                "name": researcher.get("name", ""),
                "institution": researcher.get("institution", ""),
                "email": researcher.get("email", ""),
            },
            "provider": {
                "name": provider["name"],
                "api_key": provider.get("api_key", ""),
                "model": provider["model"],
            },
            "experiment": {
                "temperature": float(temp),
                "max_retries": int(experiment.get("max_retries", 3)),
                "rate_limit": float(experiment.get("rate_limit", 1.5)),
                "usd_idr_rate": float(experiment.get("usd_idr_rate", 16500.0)),
            },
            "pricing": config.get("pricing", {}) or {},
        }

    # ------------------------------------------------------------------ #
    # Dot-notation get / set
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_key(config: dict, key: str) -> tuple[dict, str]:
        """Return (parent_dict, last_key) for a dotted key, validating path."""
        parts = key.split(".")
        node: Any = config
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"Unknown config path: '{key}'")
            node = node[part]
        if not isinstance(node, dict):
            raise ConfigError(f"Unknown config path: '{key}'")
        return node, parts[-1]

    def set_value(self, key: str, value: str) -> dict:
        config = self.load()
        parent, leaf = self._resolve_key(config, key)
        if leaf not in parent:
            raise ConfigError(f"Unknown config path: '{key}'")
        parent[leaf] = _coerce(value)
        validated = self.validate(config)
        self.save(validated)
        return validated

    def reset(self) -> None:
        if self.config_path.exists():
            self.config_path.unlink()

    # ------------------------------------------------------------------ #
    def as_dict(self) -> dict:
        try:
            return self.load()
        except ConfigError:
            return {}


def _coerce(value: str) -> Any:
    """Best-effort cast of a CLI string to int/float/bool, else str."""
    v = value.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result