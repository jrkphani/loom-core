"""T1 seam test: arena + engagement through API, then list_engagements via MCP tool.

This test proves the vertical slice cuts through every layer end-to-end.
The MCP tool's LoomCoreClient is redirected to the in-process ASGITransport
so no real port is needed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport
from httpx import AsyncClient as HttpxClient

from loom_core.main import app as loom_core_app


@pytest.mark.integration
async def test_t1_create_arena_create_engagement_list_via_mcp_tool(
    client: HttpxClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create arena + engagement via API, then verify list_engagements tool returns it."""
    # --- loom-core side: create the entities via the real API --------------------
    arena_resp = await client.post("/v1/arenas", json={"domain": "work", "name": "Panasonic"})
    assert arena_resp.status_code == 201
    arena_id = arena_resp.json()["id"]

    eng_resp = await client.post(
        "/v1/engagements",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "name": "Wave 2",
            "type_tag": "delivery_wave",
        },
    )
    assert eng_resp.status_code == 201

    # --- loom-mcp side: patch LoomCoreClient to hit the in-process ASGI app ------
    # The client fixture has already set app.dependency_overrides[get_session]
    # so the in-process app uses the same test DB that received the writes above.
    import loom_mcp.client as mcp_client_module

    async def _patched_aenter(
        self: mcp_client_module.LoomCoreClient,
    ) -> mcp_client_module.LoomCoreClient:
        transport = ASGITransport(app=loom_core_app)
        self._client = HttpxClient(transport=transport, base_url="http://testserver")
        return self

    async def _patched_aexit(self: mcp_client_module.LoomCoreClient, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    monkeypatch.setattr(mcp_client_module.LoomCoreClient, "__aenter__", _patched_aenter)
    monkeypatch.setattr(mcp_client_module.LoomCoreClient, "__aexit__", _patched_aexit)

    # --- exercise the tool -------------------------------------------------------
    from loom_mcp.tools.engagements import list_engagements

    result = await list_engagements(domain="work")

    assert "Wave 2" in result
