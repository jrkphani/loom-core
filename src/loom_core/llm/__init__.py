"""LLM client wrappers — Claude (Anthropic SDK) and Apple AI sidecar (HTTP)."""

from loom_core.llm.claude import (
    AnthropicClaudeClient,
    AtomExtractionResponse,
    AtomKind,
    ClaudeClient,
    ExtractedAtom,
)

__all__ = [
    "AnthropicClaudeClient",
    "AtomExtractionResponse",
    "AtomKind",
    "ClaudeClient",
    "ExtractedAtom",
]
