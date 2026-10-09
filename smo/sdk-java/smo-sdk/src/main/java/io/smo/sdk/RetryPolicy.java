package io.smo.sdk;

import java.time.Duration;
import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;

/**
 * How a call that failed for a transient reason is repeated: up to {@code maxAttempts} sends in all, exponential
 * backoff from {@code baseDelay} (doubling, capped at {@code maxDelay}) with full jitter, and the server's
 * {@code Retry-After} (seconds) when it sends one. Only transport failures and the statuses in
 * {@link #retryableStatuses()} are repeated; any other answer, a 4xx in particular, is final.
 *
 * <p>Every POST made through {@link R1Client} carries an {@code Idempotency-Key} that its repeats reuse, so
 * repeating a POST is safe in the sense the platform defines (PR-ST-3).
 */
public record RetryPolicy(int maxAttempts, Duration baseDelay, Duration maxDelay, boolean jitter, Set<Integer> retryableStatuses) {

    public RetryPolicy {
        if (maxAttempts < 1) {
            throw new IllegalArgumentException("maxAttempts must be at least 1");
        }
        retryableStatuses = Set.copyOf(retryableStatuses);
    }

    /** Four attempts, 200 ms base, 5 s cap, jitter, on 429, 502, 503 and 504. */
    public static RetryPolicy defaults() {
        return new RetryPolicy(4, Duration.ofMillis(200), Duration.ofSeconds(5), true, Set.of(429, 502, 503, 504));
    }

    /** No repeat at all. */
    public static RetryPolicy none() {
        return new RetryPolicy(1, Duration.ZERO, Duration.ZERO, false, Set.of());
    }

    public boolean retriesStatus(int status) {
        return retryableStatuses.contains(status);
    }

    /**
     * The wait before send number {@code nextAttempt} (2 for the first repeat).
     *
     * @param retryAfter the server's {@code Retry-After}, or null; it wins over the computed delay but never exceeds 30 s
     */
    public Duration delayBefore(int nextAttempt, Duration retryAfter) {
        if (retryAfter != null) {
            Duration cap = Duration.ofSeconds(30);
            return retryAfter.compareTo(cap) > 0 ? cap : retryAfter;
        }
        long exponent = Math.min(Math.max(nextAttempt - 2, 0), 20);
        long millis = Math.min(baseDelay.toMillis() << exponent, maxDelay.toMillis());
        if (jitter && millis > 0) {
            millis = ThreadLocalRandom.current().nextLong(millis + 1);
        }
        return Duration.ofMillis(millis);
    }
}
