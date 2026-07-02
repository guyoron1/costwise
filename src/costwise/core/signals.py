"""Extract classification signals from LLM request bodies."""

from __future__ import annotations

import re

from costwise.core.models import SignalBundle

# --- Phase 2: Intent detection patterns (ordered by priority) ---
# Order matters: narrow/specific patterns first, broad ones (generate) last.
_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("explain", re.compile(
        r"\b(explain|what does|how does|what is|describe|walk me through|tell me about"
        r"|why does|what\'s the purpose|understand)\b", re.IGNORECASE)),
    ("refactor", re.compile(
        r"\b(refactor|restructure|reorganize|clean up|simplify|extract|decompose"
        r"|split into|move .+ to|rename)\b", re.IGNORECASE)),
    ("test", re.compile(
        r"\b(write tests?|add tests?|unit tests?|integration tests?"
        r"|test cases?|coverage|spec|assert)\b"
        r"|^tests?\b", re.IGNORECASE)),
    ("review", re.compile(
        r"\b(review|check|audit|look over|feedback|comments on"
        r"|what do you think|is .{0,30}(correct|right|ok|good))\b", re.IGNORECASE)),
    ("debug", re.compile(
        r"\b(debug|investigate|diagnose|figure out why|trace|root cause"
        r"|why is .+ (failing|broken|not working)|step through)\b", re.IGNORECASE)),
    ("fix", re.compile(
        r"\b(fix|resolve|repair|patch|correct|address|handle .+ error"
        r"|solve|work around)\b", re.IGNORECASE)),
    ("generate", re.compile(
        r"\b(write|create|implement|add|generate|build|make|set up|scaffold"
        r"|new file|new function|new class|new component)\b", re.IGNORECASE)),
    ("chat", re.compile(
        r"\b(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|got it)\b", re.IGNORECASE)),
]

# --- Phase 2: Graduated error severity patterns ---
_ERROR_SEVERITY_CRITICAL = re.compile(
    r"\b(crash|segfault|SIGSEGV|panic|OOM|out of memory|kernel panic"
    r"|fatal|CRITICAL|production .+(down|error|failure)|data loss"
    r"|corrupted|unrecoverable)\b", re.IGNORECASE)

_ERROR_SEVERITY_RUNTIME = re.compile(
    r"\b(TypeError|ValueError|KeyError|AttributeError|ImportError"
    r"|NullPointerException|IndexOutOfBoundsException"
    r"|RuntimeError|exception|traceback|stack trace"
    r"|undefined is not|cannot read propert"
    r"|compilation failed|build failed|test failed)\b", re.IGNORECASE)

_ERROR_SEVERITY_WARNING = re.compile(
    r"\b(warning|deprecated|deprecation|lint|linting"
    r"|unused|unreachable|shadowed|type mismatch)\b", re.IGNORECASE)

# --- Phase 2: File path detection ---
_FILE_PATH_RE = re.compile(
    r"(?:^|\s|[\"'`])("
    r"(?:[a-zA-Z]:)?(?:[/\\][\w\-.]+)+(?:\.\w+)"
    r"|[\w\-./]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|cpp|c|h|cs|swift|kt)"
    r")")

_CODE_PATTERN = re.compile(
    r"```[\s\S]*?```"
    r"|def \w+\(|class \w+[:\(]"
    r"|function \w+\(|const \w+ ="
    r"|import \w|from \w+ import"
    r"|#include|package \w+",
)

_ERROR_KEYWORDS = re.compile(
    r"\b(error|traceback|exception|failed|failure|crash|panic|segfault|SIGSEGV"
    r"|TypeError|ValueError|KeyError|AttributeError|ImportError"
    r"|NullPointerException|IndexOutOfBoundsException"
    r"|undefined is not|cannot read propert"
    r"|compilation failed|build failed|test failed)\b",
    re.IGNORECASE,
)

_RETRY_KEYWORDS = re.compile(
    r"\b(retry|again|re-?try|fix this|try again|one more time"
    r"|that didn'?t work|still broken|same error|wrong)\b",
    re.IGNORECASE,
)

def _count_tokens_approx(messages: list[dict]) -> int:
    """Rough token estimate: ~4 chars per token."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", ""))
    return total_chars // 4


def _extract_text(messages: list[dict]) -> str:
    """Concatenate all text content from messages."""
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        parts.append(block["text"])
    return "\n".join(parts)


def _count_images(messages: list[dict]) -> int:
    count = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("image", "image_url"):
                        count += 1
                    if block.get("type") == "image" and "source" in block:
                        count += 1
    return count


def _detect_intent(messages: list[dict]) -> str:
    """Detect task intent from the last user message."""
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            elif isinstance(content, list):
                last_user_msg = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            break

    if not last_user_msg:
        return "unknown"

    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(last_user_msg):
            return intent

    return "unknown"


def _compute_error_severity(text: str) -> float:
    """Compute graduated error severity (0.0–1.0)."""
    if _ERROR_SEVERITY_CRITICAL.search(text):
        return 1.0
    if _ERROR_SEVERITY_RUNTIME.search(text):
        return 0.6
    if _ERROR_SEVERITY_WARNING.search(text):
        return 0.3
    return 0.0


def _detect_file_scope(text: str) -> tuple[bool, int]:
    """Detect multi-file scope from file path references in text."""
    matches = set(_FILE_PATH_RE.findall(text))
    count = len(matches)
    return count > 1, count


def _detect_ponytail() -> str | None:
    """Read Ponytail mode: runtime flag first, then config file."""
    from costwise.integrations.ponytail import PonytailReader
    return PonytailReader().get_mode()


def extract_signals(request_body: dict) -> SignalBundle:
    """Extract classification signals from an LLM API request body.

    Handles both Anthropic and OpenAI request formats.
    """
    messages = request_body.get("messages", [])
    tools = request_body.get("tools", [])
    tool_choice = request_body.get("tool_choice")

    system = request_body.get("system", "")
    system_text = ""
    if isinstance(system, str):
        system_text = system
    elif isinstance(system, list):
        system_text = " ".join(
            b.get("text", "") for b in system if isinstance(b, dict)
        )

    full_text = _extract_text(messages)

    has_tools = bool(tools) or tool_choice is not None
    code_matches = _CODE_PATTERN.findall(full_text)

    intent = _detect_intent(messages)
    error_severity = _compute_error_severity(full_text)
    multi_file_scope, referenced_file_count = _detect_file_scope(full_text)

    return SignalBundle(
        token_count=_count_tokens_approx(messages) + len(system_text) // 4,
        has_tools=has_tools,
        tool_count=len(tools),
        has_code=bool(code_matches),
        code_block_count=len(code_matches),
        conversation_depth=len(messages),
        has_error_context=bool(_ERROR_KEYWORDS.search(full_text)),
        has_retry_context=bool(_RETRY_KEYWORDS.search(full_text)),
        has_system_prompt=bool(system_text),
        system_prompt_length=len(system_text),
        image_count=_count_images(messages),
        ponytail_mode=_detect_ponytail(),
        intent=intent,
        error_severity=error_severity,
        multi_file_scope=multi_file_scope,
        referenced_file_count=referenced_file_count,
    )
