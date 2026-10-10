"""The status checks of `write_config_job` and `write_config_sub_change` admit the states the code uses.

A staged CM job (`waveSize`, MGT-5, since 0.2.0) goes to `HALTED` between its waves, and an automatically reverted one marks its applied
sub-changes `REVERTED` (MGT-5.5). The CHECK constraints written with the baseline allowed neither, so on Postgres a staged job failed with a 500
the moment it paused after its first wave (`new row for relation "write_config_job" violates check constraint`). The unit tests run on SQLite,
which does not carry these constraints, and the runbook uses single-wave jobs, so nothing before the HA failover test (PR-HA-4.2) put a staged job
through Postgres.

The constraints are replaced by ones that list every state of `JobState` and the sub-change states of `ran-nf-oam/app/main.py`;
`tests_integration/test_status_checks.py` keeps them in step. No data changes. The previous release's code runs on the new schema (the new checks
allow more).

Revision ID: 0026
Revises: 0025
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

JOB = "ran_nf_oam.write_config_job"
SUB = "ran_nf_oam.write_config_sub_change"


def upgrade() -> None:
    """Replace the status checks of `write_config_job` and `write_config_sub_change` with ones that also allow HALTED and REVERTED, the states a staged job uses."""
    op.execute(f"ALTER TABLE {JOB} DROP CONSTRAINT write_config_job_status_check")
    op.execute(f"ALTER TABLE {JOB} ADD CONSTRAINT write_config_job_status_check "
               "CHECK (status IN ('PENDING', 'PROCESSING', 'HALTED', 'COMPLETED', 'PARTIAL_SUCCESS', 'FAILED'))")
    op.execute(f"ALTER TABLE {SUB} DROP CONSTRAINT write_config_sub_change_status_check")
    op.execute(f"ALTER TABLE {SUB} ADD CONSTRAINT write_config_sub_change_status_check "
               "CHECK (status IN ('PENDING', 'APPLIED', 'REJECTED', 'REVERTED'))")


def downgrade() -> None:
    """Move rows in the new states to the nearest old state, then put the old, narrower checks back."""
    # rows already in the new states would stop the old checks from being added: they are moved to the nearest old state first
    op.execute(f"UPDATE {JOB} SET status = 'PROCESSING' WHERE status = 'HALTED'")
    op.execute(f"UPDATE {SUB} SET status = 'APPLIED' WHERE status = 'REVERTED'")
    op.execute(f"ALTER TABLE {JOB} DROP CONSTRAINT write_config_job_status_check")
    op.execute(f"ALTER TABLE {JOB} ADD CONSTRAINT write_config_job_status_check "
               "CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'PARTIAL_SUCCESS', 'FAILED'))")
    op.execute(f"ALTER TABLE {SUB} DROP CONSTRAINT write_config_sub_change_status_check")
    op.execute(f"ALTER TABLE {SUB} ADD CONSTRAINT write_config_sub_change_status_check "
               "CHECK (status IN ('PENDING', 'APPLIED', 'REJECTED'))")
