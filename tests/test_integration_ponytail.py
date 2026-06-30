"""Tests for Ponytail integration — mode detection and output savings."""

from __future__ import annotations

import json

import pytest

from costwise.integrations.ponytail import PonytailConfig, PonytailReader, get_ponytail_mode


@pytest.fixture()
def ponytail_config(tmp_path):
    config_path = tmp_path / "config.json"
    return config_path


def _write_config(path, mode: str):
    path.write_text(json.dumps({"mode": mode}), encoding="utf-8")


class TestPonytailReader:
    def test_reads_ultra_mode(self, ponytail_config):
        _write_config(ponytail_config, "ultra")
        reader = PonytailReader(ponytail_config)
        cfg = reader.get_config()
        assert cfg.mode == "ultra"
        assert cfg.enabled is True
        assert cfg.output_savings_ratio == 0.54

    def test_reads_lite_mode(self, ponytail_config):
        _write_config(ponytail_config, "lite")
        cfg = PonytailReader(ponytail_config).get_config()
        assert cfg.mode == "lite"
        assert cfg.enabled is True
        assert cfg.output_savings_ratio == 0.20

    def test_reads_full_mode(self, ponytail_config):
        _write_config(ponytail_config, "full")
        cfg = PonytailReader(ponytail_config).get_config()
        assert cfg.mode == "full"
        assert cfg.output_savings_ratio == 0.40

    def test_off_mode_is_disabled(self, ponytail_config):
        _write_config(ponytail_config, "off")
        cfg = PonytailReader(ponytail_config).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False
        assert cfg.output_savings_ratio == 0.0

    def test_missing_config_returns_off(self, tmp_path):
        reader = PonytailReader(tmp_path / "nonexistent.json")
        cfg = reader.get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_invalid_json_returns_off(self, ponytail_config):
        ponytail_config.write_text("not json", encoding="utf-8")
        cfg = PonytailReader(ponytail_config).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_unknown_mode_returns_off(self, ponytail_config):
        _write_config(ponytail_config, "turbo")
        cfg = PonytailReader(ponytail_config).get_config()
        assert cfg.mode == "off"
        assert cfg.enabled is False

    def test_config_path_stored(self, ponytail_config):
        _write_config(ponytail_config, "full")
        cfg = PonytailReader(ponytail_config).get_config()
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
        _write_config(ponytail_config, "ultra")
        assert get_ponytail_mode(ponytail_config) == "ultra"

    def test_get_ponytail_mode_missing(self, tmp_path):
        assert get_ponytail_mode(tmp_path / "nope.json") is None
