package io.smo.sdk;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.MissingNode;

/**
 * One answer from the platform: the status and the raw body. {@link #ok()} is the SDK's response boundary, the
 * counterpart of the Python SDK's {@code ensure_ok}: a status of 400 or more becomes an {@link SdkException}, an empty body
 * (a 204) becomes a missing node, and a JSON object with a list-valued {@code items} (every paginated route answers
 * {@code {items, total, limit, offset}}) is unwrapped to that list, so every {@code list*} method returns a JSON array.
 * Consequence, the same as in Python: {@code total}, {@code limit} and {@code offset} are dropped.
 */
public record R1Response(int status, String body) {

    public boolean isSuccess() {
        return status < 400;
    }

    /** The body parsed, or a missing node when there is none. A body that is not JSON is an {@link SdkException}. */
    public JsonNode json() {
        if (body == null || body.isBlank()) {
            return MissingNode.getInstance();
        }
        try {
            return Json.MAPPER.readTree(body);
        } catch (JsonProcessingException e) {
            throw new SdkException("the platform answered " + status + " with a body that is not JSON", e);
        }
    }

    /** The decoded body, or the failure: see the class comment. */
    public JsonNode ok() {
        if (status >= 400) {
            throw new SdkException(status, body);
        }
        if (status == 204) {
            return MissingNode.getInstance();
        }
        JsonNode node = json();
        JsonNode items = node.get("items");
        return items != null && items.isArray() ? items : node;
    }

    /** True for the 409 whose ProblemDetails title is CONCURRENT_MODIFICATION (a lost write race, PR-ST-2). */
    boolean isConcurrentModification() {
        if (status != 409) {
            return false;
        }
        try {
            JsonNode detail = json().path("detail");
            return "CONCURRENT_MODIFICATION".equals(detail.path("title").asText());
        } catch (SdkException e) {
            return false;
        }
    }
}
