package io.smo.sdk;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

/**
 * The rApp instance's own routes at rApp Management: what a running rApp reports about itself. (The operator creates,
 * upgrades and kills instances; a rApp may register the operator API of <em>its own</em> instance only, which the gateway
 * enforces by the invoker id.)
 */
public final class InstancesClient extends NamespaceClient {
    InstancesClient(R1Client r1) {
        super(r1);
    }

    public JsonNode get(String instanceId) {
        return call(Routes.INSTANCE_GET, null, null, instanceId);
    }

    /** The configuration the operator gave the instance. */
    public JsonNode config(String instanceId) {
        return call(Routes.INSTANCE_CONFIG, null, null, instanceId);
    }

    /**
     * {@code POST /rapp-mgmt/instances/{id}/performance}: the rApp's periodic self-report. It is also the closest the
     * platform has to a heartbeat: the GUI shows the newest report of each instance with its time.
     */
    public JsonNode reportPerformance(String instanceId, Map<String, ?> metrics) {
        return call(Routes.INSTANCE_PERFORMANCE, null, metrics, instanceId);
    }

    /** {@code severity=critical} also moves a RUNNING instance to FAULTED. */
    public JsonNode reportFault(String instanceId, String severity, String description) {
        return call(Routes.INSTANCE_FAULT, params("severity", severity, "description", description), Map.of(), instanceId);
    }

    /** Tells the platform where this instance serves its declared operator page (ADR 0004). */
    public JsonNode registerOperatorApi(String instanceId, String operatorApiBase) {
        return call(Routes.INSTANCE_OPERATOR_API_PUT, null, Map.of("operatorApiBase", operatorApiBase), instanceId);
    }

    /** Forgets the operator API; idempotent. */
    public void clearOperatorApi(String instanceId) {
        call(Routes.INSTANCE_OPERATOR_API_DELETE, null, null, instanceId);
    }

    /** DEPLOYING to RUNNING. Normally the deploying operator's call; offered for rApps that close their own bootstrap. */
    public JsonNode bootstrapComplete(String instanceId) {
        return call(Routes.INSTANCE_BOOTSTRAP_COMPLETE, null, Map.of(), instanceId);
    }

    /** TerminateInstance. Subject to the gateway's role policy for a rApp; see {@code docs/ARCHITECTURE.md}. */
    public JsonNode terminate(String instanceId) {
        return call(Routes.INSTANCE_TERMINATE, null, Map.of(), instanceId);
    }
}
