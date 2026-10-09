package smosdk

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"testing"
	"time"
)

func get(t *testing.T, c *Client, path string) error {
	t.Helper()
	return c.Do(context.Background(), Request{Method: "GET", Path: path}, nil)
}

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

func TestTokenEndpointWithoutTheTokenPathIsRefused(t *testing.T) {
	s := newStack(t)
	s.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"apiEndpoints": []any{map[string]any{"tokenEndPoint": map[string]any{"uri": "http://sme/elsewhere"}}}})
	})
	if err := get(t, s.client(t), "/x"); err == nil {
		t.Fatal("expected an error")
	}
}
