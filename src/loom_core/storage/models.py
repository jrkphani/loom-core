"""SQLAlchemy DeclarativeBase and ORM models.

Models will land workstream-by-workstream:
  W1 — universal core (domains, arenas, engagements, hypotheses)
  W2 — spine state (hypothesis_state_changes, state_change_evidence, stakeholders)
  W3 — events, atoms, atom_*_details
  W4 — atom_attachments, triage_items
  W6 — entity_pages, tags, entity_tags
  W9 — migration_records
  W2 — work_account_metadata, work_engagement_metadata, work_stakeholder_roles,
       work_commitment_direction, work_ask_side
  W7 — brief_runs
  W13 — processor_runs

The schema is locked in `../../../loom-meta/docs/loom-schema-v1.sql`. Models
must mirror it exactly; deviations require an RFC.
"""

from __future__ import annotations

from loom_core.storage.session import Base

# Models are added here as workstreams land. Keeping this import surface
# intentionally minimal for the W1 scaffold.

__all__ = ["Base"]
