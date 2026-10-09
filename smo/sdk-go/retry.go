// retry.go is the retry policy of the SDK: RetryPolicy (the Config.Retry value), the wait between attempts, which
// statuses are worth repeating and which of those leave it unknown whether the server acted, and the Retry-After header.
//
// Client.roundTrip (client.go) is the only caller; this file does no I/O except sleep, which waits on the context. The
// defaults (4 attempts, 200 ms, 5 s) are repeated in sdk-go/README.md and in the tests: change them together.

package smosdk

import (
	"context"
	"math/rand/v2"
	"net/http"
	"strconv"
	"time"
)

// maxRetryAfter bounds how long a server's Retry-After can make the SDK wait.
const maxRetryAfter = 30 * time.Second

// RetryPolicy is how a call is repeated when the platform or the network
// fails in a way that is worth repeating: a transport error, or 429, 502, 503
// or 504. The wait before attempt n+1 is BaseDelay * 2^(n-1), at most
// MaxDelay, with "equal jitter" (half fixed, half random) unless NoJitter; a
// Retry-After header (seconds) raises it, up to 30 s. Every wait ends early,
// with the context's error, when the context is done.
//
// Only calls that are safe to repeat are repeated on a transport error or
// 502/504 (where the server may have acted): GET, PUT, DELETE, and a POST
// that carries an Idempotency-Key (the SDK adds one to every POST). A call
// that is not safe to repeat is retried only on 429 and 503.
type RetryPolicy struct {
	MaxAttempts int           // total attempts including the first; 0 = default (4); 1 = never retry
	BaseDelay   time.Duration // 0 = default (200 ms)
	MaxDelay    time.Duration // 0 = default (5 s)
	NoJitter    bool          // deterministic waits (tests)
}

// attempts returns the total number of tries including the first: MaxAttempts, or 4 when it is not positive.
func (p RetryPolicy) attempts() int {
	if p.MaxAttempts <= 0 {
		return 4
	}
	return p.MaxAttempts
}

// wait is the pause after the failed attempt number `attempt` (1-based).
func (p RetryPolicy) wait(attempt int, retryAfter time.Duration) time.Duration {
	base, ceiling := p.BaseDelay, p.MaxDelay
	if base <= 0 {
		base = 200 * time.Millisecond
	}
	if ceiling <= 0 {
		ceiling = 5 * time.Second
	}
	d := base
	for i := 1; i < attempt && d < ceiling; i++ {
		d *= 2
	}
	if d > ceiling {
		d = ceiling
	}
	if !p.NoJitter && d > 1 {
		half := d / 2
		d = half + rand.N(half+1)
	}
	if retryAfter > maxRetryAfter {
		retryAfter = maxRetryAfter
	}
	return max(d, retryAfter)
}

// retryableStatus says whether a response status is worth repeating; ambiguous
// marks the statuses after which the server may already have acted.
func retryableStatus(status int) (retry, ambiguous bool) {
	switch status {
	case http.StatusTooManyRequests, http.StatusServiceUnavailable:
		return true, false
	case http.StatusBadGateway, http.StatusGatewayTimeout:
		return true, true
	}
	return false, false
}

// parseRetryAfter reads a Retry-After header in seconds; the HTTP-date form is ignored (0).
func parseRetryAfter(h http.Header) time.Duration {
	n, err := strconv.Atoi(h.Get("Retry-After"))
	if err != nil || n <= 0 {
		return 0
	}
	return time.Duration(n) * time.Second
}

// sleep waits d or until ctx is done.
func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
