// auth_test.go covers the token flow of auth.go through Client.Do: discovery through /bootstrap, enrollment, the grant,
// caching, renewal before expiry and on a 401, a pinned invoker, a bootstrap key, a fixed bearer token, and many
// goroutines at once (run with -race for those). It uses fakeStack (stack_test.go), which counts the bootstrap,
// registration and grant calls. No network beyond httptest. Run: cd smo/sdk-go && go test -race ./... .

package smosdk

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"testing"
	"time"
)

// get sends GET path through c and returns only the error, for the tests that care about the token traffic and not the answer.
func get(t *testing.T, c *Client, path string) error {
	t.Helper()
	return c.Do(context.Background(), Request{Method: "GET", Path: path}, nil)
}

// TestFirstCallDiscoversEnrollsAndAsksForTheRappScope
// pins the first-call sequence: one /bootstrap, one registration (without an enrollment header, so SME records an rApp)
// and one client_credentials grant for scope smo-rapp with the enrolled invoker.
func TestFirstCallDiscoversEnrollsAndAsksForTheRappScope(t *testing.T) {
	s := newStack(t)
	s.api = func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer tok-1" {
			t.Errorf("Authorization = %q", got)
		}
		writeJSON(w, 200, map[string]any{"ok": true})
	}
	c := s.client(t)
	if err := get(t, c, "/dme/dme-types"); err != nil {
		t.Fatal(err)
	}
	if b, r, g := s.counts(); b != 1 || r != 1 || g != 1 {
		t.Fatalf("bootstrap/registration/grant = %d/%d/%d, want 1/1/1", b, r, g)
	}
	if got := s.lastGrant; got["grant_type"] != "client_credentials" || got["scope"] != "smo-rapp" ||
		got["client_id"] != "api-invoker-1" || got["client_secret"] != "secret-1" {
		t.Fatalf("grant = %v", got)
	}
	// an rApp presents no enrollment secret: that is what makes SME record it as an rApp (PR-SEC-14)
	if s.regHeaders.Get("X-SMO-Enrollment") != "" {
		t.Fatal("the registration carried an enrollment header")
	}
}

// TestTokenIsCachedAcrossCalls
// pins that later calls reuse the token: a grant per call would hammer SME.
func TestTokenIsCachedAcrossCalls(t *testing.T) {
	s := newStack(t)
	c := s.client(t)
	for range 3 {
		if err := get(t, c, "/dme/dme-types"); err != nil {
			t.Fatal(err)
		}
	}
	if b, r, g := s.counts(); b != 1 || r != 1 || g != 1 {
		t.Fatalf("bootstrap/registration/grant = %d/%d/%d, want 1/1/1", b, r, g)
	}
}

// TestTokenIsRenewedBeforeItExpires
// pins renewal 30 s before expiry, and that a renewal reuses the invoker and the token endpoint (no new registration or
// bootstrap).
func TestTokenIsRenewedBeforeItExpires(t *testing.T) {
	s := newStack(t)
	s.expiresIn = 40 // minus the 30 s margin: usable for 10 s
	c := s.client(t)
	clock := time.Date(2026, 10, 9, 12, 0, 0, 0, time.UTC)
	c.now = func() time.Time { return clock }

	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	clock = clock.Add(9 * time.Second)
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if _, _, g := s.counts(); g != 1 {
		t.Fatalf("grants = %d after 9 s, want 1", g)
	}
	clock = clock.Add(2 * time.Second) // 11 s: past the margin
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if b, r, g := s.counts(); g != 2 || r != 1 || b != 1 {
		t.Fatalf("bootstrap/registration/grant = %d/%d/%d, want 1/1/2 (renewal reuses the identity)", b, r, g)
	}
}

// TestShortLivedTokenStillGetsAtLeastOneSecond
// pins the one-second floor on the cached lifetime: a token that lives less than the 30 s margin is still cached for about a second, not for zero or negative time.
func TestShortLivedTokenStillGetsAtLeastOneSecond(t *testing.T) {
	s := newStack(t)
	s.expiresIn = 5 // below the margin
	c := s.client(t)
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if exp := c.tokens.expires.Sub(c.now()); exp < 0 || exp > time.Second+time.Millisecond {
		t.Fatalf("lifetime = %v, want about 1 s", exp)
	}
}

// TestA401RenewsTheTokenOnceAndRepeatsTheCall
// pins that a token SME has revoked is replaced and the call repeated, without a new registration.
func TestA401RenewsTheTokenOnceAndRepeatsTheCall(t *testing.T) {
	s := newStack(t)
	c := s.client(t)
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	s.revoke("tok-1") // revoked at SME since it was cached
	if err := get(t, c, "/x"); err != nil {
		t.Fatalf("call after revocation: %v", err)
	}
	if _, r, g := s.counts(); g != 2 || r != 1 {
		t.Fatalf("registrations/grants = %d/%d, want 1/2", r, g)
	}
}

// TestAPersistent401IsReturnedAfterOneRenewal
// pins that a 401 that survives one fresh token is returned to the caller, so a refused call cannot loop.
func TestAPersistent401IsReturnedAfterOneRenewal(t *testing.T) {
	s := newStack(t)
	s.api = func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 401, map[string]any{"title": "UNAUTHORIZED", "status": 401, "detail": "no"})
	}
	c := s.client(t)
	err := get(t, c, "/x")
	if StatusOf(err) != 401 {
		t.Fatalf("err = %v, want a 401 *Error", err)
	}
	if _, _, g := s.counts(); g != 2 {
		t.Fatalf("grants = %d, want 2 (the first and exactly one renewal)", g)
	}
}

// TestConcurrentCallsShareOneGrant
// pins single flight: twenty goroutines starting together cause one bootstrap, one registration and one grant.
func TestConcurrentCallsShareOneGrant(t *testing.T) {
	s := newStack(t)
	c := s.client(t)
	var wg sync.WaitGroup
	for range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := get(t, c, "/x"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if b, r, g := s.counts(); b != 1 || r != 1 || g != 1 {
		t.Fatalf("bootstrap/registration/grant = %d/%d/%d, want 1/1/1", b, r, g)
	}
}

// TestConcurrent401sRenewOnce
// pins that callers rejected with the same revoked token renew it once between them, not once each.
func TestConcurrent401sRenewOnce(t *testing.T) {
	s := newStack(t)
	c := s.client(t)
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	s.revoke("tok-1")
	var wg sync.WaitGroup
	for range 10 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := get(t, c, "/x"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if _, _, g := s.counts(); g != 2 {
		t.Fatalf("grants = %d, want 2: ten callers rejected with the same token must renew it once", g)
	}
}

// TestPinnedInvokerIsNotEnrolled
// pins that an invoker from the configuration (the one rApp Management issues) is used for the grant and no registration is made.
func TestPinnedInvokerIsNotEnrolled(t *testing.T) {
	s := newStack(t)
	c := s.client(t, func(c *Config) { c.InvokerID, c.InvokerSecret = "api-invoker-instance", "s3cret" })
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if _, r, _ := s.counts(); r != 0 {
		t.Fatalf("registrations = %d, want 0", r)
	}
	if s.lastGrant["client_id"] != "api-invoker-instance" || s.lastGrant["client_secret"] != "s3cret" {
		t.Fatalf("grant = %v", s.lastGrant)
	}
}

// TestAPinnedInvokerSMEForgotIsAnErrorNotAReplacement
// pins that a pinned invoker SME no longer knows gives the invalid_client error and is never replaced by a new
// registration, because the pinned identity is the instance's own.
func TestAPinnedInvokerSMEForgotIsAnErrorNotAReplacement(t *testing.T) {
	s := newStack(t)
	s.forgetInvoker = true
	c := s.client(t, func(c *Config) { c.InvokerID, c.InvokerSecret = "api-invoker-instance", "s3cret" })
	err := get(t, c, "/x")
	var e *Error
	if !errors.As(err, &e) || e.StatusCode != 400 || e.Title != "invalid_client" {
		t.Fatalf("err = %v, want the invalid_client answer", err)
	}
	if _, r, _ := s.counts(); r != 0 {
		t.Fatalf("registrations = %d: a pinned identity must never be replaced", r)
	}
}

// TestAnEnrolledInvokerSMEForgotIsEnrolledAfresh
// pins that an invoker the SDK enrolled itself and SME then purged is enrolled again, once, and the new one is used.
func TestAnEnrolledInvokerSMEForgotIsEnrolledAfresh(t *testing.T) {
	s := newStack(t)
	c := s.client(t)
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	s.revoke("tok-1")
	s.mu.Lock()
	s.forgetInvoker = true // the renewal's grant is refused: SME purged the invoker
	s.mu.Unlock()
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if _, r, _ := s.counts(); r != 2 {
		t.Fatalf("registrations = %d, want 2", r)
	}
	if s.lastGrant["client_id"] != "api-invoker-2" {
		t.Fatalf("grant used %q, want the new invoker", s.lastGrant["client_id"])
	}
}

// TestBootstrapKeyIsSentWhenConfigured
// pins that Config.BootstrapKey is sent to /bootstrap as X-Bootstrap-Key, and that a gateway asking for it answers 401 without it.
func TestBootstrapKeyIsSentWhenConfigured(t *testing.T) {
	s := newStack(t)
	s.bootstrapKey = "k3y"
	if err := get(t, s.client(t, func(c *Config) { c.BootstrapKey = "k3y" }), "/x"); err != nil {
		t.Fatal(err)
	}
	err := get(t, s.client(t), "/x")
	if StatusOf(err) != 401 {
		t.Fatalf("without the key: err = %v, want the gateway's 401", err)
	}
}

// TestAFailedTokenFlowIsAnErrorNotAnUnauthenticatedCall
// pins that when the token cannot be obtained the call fails with that error and the API is never called without a token.
func TestAFailedTokenFlowIsAnErrorNotAnUnauthenticatedCall(t *testing.T) {
	apiCalled := false
	srv := newStack(t)
	srv.api = func(w http.ResponseWriter, r *http.Request) { apiCalled = true }
	srv.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 500, map[string]any{"detail": "down"})
	})
	c := srv.client(t, func(c *Config) { c.Retry.MaxAttempts = 1 })
	err := get(t, c, "/x")
	if StatusOf(err) != 500 || apiCalled {
		t.Fatalf("err = %v, apiCalled = %v", err, apiCalled)
	}
}

// TestBearerTokenBypassesSME
// pins that Config.BearerToken is sent as is with no SME traffic, and that its 401 is returned with no renewal attempt.
func TestBearerTokenBypassesSME(t *testing.T) {
	s := newStack(t)
	s.mu.Lock()
	s.validToken["fixed"] = true
	s.mu.Unlock()
	c := s.client(t, func(c *Config) { c.BearerToken = "fixed" })
	if err := get(t, c, "/x"); err != nil {
		t.Fatal(err)
	}
	if b, _, g := s.counts(); b != 0 || g != 0 {
		t.Fatalf("bootstrap/grants = %d/%d, want none", b, g)
	}
	s.revoke("fixed")
	if StatusOf(get(t, c, "/x")) != 401 {
		t.Fatal("a rejected fixed token must be returned as 401, with no renewal attempt")
	}
	if b, _, _ := s.counts(); b != 0 {
		t.Fatal("renewal attempted for a fixed token")
	}
}

// TestTokenEndpointWithoutTheTokenPathIsRefused
// pins that a bootstrap answer whose token endpoint does not end in /oauth2/token is an error.
func TestTokenEndpointWithoutTheTokenPathIsRefused(t *testing.T) {
	s := newStack(t)
	s.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"apiEndpoints": []any{map[string]any{"tokenEndPoint": map[string]any{"uri": "http://sme/elsewhere"}}}})
	})
	if err := get(t, s.client(t), "/x"); err == nil {
		t.Fatal("expected an error")
	}
}
