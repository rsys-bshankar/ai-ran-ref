package io.smo.sdk;

import com.fasterxml.jackson.databind.JsonNode;

/** {@code sdk.models}: the model registry (MLMR), read side. */
public final class ModelsClient extends NamespaceClient {
    ModelsClient(R1Client r1) {
        super(r1);
    }

    /** {@code GET /mlmr/models}: the registered models (the first {@code limit}, at most 500). */
    public JsonNode listModels(int limit) {
        return call(Routes.MLMR_MODELS, params("limit", limit), null);
    }

    public JsonNode getModel(String modelId) {
        return call(Routes.MLMR_MODEL_GET, null, null, modelId);
    }
}
