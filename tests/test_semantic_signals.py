"""Tests for Phase 2: Semantic Signal Enrichment."""

from __future__ import annotations

import pytest

from costwise.core.classifier import ClassifierConfig, classify
from costwise.core.models import SignalBundle, Tier
from costwise.core.signals import (
    _compute_error_severity,
    _detect_file_scope,
    _detect_intent,
    extract_signals,
)

# ---------------------------------------------------------------------------
# Intent Detection
# ---------------------------------------------------------------------------

class TestDetectIntent:

    @pytest.mark.parametrize("prompt, expected", [
        ("explain how the auth middleware works", "explain"),
        ("what does this function do?", "explain"),
        ("walk me through the deployment process", "explain"),
        ("describe the architecture", "explain"),
        ("why does the cache invalidate here?", "explain"),

        ("refactor the database layer into separate modules", "refactor"),
        ("clean up this messy function", "refactor"),
        ("restructure the API routes", "refactor"),
        ("rename the variable to something clearer", "refactor"),
        ("split into smaller functions", "refactor"),

        ("write a function that validates emails", "generate"),
        ("create a new endpoint for user profiles", "generate"),
        ("implement the retry logic", "generate"),
        ("add a caching layer", "generate"),
        ("scaffold a new React component", "generate"),

        ("fix the login bug", "fix"),
        ("resolve the race condition in the queue", "fix"),
        ("patch the SQL injection vulnerability", "fix"),
        ("correct the off-by-one error", "fix"),

        ("debug the memory leak", "debug"),
        ("investigate why the tests are flaky", "debug"),
        ("diagnose the performance regression", "debug"),
        ("figure out why the API is timing out", "debug"),
        ("trace the root cause of the 500 errors", "debug"),

        ("write tests for the auth module", "test"),
        ("add unit tests for the parser", "test"),
        ("increase test coverage for utils", "test"),

        ("review my pull request", "review"),
        ("check this code for issues", "review"),
        ("is this implementation correct?", "review"),
        ("what do you think about this approach?", "review"),

        ("hi there", "chat"),
        ("thanks!", "chat"),
        ("ok got it", "chat"),

        # TODO(guy): Add 2-3 more edge-case prompts per intent category
        # that you think a real user would send. Focus on ambiguous ones.
    ])
    def test_intent_detection_accuracy(self, prompt, expected):
        messages = [{"role": "user", "content": prompt}]
        assert _detect_intent(messages) == expected

    def test_unknown_intent_for_ambiguous(self):
        messages = [{"role": "user", "content": "the sky is blue today"}]
        assert _detect_intent(messages) == "unknown"

    def test_empty_messages(self):
        assert _detect_intent([]) == "unknown"

    def test_no_user_message(self):
        messages = [{"role": "assistant", "content": "Here is the code"}]
        assert _detect_intent(messages) == "unknown"

    def test_uses_last_user_message_only(self):
        messages = [
            {"role": "user", "content": "explain the architecture"},
            {"role": "assistant", "content": "The architecture uses..."},
            {"role": "user", "content": "now refactor it"},
        ]
        assert _detect_intent(messages) == "refactor"

    def test_multipart_content(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "explain this code"},
            {"type": "image", "source": {"data": "base64..."}},
        ]}]
        assert _detect_intent(messages) == "explain"

    def test_intent_priority_explain_over_generate(self):
        messages = [{"role": "user", "content": "explain how to write a function"}]
        assert _detect_intent(messages) == "explain"


# ---------------------------------------------------------------------------
# Error Severity
# ---------------------------------------------------------------------------

class TestComputeErrorSeverity:

    @pytest.mark.parametrize("text, expected", [
        ("the code looks fine", 0.0),
        ("everything is working", 0.0),

        ("there's a deprecation warning here", 0.3),
        ("lint errors in the codebase", 0.3),
        ("unused import detected", 0.3),
        ("type mismatch on line 42", 0.3),

        ("getting TypeError: cannot read property", 0.6),
        ("I see a traceback in the logs", 0.6),
        ("compilation failed on CI", 0.6),
        ("ValueError: invalid literal for int()", 0.6),
        ("test failed with AttributeError", 0.6),

        ("the server crashed with OOM", 1.0),
        ("production is down with a fatal error", 1.0),
        ("kernel panic after deployment", 1.0),
        ("data loss in the user table", 1.0),
        ("segfault in the worker process", 1.0),
    ])
    def test_severity_levels(self, text, expected):
        assert _compute_error_severity(text) == expected

    def test_critical_takes_precedence_over_runtime(self):
        text = "TypeError occurred and then the server crashed with OOM"
        assert _compute_error_severity(text) == 1.0

    def test_runtime_takes_precedence_over_warning(self):
        text = "deprecated function raised a ValueError"
        assert _compute_error_severity(text) == 0.6


# ---------------------------------------------------------------------------
# File Scope Detection
# ---------------------------------------------------------------------------

class TestDetectFileScope:

    def test_multi_file(self):
        text = "update src/auth.py and src/middleware.py"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is True
        assert count == 2

    def test_single_file(self):
        text = "fix the bug in main.py"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is False
        assert count == 1

    def test_no_files(self):
        text = "just a regular question about coding"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is False
        assert count == 0

    def test_many_files(self):
        text = "modify src/auth.py, src/routes.py, src/models.py, and tests/test_auth.py"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is True
        assert count >= 4

    def test_various_extensions(self):
        text = "check app.ts and server.go and utils.rs"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is True
        assert count == 3

    def test_absolute_paths(self):
        text = "edit /usr/local/src/main.py and /etc/config.py"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is True
        assert count == 2

    def test_deduplication(self):
        text = "update main.py then check main.py again"
        is_multi, count = _detect_file_scope(text)
        assert is_multi is False
        assert count == 1


# ---------------------------------------------------------------------------
# Integration: extract_signals populates new fields
# ---------------------------------------------------------------------------

class TestExtractSignalsSemanticFields:

    def test_intent_populated(self):
        body = {"messages": [{"role": "user", "content": "explain the database schema"}]}
        signals = extract_signals(body)
        assert signals.intent == "explain"

    def test_error_severity_populated(self):
        body = {"messages": [{"role": "user", "content": "the server crashed with a segfault"}]}
        signals = extract_signals(body)
        assert signals.error_severity == 1.0

    def test_multi_file_scope_populated(self):
        body = {"messages": [{"role": "user", "content": "update auth.py and routes.py"}]}
        signals = extract_signals(body)
        assert signals.multi_file_scope is True
        assert signals.referenced_file_count == 2

    def test_defaults_for_simple_request(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        signals = extract_signals(body)
        assert signals.intent == "chat"
        assert signals.error_severity == 0.0
        assert signals.multi_file_scope is False
        assert signals.referenced_file_count == 0

    def test_backward_compat_error_context_still_set(self):
        body = {"messages": [{"role": "user", "content": "got a TypeError in the module"}]}
        signals = extract_signals(body)
        assert signals.has_error_context is True
        assert signals.error_severity == 0.6


# ---------------------------------------------------------------------------
# Classifier integration with new signals
# ---------------------------------------------------------------------------

class TestClassifierWithSemanticSignals:

    def test_chat_intent_biases_simple(self):
        signals = SignalBundle(intent="chat", token_count=100)
        result = classify(signals)
        assert result.tier == Tier.SIMPLE
        assert "intent" in result.breakdown

    def test_refactor_intent_biases_higher(self):
        signals = SignalBundle(
            intent="refactor", has_code=True, code_block_count=2,
            token_count=2000, has_tools=True, tool_count=1,
        )
        result = classify(signals)
        assert result.score > 0.20
        assert result.breakdown["intent"] > 0

    def test_graduated_error_severity_lower_than_binary(self):
        warning_signals = SignalBundle(error_severity=0.3, has_error_context=True)
        critical_signals = SignalBundle(error_severity=1.0, has_error_context=True)

        warning_result = classify(warning_signals)
        critical_result = classify(critical_signals)

        assert critical_result.breakdown["error"] > warning_result.breakdown["error"]

    def test_multi_file_scope_adds_complexity(self):
        single = SignalBundle(has_code=True, multi_file_scope=False)
        multi = SignalBundle(
            has_code=True, multi_file_scope=True, referenced_file_count=3,
        )

        single_result = classify(single)
        multi_result = classify(multi)

        assert multi_result.score > single_result.score
        assert multi_result.breakdown["multi_file"] > single_result.breakdown["multi_file"]

    def test_multi_file_without_code_no_bonus(self):
        signals = SignalBundle(
            multi_file_scope=True, referenced_file_count=3, has_code=False,
        )
        result = classify(signals)
        assert result.breakdown["multi_file"] == 0.0

    def test_weight_sum_approximately_one(self):
        cfg = ClassifierConfig()
        total = (
            cfg.w_tools + cfg.w_token_count + cfg.w_code + cfg.w_depth
            + cfg.w_error + cfg.w_retry + cfg.w_images
            + cfg.w_code_tools_compound + cfg.w_graph_complexity
            + cfg.w_intent + cfg.w_multi_file
        )
        assert total == pytest.approx(1.0, abs=0.02)

    def test_existing_simple_classification_unchanged(self):
        signals = SignalBundle(token_count=100, conversation_depth=1)
        result = classify(signals)
        assert result.tier == Tier.SIMPLE

    def test_existing_complex_classification_unchanged(self):
        signals = SignalBundle(
            token_count=8000, has_tools=True, tool_count=5,
            has_code=True, code_block_count=4,
            has_error_context=True, error_severity=1.0,
            has_retry_context=True,
            conversation_depth=15,
            graph_complexity=0.8,
            intent="debug",
        )
        result = classify(signals)
        assert result.tier == Tier.COMPLEX
