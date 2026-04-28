"""inbox_sweep pipeline job — scans inbox dirs and routes files via the sniffer."""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from loom_core.pipelines.sniffer import process_file
from loom_core.services.processor_runs import finish_processor_run, start_processor_run

_logger = structlog.get_logger(__name__)

_INBOX_SUBDIRS = ("transcripts", "dictation", "emails", "notes")


async def inbox_sweep_job(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    vault_path: Path,
) -> None:
    """Scan all inbox subdirs and route each file through the sniffer.

    Per-file session isolation: a fresh session is opened for each file.
    Failure in one file is caught, logged at WARNING, and the sweep continues.
    A processor_runs row is written before and after the sweep.
    """
    async with session_factory() as session:
        run = await start_processor_run(session, pipeline="inbox_sweep")
        run_id = run.id
        await session.commit()

    items_processed = 0
    items_failed = 0

    inbox_root = vault_path / "inbox" / "work"
    for subdir in _INBOX_SUBDIRS:
        dir_path = inbox_root / subdir
        if not dir_path.is_dir():
            continue
        for entry in sorted(dir_path.iterdir()):
            if not entry.is_file():
                continue
            try:
                async with session_factory() as session:
                    outcome = await process_file(session, entry, vault_path=vault_path)
                    await session.commit()
                if outcome.outcome != "skipped_duplicate":
                    items_processed += 1
            except Exception as exc:
                _logger.warning("inbox_sweep_file_failed", path=str(entry), error=str(exc))
                items_failed += 1

    async with session_factory() as session:
        await finish_processor_run(
            session,
            run_id,
            items_processed=items_processed,
            items_failed=items_failed,
        )
        await session.commit()
