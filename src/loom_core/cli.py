"""`loom` CLI — small operator tooling.

v1 commands:
    loom doctor     — diagnostics across daemons, database, vault, queues.

System design reference: `../loom-meta/docs/loom-system-design-v1.md` § 11.3.
"""

from __future__ import annotations

import sys

import typer

from loom_core import __version__

app = typer.Typer(
    name="loom",
    help="Loom Core operator CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def doctor() -> None:
    """Run diagnostics: daemon health, DB, vault, cron pipelines, triage queue.

    Output is plain text, intended for human reading. Exit code is 0 if every
    check passes, 1 if any check fails.
    """
    # TODO(W13): wire up real diagnostics.
    typer.echo("loom doctor — not yet implemented (W13).")
    typer.echo("Will check: Loom Core /health, Apple AI /health, MCP server, ")
    typer.echo("            DB size + last VACUUM, vault disk free, cron pipelines, ")
    typer.echo("            triage queue depth, migration review queue depth.")
    sys.exit(0)


@app.command()
def version() -> None:
    """Print the Loom Core version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
