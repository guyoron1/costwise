"""Tests for Ponytail integration — mode detection and output savings."""

from __future__ import annotations

import json

import pytest

from costwise.integrations.ponytail import (
    PonytailConfig,
    PonytailReader,
    _read_runtime_mode,
    _read_config_mode,
    get_ponytail_mode,
)


@pytest.fixture()
def ponytail_config(tmp_path):
    return tmp_path / "config.json"


@pytest.fixture()
def runtime_flag(tmp_path):
    return tmp_path / ".ponytail-active"


def _write_config(path, mode: str = "", default_mode: str = ""):
    data = {}
    if mode:
        data["mode"] = mode
    if default_mode:
        data["defaultMode"] = default_mode
    path.write_text(json.dumps(data), encoding="utf-8")


class TestRuntimeFlagReading:
    def test_reads_runtime_flag(self, runtime_flag):
        runtime_flag.write_text("ultra", encoding="utf-8")
        assert _read_runtime_mode(runtime_flag) == "ultra"

    def test_reads_runtime_flag_with_whitespace(self, runtime_flag):
        runtime_flag.write_text("  full\n", encoding="utf-8")
        assert _read_runtime_mode(runtime_flag) == "full"

    def test_missing_flag_returns_none(self, tmp_path):
        assert _read_runtime_mode(tmp_path / "missing") is None

    def test_invalid_mode_in_flag_returns_none(self, runtime_flag):
        runtime_flag.write_text("turbo", encoding="utf-8")
        assert _read_runtime_mode(runtime_flag) is None

    def test_off_mode_in_flag(self, runtime_flag):
        runtime_flag.write_text("off", encoding="utf-8")
        assert _read_runtime_mode(runtime_flag) == "off"


class TestConfigModeReading:
    def test_reads_default_mode(self, ponytail_config):
        _write_config(ponytail_config, default_mode="ultra")
        assert _read_config_mode(ponytail_config) == "ultra"

    def test_reads_mode_fallback(self, ponytail_config):
        _write_config(ponytail_config, mode="lite")
        assert _read_config_mode(ponytail_config) == "lite"

    def test_default_mode_takes_priority(self, ponytail_config):
        _write_config(ponytail_config, mode="lite", default_mode="ultra")
        assert _read_config_mode(ponytail_config) == "ultra"

    def test_missing_config_returns_none(self, tmp_path):
        assert _read_config_mode(tmp_path / "nope.json") is None

    def test_invalid_json_returns_none(self, ponytail_config):
        ponytail_config.write_text("not json", encoding="utf-8")
        assert _read_config_mode(ponytail_config) is None


class TestPonytailReader:
    def test_runtime_flag_takes_priority(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, default_mode="lite")
        runtime_flag.write_text("ultra", encoding="utf-8")
        reader = PonytailReader(ponytail_config, runtime_flag)
        assert reader.get_mode() == "ultra"

    def test_falls_back_to_config(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, default_mode="full")
        reader = PonytailReader(ponytail_config, runtime_flag)
        assert reader.get_mode() == "full"

    def test_reads_config_mode_field(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="ultra")
        reader = PonytailReader(ponytail_config, runtime_flag)
        cfg = reader.get_config()
        assert cfg.mode == "ultra"
        assert cfg.enabled is True
        assert cfg.output_savings_ratio == 0.54

    def test_reads_lite_mode(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="lite")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.mode == "lite"
        assert cfg.enabled is True
        assert cfg.output_savings_ratio == 0.20

    def test_reads_full_mode(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="full")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.mode == "full"
        assert cfg.output_savings_ratio == 0.40

    def test_off_mode_is_disabled(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="off")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False
        assert cfg.output_savings_ratio == 0.0

    def test_missing_everything_returns_off(self, tmp_path):
        reader = PonytailReader(
            tmp_path / "nonexistent.json",
            tmp_path / "nonexistent-flag",
        )
        cfg = reader.get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_invalid_json_returns_off(self, ponytail_config, runtime_flag):
        ponytail_config.write_text("not json", encoding="utf-8")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_unknown_mode_returns_off(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="turbo")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_config_path_stored(self, ponytail_config, runtime_flag):
        _write_config(ponytail_config, mode="full")
        cfg = PonytailReader(ponytail_config, runtime_flag).get_config()
        assert cfg.config_path == str(ponytail_config)


class TestOutputSavings:
    def test_estimate_known_modes(self):
        assert PonytailReader.estimate_output_savings("off") == 0.0
        assert PonytailReader.estimate_output_savings("lite") == 0.20
        assert PonytailReader.estimate_output_savings("full") == 0.40
        assert PonytailReader.estimate_output_savings("ultra") == 0.54

    def test_estimate_unknown_mode(self):
        assert PonytailReader.estimate_output_savings("imaginary") == 0.0

    def test_adjust_output_tokens_ultra(self):
        adjusted = PonytailReader.adjust_output_tokens(1000, "ultra")
        assert 459 <= adjusted <= 460

    def test_adjust_output_tokens_off(self):
        adjusted = PonytailReader.adjust_output_tokens(1000, "off")
        assert adjusted == 1000

    def test_adjust_output_tokens_lite(self):
        adjusted = PonytailReader.adjust_output_tokens(1000, "lite")
        assert adjusted == 800


class TestStandaloneFunction:
    def test_get_ponytail_mode_found(self, ponytail_config):
        _write_config(ponytail_config, mode="ultra")
        assert get_ponytail_mode(ponytail_config) == "ultra"

    def test_get_ponytail_mode_missing(self, tmp_path):
        assert get_ponytail_mode(tmp_path / "nope.json") is None
