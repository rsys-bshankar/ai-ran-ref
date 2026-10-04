"""Foreign keys between tables of different modules are dropped (PR-DB-2.4): the column stays, the constraint goes.

Modules talk to each other through R1, not through each other's tables (docs/ARCHITECTURE.md), and per-module schemas and roles (PR-DB-2.5 onward)
cannot keep a constraint into a schema a module may not read. 24 constraints crossed a module boundary (`migrations/table_owners.json` is the map;
`scripts/check_migration_matches_models.py` now fails on a new one). The ORM already declared every one of these columns as a plain id, and the
unit tests run on SQLite, which never had the constraints, so what the code was tested against is what the schema now is.

What goes with them, and what stays:
  * the existence check (a row naming a package, model or order that does not exist): the API of the owning module is where that is checked (the
    modules validate through R1 as they always did); the database no longer refuses it.
  * `ON DELETE CASCADE` from `aiml_model` (MLMR) onto AIMGF's jobs and records, and `ON DELETE SET NULL` from `ml_model_repository`: deleting an MLMR model
    no longer deletes AIMGF's rows about it behind AIMGF's back; they stay, naming a model that is gone.
No data changes. The previous release's code, which never relied on the constraints being absent, runs on the new schema.

Revision ID: 0022
Revises: 0021
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# (table, constraint, definition): the definition is what `downgrade` puts back
CROSS_MODULE_FOREIGN_KEYS = [
    ("a1_ei_type", "a1_ei_type_ei_source_dme_type_id_fkey",
     "FOREIGN KEY (ei_source_dme_type_id) REFERENCES dme_type(dme_type_id)"),
    ("application_package", "application_package_nf_deployment_descriptor_id_fkey",
     "FOREIGN KEY (nf_deployment_descriptor_id) REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id)"),
    ("assurance_monitor", "assurance_monitor_analytics_subscription_id_fkey",
     "FOREIGN KEY (analytics_subscription_id) REFERENCES mda_subscription(subscription_id)"),
    ("assurance_monitor", "assurance_monitor_target_coordination_group_id_fkey",
     "FOREIGN KEY (target_coordination_group_id) REFERENCES ml_model_coordination_group(group_id)"),
    ("assurance_monitor", "assurance_monitor_target_order_id_fkey",
     "FOREIGN KEY (target_order_id) REFERENCES service_order(order_id)"),
    ("certification_record", "certification_record_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("emulation_job", "emulation_job_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("emulation_job", "emulation_job_nf_deployment_descriptor_id_fkey",
     "FOREIGN KEY (nf_deployment_descriptor_id) REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id)"),
    ("inference_job", "inference_job_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("lifecycle_transition", "lifecycle_transition_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("ml_training_function", "ml_training_function_ml_model_repository_ref_fkey",
     "FOREIGN KEY (ml_model_repository_ref) REFERENCES ml_model_repository(ml_model_repository_id) ON DELETE SET NULL"),
    ("mlmf_subscription", "mlmf_subscription_dme_type_id_fkey",
     "FOREIGN KEY (dme_type_id) REFERENCES dme_type(dme_type_id)"),
    ("mlmf_subscription", "mlmf_subscription_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("model_lifecycle", "model_lifecycle_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("model_lifecycle", "model_lifecycle_nf_deployment_descriptor_id_fkey",
     "FOREIGN KEY (nf_deployment_descriptor_id) REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id)"),
    ("nf_deployment_descriptor", "nf_deployment_descriptor_package_id_fkey",
     "FOREIGN KEY (package_id) REFERENCES application_package(package_id)"),
    ("rapp_instance", "rapp_instance_package_id_fkey",
     "FOREIGN KEY (package_id) REFERENCES application_package(package_id)"),
    ("rapp_instance", "rapp_instance_package_usage_registration_id_fkey",
     "FOREIGN KEY (package_usage_registration_id) REFERENCES package_usage_registration(id)"),
    ("training_job", "training_job_model_coordination_group_id_fkey",
     "FOREIGN KEY (model_coordination_group_id) REFERENCES ml_model_coordination_group(group_id)"),
    ("training_job", "training_job_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("training_job", "training_job_nf_deployment_descriptor_id_fkey",
     "FOREIGN KEY (nf_deployment_descriptor_id) REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id)"),
    ("validation_job", "validation_job_model_coordination_group_id_fkey",
     "FOREIGN KEY (model_coordination_group_id) REFERENCES ml_model_coordination_group(group_id)"),
    ("validation_job", "validation_job_model_id_fkey",
     "FOREIGN KEY (model_id) REFERENCES aiml_model(model_id) ON DELETE CASCADE"),
    ("validation_job", "validation_job_nf_deployment_descriptor_id_fkey",
     "FOREIGN KEY (nf_deployment_descriptor_id) REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id)"),
]


def upgrade() -> None:
    for table, name, _ in CROSS_MODULE_FOREIGN_KEYS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def downgrade() -> None:
    # re-adding validates the rows: a row written while the constraint was gone, naming something that does not exist, makes this fail, which is the honest answer
    for table, name, definition in CROSS_MODULE_FOREIGN_KEYS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")
