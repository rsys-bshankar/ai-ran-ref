"""The shared rate limiter's buckets (PR-SEC-8.5): `rate_bucket`, one row per caller.

R1 Termination's per-caller token bucket (PR-SEC-8.2) lived in each replica, so N replicas gave a caller N times its budget. With `R1_RATE_STORE=postgres` the
gateway keeps the bucket here instead and takes a token with one `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` (`smo_shared/ratelimit.py` says exactly what it
computes). `tokens` and `refilled_at` (epoch seconds) are the bucket's state, `last_allowed` is what the statement decided, returned to the caller in the same
round trip. A row is deleted once its bucket would be full again, so the table is as large as the set of recently active callers.

Expand only: a new table nobody reads or writes until an operator sets `R1_RATE_STORE=postgres`, so the previous release's code is unaffected. It is a shared table
(`migrations/table_owners.json`), and `migrations/db_roles.json` grants it to R1 Termination's role and no other.

Revision ID: 0028
Revises: 0027
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS rate_bucket (
            caller TEXT PRIMARY KEY,
            tokens DOUBLE PRECISION NOT NULL,
            refilled_at DOUBLE PRECISION NOT NULL,
            last_allowed BOOLEAN NOT NULL DEFAULT true
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rate_bucket")
