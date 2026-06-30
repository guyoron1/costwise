"""TOML config loader with environment variable interpolation."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from costwise.config.schema import CostwiseConfig

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

_SEARCH_PATHS = [
    Path("costwise.toml"),
    Path.home() / ".config" / "costwise" / "costwise.toml",
]


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var_name, default = m.group(1), m.group(2)
            return os.environ.get(var_name, default if default is not None else "")
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def find_config_file(extra_paths: list[Path] | None = None) -> Path | None:
    paths = list(extra_paths or []) + _SEARCH_PATHS
    for p in paths:
        resolved = p.resolve()
        if resolved.is_file():
            return resolved
    return None


def load_config(path: Path | None = None) -> CostwiseConfig:
    if path is None:
        path = find_config_file()

    if path is None:
        return CostwiseConfig()

    raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)

    costwise_data = data.get("costwise", data)
    costwise_data = _interpolate_env(costwise_data)

    return CostwiseConfig.model_validate(costwise_data)
