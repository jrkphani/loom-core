"""Claude (Anthropic) LLM client wrapper.

The extractor depends on the `ClaudeClient` Protocol, not the concrete
`anthropic.AsyncAnthropic` SDK. The real implementation `AnthropicClaudeClient`
wraps the SDK and is used in production; tests inject a fake that satisfies
the Protocol. This keeps unit tests fast, deterministic, and decoupled from
SDK retry/auth/streaming layers.

Forced tool use + temperature=0 + Pydantic-validated response is the contract:
the LLM emits a single `tool_use` block whose `input` parses cleanly through
`AtomExtractionResponse`. ValidationError propagates — no silent drops.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Protocol

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field

# Atom kinds — must match the Atom.type CHECK constraint in the schema.
AtomKind = Literal["decision", "commitment", "ask", "risk", "status_update"]


class ExtractedAtom(BaseModel):
    """A single atom emitted by the LLM, before persistence.

    `extraction_confidence` is the LLM's self-reported confidence in the
    extraction; bounded to [0, 1] by Pydantic. Source spans are byte offsets
    into the original file content; required (no None).

    `owner_email` and `due_date` are commitment-only fields. The Pydantic
    schema permits them on any kind because the LLM may emit them with
    spurious values for non-commitments; the extractor ignores them for
    non-commitment kinds at construction time.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AtomKind
    content: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    source_span_start: int = Field(ge=0)
    source_span_end: int = Field(ge=0)
    owner_email: str | None = None
    due_date: date | None = None


class AtomExtractionResponse(BaseModel):
    """Top-level response shape — a list of extracted atoms.

    The Anthropic tool's `input_schema` is generated from this model, and the
    response's `tool_use.input` is validated through it.
    """

    model_config = ConfigDict(extra="forbid")

    atoms: list[ExtractedAtom]


class ClaudeClient(Protocol):
    """The interface the extractor depends on.

    Production: wrapped Anthropic SDK call.
    Tests: in-memory fake returning canned `AtomExtractionResponse`.
    """

    async def extract_atoms(
        self,
        *,
        file_content: str,
        file_path_relative: str,
    ) -> AtomExtractionResponse: ...


class AnthropicClaudeClient:
    """Real implementation of `ClaudeClient`.

    Wraps `anthropic.AsyncAnthropic`. Constructed once per process with the
    API key from config; passed into the extractor via dependency injection.

    Forces tool use on `emit_atoms` to guarantee a structured response. After
    the SDK call, the `tool_use` block's `input` is parsed through
    `AtomExtractionResponse` — any deviation from the schema raises
    `pydantic.ValidationError`.
    """

    _TOOL_NAME = "emit_atoms"
    _SYSTEM_PROMPT = (
        "You are an atom extractor for the Loom personal knowledge fabric. "
        "Extract structured facts (decisions, commitments, asks, risks, "
        "status updates) from the provided file content. Use the emit_atoms "
        "tool to return them. Each atom must include exact byte-offset source "
        "spans into the file content. Set extraction_confidence honestly: "
        "1.0 only when the fact is verbatim and unambiguous; lower values "
        "when interpretation is involved. If no facts are present, emit an "
        "empty atoms list."
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def extract_atoms(
        self,
        *,
        file_content: str,
        file_path_relative: str,
    ) -> AtomExtractionResponse:
        tool_schema: dict[str, Any] = AtomExtractionResponse.model_json_schema()
        user_message = f"File: {file_path_relative}\n\n" f"Content:\n{file_content}"

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=0.0,
            system=self._SYSTEM_PROMPT,
            tools=[
                {
                    "name": self._TOOL_NAME,
                    "description": "Emit extracted atoms as structured data.",
                    "input_schema": tool_schema,
                }
            ],
            tool_choice={"type": "tool", "name": self._TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )

        # Forced tool use guarantees a tool_use block; find and validate it.
        for block in response.content:
            if block.type == "tool_use" and block.name == self._TOOL_NAME:
                return AtomExtractionResponse.model_validate(block.input)

        # Defensive: forced tool use should make this unreachable, but if the
        # SDK shape changes we surface loudly rather than silently emit [].
        raise RuntimeError(
            f"Claude response did not contain a tool_use block for {self._TOOL_NAME!r}"
        )


__all__ = [
    "AnthropicClaudeClient",
    "AtomExtractionResponse",
    "AtomKind",
    "ClaudeClient",
    "ExtractedAtom",
]
