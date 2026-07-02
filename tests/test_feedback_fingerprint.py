"""Tests for feedback fingerprint: content hashing and similarity."""

from __future__ import annotations

from costwise.feedback.fingerprint import _extract_user_text, _normalize, fingerprint, similarity


def _msgs(text: str, role: str = "user") -> list[dict]:
    return [{"role": role, "content": text}]


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("Fix The Bug") == "fix the bug"

    def test_collapse_whitespace(self) -> None:
        assert _normalize("fix   the\n\tbug") == "fix the bug"

    def test_strip_punctuation(self) -> None:
        assert _normalize("fix the bug!") == "fix the bug"

    def test_empty(self) -> None:
        assert _normalize("") == ""


class TestExtractUserText:
    def test_string_content(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        assert _extract_user_text(msgs) == "hello"

    def test_block_content(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello world"}]}]
        assert _extract_user_text(msgs) == "hello world"

    def test_last_user_message(self) -> None:
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "second"},
        ]
        assert _extract_user_text(msgs) == "second"

    def test_no_user_messages(self) -> None:
        msgs = [{"role": "assistant", "content": "hello"}]
        assert _extract_user_text(msgs) == ""

    def test_empty_messages(self) -> None:
        assert _extract_user_text([]) == ""


class TestFingerprint:
    def test_identical_messages_same_hash(self) -> None:
        a = _msgs("fix the authentication bug")
        b = _msgs("fix the authentication bug")
        assert fingerprint(a) == fingerprint(b)

    def test_different_messages_different_hash(self) -> None:
        a = _msgs("fix the authentication bug")
        b = _msgs("add a new feature for login")
        assert fingerprint(a) != fingerprint(b)

    def test_whitespace_insensitive(self) -> None:
        a = _msgs("fix   the   bug")
        b = _msgs("fix the bug")
        assert fingerprint(a) == fingerprint(b)

    def test_case_insensitive(self) -> None:
        a = _msgs("Fix The Bug")
        b = _msgs("fix the bug")
        assert fingerprint(a) == fingerprint(b)

    def test_punctuation_insensitive(self) -> None:
        a = _msgs("Fix the bug!")
        b = _msgs("Fix the bug")
        assert fingerprint(a) == fingerprint(b)

    def test_ignores_assistant_messages(self) -> None:
        a = [
            {"role": "assistant", "content": "some output"},
            {"role": "user", "content": "fix it"},
        ]
        b = [
            {"role": "assistant", "content": "different output"},
            {"role": "user", "content": "fix it"},
        ]
        assert fingerprint(a) == fingerprint(b)

    def test_empty_messages(self) -> None:
        fp = fingerprint([])
        assert isinstance(fp, str)
        assert len(fp) == 64

    def test_deterministic(self) -> None:
        msgs = _msgs("hello world")
        assert fingerprint(msgs) == fingerprint(msgs)


class TestSimilarity:
    def test_identical_returns_1(self) -> None:
        a = _msgs("fix the authentication bug")
        assert similarity(a, a) == 1.0

    def test_unrelated_returns_low(self) -> None:
        a = _msgs("fix the authentication bug in the login flow")
        b = _msgs("deploy the kubernetes cluster to production")
        assert similarity(a, b) < 0.3

    def test_rephrased_returns_high(self) -> None:
        a = _msgs("fix this authentication error in the login")
        b = _msgs("please fix the authentication error in login")
        assert similarity(a, b) > 0.6

    def test_empty_both_returns_1(self) -> None:
        assert similarity([], []) == 1.0

    def test_empty_one_returns_0(self) -> None:
        a = _msgs("hello world")
        assert similarity(a, []) == 0.0

    def test_exact_match_is_1(self) -> None:
        a = _msgs("fix the bug")
        b = _msgs("fix the bug")
        assert similarity(a, b) == 1.0
