"""Tests for costwise.graph.pruner — context pruning via graph relevance."""

from costwise.graph.loader import CodeGraph
from costwise.graph.pruner import prune_context


def _make_messages(file_contents: dict[str, str], question: str) -> list[dict]:
    """Build a realistic conversation with file context + question."""
    msgs: list[dict] = [
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "assistant", "content": "I found the relevant files."},
    ]
    for filename, content in file_contents.items():
        msgs.append({
            "role": "user",
            "content": f"Here is {filename}:\n{content}",
        })
    msgs.append({"role": "user", "content": question})
    return msgs


class TestPruneContext:
    def test_pruning_drops_irrelevant_files(self, httpx_graph: CodeGraph) -> None:
        messages = _make_messages(
            {
                "auth.py": "class FunctionAuth:\n    def auth_flow(self): pass",
                "transport.py": "class BaseTransport:\n    def handle_request(self): pass",
                "utils.py": "def get_environment_proxies(): pass",
                "exceptions.py": "class HTTPError(Exception): pass",
            },
            "Fix the FunctionAuth.auth_flow method in auth.py",
        )
        pruned, result = prune_context(messages, httpx_graph, threshold=0.3)
        assert result.dropped_entries > 0
        assert result.pruned_messages < result.original_messages

    def test_system_prompt_never_pruned(self, httpx_graph: CodeGraph) -> None:
        messages = _make_messages(
            {"transport.py": "class BaseTransport: pass"},
            "Fix auth.py",
        )
        pruned, _ = prune_context(messages, httpx_graph, threshold=0.9)
        system_msgs = [m for m in pruned if m["role"] == "system"]
        assert len(system_msgs) == 1

    def test_last_message_never_pruned(self, httpx_graph: CodeGraph) -> None:
        messages = _make_messages(
            {"transport.py": "class BaseTransport: pass"},
            "Fix auth.py",
        )
        pruned, _ = prune_context(messages, httpx_graph, threshold=0.9)
        assert pruned[-1]["content"] == "Fix auth.py"

    def test_no_pruning_without_graph_refs(self, httpx_graph: CodeGraph) -> None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        pruned, result = prune_context(messages, httpx_graph)
        assert result.dropped_entries == 0
        assert len(pruned) == len(messages)

    def test_tokens_saved_is_positive(self, httpx_graph: CodeGraph) -> None:
        messages = _make_messages(
            {
                "auth.py": "class FunctionAuth: pass" * 50,
                "transport.py": "class BaseTransport: pass" * 50,
                "utils.py": "def helper(): pass" * 50,
            },
            "Fix FunctionAuth in auth.py",
        )
        _, result = prune_context(messages, httpx_graph, threshold=0.3)
        if result.dropped_entries > 0:
            assert result.tokens_saved > 0
            assert result.reduction_pct > 0

    def test_high_threshold_prunes_more(self, httpx_graph: CodeGraph) -> None:
        messages = _make_messages(
            {
                "auth.py": "class FunctionAuth: pass",
                "client.py": "class Client: pass",
                "transport.py": "class BaseTransport: pass",
                "utils.py": "def helper(): pass",
                "models.py": "class Request: pass",
            },
            "Fix auth.py",
        )
        _, result_low = prune_context(messages, httpx_graph, threshold=0.1)
        _, result_high = prune_context(messages, httpx_graph, threshold=0.5)
        assert result_high.dropped_entries >= result_low.dropped_entries
