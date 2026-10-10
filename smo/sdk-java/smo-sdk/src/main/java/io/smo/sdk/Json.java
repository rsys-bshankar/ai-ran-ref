package io.smo.sdk;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

/** The one JSON mapper of the SDK. */
final class Json {
    static final ObjectMapper MAPPER = new ObjectMapper();

    private Json() {
    }

    /**
     * Encodes {@code value} as JSON text.
     *
     * @throws SdkException (status 0) when Jackson cannot encode it
     */
    static String write(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new SdkException("cannot encode the request body as JSON", e);
        }
    }
}
