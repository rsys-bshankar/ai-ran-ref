// stack_test.go is the fake platform the other tests in this package use: fakeStack serves /bootstrap, SME's invoker
// registration and token endpoint, and hands every other path to a handler the test sets. It is test support only and has
// no tests of its own. It counts what it received and hands out tokens tok-1, tok-2, ..., so the tests can assert how
// often the SDK enrolled and renewed.

package smosdk

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeStack answers like the pieces of the platform the SDK talks to: the gateway's /bootstrap, SME's invoker
// registration and token endpoint under /sme, and an API handler for everything else.
type fakeStack struct {
	t   *testing.T
	srv *httptest.Server

	mu            sync.Mutex
	bootstraps    int
	registrations int
	grants        int
	issued        []string // every access token handed out, in order
	validToken    map[string]bool
	expiresIn     int
	bootstrapKey  string // required X-Bootstrap-Key, "" = none
	forgetInvoker bool   // the next grant answers invalid_client (SME purged the invoker)
	lastGrant     map[string]string
	regHeaders    http.Header
	api           func(w http.ResponseWriter, r *http.Request)
}

// newStack starts a fakeStack on a local port and closes it when the test ends. Tokens are valid for 300 s until a test changes expiresIn.
func newStack(t *testing.T) *fakeStack {
	s := &fakeStack{t: t, validToken: map[string]bool{}, expiresIn: 300}
	s.srv = httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(s.srv.Close)
	return s
}

// serve answers the platform paths itself and passes the rest to s.api after checking that the Bearer token is one it
// issued and has not revoked (401 otherwise). s.mu is held, except while s.api runs.
func (s *fakeStack) serve(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case r.URL.Path == "/bootstrap":
		s.bootstraps++
		if s.bootstrapKey != "" && r.Header.Get("X-Bootstrap-Key") != s.bootstrapKey {
			writeJSON(w, 401, map[string]any{"title": "UNAUTHORIZED", "status": 401, "detail": "this gateway asks for the bootstrap key"})
			return
		}
		writeJSON(w, 200, map[string]any{"apiEndpoints": []any{
			map[string]any{"uri": s.srv.URL + "/sme/service-apis/v1"},
			map[string]any{"tokenEndPoint": map[string]any{"uri": s.srv.URL + "/sme/oauth2/token"}},
		}})
	case r.URL.Path == "/sme/invoker-registrations" && r.Method == "POST":
		s.registrations++
		s.regHeaders = r.Header.Clone()
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if !strings.HasPrefix(body["apiInvokerPublicKey"], "smo-rapp:") {
			writeJSON(w, 422, map[string]any{"detail": "label"})
			return
		}
		writeJSON(w, 201, map[string]any{"apiInvokerId": fmt.Sprintf("api-invoker-%d", s.registrations), "onboardingSecret": fmt.Sprintf("secret-%d", s.registrations), "role": "rapp"})
	case r.URL.Path == "/sme/oauth2/token" && r.Method == "POST":
		s.grants++
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		s.lastGrant = body
		if s.forgetInvoker {
			s.forgetInvoker = false
			writeJSON(w, 400, map[string]any{"error": "invalid_client", "error_description": "invoker not registered"})
			return
		}
		tok := fmt.Sprintf("tok-%d", s.grants)
		s.issued = append(s.issued, tok)
		s.validToken[tok] = true
		writeJSON(w, 200, map[string]any{"access_token": tok, "expires_in": s.expiresIn, "token_type": "Bearer", "scope": body["scope"]})
	default:
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !s.validToken[tok] {
			writeJSON(w, 401, map[string]any{"detail": map[string]any{"title": "UNAUTHORIZED", "status": 401, "detail": "token not valid"}})
			return
		}
		if s.api == nil {
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		s.mu.Unlock() // the API handler runs unlocked: it may call back into the stack's helpers
		defer s.mu.Lock()
		s.api(w, r)
	}
}

// revoke makes the stack answer 401 to tok from now on.
func (s *fakeStack) revoke(tok string) { s.mu.Lock(); s.validToken[tok] = false; s.mu.Unlock() }

// counts returns how many times /bootstrap, the registration and the token endpoint were called.
func (s *fakeStack) counts() (bootstraps, registrations, grants int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.bootstraps, s.registrations, s.grants
}

// writeJSON answers with status and v encoded as JSON.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// readBody returns the request body as a string.
func readBody(r *http.Request) string {
	b, _ := io.ReadAll(r.Body)
	return string(b)
}

// fast is a retry policy that waits (almost) nothing and is deterministic.
var fast = RetryPolicy{MaxAttempts: 4, BaseDelay: time.Millisecond, MaxDelay: 2 * time.Millisecond, NoJitter: true}

// client returns a Client for the stack with the fast retry policy; mutate edits the Config first.
func (s *fakeStack) client(t *testing.T, mutate ...func(*Config)) *Client {
	t.Helper()
	cfg := Config{GatewayURL: s.srv.URL, Name: "test-rapp", Retry: fast}
	for _, m := range mutate {
		m(&cfg)
	}
	c, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return c
}
