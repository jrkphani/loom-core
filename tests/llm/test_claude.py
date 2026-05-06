"""Tests for the Claude/Anthropic SDK wrapper and shared LLM types.

Pydantic-validation tests + AnthropicClaudeClient unit tests using a mocked
SDK. No real API calls — those live in tests/external/test_claude_client_smoke.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from loom_core.llm.claude import AtomExtractionResponse


def test_pydantic_validation_raises_on_invalid_kind() -> None:
    """An LLM response with a kind not in the Literal enum raises ValidationError.

    Locks the contract: invalid kind from LLM → ValidationError, not silent
    drop. The real client's `AtomExtractionResponse.model_validate(...)` is
    the same path; this test exercises it at the boundary.
    """
    malformed = {
        "atoms": [
            {
                "kind": "garbage_kind",
                "content": "Some content",
                "extraction_confidence": 0.5,
                "source_span_start": 0,
                "source_span_end": 12,
            }
        ]
    }

    with pytest.raises(ValidationError):
        AtomExtractionResponse.model_validate(malformed)


async def test_anthropic_client_real_impl_constructs_correct_message() -> None:
    """`AnthropicClaudeClient.extract_atoms` calls `messages.create` with the
    expected model, temperature, max_tokens, tool definition, and forced
    tool_choice. Confirms the SDK call shape without hitting the real API.

    The mock SDK returns a synthetic response containing one tool_use block;
    the wrapper extracts and validates it through `AtomExtractionResponse`.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from loom_core.llm.claude import AnthropicClaudeClient, AtomExtractionResponse

    fake_response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="emit_atoms",
                input={"atoms": []},
            )
        ]
    )

    with patch("loom_core.llm.claude.AsyncAnthropic") as mock_anthropic_cls:
        mock_instance = mock_anthropic_cls.return_value
        mock_instance.messages = SimpleNamespace(create=AsyncMock(return_value=fake_response))

        client = AnthropicClaudeClient(
            api_key="test-key",
            model="claude-sonnet-4-6",
            max_tokens=4096,
        )

        result = await client.extract_atoms(
            file_content="Some content",
            file_path_relative="inbox/work/notes/foo.md",
        )

    # Returned a valid AtomExtractionResponse.
    assert isinstance(result, AtomExtractionResponse)
    assert result.atoms == []

    # SDK constructed with the API key.
    mock_anthropic_cls.assert_called_once_with(api_key="test-key")

    # messages.create called with the locked shape.
    create_call = mock_instance.messages.create
    assert create_call.await_count == 1
    kwargs = create_call.await_args.kwargs

    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 4096
    assert kwargs["temperature"] == 0.0
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_atoms"}

    tools = kwargs["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "emit_atoms"
    assert tools[0]["input_schema"] == AtomExtractionResponse.model_json_schema()

    # User message contains both the file path and the content.
    messages = kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "inbox/work/notes/foo.md" in messages[0]["content"]
    assert "Some content" in messages[0]["content"]
