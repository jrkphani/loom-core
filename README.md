# loom-core

The Python daemon at the centre of Loom — sole writer to SQLite and the Obsidian vault. FastAPI + SQLAlchemy 2.0 async + Alembic. Runs as a launchd-managed service on `127.0.0.1:9100`.

See [`../loom-meta/docs/loom-system-design-v1.md`](../loom-meta/docs/loom-system-design-v1.md) for the architecture.

## Setup

```bash
# Install uv if you haven't:
# curl -LsSf https://astral.sh/uv/install.sh | sh

cd /Users/jrkphani/Projects/loom/loom-core
uv sync                            # creates .venv with pinned deps
uv run alembic upgrade head        # initialise the database (once migrations exist)
```

## Run (development)

```bash
uv run uvicorn loom_core.main:app --reload --host 127.0.0.1 --port 9100
```

Then in another shell:

```bash
curl http://127.0.0.1:9100/v1/health
# {"status":"ok","version":"1.0.0", ...}
```

## Verification gates

A task is not complete until **all six** pass with zero errors:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy --strict
uv run pytest
uv run pytest -m visibility    # explicit visibility regression run (#079)
uv run alembic check           # ORM models match migration head
```

The `pytest -m visibility` run is redundant with the broader `pytest` run (which includes the marked tests by default), but invoking it explicitly catches a regression where a new visibility test is added without the marker — the marker-only run would surface a missing test or a wrong count.

## Layout

```
loom-core/
├── pyproject.toml                  # deps + ruff/mypy/pytest config
├── alembic.ini                     # Alembic config
├── alembic/                        # schema migrations (initially empty)
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── launchd/
│   └── com.loom.core.plist         # macOS service definition
├── src/loom_core/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app entry
│   ├── config.py                   # TOML config loader
│   ├── cli.py                      # `loom doctor` CLI
│   ├── api/                        # HTTP route handlers (thin)
│   ├── services/                   # Business logic
│   ├── pipelines/                  # Cron processors
│   ├── llm/                        # Claude + Apple AI clients
│   ├── storage/                    # SQLAlchemy models + session
│   └── vault/                      # Obsidian filesystem layer + Jinja2 templates
└── tests/
    ├── conftest.py
    └── test_health.py              # green from day 1
```

## Architecture invariants

- **Single uvicorn worker.** Loom Core is the sole writer to SQLite. One worker keeps WAL mode and write semantics simple.
- **Localhost-bound.** Never binds to `0.0.0.0`.
- **Sole writer to the vault.** No other process writes to `~/Documents/Loom/outbox/`.
- **Events are immutable.** No PATCH or DELETE on events.

## launchd

```bash
# Install the agent (one-time)
cp launchd/com.loom.core.plist ~/Library/LaunchAgents/
# Edit the plist to replace USERNAME and set ANTHROPIC_API_KEY
launchctl load ~/Library/LaunchAgents/com.loom.core.plist
launchctl list | grep loom
```

## Issues

Issues live in [`../loom-meta/issues/`](../loom-meta/issues/). The PRD is at [`../loom-meta/issues/prd.md`](../loom-meta/issues/prd.md).
