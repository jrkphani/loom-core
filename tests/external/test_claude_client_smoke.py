"""Real-API smoke test for AnthropicClaudeClient.

Skipped by default; pass --run-external to run. Requires ANTHROPIC_API_KEY in
the environment. Validates the wiring end-to-end against the live Anthropic
API with a tiny canned input.
"""

from __future__ import annotations

import pytest

from loom_core.config import load_settings
from loom_core.llm.claude import AnthropicClaudeClient

pytestmark = pytest.mark.external


async def test_anthropic_client_smoke() -> None:
    """Hit the real Anthropic API with a tiny input.

    Sanity-checks the SDK wiring, auth, and tool-use parsing path. Does not
    assert on specific atoms — the LLM may interpret content differently
    across runs. Asserts only that the call completes and returns a valid
    `AtomExtractionResponse`.
    """
    settings = load_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set in environment")

    client = AnthropicClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.claude.model_extraction,
        max_tokens=settings.claude.extraction_max_tokens,
    )

    response = await client.extract_atoms(
        file_content="I decided to use Python over Go for the backend.",
        file_path_relative="smoke-test.md",
    )

    # Don't assert on specific atom content — the LLM may interpret differently.
    # Just confirm: no exception, response is valid, parsed atoms (if any)
    # have confidences in range.
    assert response.atoms is not None
    if response.atoms:
        assert 0.0 <= response.atoms[0].extraction_confidence <= 1.0
