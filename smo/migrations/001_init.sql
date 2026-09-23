-- AI-RAN SMO Phase 1 — consolidated schema.
-- One shared Postgres instance, moduleScope-partitioned per Requirements v0.1 section 3.
-- This file merges every table from all eight LLD passes (HLD fields + LLD
-- extensions, unified into single CREATE TABLEs rather than the ALTER-chain
-- form each LLD document used incrementally) into the buildable Phase 1 schema.
-- Ordered so foreign keys resolve top to bottom.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ============================================================
-- Foundational Platform: SME  (Foundational Platform LLD section 2.4)
-- ============================================================

CREATE TABLE service_profile (
  service_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_name          TEXT NOT NULL,
  producer_id           TEXT NOT NULL,        -- == rapp_instance.instance_id (identity.py)
  endpoint               TEXT NOT NULL,
  version                 TEXT NOT NULL,
  full_api_versions        TEXT[],
  service_capabilities      JSONB,             -- generic extension point (e.g. DME's supportedDataDeliveryModes)
  selection_criteria         JSONB,
  module_scope                 TEXT NOT NULL,
  -- UNIQUE on service_name ALONE (not paired with producer_id): a different
  -- producer registering the same name is the conflict this LLD closes;
  -- the same producer re-registering it is an idempotent update-in-place,
  -- handled at the application layer (sme/app/main.py), not blocked here.
  UNIQUE (service_name)
);

CREATE TABLE service_authz_policy (
  service_id                  UUID PRIMARY KEY REFERENCES service_profile(service_id) ON DELETE CASCADE,
  allowed_consumers           TEXT[],
  gates_discovery_visibility  BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE service_event_subscription (
  subscription_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscriber_id      TEXT NOT NULL,
  event_types        TEXT[] NOT NULL CHECK (event_types <@ ARRAY['SERVICE_API_AVAILABLE','SERVICE_API_UNAVAILABLE','SERVICE_API_UPDATE']),
  callback_uri        TEXT NOT NULL
);

-- ============================================================
-- Foundational Platform: DME  (Foundational Platform LLD section 3.8)
-- ============================================================

CREATE TABLE dme_type (
  dme_type_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  namespace                     TEXT NOT NULL,
  name                           TEXT NOT NULL CHECK (name NOT LIKE '%:%'),
  version                         TEXT NOT NULL,
  type_name                        TEXT NOT NULL,
  producer_id                        TEXT NOT NULL,
  data_production_schema               JSONB NOT NULL,
  collection_spec                        JSONB,               -- above-spec addition, kept deliberately (section 3.4)
  producer_health_callback_url             TEXT NOT NULL,     -- ADOPT from ICS (repo inventory)
  job_callback_url                            TEXT NOT NULL,  -- NEW section 5: ICS's own InfoProducer.jobCallbackUrl
  UNIQUE (namespace, name, version)
);

CREATE TABLE dme_delivery_schema (
  delivery_schema_id  TEXT PRIMARY KEY,
  dme_type_id          UUID NOT NULL REFERENCES dme_type(dme_type_id) ON DELETE CASCADE,
  schema_type            TEXT NOT NULL CHECK (schema_type IN ('JSON_SCHEMA','XML_SCHEMA','PROTOBUF_SCHEMA','AVRO_SCHEMA')),
  schema                    JSONB NOT NULL
);

CREATE TABLE data_job (
  data_job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_delivery_mode   TEXT NOT NULL CHECK (data_delivery_mode IN ('ONE_TIME','CONTINUOUS')),
  dme_type_id           UUID NOT NULL REFERENCES dme_type(dme_type_id),
  production_job_definition JSONB,
  data_delivery_method  TEXT NOT NULL CHECK (data_delivery_method IN ('PULL_HTTP','PUSH_HTTP','STREAMING_KAFKA')),
  delivery_details       JSONB,
  consumer_id             TEXT NOT NULL,   -- rAppId, or 'DME_FRAMEWORK' (section 3.7)
  status                    TEXT NOT NULL DEFAULT 'PENDING'
);

CREATE TABLE data_offer (
  offer_id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dme_type_id                        UUID NOT NULL REFERENCES dme_type(dme_type_id),
  data_delivery_methods_offered       TEXT[] NOT NULL,
  data_delivery_method_committed      TEXT,
  data_availability_notification_uri  TEXT,   -- REVERSED direction — section 3.5
  data_offer_termination_notification_uri TEXT NOT NULL
);

-- ============================================================
-- Application Hosting: Software Package Onboarding  (Onboarding/rApp Mgmt LLD)
-- ============================================================

CREATE TABLE application_package (
  package_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_type       TEXT NOT NULL CHECK (application_type IN ('rApp','xApp','CloudifiedNF','PNF')),
  name                     TEXT NOT NULL,
  vendor                     TEXT,
  version                     TEXT NOT NULL,
  state                         TEXT NOT NULL DEFAULT 'ONBOARDING'
                                   CHECK (state IN ('ONBOARDING','AVAILABLE','PRIMING','PRIMED','DEPRIMING','DEPRECATED','DELETING','FAILED')),
  scheduled_deletion_date        TIMESTAMPTZ,
  parent_package_id                UUID REFERENCES application_package(package_id),
  manifest_ref                       TEXT NOT NULL,
  tosca_entry_definitions              TEXT,     -- NEW section 1: TOSCA-Metadata/Definitions/Artifacts
  signature_verified                     BOOLEAN NOT NULL DEFAULT false,
  integrity_hash                           TEXT
);

CREATE TABLE artifact (
  artifact_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  package_id     UUID NOT NULL REFERENCES application_package(package_id) ON DELETE CASCADE,
  path            TEXT NOT NULL,
  access_url        TEXT NOT NULL
);

CREATE TABLE package_usage_registration (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  package_id      UUID NOT NULL REFERENCES application_package(package_id),
  consumer_id       TEXT NOT NULL,
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  stopped_at            TIMESTAMPTZ    -- NEW section 4: NULL == active
);

-- ============================================================
-- Application Hosting: rApp Management
-- ============================================================

CREATE TABLE rapp_instance (
  instance_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  package_id               UUID NOT NULL REFERENCES application_package(package_id),
  state                       TEXT NOT NULL DEFAULT 'DEPLOYING'
                                 CHECK (state IN ('DEPLOYING','RUNNING','UPGRADING','TERMINATING','FAULTED')),
  configuration                   JSONB,
  workload_ref                      TEXT,
  -- Nullable, not NOT NULL as originally written: _revoke_credential
  -- (statemachine.py) explicitly sets this to NULL on TERMINATE and
  -- UPGRADE_COMMIT, closing v1.3's RT-3 red-team finding — a NOT NULL
  -- constraint here would reject that commit outright on real Postgres.
  -- Only caught by running this schema for real, not just SQLite's
  -- ORM-generated tables the unit tests use.
  oauth_client_id                     TEXT,            -- == rAppId, identity.py, until revoked
  created_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
  upgrade_timeout_seconds                 INTEGER NOT NULL DEFAULT 300,  -- section 6: confirmed default (OPEN_ITEMS.md section 1)
  -- Also missing from this table until this pass, for the same reason:
  -- both are read/written by rapp-mgmt/app/upgrade.py and main.py but
  -- SQLite's unit tests build their schema from the ORM models
  -- directly, never from this file, so the gap went uncaught.
  pending_upgrade_instance_id             UUID REFERENCES rapp_instance(instance_id),
  package_usage_registration_id             UUID REFERENCES package_usage_registration(id)
);

CREATE TABLE rapp_fault_report (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id    UUID NOT NULL REFERENCES rapp_instance(instance_id),
  severity         TEXT NOT NULL CHECK (severity IN ('critical','major','minor','warning')),
  description        TEXT
);

CREATE TABLE rapp_performance_report (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id    UUID NOT NULL REFERENCES rapp_instance(instance_id),
  metrics          JSONB NOT NULL
);

-- ============================================================
-- RAN & Platform Integration: RAN NF OAM  (RAN NF OAM LLD sections 1-4)
-- ============================================================

CREATE TABLE o1_adaptor_endpoint (
  endpoint_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  managed_element_ref TEXT NOT NULL,
  adaptor_uri       TEXT NOT NULL,
  protocol_support  TEXT[] NOT NULL,
  registered_via    TEXT NOT NULL DEFAULT 'MNS_REGISTRY_NRM',
  health_status     TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (health_status IN ('ACTIVE','DEGRADED','UNREACHABLE')),
  last_heartbeat_at TIMESTAMPTZ,
  UNIQUE (managed_element_ref)
);

CREATE TABLE managed_entity (
  managed_element_ref     TEXT PRIMARY KEY,
  managed_function_ref    TEXT,
  entity_type               TEXT NOT NULL CHECK (entity_type IN ('O-CU-CP','O-CU-UP','O-DU','O-RU','Near-RT-RIC')),
  vendor_name                 TEXT,
  o1_protocol                   TEXT NOT NULL CHECK (o1_protocol IN ('RESTCONF','NETCONF')),
  o1_adaptor_endpoint_id           UUID REFERENCES o1_adaptor_endpoint(endpoint_id)
);

CREATE TABLE alarm (
  alarm_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_alarm_id    TEXT NOT NULL,
  managed_element_ref TEXT NOT NULL REFERENCES managed_entity(managed_element_ref),
  managed_function_ref TEXT,
  severity           TEXT NOT NULL CHECK (severity IN ('critical','major','minor','warning','cleared')),
  ack_state          TEXT NOT NULL DEFAULT 'UNACKNOWLEDGED' CHECK (ack_state IN ('ACKNOWLEDGED','UNACKNOWLEDGED')),
  correlation_group  TEXT,
  raised_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  probable_cause        TEXT,
  specific_problem       TEXT,
  root_cause_indicator    BOOLEAN NOT NULL DEFAULT false,
  correlated_notifications UUID[] NOT NULL DEFAULT '{}',
  proposed_repair_actions   TEXT,
  cleared_at                 TIMESTAMPTZ,
  clear_user_id                TEXT
);
CREATE INDEX idx_alarm_correlation ON alarm (correlation_group) WHERE correlation_group IS NOT NULL;
CREATE INDEX idx_alarm_me ON alarm (managed_element_ref);

CREATE TABLE cm_schema_cache (
  schema_name  TEXT NOT NULL,
  revision     TEXT NOT NULL DEFAULT '',
  location     TEXT NOT NULL,
  type         TEXT NOT NULL DEFAULT 'YANG',
  cached_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, revision)
);

CREATE TABLE write_config_job (
  job_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requested_by       TEXT NOT NULL,
  scope              TEXT NOT NULL,
  schema_validated_at TIMESTAMPTZ,
  status             TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','COMPLETED','PARTIAL_SUCCESS','FAILED')),
  conflict_resolution TEXT CHECK (conflict_resolution IN ('LAST_WRITE_WINS','REJECT')),
  msac_role          TEXT
);

CREATE TABLE write_config_sub_change (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id             UUID NOT NULL REFERENCES write_config_job(job_id) ON DELETE CASCADE,
  managed_element_ref TEXT NOT NULL,
  managed_function_ref TEXT,
  attribute_changes  JSONB NOT NULL,
  status             TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPLIED','REJECTED')),
  rejection_reason   TEXT
);

CREATE TABLE pm_subscription (
  subscription_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  managed_element_ref  TEXT NOT NULL REFERENCES managed_entity(managed_element_ref),
  counter_type          TEXT NOT NULL,
  delivery_method         TEXT NOT NULL CHECK (delivery_method IN ('pull','push','stream')),
  southbound_engine         TEXT NOT NULL CHECK (southbound_engine IN ('ProvMnS','PMJobControl','FileDataReporting','StreamingDataReporting'))
);

CREATE TABLE software_management_job (
  job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  managed_element_ref  TEXT NOT NULL REFERENCES managed_entity(managed_element_ref),
  ru_instance_id        TEXT,   -- NEW section 3.4: reserved until O1 Adaptor's WG4 SWM RPC augment exists
  phase                   TEXT NOT NULL CHECK (phase IN ('DOWNLOAD','INSTALL','ACTIVATE')),
  status                    TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','IN_PROGRESS','COMPLETED','FAILED'))
);

-- ============================================================
-- RAN & Platform Integration: A1 Related  (A1 Related LLD section 4)
-- ============================================================

CREATE TABLE a1_policy (
  policy_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  near_rt_ric_policy_id TEXT,   -- the Near-RT RIC's OWN identifier for this policy, distinct from policy_id (see a1-related/app/models.py)
  policy_type_id       TEXT NOT NULL,
  creator_id             TEXT NOT NULL,   -- == rapp_instance.instance_id
  near_rt_ric_id           TEXT NOT NULL,
  policy_object              JSONB NOT NULL,   -- opaque, A1TD-owned, never interpreted here
  enforcement_status            TEXT NOT NULL DEFAULT 'PENDING'
                                   CHECK (enforcement_status IN ('PENDING','ENFORCED','REJECTED','SUSPENDED')),
  rejection_reason                 TEXT
);

CREATE TABLE policy_status_subscription (
  subscription_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  notification_destination TEXT NOT NULL,
  subscription_scope     TEXT CHECK (subscription_scope IN ('OWN','OTHERS','ALL')),
  policy_id_list          TEXT[],
  policy_type_id_list     TEXT[],
  near_rt_ric_id_list      TEXT[],
  CONSTRAINT scope_xor_ids CHECK (NOT (subscription_scope IS NOT NULL AND policy_id_list IS NOT NULL))
);

CREATE TABLE a1_ei_type (
  ei_type_id            TEXT PRIMARY KEY,
  registered_by          TEXT NOT NULL,
  ei_source_dme_type_id   UUID NOT NULL REFERENCES dme_type(dme_type_id)
);
-- A1TrainingCapability: DORMANT (section 0 of the A1 Related LLD) — no table until
-- a future scope decision explicitly reaches into A1AP. Do not create this table.

-- ============================================================
-- RAN & Platform Integration: NFO  (NFO+FOCOM LLD sections 2, 5)
-- ============================================================

CREATE TABLE nf_deployment_descriptor (
  nf_deployment_descriptor_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  package_id                    UUID NOT NULL REFERENCES application_package(package_id),
  name                            TEXT NOT NULL,
  required_resource_type_id        TEXT,
  workload_template                 JSONB NOT NULL
);

-- Added after nf_deployment_descriptor rather than inline on
-- application_package (defined earlier in this file) because the FK
-- target has to exist first. NFO's CreateDescriptor (NFO+FOCOM LLD
-- section 2) populates this once OnboardPackage's validation succeeds —
-- closes the gap where rApp Management used to pass packageId where NFO
-- expected a real nfDeploymentDescriptorId.
ALTER TABLE application_package
  ADD COLUMN nf_deployment_descriptor_id UUID REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id);

CREATE TABLE nf_deployment (
  nf_deployment_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nf_deployment_descriptor_id  UUID NOT NULL REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id),
  name                          TEXT NOT NULL,   -- NEW section 5: the reference's own duplication guard needs a real name
  cluster_id                     TEXT NOT NULL,   -- degenerate single value, Phase 1
  state                            TEXT NOT NULL DEFAULT 'INITIAL'
                                      -- NEW section 5: the reference's real 7-state NfDeploymentState
                                      -- (Initial/Installing/Installed/Updating/Uninstalling/Abnormal/Deleting)
                                      CHECK (state IN ('INITIAL','INSTANTIATING','RUNNING','UPDATING','TERMINATING','ABNORMAL','DELETING')),
  workload_ref                       TEXT,
  required_resource_type_id            TEXT,
  config_secrets                         JSONB
);

-- NEW section 5: the reference's own NfOCloudVResource — the
-- resource-linkage object between an NfDeployment and the O-Cloud
-- resource(s) it actually consumes, entirely missing before this pass.
CREATE TABLE nf_ocloud_resource (
  resource_link_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nf_deployment_id   UUID NOT NULL REFERENCES nf_deployment(nf_deployment_id),
  resource_ref          TEXT NOT NULL,
  vresource_type           TEXT NOT NULL DEFAULT 'COMPUTE'
);

CREATE TABLE lcm_operation (
  operation_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nf_deployment_id   UUID NOT NULL REFERENCES nf_deployment(nf_deployment_id),
  operation_type       TEXT NOT NULL CHECK (operation_type IN ('INSTANTIATE','TERMINATE','HEAL','SCALE')),
  status                 TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','IN_PROGRESS','COMPLETED','FAILED'))
);

-- ============================================================
-- RAN & Platform Integration: FOCOM  (NFO+FOCOM LLD section 1)
-- ============================================================

CREATE TABLE ocloud_alarm (
  alarm_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_ref   TEXT NOT NULL,   -- distinct domain from RAN NF OAM's alarm table — infrastructure, not RAN-function
  severity        TEXT NOT NULL,
  raised_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ocloud_performance_metric (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_ref    TEXT NOT NULL,
  metric_name      TEXT NOT NULL,
  value             DOUBLE PRECISION NOT NULL,
  collected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE inventory_subscription (
  subscription_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  callback_uri      TEXT NOT NULL,
  resource_type_id   TEXT   -- optional filter; unset matches every resource type
);

CREATE TABLE resource_type (
  resource_type_id  TEXT PRIMARY KEY,   -- literal ID, not a UUID (Phase 1's degenerate topology is fixed, not generated)
  name               TEXT NOT NULL,
  description         TEXT,
  vendor               TEXT,
  model                 TEXT,
  version               TEXT
);

CREATE TABLE resource_pool (
  resource_pool_id  TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  description         TEXT,
  o_cloud_id           TEXT NOT NULL
);

CREATE TABLE resource (
  resource_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_type_id   TEXT NOT NULL REFERENCES resource_type(resource_type_id),
  resource_pool_id   TEXT NOT NULL REFERENCES resource_pool(resource_pool_id),
  parent_id            UUID,
  description           TEXT
);

CREATE TABLE deployment_manager (
  deployment_manager_id  TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  description                TEXT,
  o_cloud_id                  TEXT NOT NULL,
  service_uri                  TEXT
);

-- ============================================================
-- AI/ML Content: AI/ML Workflow  (AI/ML Workflow LLD sections 4, 6)
-- ============================================================

CREATE TABLE ml_model_coordination_group (
  group_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  group_type                    TEXT NOT NULL DEFAULT 'SHARED_MODEL' CHECK (group_type IN ('SHARED_MODEL','JOINT_TRAINING')),
  member_model_ids                UUID[] NOT NULL CHECK (array_length(member_model_ids, 1) >= 2),
  member_use_cases                  TEXT[],
  shared_feature_pipeline_ref         TEXT,
  retrain_propagation                   TEXT NOT NULL DEFAULT 'ANY_MEMBER_TRIGGERS'
                                           CHECK (retrain_propagation IN ('ANY_MEMBER_TRIGGERS','MAJORITY_TRIGGERS','WEIGHTED_TRIGGERS'))
);

CREATE TABLE aiml_model (
  model_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id       TEXT NOT NULL,
  model_type              TEXT NOT NULL,
  version                   TEXT NOT NULL,
  state                       TEXT NOT NULL DEFAULT 'REGISTERED'
                                 CHECK (state IN ('REGISTERED','TRAINING','TESTED','EMULATED','CERTIFIED','LOADED','ACTIVE','DEPRECATED')),
  training_job_id                UUID,
  training_data_lineage             JSONB,
  integrity_hash                       TEXT,
  artifact_location                       TEXT,
  required_resource_type_id                 TEXT,
  cleared_node_groups                          TEXT[],   -- NEW section 5: MultiNode Q2 gap closure
  UNIQUE (model_type, version)                           -- NEW section 5: the reference's own (modelName, modelVersion) uniqueness
);

CREATE TABLE model_artifact (
  artifact_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id),
  artifact_version  INTEGER NOT NULL CHECK (artifact_version >= 1),
  filename          TEXT NOT NULL,
  content           BYTEA NOT NULL,
  uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE training_job (
  training_job_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id                     UUID REFERENCES aiml_model(model_id),
  model_coordination_group_id  UUID REFERENCES ml_model_coordination_group(group_id),
  producer_type                  TEXT NOT NULL DEFAULT 'rApp' CHECK (producer_type = 'rApp'),
  producer_id                      TEXT NOT NULL,
  required_data                       JSONB,
  validation_criteria                   JSONB,
  status                                   TEXT NOT NULL DEFAULT 'PENDING'
                                             CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED')),
  notification_uri                          TEXT,
  CONSTRAINT exactly_one_target CHECK (
    (model_id IS NOT NULL AND model_coordination_group_id IS NULL)
    OR (model_id IS NULL AND model_coordination_group_id IS NOT NULL)
  )
);

CREATE TABLE model_change_subscription (
  subscription_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id),
  consumer_id         TEXT NOT NULL
);

CREATE TABLE mlmf_subscription (
  subscription_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id),
  metric_types      TEXT[] NOT NULL,
  dme_type_id       UUID NOT NULL REFERENCES dme_type(dme_type_id),
  guard_kpi_floor   JSONB
);

CREATE TABLE performance_report (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id  UUID NOT NULL REFERENCES mlmf_subscription(subscription_id),
  metrics          JSONB NOT NULL,
  breached_floor   BOOLEAN NOT NULL DEFAULT false,
  reported_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_perf_breach ON performance_report (subscription_id) WHERE breached_floor = true;

CREATE TABLE inference_job (
  inference_job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id         UUID NOT NULL REFERENCES aiml_model(model_id),
  status           TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
  notification_destination TEXT
);

-- ============================================================
-- AI/ML Content: RAN Analytics  (RAN Analytics LLD section 4)
-- ============================================================

CREATE TABLE mdaf_producer (
  producer_id      TEXT NOT NULL,
  analytics_type    TEXT NOT NULL,
  dme_input_types    UUID[] NOT NULL,
  output_schema       JSONB NOT NULL,
  PRIMARY KEY (producer_id, analytics_type)
);

CREATE TABLE mdaf_report (
  report_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  analytics_type      TEXT NOT NULL,
  scope                 JSONB,
  input_sources          UUID[] NOT NULL,
  output                  JSONB NOT NULL,
  generated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  subscriber_attribution    TEXT
);

CREATE TABLE mda_subscription (
  subscription_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  analytics_type     TEXT NOT NULL,
  scope                JSONB,
  requested_by          TEXT NOT NULL
);

-- ============================================================
-- Governance & Assurance: Policy Management & Info  (Policy Mgmt LLD sections 1-4)
-- ============================================================

CREATE TABLE intent (
  intent_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                   TEXT,
  intent_expectations            JSONB NOT NULL,   -- opaque; TS 28.312 text not in this project's corpus
  intent_mgmt_purpose               TEXT,
  intent_admin_state                  TEXT NOT NULL DEFAULT 'ACTIVATED' CHECK (intent_admin_state IN ('ACTIVATED','DEACTIVATED')),
  intent_priority                       INTEGER NOT NULL DEFAULT 1 CHECK (intent_priority BETWEEN 1 AND 100),
  intent_preemption_capability             BOOLEAN NOT NULL DEFAULT false,
  rmio_id                                    TEXT NOT NULL
);

CREATE TABLE intent_report (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  intent_id            UUID NOT NULL REFERENCES intent(intent_id),   -- was intent_reference (bare string) pre-LLD
  intent_fulfilment_report JSONB,
  intent_conflict_reports    JSONB,
  last_updated_time            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intent_handling_function (
  rmih_id                        TEXT PRIMARY KEY,
  sme_service_id                  TEXT NOT NULL,
  intent_handling_scope             JSONB,
  intent_handling_capability_list     JSONB NOT NULL,
  notification_callback_uri            TEXT NOT NULL  -- NEW: closes the Intent-to-RMIH dispatch gap
);

-- ============================================================
-- Governance & Assurance: SO SMOS  (SO/SA SMOS LLD sections 1, 3)
-- ============================================================

CREATE TABLE service_order (
  order_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope               TEXT NOT NULL,
  steps                 JSONB NOT NULL,   -- array of {stepType, targetModule, status}, dispatched per section 1
  homing_decision          JSONB,          -- NEW section 1.2: resolvedClusterId / resolvedNodeGroup
  rmih_registration           TEXT NOT NULL
);

-- ============================================================
-- Governance & Assurance: SA SMOS
-- ============================================================

CREATE TABLE assurance_monitor (
  monitor_id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  target_order_id                UUID REFERENCES service_order(order_id),
  target_coordination_group_id     UUID REFERENCES ml_model_coordination_group(group_id),   -- NEW section 2.2
  analytics_subscription_id           UUID REFERENCES mda_subscription(subscription_id),
  requirement_thresholds                 JSONB NOT NULL,
  CONSTRAINT one_target_only CHECK (
    NOT (target_order_id IS NOT NULL AND target_coordination_group_id IS NOT NULL)
  )
);

CREATE TABLE remedial_action (
  action_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  monitor_id                UUID NOT NULL REFERENCES assurance_monitor(monitor_id),
  action_type                 TEXT NOT NULL CHECK (action_type IN ('CONFIG_CHANGE','SCALE','RECONNECT','ROLLBACK')),
  auto_executed                  BOOLEAN NOT NULL DEFAULT false,
  auto_execution_scope_config       TEXT,   -- ADMIN-ONLY to change, per REQ-CNFG-ADM pattern
  outcome                              TEXT CHECK (outcome IN ('RESOLVED','ESCALATED','FAILED'))
);
