"""Health endpoint contract tests.

These run green from day 1 — they're the smoke test that the FastAPI app
boots, the router wires up, and the Pydantic response model validates.
"""

from __future__ import annotations

from httpx import AsyncClient

from loom_core import __version__


async def test_health_returns_ok(client: AsyncClient) -> None:
    """GET /v1/health returns 200 with status=ok."""
    response = await client.get("/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__
    assert isinstance(data["uptime_seconds"], int | float)
    assert data["uptime_seconds"] >= 0
    # db_size_bytes is None until W1 wires the database.
    assert data["db_size_bytes"] is None


async def test_health_response_shape(client: AsyncClient) -> None:
    """Response includes exactly the documented fields, no extras."""
    response = await client.get("/v1/health")
    data = response.json()

    expected_keys = {"status", "version", "uptime_seconds", "db_size_bytes"}
    assert set(data.keys()) == expected_keys
