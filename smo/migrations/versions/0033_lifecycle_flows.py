"""Zero-touch onboarding and software campaigns (PR-MGT-14.1, MGT-14.5, MGT-15.1).

RAN NF OAM gets three tables (all in its schema `ran_nf_oam`, `ran-nf-oam/app/models.py` is the model):

  onboarding_template   the initial configuration of an element type, with the software version it is expected to run (MGT-14.1, 14.4)
  element_onboarding    where one newly registered element is in its onboarding: DISCOVERED, NO_TEMPLATE, TEMPLATE_SELECTED, APPLYING, ONBOARDED, FAILED (MGT-14.5)
  software_campaign     a software change over many elements, run in waves with a health gate between them, with its rollback and its report (MGT-15)

and four nullable columns on `ran_nf_oam.software_management_job` (`campaign_id`, `campaign_wave`, `rollback_of`, `software_version`): the campaign a job belongs to,
its wave, the job it undoes, and the version it was started for.

Expand only: new tables the previous release never reads or writes, and nullable columns it ignores. No row exists until an operator defines a template or starts a
campaign, so an upgrade changes nothing for a running system: a registration with no template matches nothing and creates no onboarding row, and a software job
started the old way carries NULL in all four columns. The tables of a module with a database role need no grant of their own: the role's default privileges on its
schema cover them (`scripts/db_roles.py`).

Revision ID: 0033
Revises: 0032
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `onboarding_template`, `element_onboarding` and `software_campaign` with their checks and indexes, and add `campaign_id`, `campaign_wave`, `rollback_of` and `software_version` to `software_management_job`; every statement tolerates a rerun."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.onboarding_template (
            name              VARCHAR PRIMARY KEY,
            description       VARCHAR,
            entity_type       VARCHAR NOT NULL,
            vendor_name       VARCHAR,
            software_baseline VARCHAR,
            require_baseline  BOOLEAN NOT NULL DEFAULT false,
            auto_apply        BOOLEAN NOT NULL DEFAULT false,
            enabled           BOOLEAN NOT NULL DEFAULT true,
            changes           JSON NOT NULL,
            created_at        TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at        TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_onboarding_template_entity_type ON ran_nf_oam.onboarding_template (entity_type)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.element_onboarding (
            managed_element_ref VARCHAR PRIMARY KEY REFERENCES ran_nf_oam.managed_entity (managed_element_ref),
            row_version         INTEGER NOT NULL DEFAULT 1,
            status              VARCHAR NOT NULL DEFAULT 'DISCOVERED',
            template_name       VARCHAR,
            software_version    VARCHAR,
            software_baseline   VARCHAR,
            software_check      VARCHAR NOT NULL DEFAULT 'NOT_CHECKED',
            config_job_id       UUID,
            detail              VARCHAR,
            created_at          TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at          TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT element_onboarding_status_check CHECK (status IN ('DISCOVERED', 'NO_TEMPLATE', 'TEMPLATE_SELECTED', 'APPLYING', 'ONBOARDED', 'FAILED')),
            CONSTRAINT element_onboarding_software_check_check CHECK (software_check IN ('NOT_CHECKED', 'MATCH', 'MISMATCH'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_element_onboarding_status ON ran_nf_oam.element_onboarding (status)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.software_campaign (
            campaign_id         UUID PRIMARY KEY,
            row_version         INTEGER NOT NULL DEFAULT 1,
            name                VARCHAR NOT NULL,
            requested_by        VARCHAR NOT NULL,
            status              VARCHAR NOT NULL DEFAULT 'PENDING',
            software_version    VARCHAR,
            selector            JSON,
            elements            JSON NOT NULL,
            wave_size           INTEGER,
            wave_count          INTEGER NOT NULL DEFAULT 1,
            current_wave        INTEGER NOT NULL DEFAULT 0,
            wave_pause_seconds  INTEGER NOT NULL DEFAULT 0,
            gate_max_new_alarms INTEGER NOT NULL DEFAULT 0,
            on_gate_failure     VARCHAR NOT NULL DEFAULT 'halt',
            wave_started_at     TIMESTAMP WITH TIME ZONE,
            next_wave_at        TIMESTAMP WITH TIME ZONE,
            halted_reason       VARCHAR,
            halted_detail       VARCHAR,
            wave_log            JSON NOT NULL,
            created_at          TIMESTAMP WITH TIME ZONE NOT NULL,
            finished_at         TIMESTAMP WITH TIME ZONE,
            CONSTRAINT software_campaign_status_check CHECK (status IN ('PENDING', 'RUNNING', 'HALTED', 'COMPLETED', 'ABORTED', 'ROLLING_BACK', 'ROLLED_BACK', 'ROLLBACK_FAILED')),
            CONSTRAINT software_campaign_on_gate_failure_check CHECK (on_gate_failure IN ('halt', 'rollback'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_software_campaign_status ON ran_nf_oam.software_campaign (status)")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job ADD COLUMN IF NOT EXISTS campaign_id UUID")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job ADD COLUMN IF NOT EXISTS campaign_wave INTEGER")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job ADD COLUMN IF NOT EXISTS rollback_of UUID")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job ADD COLUMN IF NOT EXISTS software_version VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_software_management_job_campaign_id ON ran_nf_oam.software_management_job (campaign_id)")


def downgrade() -> None:
    """Drop the index, the four columns and the three tables, in reverse order; onboarding and campaign history is lost."""
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_software_management_job_campaign_id")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job DROP COLUMN IF EXISTS software_version")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job DROP COLUMN IF EXISTS rollback_of")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job DROP COLUMN IF EXISTS campaign_wave")
    op.execute("ALTER TABLE ran_nf_oam.software_management_job DROP COLUMN IF EXISTS campaign_id")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.software_campaign")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.element_onboarding")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.onboarding_template")
