"""The second group of modules' tables move into schemas of their own (PR-DB-2.7): SME, DME, NFO, RApp Management, A1 Related, FOCOM, AIMgF and RAN NF OAM.

The same as `0023` and `0024`: `ALTER TABLE ... SET SCHEMA`, and an updatable view of the same name stays in `public` for the previous release until the revision after the next release.
R1 Termination owns no table (it writes the shared audit chain and reads RAN NF OAM's `rapp_kill`, a read-only grant listed in `migrations/db_roles.json`), so it gets a role and no schema.
RAN NF OAM's schema is `ran_nf_oam`; the gateway reads `ran_nf_oam.rapp_kill` by name (`R1_KILL_SWITCH_SCHEMA`), and the previous release's gateway still finds the view.

Revision ID: 0025
Revises: 0024
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

SCHEMAS = {
    "sme": (
        "invoker_registration",
        "issued_access_token",
        "provider_registration",
        "service_authz_policy",
        "service_event_subscription",
        "service_profile",
        "trusted_invoker",
        "used_client_assertion",
    ),
    "dme": (
        "data_job",
        "data_offer",
        "data_record",
        "dme_action_record",
        "dme_delivery_schema",
        "dme_producer",
        "dme_producer_type",
        "dme_type",
        "dme_type_subscription",
    ),
    "nfo": (
        "lcm_operation",
        "nf_deployment",
        "nf_deployment_descriptor",
        "nf_ocloud_resource",
    ),
    "rapp_mgmt": (
        "rapp_fault_report",
        "rapp_instance",
        "rapp_instance_version",
        "rapp_performance_report",
    ),
    "a1_related": (
        "a1_ei_type",
        "a1_policy",
        "a1_service_registration",
        "policy_status_subscription",
    ),
    "focom": (
        "deployment_manager",
        "inventory_subscription",
        "o2ims_object",
        "ocloud_alarm",
        "ocloud_alarm_subscription",
        "ocloud_location",
        "ocloud_performance_job",
        "ocloud_performance_metric",
        "ocloud_performance_subscription",
        "ocloud_site",
        "resource",
        "resource_pool",
        "resource_type",
    ),
    "aimgf": (
        "aiml_inference_emulation_function",
        "aiml_inference_function",
        "aiml_inference_report",
        "certification_record",
        "emulation_job",
        "feature_group",
        "inference_job",
        "lifecycle_transition",
        "ml_model_loading_policy",
        "ml_model_loading_process",
        "ml_model_loading_request",
        "ml_testing_function",
        "ml_testing_report",
        "ml_training_function",
        "ml_training_process",
        "ml_training_report",
        "ml_update_function",
        "ml_update_process",
        "ml_update_report",
        "ml_update_request",
        "mlmf_subscription",
        "model_lifecycle",
        "performance_report",
        "training_job",
        "validation_job",
    ),
    "ran_nf_oam": (
        "alarm",
        "cm_schema_cache",
        "cm_snapshot",
        "file_subscription",
        "fm_subscription",
        "kpi_definition",
        "kpi_schedule",
        "managed_entity",
        "managed_object",
        "msac_access_rule",
        "msac_identity",
        "msac_role",
        "o1_adaptor_endpoint",
        "o1_adaptor_host_key",
        "pm_file",
        "pm_subscription",
        "rapp_kill",
        "rapp_limit",
        "safeguard_refusal",
        "safeguard_subscription",
        "software_management_job",
        "vendor_capability",
        "write_config_job",
        "write_config_sub_change",
    ),
}


def upgrade() -> None:
    for schema, tables in SCHEMAS.items():
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for table in tables:
            op.execute(f"ALTER TABLE public.{table} SET SCHEMA {schema}")
            op.execute(f"CREATE VIEW public.{table} AS SELECT * FROM {schema}.{table}")


def downgrade() -> None:
    for schema, tables in SCHEMAS.items():
        for table in tables:
            op.execute(f"DROP VIEW IF EXISTS public.{table}")
            op.execute(f"ALTER TABLE {schema}.{table} SET SCHEMA public")
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
