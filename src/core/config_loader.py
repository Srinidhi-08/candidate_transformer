"""
src/core/config_loader.py
=========================
Loads pipeline_config.yaml and exposes it as a typed, dot-accessible
Config object.  Every module imports `get_config()` — the file is
parsed only once (module-level singleton).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.core.exceptions import ConfigurationError

logger = logging.getLogger("core.config_loader")

# Default config path — override with env var PIPELINE_CONFIG_PATH
_DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "pipeline_config.yaml"


class Config:
    """
    Thin wrapper around the parsed YAML dict.
    Supports nested dot-access: cfg.extraction.nlp_model
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getattr__(self, key: str) -> Any:
        try:
            val = self._data[key]
        except KeyError:
            raise AttributeError(f"Config key not found: '{key}'")
        return Config(val) if isinstance(val, dict) else val

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        val = self._data.get(key, default)
        return Config(val) if isinstance(val, dict) else val

    def raw(self) -> dict:
        """Return the underlying dict (useful for iteration)."""
        return self._data

    def __repr__(self) -> str:
        return f"Config({list(self._data.keys())})"


@lru_cache(maxsize=1)
def get_config(config_path: str | None = None) -> Config:
    """
    Load and cache the YAML config.  Call with no args everywhere;
    only pass `config_path` in tests to use a fixture config.
    """
    path = Path(config_path) if config_path else Path(
        os.environ.get("PIPELINE_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH))
    )

    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Failed to parse config YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError("Config file must contain a YAML mapping at the top level.")

    logger.info("Config loaded from: %s", path)
    return Config(data)
