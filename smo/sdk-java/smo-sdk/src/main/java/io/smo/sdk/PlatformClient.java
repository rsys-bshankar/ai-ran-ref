package io.smo.sdk;

import com.fasterxml.jackson.databind.JsonNode;

/** {@code sdk.platform}: CAPIF service discovery at SME. Token endpoints and invoker registration are {@link TokenProvider}'s concern. */
public final class PlatformClient extends NamespaceClient {
    PlatformClient(R1Client r1) {
        super(r1);
    }

    /** {@code GET /sme/service-apis/v1/allServiceAPIs}; every filter may be null. */
    public JsonNode discoverServices(String apiInvokerId, String apiName, String apiVersion) {
        return call(Routes.SME_DISCOVER, params("api_invoker_id", apiInvokerId, "api_name", apiName, "api_version", apiVersion), null);
    }
}
