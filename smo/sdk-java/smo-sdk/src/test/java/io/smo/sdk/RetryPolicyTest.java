package io.smo.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.Set;
import org.junit.jupiter.api.Test;

class RetryPolicyTest {
    private final RetryPolicy plain = new RetryPolicy(5, Duration.ofMillis(200), Duration.ofSeconds(1), false, Set.of(503));

    @Test
    void backoffDoublesAndIsCapped() {
        assertEquals(Duration.ofMillis(200), plain.delayBefore(2, null));
        assertEquals(Duration.ofMillis(400), plain.delayBefore(3, null));
        assertEquals(Duration.ofMillis(800), plain.delayBefore(4, null));
        assertEquals(Duration.ofSeconds(1), plain.delayBefore(5, null));
        assertEquals(Duration.ofSeconds(1), plain.delayBefore(60, null), "a large attempt number cannot overflow the shift");
    }

    @Test
    void jitterStaysWithinTheComputedDelay() {
        RetryPolicy jittered = new RetryPolicy(5, Duration.ofMillis(200), Duration.ofSeconds(1), true, Set.of(503));
        for (int i = 0; i < 200; i++) {
            long ms = jittered.delayBefore(3, null).toMillis();
            assertTrue(ms >= 0 && ms <= 400, "was " + ms);
        }
    }

    @Test
    void retryAfterWinsButIsCappedAtThirtySeconds() {
        assertEquals(Duration.ofSeconds(7), plain.delayBefore(2, Duration.ofSeconds(7)));
        assertEquals(Duration.ofSeconds(30), plain.delayBefore(2, Duration.ofMinutes(10)));
    }

    @Test
    void defaultsRetryTheTransientStatusesOnly() {
        RetryPolicy d = RetryPolicy.defaults();
        for (int s : new int[] {429, 502, 503, 504}) {
            assertTrue(d.retriesStatus(s));
        }
        for (int s : new int[] {200, 400, 401, 404, 409, 422, 500}) {
            assertFalse(d.retriesStatus(s));
        }
        assertEquals(1, RetryPolicy.none().maxAttempts());
    }

    @Test
    void atLeastOneAttempt() {
        assertThrows(IllegalArgumentException.class, () -> new RetryPolicy(0, Duration.ZERO, Duration.ZERO, false, Set.of()));
    }
}
