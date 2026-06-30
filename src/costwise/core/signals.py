"""Extract classification signals from LLM request bodies."""

from __future__ import annotations

import json
import re
from pathlib import Path

from costwise.core.models import SignalBundle

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

_PONYTAIL_CONFIG = Path.home() / ".config" / "ponytail" / "config.json"


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


def _detect_ponytail() -> str | None:
    """Read Ponytail mode from its config file. Returns None if not installed."""
    try:
        data = json.loads(_PONYTAIL_CONFIG.read_text())
        mode = data.get("mode", "off")
        return mode if mode in ("lite", "full", "ultra", "off") else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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
    )
