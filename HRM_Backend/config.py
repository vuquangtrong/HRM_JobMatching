"""
Configuration loader for HRM_Backend.

Single source of truth for the service port, the LLM model, and the FastEmbed
semantic model (and their related settings). Driven from a JSON config file
so model names are never hardcoded in Python or in the setup/run shell scripts.

Precedence (lowest to highest):
1. Built-in defaults defined in this module
2. `config.json`     - committed template with recommended defaults
3. `config.local.json` - optional machine-specific overrides (gitignored)
4. Environment variables (LLM_*, SEMANTIC_MODEL, PORT, ...)

The same file is used by:
- `get_config.py`   - CLI helper so `setup_backend.sh` / `run.sh` can read values
- Python backend    - `extracting_engine.py`, `app.py` load models at startup
"""

import os
import json
import copy
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("hrm.config")

_HERE = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH_ENV = "HRM_CONFIG_PATH"
CONFIG_FILES = ["config.json", "config.local.json"]

# Last-resort defaults evaluated only when no config file exists.
# They mirror `config.json`, which is the editable source of truth for the
# model names and settings used by the setup scripts and the runtime.
_DEFAULT_CONFIG = {
    "server": {
        "port": 8765,
    },
    "llm": {
        "enabled": True,
        "base_url": "http://localhost:11434",
        "model": "qwen2.5:3b",
        "timeout_seconds": 30,
        "cache_size": 4096,
    },
    "fastembed": {
        "model": "BAAI/bge-large-en-v1.5",
        "dim": 1024,
        "cache_dir": os.path.join(_HERE, "fastembed_cache"),
        "prewarm": False,
    },
    "taxonomy": {
        "path": os.path.join(_HERE, "data", "taxonomy.json"),
    },
}

# Environment variables override every file-based value.
_ENV_OVERRIDES = {
    "PORT": ("server", "port"),
    "LLM_ENABLED": ("llm", "enabled"),
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_MODEL": ("llm", "model"),
    "LLM_TIMEOUT": ("llm", "timeout_seconds"),
    "LLM_CACHE_SIZE": ("llm", "cache_size"),
    "SEMANTIC_MODEL": ("fastembed", "model"),
    "SEMANTIC_MODEL_DIM": ("fastembed", "dim"),
    "FASTEMBED_CACHE_DIR": ("fastembed", "cache_dir"),
    "HRM_TAXONOMY_PATH": ("taxonomy", "path"),
}

# Keys holding filesystem paths; relative values are resolved against the
# config file directory so scripts work from any working directory.
_PATH_KEYS = {
    ("fastembed", "cache_dir"),
    ("taxonomy", "path"),
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    """Recursively merges override dict into base dict, in place."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _coerce(value: str, current: Any) -> Any:
    """Converts a raw environment string to the type of the current value."""
    try:
        if isinstance(current, bool):
            return str(value).strip().lower() not in ("0", "false", "no", "off", "")
        if isinstance(current, int):
            return int(float(str(value)))
        if isinstance(current, float):
            return float(value)
    except (TypeError, ValueError):
        logger.warning("Environment value '%s' could not be parsed for config type %s.",
                       value, type(current))
        return current
    return value


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    for env_name, keys in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        node = cfg
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = _coerce(value, node.get(keys[-1]))


def _resolve_paths(cfg: Dict[str, Any], base_dir: str) -> None:
    for keys in _PATH_KEYS:
        node = cfg
        for key in keys[:-1]:
            node = node.get(key) or {}
        value = node.get(keys[-1])
        if isinstance(value, str) and not os.path.isabs(value):
            node[keys[-1]] = os.path.normpath(os.path.join(base_dir, value))


_config_cache: Optional[Dict[str, Any]] = None
_config_lock = threading.Lock()


def get_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Loads and caches the effective configuration dict."""
    global _config_cache
    if config_path is None:
        if _config_cache is not None:
            return _config_cache
        with _config_lock:
            if _config_cache is not None:
                return _config_cache

    cfg = copy.deepcopy(_DEFAULT_CONFIG)

    if config_path is not None:
        files = [config_path]
        base_dir = os.path.dirname(os.path.abspath(config_path))
    else:
        configured = os.environ.get(CONFIG_PATH_ENV)
        files = [configured] if configured else CONFIG_FILES
        base_dir = _HERE

    for rel in files:
        path = rel if os.path.isabs(rel) else os.path.join(base_dir, rel)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            if isinstance(file_cfg, dict):
                _deep_merge(cfg, file_cfg)
            logger.info("Loaded config from %s", path)
        except Exception as e:
            logger.warning("Skipping config file %s: %s", path, e)

    _resolve_paths(cfg, base_dir)
    _apply_env_overrides(cfg)

    if config_path is None:
        _config_cache = cfg
    return cfg


def reset_config() -> None:
    """Clears the cached config (mainly useful for tests)."""
    global _config_cache
    with _config_lock:
        _config_cache = None