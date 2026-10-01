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
  aef_profiles                    JSONB,       -- NEW section 5: real AefProfile[] (aefId, protocol, dataFormat, versions[].resources[].commType)
  api_supp_feats                    TEXT,      -- NEW section 5
  shareable_info                      JSONB,   -- NEW section 5: {isShareable, capifProvDoms}
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
  event_types        TEXT[] NOT NULL CHECK (event_types <@ ARRAY['SERVICE_API_AVAILABLE','SERVICE_API_UNAVAILABLE','SERVICE_API_UPDATE',
                                                                 'API_INVOKER_ONBOARDED','API_INVOKER_OFFBOARDED','API_INVOKER_UPDATED']),
  callback_uri        TEXT NOT NULL,
  api_ids               TEXT[],  -- NEW section 5: CAPIFEventFilter.apiIds
  -- OI-5-sme-filters: the rest of CAPIFEventFilter
  api_invoker_ids         TEXT[],
  aef_ids                   TEXT[]
);

-- NEW section 5: Provider (APF) enrolment (providermanagement.go's own
-- POST/DELETE /registrations) — the real registry register_service's own
-- apf_id check needs. apf_id is this build's own flattened identity
-- (apfId == producerId == rAppId), not a separate provider-domain-id.
CREATE TABLE provider_registration (
  apf_id                 TEXT PRIMARY KEY,
  provider_domain_info     TEXT
);

-- NEW section 2: API Invoker onboarding (invokermanagement.go) — the
-- real registry the Security/token API's own IsInvokerRegistered/
-- VerifyInvokerSecret gate needs. onboarding_secret_hash: security
-- review — never store the raw secret, only a salted scrypt hash
-- ("salt_hex:digest_hex"), or a DB leak hands out reusable client
-- credentials directly.
CREATE TABLE invoker_registration (
  api_invoker_id            TEXT PRIMARY KEY,
  -- HISTORY.md §7 SME item 1: the real CAPIF core's onboarding is
  -- public-key-based -- the client's own apiInvokerPublicKey. A PEM key
  -- verifies the invoker's RFC 7523 client assertions (SA-SME-1-public-key);
  -- anything else is an opaque label (onboarding-secret auth only).
  public_key                 TEXT NOT NULL,
  onboarding_secret_hash       TEXT NOT NULL
);

-- NEW section 2: opaque, server-tracked bearer tokens — the honest
-- substitute for the reference's own externally-signed JWT (Keycloak,
-- an external IdP this build doesn't run). Validated by R1 Termination
-- via POST /oauth2/introspect on every proxied request.
-- access_token_hash: security review — the raw token is returned to
-- the caller once at issuance and never stored; only its SHA-256 hash
-- is, so a DB leak can't hand out live, reusable bearer tokens.
CREATE TABLE issued_access_token (
  access_token_hash       TEXT PRIMARY KEY,
  api_invoker_id             TEXT NOT NULL,
  expires_at                    TIMESTAMPTZ NOT NULL,
  scope                           TEXT   -- OI-2-oauth2-scope: the checked, granted scope; NULL = unscoped
);

-- SA-SME-1-public-key: RFC 7523 client-assertion replay protection -- each
-- assertion's jti, kept until the assertion expires.
CREATE TABLE used_client_assertion (
  jti               TEXT PRIMARY KEY,
  api_invoker_id    TEXT NOT NULL,
  expires_at        TIMESTAMPTZ NOT NULL
);

-- HISTORY.md §7 SME item 2: the real CAPIF core's "Trusted Invokers"
-- security-context subsystem (capifcore/internal/securityservice/
-- security.go) -- a second, separate real mechanism beyond OAuth2
-- token issuance, that a real AEF (resource server) would consult
-- directly. security_info is one JSON list per invoker, matching the
-- real CAPIF core's own in-memory ServiceSecurity struct shape.
CREATE TABLE trusted_invoker (
  api_invoker_id              TEXT PRIMARY KEY,
  notification_destination      TEXT NOT NULL,
  request_test_notification       BOOLEAN NOT NULL DEFAULT FALSE,
  security_info                     JSONB NOT NULL
);

-- ============================================================
-- Foundational Platform: DME  (Foundational Platform LLD section 3.8)
-- ============================================================

-- HISTORY.md §7 — DME vs. the real ICS API, Producer/Type conflation
-- finding, closed: ICS's own real Information Producer
-- (producer_registration_info) is a separate first-class entity from
-- Information Type, in a genuine many-to-many relationship
-- (consumer_information_type.no_of_producers) — dme_type used to
-- conflate the two, making a second producer for the same type
-- structurally impossible.
CREATE TABLE dme_producer (
  producer_id                  TEXT PRIMARY KEY,
  producer_health_callback_url  TEXT NOT NULL,     -- ADOPT from ICS (repo inventory)
  job_callback_url                 TEXT NOT NULL   -- NEW section 5: ICS's own InfoProducer.jobCallbackUrl
);

CREATE TABLE dme_type (
  dme_type_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  namespace                     TEXT NOT NULL,
  name                           TEXT NOT NULL CHECK (name NOT LIKE '%:%'),
  version                         TEXT NOT NULL,
  type_name                        TEXT NOT NULL,
  data_production_schema               JSONB NOT NULL,
  collection_spec                        JSONB,               -- above-spec addition, kept deliberately (section 3.4)
  source_domain                                 TEXT CHECK (source_domain IN ('LIVE_RAN','DIGITAL_TWIN')),  -- Wave 3: docs/ARCHITECTURE.md (DME)
  source_context                                  JSONB,
  UNIQUE (namespace, name, version)
);

CREATE TABLE dme_producer_type (  -- the real many-to-many join, see dme_producer's own comment above
  producer_id  TEXT NOT NULL REFERENCES dme_producer(producer_id) ON DELETE CASCADE,
  dme_type_id   UUID NOT NULL REFERENCES dme_type(dme_type_id) ON DELETE CASCADE,
  PRIMARY KEY (producer_id, dme_type_id)
);

CREATE TABLE dme_type_subscription (  -- NEW section 5: ICS's own /info-type-subscription
  subscription_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  notification_destination   TEXT NOT NULL,
  owner                         TEXT NOT NULL
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
  dme_type_id           UUID NOT NULL REFERENCES dme_type(dme_type_id) ON DELETE CASCADE,  -- NEW section 5: matches dme_delivery_schema's own already-cascading FK
  production_job_definition JSONB,
  data_delivery_method  TEXT NOT NULL CHECK (data_delivery_method IN ('PULL_HTTP','PUSH_HTTP','STREAMING_KAFKA')),
  delivery_details       JSONB,
  consumer_id             TEXT NOT NULL,   -- rAppId, or 'DME_FRAMEWORK' (section 3.7)
  status                    TEXT NOT NULL DEFAULT 'PENDING',
  lifecycle_stage             TEXT CHECK (lifecycle_stage IN ('TRAINING','TESTING','EMULATION','INFERENCE','CLOSED_LOOP_FEEDBACK'))  -- Wave 3: docs/ARCHITECTURE.md (DME)
);

CREATE TABLE data_offer (
  offer_id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dme_type_id                        UUID NOT NULL REFERENCES dme_type(dme_type_id) ON DELETE CASCADE,  -- NEW section 5: matches dme_delivery_schema's own already-cascading FK
  data_delivery_methods_offered       TEXT[] NOT NULL,
  data_delivery_method_committed      TEXT,
  data_availability_notification_uri  TEXT,   -- REVERSED direction — section 3.5
  data_offer_termination_notification_uri TEXT NOT NULL
);

-- Wave 3 (AI Platform Service Decomposition) — docs/ARCHITECTURE.md (DME).
-- DME's real data-plane store: a producer's actual payload, ingested
-- against its own DataJob and fetched back by either an rApp or MDAF.
CREATE TABLE data_record (
  record_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_job_id    UUID NOT NULL REFERENCES data_job(data_job_id) ON DELETE CASCADE,
  payload           JSONB NOT NULL,
  produced_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- DME's O1 action-mediation audit trail: what an rApp's AI/ML decision
-- asked for. ran-nf-oam's own write_config_job/write_config_sub_change
-- (forwarded_job_id below) remain the record of what NETCONF actually did.
CREATE TABLE dme_action_record (
  action_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requested_by         TEXT NOT NULL,
  managed_element_ref     TEXT NOT NULL,
  class_name                 TEXT,    -- real ProvMnS IOC name where known, e.g. GNBDUFunction/NRCellDU
  changes                       JSONB NOT NULL,
  source_context                   JSONB,
  forwarded_job_id                    UUID,
  status                                 TEXT NOT NULL DEFAULT 'FORWARDED',
  correlation_id                            TEXT,  -- Wave 10.1 (W10-23): X-Correlation-ID of the causing request
  created_at                                TIMESTAMPTZ NOT NULL DEFAULT now()
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
  integrity_hash                           TEXT,
  ai_capabilities                            JSONB,     -- Wave 1: optional manifest.yaml/capabilities.yaml declaration
  descriptor_id                                TEXT,     -- real ASD schema field, grounded against nonrtric-plt-rappmanager's own sample CSARs
  descriptor_invariant_id                      TEXT,
  descriptor_version                           TEXT,
  schema_version                               TEXT,
  sme_declarations                             JSONB     -- real CSAR-bundled Files/Sme/providers+serviceapis, registered per-instance at bootstrap-complete
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
                                 CHECK (state IN ('DEPLOYING','RUNNING','UPGRADING','UNDEPLOYED','FAULTED')),
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
  upgrade_timeout_seconds                 INTEGER NOT NULL DEFAULT 300,  -- section 6: confirmed default (HISTORY.md §1)
  -- Also missing from this table until this pass, for the same reason:
  -- both are read/written by rapp-mgmt/app/upgrade.py and main.py but
  -- SQLite's unit tests build their schema from the ORM models
  -- directly, never from this file, so the gap went uncaught.
  pending_upgrade_instance_id             UUID REFERENCES rapp_instance(instance_id),
  package_usage_registration_id             UUID REFERENCES package_usage_registration(id),
  sme_service_ids                              JSONB,     -- SME serviceId(s) this instance registered at bootstrap-complete; deregistered on TERMINATE/CRASH
  -- HISTORY.md OI-6.3 — rApp Autonomy Modes: fixed at onboarding,
  -- defaults to SHADOW (no enforcement) for every existing caller.
  autonomy_mode                                   TEXT NOT NULL DEFAULT 'SHADOW'
                                                     CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  region_scope                                        JSONB,     -- AUTONOMOUS's own pre-configured RAN node/cell/slice scope
  -- OI-2-terminate-workload: outcome of the most recent best-effort
  -- teardown (NFO terminate, usage/stop) this row performed or inherited
  -- through an upgrade commit/rollback — {instanceId, reason, nfoTerminate, usageStop, at}.
  last_teardown                                         JSONB,
  -- OI-1-sa-rollback: on a rollback's replacement, the UPGRADE version it undoes.
  rollback_of_version_id                                  UUID
);

-- OI-1-sa-rollback: rApp Management's version history, one row per committed
-- upgrade or rollback. Instance ids are bare references: the retired row is
-- deleted on commit and the history outlives it. The previous_* columns are
-- what the retired instance ran, so a rollback re-provisions exactly that.
CREATE TABLE rapp_instance_version (
  version_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id                UUID NOT NULL,
  previous_instance_id       UUID NOT NULL,
  package_id                 UUID NOT NULL,
  previous_package_id        UUID NOT NULL,
  previous_configuration     JSONB,
  previous_autonomy_mode     TEXT NOT NULL,
  previous_region_scope      JSONB,
  kind                       TEXT NOT NULL CHECK (kind IN ('UPGRADE','ROLLBACK')),
  rolled_back_by_version_id  UUID REFERENCES rapp_instance_version(version_id),
  committed_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX rapp_instance_version_instance_idx ON rapp_instance_version (instance_id);
CREATE INDEX rapp_instance_version_previous_idx ON rapp_instance_version (previous_instance_id);

CREATE TABLE rapp_fault_report (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- ON DELETE CASCADE: NEW section 5, delete_instance's cascade (matches
  -- dme_delivery_schema's own already-cascading FK) — this table has no
  -- meaning once its rapp_instance row is gone.
  instance_id    UUID NOT NULL REFERENCES rapp_instance(instance_id) ON DELETE CASCADE,
  severity         TEXT NOT NULL CHECK (severity IN ('critical','major','minor','warning')),
  description        TEXT,
  reported_at          TIMESTAMPTZ NOT NULL DEFAULT now()   -- NEW (GUI pass): GET /instances/{id}/faults orders on it
);

CREATE TABLE rapp_performance_report (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id    UUID NOT NULL REFERENCES rapp_instance(instance_id) ON DELETE CASCADE,  -- NEW section 5: delete_instance's cascade
  metrics          JSONB NOT NULL,
  reported_at        TIMESTAMPTZ NOT NULL DEFAULT now()   -- NEW (GUI pass): GET /instances/{id}/performance orders on it
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
  health_status     TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (health_status IN ('DISCOVERED','ACTIVE','DEGRADED','UNREACHABLE')),  -- DISCOVERED: the endpoint FSM's own starting state (register_o1_adaptor_endpoint); without it every registration failed this CHECK
  last_heartbeat_at TIMESTAMPTZ,
  -- Wave 9 (W9-01): MnS services this adaptor declares; NULL = its vendor's capability
  supported_services TEXT[],
  UNIQUE (managed_element_ref)
);

CREATE TABLE managed_entity (
  managed_element_ref     TEXT PRIMARY KEY,
  managed_function_ref    TEXT,
  entity_type               TEXT NOT NULL CHECK (entity_type IN ('O-CU-CP','O-CU-UP','O-DU','O-RU','Near-RT-RIC')),
  vendor_name                 TEXT,
  o1_protocol                   TEXT NOT NULL CHECK (o1_protocol IN ('RESTCONF','NETCONF')),
  o1_adaptor_endpoint_id           UUID REFERENCES o1_adaptor_endpoint(endpoint_id),
  -- Wave 9 (W9-06, D-5): per-cell guard attributes {cellId: {cellClass, sectorGroup, incidentZone, neighbourRefs}}
  cell_guards                        JSONB NOT NULL DEFAULT '{}'
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
  alarm_type                TEXT CHECK (alarm_type IN ('COMMUNICATIONS_ALARM','QUALITY_OF_SERVICE_ALARM','PROCESSING_ERROR_ALARM','EQUIPMENT_ALARM','ENVIRONMENTAL_ALARM','INTEGRITY_VIOLATION','OPERATIONAL_VIOLATION','PHYSICAL_VIOLATION','SECURITY_SERVICE_OR_MECHANISM_VIOLATION','TIME_DOMAIN_VIOLATION','OTHER')),
  cleared_at                 TIMESTAMPTZ,
  clear_user_id                TEXT,
  ack_user_id                  TEXT,
  changed_at                    TIMESTAMPTZ
);
CREATE INDEX idx_alarm_correlation ON alarm (correlation_group) WHERE correlation_group IS NOT NULL;
CREATE INDEX idx_alarm_me ON alarm (managed_element_ref);

CREATE TABLE cm_schema_cache (
  schema_name  TEXT NOT NULL,
  revision     TEXT NOT NULL DEFAULT '',
  location     TEXT NOT NULL,
  type         TEXT NOT NULL DEFAULT 'YANG',
  cached_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  descriptor   JSONB,  -- Wave 9 (W9-02): {"classes": {IOC: {attribute: {type, enum?}}}}
  PRIMARY KEY (schema_name, revision)
);

-- Wave 9 (W9-01/W9-04): the per-vendor Capability Registry
-- (docs/ARCHITECTURE.md)
CREATE TABLE vendor_capability (
  vendor_name            TEXT PRIMARY KEY,
  supported_services     TEXT[] NOT NULL,
  conformance_mode       TEXT NOT NULL DEFAULT 'SPEC' CHECK (conformance_mode IN ('OWN','SPEC','COMBINED')),
  supported_vendor_modes TEXT[] NOT NULL,
  schema_name            TEXT,
  schema_revision        TEXT,
  spec_schema_name       TEXT,
  spec_schema_revision   TEXT,
  discovery_uri          TEXT,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
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
  -- HISTORY.md §7 item 3: RFC 6241 section 7.2's real edit-config
  -- operation attribute, previously entirely absent from this model.
  operation          TEXT NOT NULL DEFAULT 'merge' CHECK (operation IN ('merge','replace','create','delete','remove')),
  status             TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPLIED','REJECTED')),
  rejection_reason   TEXT,
  attempts           INTEGER NOT NULL DEFAULT 0  -- Wave 10.1 (W10-19): edit-config attempts, retries included
);

CREATE TABLE pm_subscription (
  subscription_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  managed_element_ref  TEXT NOT NULL REFERENCES managed_entity(managed_element_ref),
  counter_type          TEXT NOT NULL,
  delivery_method         TEXT NOT NULL CHECK (delivery_method IN ('pull','push','stream')),
  southbound_engine         TEXT NOT NULL CHECK (southbound_engine IN ('ProvMnS','PMJobControl','FileDataReporting','StreamingDataReporting')),
  -- HISTORY.md §7 item 4: TS28550_PerfMeasJobCtrlMnS.yaml's granularityPeriod
  -- (the sampling interval, in seconds), previously absent entirely.
  granularity_period      INTEGER
);

-- HISTORY.md OI-6.7: FM's own analog of pm_subscription — RAN NF
-- OAM registering itself as a DME producer for alarm/fault visibility,
-- mirroring subscribe_pm's own PMCounters.{counter_type} registration.
CREATE TABLE fm_subscription (
  subscription_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  managed_element_ref  TEXT NOT NULL REFERENCES managed_entity(managed_element_ref),
  delivery_method         TEXT NOT NULL CHECK (delivery_method IN ('pull','push','stream')),
  southbound_engine         TEXT NOT NULL CHECK (southbound_engine IN ('FaultMnS','StreamingDataReporting'))
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

-- NEW section 5: Service Registry and Supervision (the reference's own
-- pms-api-v3.json /services routes). service_id is caller-supplied, the
-- same identity space as a1_policy.creator_id.
CREATE TABLE a1_service_registration (
  service_id                  TEXT PRIMARY KEY,
  callback_url                  TEXT,
  keep_alive_interval_seconds     INTEGER NOT NULL DEFAULT 0,  -- 0 == supervision disabled
  last_activity_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- A1TrainingCapability: DORMANT (section 0 of the A1 Related LLD) — no table until
-- a future scope decision explicitly reaches into A1AP. Do not create this table.

-- ============================================================
-- RAN & Platform Integration: NFO  (NFO+FOCOM LLD sections 2, 5)
-- ============================================================

CREATE TABLE nf_deployment_descriptor (
  nf_deployment_descriptor_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Wave 2 (AI Platform Service Decomposition): nullable since this pass —
  -- AIMgF's own Runtime Lifecycle now creates a descriptor per model
  -- runtime directly (docs/ARCHITECTURE.md (AIMgF)), and a model
  -- runtime has no onboarded ApplicationPackage behind it. Every
  -- package-derived descriptor (Onboarding's own flow, unchanged) still
  -- always sets it.
  package_id                    UUID REFERENCES application_package(package_id),
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
  -- HISTORY.md §7 item 8: ORAN.O2ims.Inventory.yaml names this field
  -- `callback`, not this build's own invented `callback_uri`.
  callback          TEXT NOT NULL,
  -- HISTORY.md §7 item 8: consumer-provided tracking id, entirely absent.
  consumer_subscription_id  TEXT,
  resource_type_id   TEXT   -- optional filter; unset matches every resource type
);

CREATE TABLE resource_type (
  resource_type_id  TEXT PRIMARY KEY,   -- literal ID, not a UUID (Phase 1's degenerate topology is fixed, not generated)
  name               TEXT NOT NULL,
  description         TEXT,
  vendor               TEXT,
  model                 TEXT,
  version               TEXT,
  -- HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml's ResourceType
  -- requires these five fields, entirely absent before.
  alarm_dictionary_id       TEXT,
  performance_dictionary_id  TEXT,
  resource_kind                TEXT CHECK (resource_kind IN ('UNDEFINED','PHYSICAL','LOGICAL')),
  resource_class                TEXT CHECK (resource_class IN ('UNDEFINED','COMPUTE','NETWORKING','STORAGE')),
  extensions                     JSONB
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
  description           TEXT,
  -- HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml's Resource requires
  -- these three, entirely absent before.
  global_asset_id         TEXT,
  tags                      TEXT[],
  groups                     TEXT[]
);

CREATE TABLE deployment_manager (
  deployment_manager_id  TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  description                TEXT,
  o_cloud_id                  TEXT NOT NULL,
  service_uri                  TEXT,
  -- HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml's DeploymentManager
  -- requires these three, entirely absent before.
  supported_locations             TEXT[],
  capabilities                     JSONB,
  capacity                          JSONB
);

-- ============================================================
-- AI/ML Content: AI/ML Workflow  (AI/ML Workflow LLD sections 4, 6)
-- ============================================================

-- Wave 4 — TS 28.105 MLModelRepository IOC (docs/ROADMAP.md D-9).
CREATE TABLE ml_model_repository (
  ml_model_repository_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label               TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_model_coordination_group (
  group_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  group_type                    TEXT NOT NULL DEFAULT 'SHARED_MODEL' CHECK (group_type IN ('SHARED_MODEL','JOINT_TRAINING')),
  member_model_ids                UUID[] NOT NULL CHECK (array_length(member_model_ids, 1) >= 2),
  member_use_cases                  TEXT[],
  shared_feature_pipeline_ref         TEXT,
  retrain_propagation                   TEXT NOT NULL DEFAULT 'ANY_MEMBER_TRIGGERS'
                                           CHECK (retrain_propagation IN ('ANY_MEMBER_TRIGGERS','MAJORITY_TRIGGERS','WEIGHTED_TRIGGERS')),
  ml_model_repository_id                  UUID REFERENCES ml_model_repository(ml_model_repository_id) ON DELETE SET NULL
);

CREATE TABLE aiml_model (
  model_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id       TEXT NOT NULL,
  model_type              TEXT NOT NULL,
  version                   TEXT NOT NULL,
  -- Wave 2 (AI Platform Service Decomposition): state/training_job_id/
  -- cleared_node_groups moved to AIMgF's own model_lifecycle table below
  -- (docs/ARCHITECTURE.md (AIMgF)) — MLMR is model truth, not
  -- lifecycle truth, and Wave 1 only left them here as a structural
  -- shortcut pending this exact move.
  training_data_lineage             JSONB,
  integrity_hash                       TEXT,
  artifact_location                       TEXT,
  required_resource_type_id                 TEXT,
  description                                    TEXT,   -- NEW section 5: ModelRelatedInformation.description
  author                                            TEXT, -- NEW section 5: Metadata.author
  owner                                               TEXT, -- NEW section 5: Metadata.owner
  input_data_type                                       TEXT, -- NEW section 5: ModelInformation.inputDataType
  output_data_type                                         TEXT, -- NEW section 5: ModelInformation.outputDataType
  target_environments                                          JSONB, -- NEW section 5: ModelInformation.targetEnvironment[]
  domain                                                          TEXT CHECK (domain IN ('SPEECH_RECOGNITION','IMAGE_RECOGNITION','IMAGE_PROCESSING','LOCATION_PREDICTION','CUSTOM')),  -- Wave 3: TS29482_MLR_MLModelManagement.yaml
  custom_domain                                                     TEXT,
  vendors                                                             TEXT[],
  -- Wave 4 — TS 28.105 MLModel IOC attributes (spec-shaped JSON for
  -- complex datatypes); read-only cross-refs are AIMgF's, joined at read.
  aiml_inference_name               TEXT,
  expected_run_time_context          JSONB,
  training_context                    JSONB,
  run_time_context                     JSONB,
  supported_performance_indicators      JSONB,
  ml_capabilities_info_list              JSONB,
  inference_scope                         JSONB,
  retraining_events_monitor_ref            TEXT,
  source_trained_ml_model_ref               UUID,
  ml_model_repository_id                     UUID REFERENCES ml_model_repository(ml_model_repository_id) ON DELETE SET NULL,
  UNIQUE (model_type, version)                           -- NEW section 5: the reference's own (modelName, modelVersion) uniqueness
);

CREATE TABLE model_artifact (
  artifact_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  artifact_version  INTEGER NOT NULL CHECK (artifact_version >= 1),
  filename          TEXT NOT NULL,
  content           BYTEA NOT NULL,
  uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  size_bytes        INTEGER NOT NULL  -- Wave 3: TS29482_MLR_MLModelManagement.yaml's MLModel.mlModelSize
);

-- Wave 4 — TS 28.105 AI/ML NRM function/request containers that the job
-- tables below reference (aimgf/app/models.py).
CREATE TABLE ml_training_function (
  ml_training_function_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                      TEXT,
  supported_learning_technology    JSONB,
  fl_participation_info             JSONB,
  ml_knowledge                       JSONB,
  ml_training_type                    TEXT CHECK (ml_training_type IN
    ('INITIAL_TRAINING','PRE_SPECIALISED_TRAINING','RE_TRAINING','FINE_TUNING')),
  ml_model_repository_ref              UUID REFERENCES ml_model_repository(ml_model_repository_id) ON DELETE SET NULL,
  created_at                            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_testing_function (
  ml_testing_function_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label               TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE aiml_inference_function (
  aiml_inference_function_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                   TEXT,
  aiml_inference_name            TEXT,
  activation_status                TEXT NOT NULL DEFAULT 'DEACTIVATED' CHECK (activation_status IN ('ACTIVATED','DEACTIVATED')),
  managed_activation_scope           JSONB,
  ml_model_refs                        JSONB NOT NULL DEFAULT '[]',
  created_at                             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE aiml_inference_emulation_function (
  aiml_inference_emulation_function_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                             TEXT,
  created_at                              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_update_function (
  ml_update_function_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                     TEXT,
  avail_ml_capability_report      JSONB,
  ml_model_refs                     JSONB NOT NULL DEFAULT '[]',
  created_at                          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_update_request (
  ml_update_request_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ml_update_function_id          UUID REFERENCES ml_update_function(ml_update_function_id) ON DELETE SET NULL,
  performance_gain_threshold      JSONB,
  new_capability_version_ids        JSONB,
  update_time_deadline                JSONB,
  request_status                        TEXT NOT NULL DEFAULT 'NOT_STARTED'
    CHECK (request_status IN ('NOT_STARTED','IN_PROGRESS','SUSPENDED','FINISHED','CANCELLED','CANCELLING')),
  ml_update_reporting_period              JSONB,
  cancel_request                            BOOLEAN NOT NULL DEFAULT false,
  suspend_request                             BOOLEAN NOT NULL DEFAULT false,
  ml_model_refs                                 JSONB NOT NULL,
  created_at                                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_update_process (
  ml_update_process_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ml_update_request_id      UUID NOT NULL REFERENCES ml_update_request(ml_update_request_id) ON DELETE CASCADE,
  status                      TEXT NOT NULL DEFAULT 'RUNNING',
  progress_percentage          INTEGER NOT NULL DEFAULT 0 CHECK (progress_percentage BETWEEN 0 AND 100),
  progress_state_info            TEXT,
  result_state_info                TEXT,
  cancel_process                     BOOLEAN NOT NULL DEFAULT false,
  suspend_process                      BOOLEAN NOT NULL DEFAULT false,
  ml_model_refs                          JSONB NOT NULL
);

CREATE TABLE training_job (
  training_job_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id                     UUID REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  model_coordination_group_id  UUID REFERENCES ml_model_coordination_group(group_id),
  producer_type                  TEXT NOT NULL DEFAULT 'rApp' CHECK (producer_type = 'rApp'),
  producer_id                      TEXT NOT NULL,
  required_data                       JSONB,
  -- HISTORY.md OI-6.4: a separate, explicitly-typed reference to
  -- the real DME DataJob(s) training actually consumed, additive to
  -- required_data's own opaque blob — validated against DME on request.
  dme_data_job_ids                    UUID[] NOT NULL DEFAULT '{}',
  validation_criteria                   JSONB,
  status                                   TEXT NOT NULL DEFAULT 'NOT_STARTED'
                                             CHECK (status IN ('NOT_STARTED','IN_PROGRESS','SUSPENDED','FINISHED','FAILED','CANCELLED')),  -- HISTORY.md §7's requestStatus vocabulary finding, closed (FAILED is this build's own honest addition beyond the real spec; CANCELLING is never produced)
  notification_uri                          TEXT,
  run_id                                       TEXT,  -- NEW section 5: trainingmgr's own TrainingJob.run_id
  training_dataset                                TEXT, -- NEW section 5
  validation_dataset                                 TEXT, -- NEW section 5
  consumer_rapp_id                                      TEXT, -- NEW section 5
  producer_rapp_id                                         TEXT, -- NEW section 5
  model_metrics                                               JSONB, -- NEW section 5: writeback target, POST .../model-metrics
  ml_training_type                                               TEXT CHECK (ml_training_type IN
    ('INITIAL_TRAINING','PRE_SPECIALISED_TRAINING','RE_TRAINING','FINE_TUNING')), -- HISTORY.md §7: TS28.105's own real enum
  -- HISTORY.md OI-6.5: where the training run's real output
  -- artifact lives — a DME DmeTypeId reference, set on completion.
  outcome_artifact_dme_type_id                                     UUID,
  -- HISTORY.md OI-6.2: a real NFO-backed execution runtime for
  -- this training run — same bare-UUID cross-module-reference shape as
  -- model_lifecycle's own pair. Set on request, cleared on completion.
  nf_deployment_descriptor_id                                         UUID REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id),
  nf_deployment_id                                                       UUID,
  -- Wave 4 — TS 28.105 MLTrainingRequest: a training_job row IS the
  -- spec's MLTrainingRequest; its remaining attributes live here.
  ml_training_function_id               UUID REFERENCES ml_training_function(ml_training_function_id) ON DELETE SET NULL,
  aiml_inference_name                    TEXT,
  fl_requirement                          JSONB,
  candidate_training_data_source           JSONB,
  training_data_quality_score               DOUBLE PRECISION,
  training_request_source                    TEXT,
  performance_requirements                    JSONB,
  rl_requirement                               JSONB,
  cancel_request                                BOOLEAN NOT NULL DEFAULT false,
  suspend_request                                 BOOLEAN NOT NULL DEFAULT false,
  training_data_statistical_properties             JSONB,
  distributed_training_expectation                  JSONB,
  ml_knowledge_name                                  TEXT,
  expected_inference_scope                            JSONB,
  clustering_info                                      JSONB,
  ml_update_process_id                                  UUID REFERENCES ml_update_process(ml_update_process_id) ON DELETE SET NULL,
  -- Wave 7 (W7-03/W7-04): the runtime profile the run was sized with and
  -- its execution deadline (started_at + timeout_seconds).
  runtime_profile                          JSONB,
  started_at                                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeout_seconds                              INTEGER CHECK (timeout_seconds > 0),
  CONSTRAINT exactly_one_target CHECK (
    (model_id IS NOT NULL AND model_coordination_group_id IS NULL)
    OR (model_id IS NULL AND model_coordination_group_id IS NOT NULL)
  ),
  -- OI-5-aiml-trainingjob-steps: the furthest step the run's execution
  -- runtime reported; each step's status is derived from it and `status`.
  current_step                     TEXT NOT NULL DEFAULT 'DATA_EXTRACTION'
                                     CHECK (current_step IN ('DATA_EXTRACTION','TRAINING','TRAINED_MODEL'))
);

CREATE TABLE model_change_subscription (
  subscription_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  consumer_id         TEXT NOT NULL
);

CREATE TABLE mlmf_subscription (
  subscription_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id          UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  metric_types      TEXT[] NOT NULL,
  dme_type_id       UUID NOT NULL REFERENCES dme_type(dme_type_id),
  guard_kpi_floor   JSONB,
  notification_destination TEXT  -- HISTORY.md §7's MLMFSubscription finding, closed
);

CREATE TABLE performance_report (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id  UUID NOT NULL REFERENCES mlmf_subscription(subscription_id) ON DELETE CASCADE,
  metrics          JSONB NOT NULL,
  breached_floor   BOOLEAN NOT NULL DEFAULT false,
  reported_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_perf_breach ON performance_report (subscription_id) WHERE breached_floor = true;

CREATE TABLE inference_job (
  inference_job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id         UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  status           TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
  notification_destination TEXT,
  -- HISTORY.md OI-6.2: a read-only reference to the model's own
  -- already-live serving deployment (model_lifecycle.nf_deployment_id,
  -- real since Wave 2) — not a new NFO deployment of this job's own; see
  -- app/models.py's InferenceJob docstring for why.
  nf_deployment_id UUID,
  -- Wave 4 — the TS 28.105 AIMLInferenceFunction it ran on, and its consumer.
  aiml_inference_function_id UUID REFERENCES aiml_inference_function(aiml_inference_function_id) ON DELETE SET NULL,
  consumer_ref               TEXT,
  -- Wave 7 (W7-04): inference deadline (default 5 s).
  started_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeout_seconds            INTEGER CHECK (timeout_seconds > 0)
);

-- Wave 2 (AI Platform Service Decomposition): the full eight-aggregate
-- domain model docs/ARCHITECTURE.md's AIMgF Wave 1 note promised —
-- AIMgF's own lifecycle-state truth (model_lifecycle) plus the
-- validation/emulation/governance/audit aggregates Wave 1 didn't need yet.

CREATE TABLE model_lifecycle (
  model_id                     UUID PRIMARY KEY REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  model_lifecycle_state         TEXT NOT NULL DEFAULT 'REGISTERED' CHECK (model_lifecycle_state IN (
    'REGISTERED','TRAINING','TRAINED','VALIDATING','VALIDATED','EMULATING','EMULATED',
    'PENDING_APPROVAL','APPROVED','CERTIFIED','PROMOTED','DEPRECATED','RETIRED','FAILED'
  )),
  runtime_lifecycle_state         TEXT NOT NULL DEFAULT 'NOT_DEPLOYED' CHECK (runtime_lifecycle_state IN (
    'NOT_DEPLOYED','DEPLOYMENT_REQUESTED','DEPLOYED','ACTIVATING','ACTIVE','SCALING','TERMINATING','TERMINATED'
  )),
  training_job_id                   UUID,
  cleared_node_groups                  TEXT[],
  nf_deployment_descriptor_id             UUID REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id),
  nf_deployment_id                          UUID, -- -> nf_deployment (NFO) — bare UUID, cross-module reference
  -- HISTORY.md OI-6.1: operator gate on Training->Validation->
  -- Emulation. Reset to false whenever CREATE_TRAINING fires.
  training_approved                           BOOLEAN NOT NULL DEFAULT false,
  validation_approved                           BOOLEAN NOT NULL DEFAULT false,
  -- Wave 7 (W7-03): the INFERENCE runtime profile the serving runtime was deployed with.
  runtime_profile                                 JSONB
);

CREATE TABLE validation_job (
  validation_job_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Wave 4 — TS 28.105 MLTestingRequest: model- or coordination-group-
  -- targeted, exactly one (same shape as training_job's own).
  model_id            UUID REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  model_coordination_group_id UUID REFERENCES ml_model_coordination_group(group_id),
  training_job_id       UUID REFERENCES training_job(training_job_id),
  producer_id             TEXT NOT NULL,
  validation_criteria       JSONB,
  status                      TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','SUSPENDED','COMPLETED','FAILED','CANCELLED')),
  metrics                       JSONB,
  -- HISTORY.md OI-6.5: same additive pair training_job gained.
  notification_uri                TEXT,
  outcome_artifact_dme_type_id       UUID,
  -- HISTORY.md OI-6.2: same pair as training_job's own.
  nf_deployment_descriptor_id          UUID REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id),
  nf_deployment_id                        UUID,
  ml_testing_function_id                     UUID REFERENCES ml_testing_function(ml_testing_function_id) ON DELETE SET NULL,
  cancel_request                                BOOLEAN NOT NULL DEFAULT false,
  suspend_request                                 BOOLEAN NOT NULL DEFAULT false,
  -- Wave 7 (W7-03/W7-04): the runtime profile the run was sized with and
  -- its execution deadline (started_at + timeout_seconds).
  runtime_profile                          JSONB,
  started_at                                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeout_seconds                              INTEGER CHECK (timeout_seconds > 0),
  CONSTRAINT validation_exactly_one_target CHECK (
    (model_id IS NOT NULL AND model_coordination_group_id IS NULL)
    OR (model_id IS NULL AND model_coordination_group_id IS NOT NULL)
  )
);

CREATE TABLE emulation_job (
  emulation_job_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id           UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  producer_id          TEXT NOT NULL,
  emulation_criteria     JSONB,
  status                   TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETED','FAILED','CANCELLED')),
  metrics                    JSONB,
  -- HISTORY.md OI-6.5: same pair as validation_job's own.
  notification_uri             TEXT,
  outcome_artifact_dme_type_id    UUID,
  -- HISTORY.md OI-6.2: same pair as training_job's own.
  nf_deployment_descriptor_id        UUID REFERENCES nf_deployment_descriptor(nf_deployment_descriptor_id),
  nf_deployment_id                      UUID,
  aiml_inference_emulation_function_id     UUID REFERENCES aiml_inference_emulation_function(aiml_inference_emulation_function_id) ON DELETE SET NULL,
  -- Wave 7 (W7-03/W7-04): the runtime profile the run was sized with and
  -- its execution deadline (started_at + timeout_seconds).
  runtime_profile                          JSONB,
  started_at                                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeout_seconds                              INTEGER CHECK (timeout_seconds > 0)
);

-- Wave 4 — TS 28.105 process/report IOCs (aimgf/app/models.py).
CREATE TABLE ml_training_process (
  ml_training_process_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  training_job_id           UUID NOT NULL UNIQUE REFERENCES training_job(training_job_id) ON DELETE CASCADE,
  priority                    INTEGER NOT NULL DEFAULT 0,
  termination_conditions        TEXT,
  status                          TEXT NOT NULL DEFAULT 'RUNNING',
  progress_percentage               INTEGER NOT NULL DEFAULT 0 CHECK (progress_percentage BETWEEN 0 AND 100),
  progress_state_info                 TEXT,
  result_state_info                     TEXT,
  cancel_process                          BOOLEAN NOT NULL DEFAULT false,
  suspend_process                           BOOLEAN NOT NULL DEFAULT false,
  participating_fl_client_refs                JSONB
);

CREATE TABLE ml_training_report (
  ml_training_report_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  training_job_id                       UUID NOT NULL REFERENCES training_job(training_job_id) ON DELETE CASCADE,
  ml_training_function_id                UUID REFERENCES ml_training_function(ml_training_function_id) ON DELETE SET NULL,
  used_consumer_training_data             JSONB,
  model_confidence_indication              INTEGER,
  model_performance_training                JSONB,
  model_performance_validation               JSONB,
  data_ratio_training_and_validation          INTEGER,
  are_new_training_data_used                   BOOLEAN,
  fl_report_per_client                          JSONB,
  last_training_report_id                        UUID,
  ml_model_generated_ref                          UUID,
  ml_model_coordination_group_generated_ref        UUID,
  created_at                                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_testing_report (
  ml_testing_report_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  validation_job_id          UUID NOT NULL REFERENCES validation_job(validation_job_id) ON DELETE CASCADE,
  ml_testing_function_id      UUID REFERENCES ml_testing_function(ml_testing_function_id) ON DELETE SET NULL,
  model_performance_testing     JSONB,
  ml_testing_result               TEXT NOT NULL CHECK (ml_testing_result IN ('PASSED','FAILED')),
  created_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE aiml_inference_report (
  aiml_inference_report_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aiml_inference_function_id             UUID REFERENCES aiml_inference_function(aiml_inference_function_id) ON DELETE CASCADE,
  aiml_inference_emulation_function_id    UUID REFERENCES aiml_inference_emulation_function(aiml_inference_emulation_function_id) ON DELETE CASCADE,
  inference_job_id                         UUID REFERENCES inference_job(inference_job_id) ON DELETE SET NULL,
  emulation_job_id                          UUID REFERENCES emulation_job(emulation_job_id) ON DELETE SET NULL,
  inference_outputs                          JSONB NOT NULL DEFAULT '[]',
  potential_impact_info                       JSONB,
  ml_model_refs                                JSONB NOT NULL DEFAULT '[]',
  created_at                                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_model_loading_policy (
  ml_model_loading_policy_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aiml_inference_function_id    UUID NOT NULL REFERENCES aiml_inference_function(aiml_inference_function_id) ON DELETE CASCADE,
  aiml_inference_name             TEXT,
  policy_for_loading                JSONB,
  ml_model_refs                       JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE ml_model_loading_request (
  ml_model_loading_request_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aiml_inference_function_id    UUID NOT NULL REFERENCES aiml_inference_function(aiml_inference_function_id) ON DELETE CASCADE,
  request_status                  TEXT NOT NULL DEFAULT 'NOT_STARTED'
    CHECK (request_status IN ('NOT_STARTED','IN_PROGRESS','SUSPENDED','FINISHED','CANCELLED','CANCELLING')),
  cancel_request                    BOOLEAN NOT NULL DEFAULT false,
  suspend_request                     BOOLEAN NOT NULL DEFAULT false,
  ml_model_to_load_refs                 JSONB NOT NULL,
  created_at                              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ml_model_loading_process (
  ml_model_loading_process_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aiml_inference_function_id    UUID NOT NULL REFERENCES aiml_inference_function(aiml_inference_function_id) ON DELETE CASCADE,
  status                          TEXT NOT NULL DEFAULT 'RUNNING',
  progress_percentage               INTEGER NOT NULL DEFAULT 0 CHECK (progress_percentage BETWEEN 0 AND 100),
  progress_state_info                 TEXT,
  result_state_info                     TEXT,
  cancel_process                          BOOLEAN NOT NULL DEFAULT false,
  suspend_process                           BOOLEAN NOT NULL DEFAULT false,
  loading_request_refs                        JSONB NOT NULL DEFAULT '[]',
  loading_policy_refs                           JSONB NOT NULL DEFAULT '[]',
  loaded_ml_model_refs                            JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE ml_update_report (
  ml_update_report_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ml_update_process_id      UUID NOT NULL REFERENCES ml_update_process(ml_update_process_id) ON DELETE CASCADE,
  updated_ml_capability       JSONB,
  ml_model_refs                 JSONB NOT NULL,
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE certification_record (
  certification_record_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id                  UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  decision                    TEXT NOT NULL CHECK (decision IN (
    'SUBMIT_FOR_APPROVAL','APPROVE','REJECT','CERTIFY','PROMOTE','ROLLBACK',
    'APPROVE_TRAINING','APPROVE_VALIDATION'  -- HISTORY.md OI-6.1's own operator-gate decisions
  )),
  decided_by                     TEXT NOT NULL,
  rationale                        TEXT,
  decided_at                         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lifecycle_transition (
  lifecycle_transition_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id                  UUID NOT NULL REFERENCES aiml_model(model_id) ON DELETE CASCADE,
  fsm                         TEXT NOT NULL CHECK (fsm IN ('MODEL','RUNTIME')),
  from_state                    TEXT NOT NULL,
  to_state                        TEXT NOT NULL,
  event                             TEXT NOT NULL,
  occurred_at                         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NEW section 5: the reference's own FeatureGroup (aiml-fw-awmf-tm) — no
-- feature-group/feature-store concept existed at all before this pass.
CREATE TABLE feature_group (
  feature_group_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feature_group_name  TEXT NOT NULL UNIQUE,
  feature_list          TEXT NOT NULL,
  datalake_source         TEXT NOT NULL,
  host                      TEXT NOT NULL,
  port                        TEXT NOT NULL,
  bucket                        TEXT NOT NULL,
  token                           TEXT NOT NULL,
  db_org                            TEXT NOT NULL,
  measurement                         TEXT NOT NULL,
  enable_dme                            BOOLEAN NOT NULL DEFAULT false,
  measured_obj_class                       TEXT,
  dme_port                                   TEXT,
  source_name                                  TEXT,
  -- OI-5-aiml-featuregroup-dme: an enable_dme group's DME type and the data
  -- job created for it (bare refs: the job is DME's, terminated with the group).
  dme_type_id                                    UUID,
  dme_data_job_id                                  UUID
);

-- ============================================================
-- AI/ML Content: RAN Analytics  (RAN Analytics LLD section 4)
-- ============================================================

CREATE TABLE mdaf_producer (
  producer_id      TEXT NOT NULL,
  analytics_type    TEXT NOT NULL,
  dme_input_types    UUID[] NOT NULL,
  output_schema       JSONB NOT NULL,
  mda_type               TEXT,  -- HISTORY.md §7: TS28104's own real, closed MDAType enum — optional, additive
  PRIMARY KEY (producer_id, analytics_type)
);

-- Wave 5 — TS 28.104 MDAFunction / MDARequest (mdaf/app/models.py).
CREATE TABLE mda_function (
  mda_function_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                     TEXT,
  supported_mda_capabilities      JSONB NOT NULL DEFAULT '[]',
  supported_mda_domain              TEXT CHECK (supported_mda_domain IN ('CN','RAN','CROSS_DOMAIN')),
  ml_model_refs                       JSONB NOT NULL DEFAULT '[]',
  aiml_inference_function_refs          JSONB NOT NULL DEFAULT '[]',
  created_at                              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mda_request (
  mda_request_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mda_function_id               UUID REFERENCES mda_function(mda_function_id) ON DELETE SET NULL,
  requested_by                    TEXT,
  requested_mda_outputs             JSONB NOT NULL,
  reporting_method                    TEXT NOT NULL CHECK (reporting_method IN ('FILE','STREAMING','NOTIFICATION')),
  reporting_target                      TEXT,
  analytics_scope                         JSONB,
  start_time                                TIMESTAMPTZ,
  stop_time                                   TIMESTAMPTZ,
  recommendation_filter                         JSONB,
  performance_threshold_info                      JSONB,
  analysis_requirements                             JSONB,
  threshold_monitor_refs                              JSONB,
  threshold_state                                       JSONB,
  created_at                                              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mdaf_report (
  report_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  analytics_type      TEXT NOT NULL,
  scope                 JSONB,
  input_sources          UUID[] NOT NULL,
  output                  JSONB NOT NULL,
  generated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  subscriber_attribution    TEXT,
  -- Wave 5 — TS 28.104 MDAReport: typed mDAOutputs + this build's report kind.
  report_kind                 TEXT NOT NULL DEFAULT 'ANALYTICS' CHECK (report_kind IN ('ANALYTICS','PREDICTION','DRIFT')),
  mda_type                      TEXT,
  mda_outputs                     JSONB,
  mda_function_id                   UUID REFERENCES mda_function(mda_function_id) ON DELETE SET NULL,
  mda_request_id                      UUID REFERENCES mda_request(mda_request_id) ON DELETE SET NULL
);

CREATE TABLE mda_report_delivery (
  delivery_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_id          UUID NOT NULL REFERENCES mdaf_report(report_id) ON DELETE CASCADE,
  mda_request_id      UUID NOT NULL REFERENCES mda_request(mda_request_id) ON DELETE CASCADE,
  reporting_method      TEXT NOT NULL CHECK (reporting_method IN ('FILE','STREAMING','NOTIFICATION')),
  notified                BOOLEAN NOT NULL DEFAULT false,
  delivered_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mda_subscription (
  subscription_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  analytics_type     TEXT NOT NULL,
  scope                JSONB,
  requested_by          TEXT NOT NULL,
  notification_destination  TEXT,  -- NEW section 5: publish_report's actual delivery target
  threshold_info               JSONB,  -- Wave 3: TS28.104 ThresholdInfo list, HISTORY.md §7's MDAF section
  threshold_state                 JSONB
);

-- ============================================================
-- Governance & Assurance: Intent Service (formerly Policy Management & Info; Policy Mgmt LLD sections 1-4)
-- ============================================================

-- Declared before `intent` (below) — Wave 3's consumer-side-selection
-- redesign (HISTORY.md §7 / docs/ARCHITECTURE.md's Intent Service
-- own "Open item carried into Wave 3") gives `intent` a real FK onto
-- this table, so it must exist first.
CREATE TABLE intent_handling_function (
  rmih_id                        TEXT PRIMARY KEY,
  sme_service_id                  TEXT NOT NULL,
  intent_handling_scope             JSONB,
  intent_handling_capability_list     JSONB NOT NULL,
  notification_destination            TEXT NOT NULL,  -- Wave 3: renamed from notification_callback_uri, unified with every other subscription-shaped resource's own callback field
  -- Wave 6 — TS 28.312 IntentHandlingFunction attributes.
  supported_negotiation_functionalities JSONB,
  supported_utility_list                 JSONB
);

-- Wave 6 — TS 28.312 IntentUtilityFormula IOC.
CREATE TABLE intent_utility_formula (
  intent_utility_formula_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  utility_function_id         TEXT NOT NULL,
  utility_parameter_list       JSONB NOT NULL,
  utility_scale                 DOUBLE PRECISION NOT NULL DEFAULT 1,
  utility_offset                 DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE intent (
  intent_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_label                   TEXT,
  intent_expectations            JSONB NOT NULL,   -- Wave 6: strict TS 28.312 IntentExpectation list (family-validated)
  -- HISTORY.md §7 item 3: this column previously stored the invented
  -- top-level `intentType` matching field; now stores the spec's real
  -- IntentMgmtPurpose (a workflow-procedure enum, unrelated to matching).
  intent_mgmt_purpose               TEXT CHECK (intent_mgmt_purpose IN ('FEASIBILITYCHECK','FEASIBILITYCHECK_WITH_RECOMMENDATIONS','FULFILMENT_WITHOUT_NEGOTIATION','EXPLORATION','FULFILMENT_WITH_NEGOTIATION')),
  intent_admin_state                  TEXT NOT NULL DEFAULT 'ACTIVATED' CHECK (intent_admin_state IN ('ACTIVATED','DEACTIVATED')),
  intent_priority                       INTEGER NOT NULL DEFAULT 1 CHECK (intent_priority BETWEEN 1 AND 100),
  intent_preemption_capability             BOOLEAN NOT NULL DEFAULT false,
  rmio_id                                    TEXT NOT NULL,
  -- Wave 3: consumer-side RMIH selection — TS28.312's own NRM containment
  -- (IntentHandlingFunction *contains* Intent) means an Intent's real
  -- identity depends on the RMIH that owns it; ON DELETE CASCADE matches
  -- that containment literally (deregistering an RMIH really does end
  -- every Intent addressed to it, not just orphan a dangling reference),
  -- same house pattern as intent_report's own cascade below.
  rmih_id                                     TEXT NOT NULL REFERENCES intent_handling_function(rmih_id) ON DELETE CASCADE,
  -- Wave 6 — the remaining TS 28.312 Intent attributes.
  context_selectivity                    TEXT CHECK (context_selectivity IN ('ALL_OF','ONE_OF','ANY_OF')),
  consumer_satisfaction_index_threshold    INTEGER,
  expectation_selectivity                    TEXT CHECK (expectation_selectivity IN ('ALL_OF','ONE_OF','ANY_OF')),
  intent_contexts                              JSONB,
  intent_report_control                          JSONB,
  implicit_intent_index                            BOOLEAN NOT NULL DEFAULT false,
  guarantee_periods                                  JSONB,
  intent_handling_info                                 JSONB,
  intent_interpretation_assistance_info                  JSONB,
  intent_report_reference                                  UUID,  -- current intent_report (no FK: intent_report already points back here)
  intent_utility_formula_id                                  UUID REFERENCES intent_utility_formula(intent_utility_formula_id) ON DELETE SET NULL
);

CREATE TABLE intent_report (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- ON DELETE CASCADE: HISTORY.md §7 item 5's new DELETE /intents/{id} —
  -- matches this build's established cascade-delete-child pattern
  -- (rapp_instance, aiml_model, write_config_job, ...) rather than leaving
  -- an FK violation on the first real delete of an intent with reports.
  intent_id            UUID NOT NULL REFERENCES intent(intent_id) ON DELETE CASCADE,   -- was intent_reference (bare string) pre-LLD
  intent_fulfilment_report JSONB,
  intent_conflict_reports    JSONB,
  -- Wave 6 — the rest of TS 28.312's report kinds.
  intent_feasibility_check_report      JSONB,
  intent_exploration_report             JSONB,
  intent_utility_reports                 JSONB,
  intent_fulfilment_negotiation_report    JSONB,
  intent_decomposition_report              JSONB,
  last_updated_time            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HISTORY.md OI-6.3 — rApp Autonomy Modes: a real, queryable
-- record of each inference-driven dispatch decision, distinct from
-- intent itself since not every mode actually produces one.
CREATE TABLE autonomy_dispatch (
  dispatch_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id           UUID NOT NULL,   -- -> rapp_instance (rApp Mgmt) — bare UUID, cross-module reference
  model_id                UUID,          -- -> aiml_model (MLMR) — the triggering inference, if any
  autonomy_mode              TEXT NOT NULL CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  expectations                  JSONB NOT NULL,
  priority                        INTEGER NOT NULL DEFAULT 1 CHECK (priority BETWEEN 1 AND 100),
  rmih_id                            TEXT NOT NULL REFERENCES intent_handling_function(rmih_id) ON DELETE CASCADE,
  intent_mgmt_purpose                   TEXT CHECK (intent_mgmt_purpose IN ('FEASIBILITYCHECK','FEASIBILITYCHECK_WITH_RECOMMENDATIONS','FULFILMENT_WITHOUT_NEGOTIATION','EXPLORATION','FULFILMENT_WITH_NEGOTIATION')),
  intent_handling_scope                    TEXT,
  region_scope                                JSONB,
  status                                          TEXT NOT NULL CHECK (status IN ('AWAITING_SCOPE','DISPATCHED','SHADOWED','REJECTED')),
  intent_id                                          UUID REFERENCES intent(intent_id) ON DELETE SET NULL,
  notification_destination                              TEXT,
  created_at                                               TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Wave 8 (W8-08): an ASSIST dispatch the operator declined.
  rejected_by                                                 TEXT,
  rejection_reason                                              TEXT
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
  -- OI-1-sa-rollback: a monitor on one rApp instance. No FK: an upgrade
  -- supersedes (and deletes) the instance row; rApp Management resolves the
  -- id through its version history.
  target_rapp_instance_id            UUID,
  analytics_subscription_id           UUID REFERENCES mda_subscription(subscription_id),
  requirement_thresholds                 JSONB NOT NULL,
  CONSTRAINT one_target_only CHECK (
    (CASE WHEN target_order_id IS NULL THEN 0 ELSE 1 END
     + CASE WHEN target_coordination_group_id IS NULL THEN 0 ELSE 1 END
     + CASE WHEN target_rapp_instance_id IS NULL THEN 0 ELSE 1 END) <= 1
  )
);

-- Wave 8 (W8-07): the generic O1-CM intent handler's enactment record.
CREATE TABLE o1_cm_enactment (
  enactment_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  intent_id             UUID NOT NULL,   -- -> intent (Intent Service); bare: the record outlives a deleted intent
  status                  TEXT NOT NULL CHECK (status IN ('FULFILLED','NOT_FULFILLED')),
  actions                   JSONB NOT NULL,
  unsupported_targets         JSONB NOT NULL,
  intent_report_id              UUID,
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE remedial_action (
  action_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  monitor_id                UUID NOT NULL REFERENCES assurance_monitor(monitor_id),
  action_type                 TEXT NOT NULL CHECK (action_type IN ('CONFIG_CHANGE','SCALE','RECONNECT','ROLLBACK')),
  auto_executed                  BOOLEAN NOT NULL DEFAULT false,
  auto_execution_scope_config       TEXT,   -- ADMIN-ONLY to change, per REQ-CNFG-ADM pattern
  outcome                              TEXT CHECK (outcome IN ('RESOLVED','ESCALATED','FAILED'))
);

-- ============================================================
-- Wave 10.1: the EnergySaving reference rApp's own state
-- (samples/energy-saving-rapp/app/models.py). A real rApp keeps this in
-- its own store; this build runs one shared Postgres.
-- ============================================================
CREATE TABLE energy_saving_instance (
  instance_id               UUID PRIMARY KEY,           -- the rapp-mgmt instance it is bound to
  package_id                UUID,
  managed_element_ref       TEXT NOT NULL,
  cells                     JSONB NOT NULL,
  actuator                  TEXT NOT NULL DEFAULT 'ADMINISTRATIVE_STATE' CHECK (actuator IN ('ADMINISTRATIVE_STATE','ENERGY_SAVING_CONTROL')),
  autonomy_mode             TEXT NOT NULL CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  rmih_id                   TEXT NOT NULL DEFAULT 'sa-smos',
  operator_notification_uri TEXT,
  data_jobs                 JSONB NOT NULL DEFAULT '{}',
  model_id                  UUID,
  model_version             TEXT,
  artifact_version          INTEGER,
  model_params              JSONB,
  lifecycle_jobs            JSONB NOT NULL DEFAULT '{}',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE energy_saving_cell (
  instance_id          UUID NOT NULL,
  cell_id              TEXT NOT NULL,
  state                TEXT NOT NULL DEFAULT 'SERVING' CHECK (state IN ('SERVING','PRE_SLEEP','SLEEP')),
  o1_value             TEXT,
  last_unlocked_at     TIMESTAMPTZ,
  override_by          TEXT,
  override_at          TIMESTAMPTZ,
  pending_dispatch_id  UUID,
  pending_decision_id  UUID,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instance_id, cell_id)
);

CREATE TABLE energy_saving_decision (
  decision_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id   TEXT NOT NULL,                 -- the evaluation's X-Correlation-ID
  instance_id    UUID NOT NULL,
  cell_id        TEXT NOT NULL,
  observed_at    TIMESTAMPTZ,
  prb            DOUBLE PRECISION,
  prediction     JSONB,
  safety         JSONB,
  decision       TEXT NOT NULL CHECK (decision IN ('LOCK','UNLOCK','NO_CHANGE')),
  reason         TEXT NOT NULL,
  outcome        TEXT NOT NULL,
  intent         JSONB,
  action         JSONB,
  verification   JSONB,
  rollback       JSONB,
  final_state    JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX energy_saving_decision_instance_cell ON energy_saving_decision (instance_id, cell_id, created_at DESC);

-- ============================================================
-- Wave 10.2: the Mobility Optimization reference rApp's own state
-- (samples/mobility-optimization-rapp/app/models.py)
-- ============================================================
CREATE TABLE mobility_instance (
  instance_id               UUID PRIMARY KEY,
  package_id                UUID,
  managed_element_ref       TEXT NOT NULL,
  relations                 JSONB NOT NULL,          -- [{relation, source, target}]
  baseline_cio              INTEGER NOT NULL DEFAULT 0,
  dmro_bounds               JSONB NOT NULL DEFAULT '{}',
  autonomy_mode             TEXT NOT NULL CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  rmih_id                   TEXT NOT NULL DEFAULT 'sa-smos',
  energy_saving_instance_id TEXT,                    -- coordination with the EnergySaving rApp (D10.2-4c)
  traffic_steering_instance_id TEXT,                 -- shared-CIO arbitration with the Traffic Steering rApp (D10.4-1)
  operator_notification_uri TEXT,
  data_jobs                 JSONB NOT NULL DEFAULT '{}',
  model_id                  UUID,
  model_version             TEXT,
  artifact_version          INTEGER,
  model_params              JSONB,
  lifecycle_jobs            JSONB NOT NULL DEFAULT '{}',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mobility_relation (
  instance_id          UUID NOT NULL,
  relation_id          TEXT NOT NULL,
  state                TEXT NOT NULL DEFAULT 'STEADY' CHECK (state IN ('STEADY','OBSERVING')),
  current_cio          INTEGER,
  last_change          JSONB,                        -- {at, from, to, preRate} until confirmed or reverted
  last_changed_at      TIMESTAMPTZ,
  pending_dispatch_id  UUID,
  pending_decision_id  UUID,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instance_id, relation_id)
);

CREATE TABLE mobility_decision (
  decision_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id   TEXT NOT NULL,
  instance_id    UUID NOT NULL,
  relation_id    TEXT NOT NULL,
  observed_at    TIMESTAMPTZ,
  rate           DOUBLE PRECISION,
  attempts       DOUBLE PRECISION,
  prediction     JSONB,
  safety         JSONB,
  decision       TEXT NOT NULL CHECK (decision IN ('RAISE_CIO','LOWER_CIO','REVERT_CIO','NO_CHANGE')),
  reason         TEXT NOT NULL,
  from_cio       INTEGER,
  to_cio         INTEGER,
  outcome        TEXT NOT NULL,
  kpi            JSONB,
  intent         JSONB,
  action         JSONB,
  verification   JSONB,
  rollback       JSONB,
  final_state    JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX mobility_decision_instance_relation ON mobility_decision (instance_id, relation_id, created_at DESC);

-- ============================================================
-- Wave 10.3: the Coverage Optimization reference rApp's own state
-- (samples/coverage-optimization-rapp/app/models.py)
-- ============================================================
CREATE TABLE coverage_instance (
  instance_id               UUID PRIMARY KEY,
  package_id                UUID,
  managed_element_ref       TEXT NOT NULL,
  cells                     JSONB NOT NULL,          -- [cellId]
  baseline_tilt             INTEGER NOT NULL DEFAULT 60,
  baseline_power            INTEGER NOT NULL DEFAULT 43,
  autonomy_mode             TEXT NOT NULL CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  rmih_id                   TEXT NOT NULL DEFAULT 'sa-smos',
  energy_saving_instance_id TEXT,                    -- coordination (D10.3-4c)
  mobility_instance_id      TEXT,
  operator_notification_uri TEXT,
  observing                 JSONB,                   -- the change set under KPI verification
  pending_dispatch          JSONB,                   -- an ASSIST change set awaiting the operator
  data_jobs                 JSONB NOT NULL DEFAULT '{}',
  model_id                  UUID,
  model_version             TEXT,
  artifact_version          INTEGER,
  model_params              JSONB,
  lifecycle_jobs            JSONB NOT NULL DEFAULT '{}',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE coverage_cell (
  instance_id      UUID NOT NULL,
  cell_id          TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'STEADY' CHECK (state IN ('STEADY','OBSERVING')),
  tilt             INTEGER,
  power            INTEGER,
  last_changed_at  TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instance_id, cell_id)
);

CREATE TABLE coverage_decision (
  decision_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id   TEXT NOT NULL,
  instance_id    UUID NOT NULL,
  cell_id        TEXT NOT NULL,
  observed_at    TIMESTAMPTZ,
  reports        DOUBLE PRECISION,
  shares         JSONB,
  prediction     JSONB,
  safety         JSONB,
  decision       TEXT NOT NULL CHECK (decision IN ('DOWNTILT','UPTILT','POWER_UP','POWER_DOWN','REVERT','NO_CHANGE')),
  reason         TEXT NOT NULL,
  from_setting   JSONB,
  to_setting     JSONB,
  outcome        TEXT NOT NULL,
  kpi            JSONB,
  intent         JSONB,
  action         JSONB,
  verification   JSONB,
  rollback       JSONB,
  final_state    JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX coverage_decision_instance_cell ON coverage_decision (instance_id, cell_id, created_at DESC);

-- ============================================================
-- Wave 10.4: the Traffic Steering reference rApp's own state
-- (samples/traffic-steering-rapp/app/models.py)
-- ============================================================
CREATE TABLE traffic_instance (
  instance_id               UUID PRIMARY KEY,
  package_id                UUID,
  managed_element_ref       TEXT NOT NULL,
  cells                     JSONB NOT NULL,          -- [{cellId, layer}]
  baseline_cio              INTEGER NOT NULL DEFAULT 0,
  baseline_priority         INTEGER NOT NULL DEFAULT 5,
  autonomy_mode             TEXT NOT NULL CHECK (autonomy_mode IN ('AUTONOMOUS','ASSIST','SHADOW')),
  rmih_id                   TEXT NOT NULL DEFAULT 'sa-smos',
  energy_saving_instance_id TEXT,                    -- coordination (D10.4-4c)
  mobility_instance_id      TEXT,                    -- shared-CIO arbitration (D10.4-1)
  coverage_instance_id      TEXT,
  operator_notification_uri TEXT,
  steering_log              JSONB NOT NULL DEFAULT '[]',   -- recent steering, for anti-oscillation
  pending_dispatch          JSONB,                   -- ASSIST steps awaiting the operator
  data_jobs                 JSONB NOT NULL DEFAULT '{}',
  model_id                  UUID,
  model_version             TEXT,
  artifact_version          INTEGER,
  model_params              JSONB,
  lifecycle_jobs            JSONB NOT NULL DEFAULT '{}',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE traffic_cell (
  instance_id      UUID NOT NULL,
  cell_id          TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'STEADY' CHECK (state IN ('STEADY','OBSERVING')),
  steering         JSONB NOT NULL DEFAULT '{}',      -- {"cio": {target: dB}, "prio": {layer: steps}} in force
  last_change      JSONB,                            -- until confirmed or reverted
  last_changed_at  TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instance_id, cell_id)
);

CREATE TABLE traffic_decision (
  decision_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id   TEXT NOT NULL,
  instance_id    UUID NOT NULL,
  cell_id        TEXT NOT NULL,
  observed_at    TIMESTAMPTZ,
  score          DOUBLE PRECISION,
  forecast       DOUBLE PRECISION,
  prediction     JSONB,
  safety         JSONB,
  decision       TEXT NOT NULL CHECK (decision IN ('STEER_IDLE','STEER_CONNECTED','RELEASE_IDLE','RELEASE_CONNECTED','REVERT','NO_CHANGE')),
  reason         TEXT NOT NULL,
  knob           TEXT CHECK (knob IN ('IDLE','CONNECTED')),
  managed_ref    TEXT,
  targets        JSONB,
  from_value     INTEGER,
  to_value       INTEGER,
  outcome        TEXT NOT NULL,
  kpi            JSONB,
  intent         JSONB,
  action         JSONB,
  verification   JSONB,
  rollback       JSONB,
  final_state    JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX traffic_decision_instance_cell ON traffic_decision (instance_id, cell_id, created_at DESC);
