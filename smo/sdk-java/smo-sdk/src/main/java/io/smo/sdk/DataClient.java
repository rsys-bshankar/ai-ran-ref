package io.smo.sdk;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.LinkedHashMap;
import java.util.Map;

/** {@code sdk.data}: DME types, data jobs and records (the Python SDK's {@code DataClient}, the routes a rApp needs). */
public final class DataClient extends NamespaceClient {
    DataClient(R1Client r1) {
        super(r1);
    }

    /** The data types DME knows ({@code GET /dme/dme-types}); {@code dataCategory} may be null. */
    public JsonNode listTypes(String dataCategory) {
        return call(Routes.DME_TYPES, params("data_category", dataCategory), null);
    }

    public JsonNode listDataJobs(String dmeTypeId, String consumerId) {
        return call(Routes.DME_JOBS_LIST, params("dme_type_id", dmeTypeId, "consumer_id", consumerId), null);
    }

    /**
     * {@code POST /dme/data-jobs}. {@code lifecycleStage} (TRAINING, TESTING, EMULATION, INFERENCE, CLOSED_LOOP_FEEDBACK),
     * {@code deliveryDetails} and {@code productionJobDefinition} may be null.
     */
    public JsonNode createDataJob(String dmeTypeId, String dataDeliveryMode, String dataDeliveryMethod, String consumerId,
            Map<String, ?> productionJobDefinition, Map<String, ?> deliveryDetails, String lifecycleStage) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("dmeTypeId", dmeTypeId);
        body.put("dataDeliveryMode", dataDeliveryMode);
        body.put("dataDeliveryMethod", dataDeliveryMethod);
        body.put("consumerId", consumerId);
        body.put("productionJobDefinition", productionJobDefinition == null ? Map.of() : productionJobDefinition);
        body.put("deliveryDetails", deliveryDetails == null ? Map.of() : deliveryDetails);
        body.put("lifecycleStage", lifecycleStage);
        return call(Routes.DME_JOB_CREATE, null, body);
    }

    public JsonNode getDataJob(String dataJobId) {
        return call(Routes.DME_JOB_GET, null, null, dataJobId);
    }

    public JsonNode queryDataJobStatus(String dataJobId) {
        return call(Routes.DME_JOB_STATUS, null, null, dataJobId);
    }

    public void terminateDataJob(String dataJobId) {
        call(Routes.DME_JOB_DELETE, null, null, dataJobId);
    }

    public JsonNode fetchDataRecords(String dataJobId, int limit) {
        return call(Routes.DME_RECORDS, params("limit", limit), null, dataJobId);
    }
}
