"""Ponytail integration — detect mode, estimate output token savings.

Ponytail has two layers of state:
  1. Runtime flag: ~/.claude/.ponytail-active (plain text, written by hooks)
  2. Config file: ~/.config/ponytail/config.json (defaultMode field)
We read the runtime flag first (live mode), fall back to config.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "ponytail" / "config.json"
_RUNTIME_FLAG_PATH = Path(
    os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
) / ".ponytail-active"

_VALID_MODES = frozenset({"off", "lite", "full", "ultra"})

_OUTPUT_SAVINGS_BY_MODE: dict[str, float] = {
    "off": 0.0,
    "lite": 0.20,
    "full": 0.40,
    "ultra": 0.54,
}


@dataclass(frozen=True, slots=True)
class PonytailConfig:
    mode: str
    enabled: bool
    config_path: str
    output_savings_ratio: float


def _read_runtime_mode(flag_path: Path = _RUNTIME_FLAG_PATH) -> str | None:
    """Read live mode from the runtime flag file written by Ponytail hooks."""
    try:
        mode = flag_path.read_text(encoding="utf-8").strip().lower()
        return mode if mode in _VALID_MODES else None
    except (FileNotFoundError, OSError):
        return None


def _read_config_mode(config_path: Path) -> str | None:
    """Read default mode from Ponytail config JSON (defaultMode, then mode)."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for key in ("defaultMode", "mode"):
            val = data.get(key)
            if isinstance(val, str) and val.strip().lower() in _VALID_MODES:
                return val.strip().lower()
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


class PonytailReader:
    def __init__(
        self,
        config_path: str | Path = "",
        runtime_flag_path: str | Path = "",
    ) -> None:
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._flag_path = Path(runtime_flag_path) if runtime_flag_path else _RUNTIME_FLAG_PATH

    def get_config(self) -> PonytailConfig:
        mode = self._read_mode()
        enabled = mode is not None and mode != "off"
        effective_mode = mode or "off"
        return PonytailConfig(
            mode=effective_mode,
            enabled=enabled,
            config_path=str(self._config_path),
            output_savings_ratio=_OUTPUT_SAVINGS_BY_MODE.get(effective_mode, 0.0),
        )

    def get_mode(self) -> str | None:
        return self._read_mode()

    def _read_mode(self) -> str | None:
        return (
            _read_runtime_mode(self._flag_path)
            or _read_config_mode(self._config_path)
        )

    @staticmethod
    def estimate_output_savings(mode: str) -> float:
        return _OUTPUT_SAVINGS_BY_MODE.get(mode, 0.0)

    @staticmethod
    def adjust_output_tokens(estimated_output_tokens: int, mode: str) -> int:
        savings = _OUTPUT_SAVINGS_BY_MODE.get(mode, 0.0)
        return int(estimated_output_tokens * (1.0 - savings))


def get_ponytail_mode(config_path: str | Path = "") -> str | None:
    return PonytailReader(config_path).get_mode()
