"""Ponytail integration — detect mode, estimate output token savings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "ponytail" / "config.json"

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


class PonytailReader:
    def __init__(self, config_path: str | Path = "") -> None:
        self._path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH

    def get_config(self) -> PonytailConfig:
        mode = self._read_mode()
        enabled = mode is not None and mode != "off"
        effective_mode = mode or "off"
        return PonytailConfig(
            mode=effective_mode,
            enabled=enabled,
            config_path=str(self._path),
            output_savings_ratio=_OUTPUT_SAVINGS_BY_MODE.get(effective_mode, 0.0),
        )

    def get_mode(self) -> str | None:
        return self._read_mode()

    def _read_mode(self) -> str | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            mode = data.get("mode", "off")
            return mode if mode in _VALID_MODES else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def estimate_output_savings(mode: str) -> float:
        return _OUTPUT_SAVINGS_BY_MODE.get(mode, 0.0)

    @staticmethod
    def adjust_output_tokens(estimated_output_tokens: int, mode: str) -> int:
        savings = _OUTPUT_SAVINGS_BY_MODE.get(mode, 0.0)
        return int(estimated_output_tokens * (1.0 - savings))


def get_ponytail_mode(config_path: str | Path = "") -> str | None:
    return PonytailReader(config_path).get_mode()
