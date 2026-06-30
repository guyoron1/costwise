"""Translate request/response formats between OpenAI and Anthropic APIs."""

from __future__ import annotations

from enum import Enum


class ApiFormat(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


def detect_format(request_body: dict) -> ApiFormat:
    """Detect whether a request body is in Anthropic or OpenAI format."""
    if "system" in request_body and isinstance(request_body.get("system"), (str, list)):
        return ApiFormat.ANTHROPIC
    if request_body.get("model", "").startswith(("claude", "claude-")):
        return ApiFormat.ANTHROPIC
    if "max_tokens" in request_body and "messages" in request_body:
        if any(
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "text" for b in m["content"])
            for m in request_body.get("messages", [])
            if isinstance(m.get("content"), list)
        ):
            return ApiFormat.ANTHROPIC
    return ApiFormat.OPENAI


def detect_response_format(response_body: dict) -> ApiFormat:
    if response_body.get("type") == "message" or "content" in response_body and isinstance(
        response_body.get("content"), list
    ):
        return ApiFormat.ANTHROPIC
    return ApiFormat.OPENAI


def anthropic_to_openai(request_body: dict) -> dict:
    """Convert an Anthropic Messages API request to OpenAI Chat Completions format."""
    result: dict = {}

    result["model"] = request_body.get("model", "")
    result["max_completion_tokens"] = request_body.get("max_tokens", 4096)

    messages: list[dict] = []

    system = request_body.get("system", "")
    if system:
        system_text = system if isinstance(system, str) else " ".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"
        )
        if system_text:
            messages.append({"role": "system", "content": system_text})

    for msg in request_body.get("messages", []):
        role = msg.get("role", "user")
        if role == "assistant":
            role = "assistant"

        content = msg.get("content", "")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        messages.append({
                            "role": "assistant",
                            "tool_calls": [{
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": _to_json_str(block.get("input", {})),
                                },
                            }],
                        })
                    elif block.get("type") == "tool_result":
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            tool_content = " ".join(
                                b.get("text", "") for b in tool_content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": str(tool_content),
                        })
            if text_parts:
                messages.append({"role": role, "content": "\n".join(text_parts)})
        else:
            messages.append({"role": role, "content": str(content)})

    result["messages"] = messages

    tools = request_body.get("tools", [])
    if tools:
        result["tools"] = [_convert_tool_anthropic_to_openai(t) for t in tools]

    if request_body.get("stream"):
        result["stream"] = True

    if request_body.get("temperature") is not None:
        result["temperature"] = request_body["temperature"]

    if request_body.get("top_p") is not None:
        result["top_p"] = request_body["top_p"]

    return result


def openai_to_anthropic(request_body: dict) -> dict:
    """Convert an OpenAI Chat Completions request to Anthropic Messages API format."""
    result: dict = {}

    result["model"] = request_body.get("model", "")
    result["max_tokens"] = request_body.get(
        "max_completion_tokens", request_body.get("max_tokens", 4096)
    )

    messages: list[dict] = []
    system_text = ""

    for msg in request_body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_text += ("\n" if system_text else "") + str(content)
            continue

        if role == "tool":
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(content),
                }],
            })
            continue

        if role == "assistant" and "tool_calls" in msg:
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "input": _from_json_str(func.get("arguments", "{}")),
                })
            messages.append({"role": "assistant", "content": blocks})
            continue

        messages.append({"role": role, "content": str(content)})

    if system_text:
        result["system"] = system_text
    result["messages"] = messages

    tools = request_body.get("tools", [])
    if tools:
        result["tools"] = [_convert_tool_openai_to_anthropic(t) for t in tools]

    if request_body.get("stream"):
        result["stream"] = True

    if request_body.get("temperature") is not None:
        result["temperature"] = request_body["temperature"]

    if request_body.get("top_p") is not None:
        result["top_p"] = request_body["top_p"]

    return result


def _convert_tool_anthropic_to_openai(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def _convert_tool_openai_to_anthropic(tool: dict) -> dict:
    func = tool.get("function", {})
    return {
        "name": func.get("name", ""),
        "description": func.get("description", ""),
        "input_schema": func.get("parameters", {}),
    }


def _to_json_str(obj: object) -> str:
    import json
    return json.dumps(obj) if not isinstance(obj, str) else obj


def _from_json_str(s: str) -> dict:
    import json
    try:
        return json.loads(s)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        return {}
