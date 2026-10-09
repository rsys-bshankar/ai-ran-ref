package io.smo.sdk;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.Optional;

/**
 * One HTTP exchange with retry: the only place the SDK touches {@link HttpClient}. A transport failure or a status in
 * the {@link RetryPolicy} is sent again after the policy's delay; whatever is left when the attempts run out is returned
 * (a status) or thrown (a transport failure, as an {@link SdkException} with status 0).
 */
final class HttpCaller {

    /** Waits; a test replaces it to record the delays instead of sleeping. */
    @FunctionalInterface
    interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final HttpClient http;
    private final Duration timeout;
    private final Sleeper sleeper;

    HttpCaller(HttpClient http, Duration timeout, Sleeper sleeper) {
        this.http = http;
        this.timeout = timeout;
        this.sleeper = sleeper;
    }

    R1Response send(String method, String url, Map<String, String> headers, String jsonBody, RetryPolicy retry) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url)).timeout(timeout);
        headers.forEach(builder::header);
        if (jsonBody != null) {
            builder.header("Content-Type", "application/json");
        }
        builder.header("Accept", "application/json");
        builder.method(method, jsonBody == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(jsonBody));
        HttpRequest request = builder.build();

        for (int attempt = 1; ; attempt++) {
            Duration retryAfter = null;
            try {
                HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
                if (attempt >= retry.maxAttempts() || !retry.retriesStatus(response.statusCode())) {
                    return new R1Response(response.statusCode(), response.body());
                }
                retryAfter = retryAfterOf(response);
            } catch (IOException e) {
                if (attempt >= retry.maxAttempts()) {
                    throw new SdkException(method + " " + url + " failed: " + e, e);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new SdkException(method + " " + url + " was interrupted", e);
            }
            pause(retry.delayBefore(attempt + 1, retryAfter));
        }
    }

    private void pause(Duration delay) {
        if (delay.isZero()) {
            return;
        }
        try {
            sleeper.sleep(delay);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new SdkException("interrupted while waiting to retry", e);
        }
    }

    /** {@code Retry-After} in seconds; the HTTP-date form is ignored (the computed backoff is used instead). */
    private static Duration retryAfterOf(HttpResponse<?> response) {
        Optional<String> value = response.headers().firstValue("Retry-After");
        if (value.isEmpty()) {
            return null;
        }
        try {
            long seconds = Long.parseLong(value.get().trim());
            return seconds < 0 ? null : Duration.ofSeconds(seconds);
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
