"""Staged rollout of a CM job (PR-MGT-5.1, 5.2): wave settings on `write_config_job`, the order and wave of each `write_config_sub_change`.

Additive: nullable columns and columns with defaults, so the previous release (which neither reads nor writes them) is unaffected and every
existing row is a job of one wave (`wave_count` 1, `current_wave` 0, `position` 0 which only matters for jobs created from now on).
`ran-nf-oam/app/models.py` is the model.

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JOB_COLUMNS = [
    "wave_size INTEGER",
    "wave_pause_seconds INTEGER NOT NULL DEFAULT 0",
    "wave_count INTEGER NOT NULL DEFAULT 1",
    "current_wave INTEGER NOT NULL DEFAULT 0",
    "gate_max_new_alarms INTEGER NOT NULL DEFAULT 0",
    "on_gate_failure VARCHAR NOT NULL DEFAULT 'halt'",
    "next_wave_at TIMESTAMP WITH TIME ZONE",
    "halted_reason VARCHAR",
    "halted_detail VARCHAR",
]


def upgrade() -> None:
    """Add the wave settings and state to `write_config_job` (`JOB_COLUMNS`) and `position` and `wave` to `write_config_sub_change`; existing rows become one-wave jobs."""
    for column in JOB_COLUMNS:
        op.execute(f"ALTER TABLE write_config_job ADD COLUMN {column}")
    op.execute("ALTER TABLE write_config_sub_change ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE write_config_sub_change ADD COLUMN wave INTEGER NOT NULL DEFAULT 1")


def downgrade() -> None:
    """Drop the columns added by `upgrade`, in reverse order."""
    op.execute("ALTER TABLE write_config_sub_change DROP COLUMN wave")
    op.execute("ALTER TABLE write_config_sub_change DROP COLUMN position")
    for column in reversed(JOB_COLUMNS):
        op.execute(f"ALTER TABLE write_config_job DROP COLUMN {column.split()[0]}")
