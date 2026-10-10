"""Ten more modules' tables move into schemas of their own (PR-DB-2.7, first group): MLMR, RAN Analytics, SO SMOS, SA SMOS, Intent Service, MDAF and the four reference rApps.

Exactly what `0023` did for Onboarding, for each: `ALTER TABLE ... SET SCHEMA`, and an updatable view of the same name stays in `public` for the previous release's code (which names the
tables without a schema, and whose open connections keep their search path) until the revision after the next release. Data, indexes and constraints are untouched. The schema is the
module's name with `_` for `-`; `migrations/table_owners.json` says whose tables these are, and `scripts/check_migration_matches_models.py` fails if a table of a module with a
schema is anywhere else, or another module's table is in it. MLLF and R1 Termination own no table, so they have no schema.

Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

SCHEMAS = {
    "mlmr": (
        "aiml_model",
        "ml_model_coordination_group",
        "ml_model_profile",
        "ml_model_repository",
        "ml_models_storage",
        "model_artifact",
        "model_change_subscription",
    ),
    "ran_analytics": (
        "mdaf_producer",
    ),
    "so_smos": (
        "service_order",
    ),
    "sa_smos": (
        "assurance_monitor",
        "o1_cm_enactment",
        "remedial_action",
    ),
    "intent_service": (
        "autonomy_dispatch",
        "intent",
        "intent_handling_function",
        "intent_report",
        "intent_utility_formula",
    ),
    "mdaf": (
        "mda_function",
        "mda_report_delivery",
        "mda_request",
        "mda_subscription",
        "mdaf_report",
    ),
    "energy_saving_rapp": (
        "energy_saving_cell",
        "energy_saving_decision",
        "energy_saving_instance",
    ),
    "mobility_optimization_rapp": (
        "mobility_decision",
        "mobility_instance",
        "mobility_relation",
    ),
    "coverage_optimization_rapp": (
        "coverage_cell",
        "coverage_decision",
        "coverage_instance",
    ),
    "traffic_steering_rapp": (
        "traffic_cell",
        "traffic_decision",
        "traffic_instance",
    ),
}


def upgrade() -> None:
    """For each module in `SCHEMAS`, create its schema, move its tables into it and leave an updatable view of the same name in `public`."""
    for schema, tables in SCHEMAS.items():
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for table in tables:
            op.execute(f"ALTER TABLE public.{table} SET SCHEMA {schema}")
            # The compatibility view: one table and no join, so it is updatable and the previous release's unqualified reads and writes keep working. Dropped by 0029.
            op.execute(f"CREATE VIEW public.{table} AS SELECT * FROM {schema}.{table}")


def downgrade() -> None:
    """For each module, drop the views, move the tables back to `public` and drop the schema."""
    for schema, tables in SCHEMAS.items():
        for table in tables:
            op.execute(f"DROP VIEW IF EXISTS public.{table}")
            op.execute(f"ALTER TABLE {schema}.{table} SET SCHEMA public")
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
