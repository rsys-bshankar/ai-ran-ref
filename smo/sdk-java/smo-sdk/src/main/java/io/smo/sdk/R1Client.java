package io.smo.sdk;

import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.StringJoiner;
import java.util.UUID;

/**
 * The HTTP client every namespace client uses: a call through R1 Termination with the bearer token of
 * {@link TokenProvider}, the Java counterpart of {@code smo_shared.r1_client.R1Client} plus the SDK's
 * {@code _RetryOnConflict} wrapper.
 *
 * <ul>
 *   <li>Every call carries {@code Authorization: Bearer <token>}; the token is cached and refreshed shortly before it
 *       expires. A 401 discards it, fetches one fresh token and sends the call once more.</li>
 *   <li>Transport failures and 429/502/503/504 are repeated with exponential backoff ({@link RetryPolicy}).</li>
 *   <li>Every POST carries an {@code Idempotency-Key} (generated unless the caller supplies one) that all repeats reuse,
 *       so the platform answers a repeat of a completed POST from the stored first answer (PR-ST-3).</li>
 *   <li>A mutating call that lost a write race (409 whose ProblemDetails title is {@code CONCURRENT_MODIFICATION}) is sent
 *       once more; the platform rolled the first attempt back. Reads are not.</li>
 * </ul>
 *
 * Paths are R1 Termination paths ({@code /<module>/...}). The methods return the raw {@link R1Response}; the namespace
 * clients turn it into a JSON value with {@link R1Response#ok()}. Thread-safe.
 */
public final class R1Client implements AutoCloseable {
    private final SmoConfig config;
    private final HttpCaller http;
    private final TokenProvider tokens;

    public R1Client(SmoConfig config) {
        this(config, HttpClient.newBuilder().connectTimeout(config.requestTimeout()).build());
    }

    /** With your own {@link HttpClient}: for mTLS (an {@code SSLContext}), a proxy, or a different executor. */
    public R1Client(SmoConfig config, HttpClient httpClient) {
        this(config, httpClient, Clock.systemUTC(), Thread::sleep);
    }

    R1Client(SmoConfig config, HttpClient httpClient, Clock clock, HttpCaller.Sleeper sleeper) {
        this.config = config;
        this.http = new HttpCaller(httpClient, config.requestTimeout(), sleeper);
        this.tokens = new TokenProvider(config, http, clock);
    }

    public TokenProvider tokens() {
        return tokens;
    }

    public SmoConfig config() {
        return config;
    }

    public R1Response get(String path, Map<String, ?> query) {
        return request("GET", path, query, null, Map.of());
    }

    public R1Response post(String path, Map<String, ?> query, Object body) {
        return request("POST", path, query, body, Map.of());
    }

    public R1Response put(String path, Map<String, ?> query, Object body) {
        return request("PUT", path, query, body, Map.of());
    }

    public R1Response patch(String path, Map<String, ?> query, Object body) {
        return request("PATCH", path, query, body, Map.of());
    }

    public R1Response delete(String path, Map<String, ?> query) {
        return request("DELETE", path, query, null, Map.of());
    }

    /**
     * @param query   query parameters; a null value is left out, a collection is sent as a repeated key; may be null
     * @param body    anything Jackson writes as JSON (a Map, a JsonNode, a record); null sends no body
     * @param headers extra request headers (for instance a chosen {@code Idempotency-Key}); the SDK's own win on a clash
     */
    public R1Response request(String method, String path, Map<String, ?> query, Object body, Map<String, String> headers) {
        String url = config.gatewayUrl() + path + queryString(query);
        String json = body == null ? null : Json.write(body);
        boolean mutating = !"GET".equals(method);
        Map<String, String> extra = new LinkedHashMap<>(headers);
        if ("POST".equals(method)) {
            extra.putIfAbsent("Idempotency-Key", UUID.randomUUID().toString().replace("-", ""));
        }

        R1Response response = once(method, url, extra, json, false);
        if (response.status() == 401) {
            response = once(method, url, extra, json, true);       // expired or revoked at SME since it was cached
        }
        if (mutating && response.isConcurrentModification()) {
            response = once(method, url, extra, json, false);
        }
        return response;
    }

    private R1Response once(String method, String url, Map<String, String> extra, String json, boolean refreshToken) {
        Map<String, String> headers = new LinkedHashMap<>(extra);
        headers.put("Authorization", "Bearer " + tokens.token(refreshToken));
        return http.send(method, url, headers, json, config.retry());
    }

    static String queryString(Map<String, ?> query) {
        if (query == null || query.isEmpty()) {
            return "";
        }
        StringJoiner joiner = new StringJoiner("&", "?", "");
        joiner.setEmptyValue("");
        for (Map.Entry<String, ?> entry : query.entrySet()) {
            Object value = entry.getValue();
            if (value instanceof Collection<?> values) {
                values.forEach(v -> add(joiner, entry.getKey(), v));
            } else {
                add(joiner, entry.getKey(), value);
            }
        }
        return joiner.toString();
    }

    private static void add(StringJoiner joiner, String key, Object value) {
        if (value != null) {
            joiner.add(URLEncoder.encode(key, StandardCharsets.UTF_8) + "=" + URLEncoder.encode(String.valueOf(value), StandardCharsets.UTF_8));
        }
    }

    /** Deregisters the invoker this process enrolled for itself (a pinned identity is left alone). */
    @Override
    public void close() {
        tokens.offboard();
    }
}
