// auth.go is the SDK's identity at SME: the OAuth2 client_credentials token every call to the platform carries.
//
// tokenSource finds SME's token endpoint through the gateway's /bootstrap, enrolls a CAPIF API invoker unless the
// configuration pins one, asks for a token with the rApp scope, caches it and renews it. Client.Token and Client.Do
// (client.go) are its callers; it sends its own HTTP through Client.roundTrip, so the retry policy applies to it too.
//
// It owns the invoker identity and the token of one Client and nothing else: retries, headers and error mapping stay in
// client.go, retry.go and errors.go. The three steps mirror smo_shared.r1_client._ModuleIdentity for an rApp
// (SMO_IDENTITY_KIND=rapp) and the Python and Java SDKs: change them together. An rApp never presents the enrollment
// secret, which is why SME records the invoker as an rApp (the scope check in auth_test.go).

package smosdk

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	// rappScope is the OAuth2 scope an rApp asks for (smo_shared/roles.py RAPP_SCOPE). SME grants the internal scopes
	// only to an invoker that presented the enrollment secret, which no rApp has.
	rappScope = "smo-rapp"
	// expiryMargin: a token is renewed this long before SME says it expires.
	expiryMargin = 30 * time.Second
	// defaultExpiresIn is assumed when the token answer carries no expires_in (as the Python client does).
	defaultExpiresIn = 60 * time.Second
	bootstrapKeyHdr  = "X-Bootstrap-Key"
	tokenPathSuffix  = "/oauth2/token"
)

// tokenSource is this process's OAuth2 client identity at SME and its cached access token. It does what
// smo_shared.r1_client._ModuleIdentity does for an rApp (SMO_IDENTITY_KIND=rapp):
//
//  1. GET {gateway}/bootstrap names SME's token endpoint (with X-Bootstrap-Key when the gateway asks for one);
//  2. unless an invoker is pinned (Config.InvokerID/InvokerSecret), POST {sme}/invoker-registrations enrolls a
//     CAPIF API invoker, without the enrollment secret: SME records it as an rApp;
//  3. POST the token endpoint with a client_credentials grant for scope smo-rapp.
//
// One identity and one cached token per Client; the token is renewed 30 s before it expires, and once when the
// gateway answers 401 for it. Callers wait on a mutex while one of them renews (single flight).
type tokenSource struct {
	c *Client

	mu            sync.Mutex
	tokenEndpoint string
	invokerID     string
	secret        string
	pinned        bool // the identity came from the configuration: never replaced by a fresh registration
	token         string
	expires       time.Time
}

func newTokenSource(c *Client, id, secret string) *tokenSource {
	return &tokenSource{c: c, invokerID: id, secret: secret, pinned: id != ""}
}

// get returns a valid token, renewing it if it is missing or about to expire.
func (t *tokenSource) get(ctx context.Context) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.token != "" && t.c.now().Before(t.expires) {
		return t.token, nil
	}
	return t.acquire(ctx)
}

// refresh replaces the token the gateway rejected. If another caller has already replaced it, that one is returned.
func (t *tokenSource) refresh(ctx context.Context, rejected string) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.token != "" && t.token != rejected {
		return t.token, nil
	}
	return t.acquire(ctx)
}

// acquire runs the three steps above as far as they are needed. t.mu is held.
func (t *tokenSource) acquire(ctx context.Context) (string, error) {
	t.token = ""
	if err := t.discover(ctx); err != nil {
		return "", fmt.Errorf("smosdk: discovering the token endpoint: %w", err)
	}
	if t.invokerID == "" {
		if err := t.onboard(ctx); err != nil {
			return "", fmt.Errorf("smosdk: enrolling an API invoker at SME: %w", err)
		}
	}
	tok, err := t.grant(ctx)
	var oauth *Error
	if errors.As(err, &oauth) && oauth.Title == "invalid_client" && !t.pinned {
		// SME no longer knows the invoker this process registered (purged as stale): enroll afresh, once. A pinned
		// invoker is never replaced: it is the instance's identity at rApp Management, and a new one would not be.
		if oerr := t.onboard(ctx); oerr != nil {
			return "", fmt.Errorf("smosdk: re-enrolling an API invoker at SME: %w", oerr)
		}
		tok, err = t.grant(ctx)
	}
	if err != nil {
		return "", fmt.Errorf("smosdk: requesting an access token: %w", err)
	}
	t.token = tok.AccessToken
	ttl := defaultExpiresIn
	if tok.ExpiresIn > 0 {
		ttl = time.Duration(tok.ExpiresIn) * time.Second
	}
	t.expires = t.c.now().Add(max(ttl-expiryMargin, time.Second))
	return t.token, nil
}

// discover learns SME's token endpoint from GET {gateway}/bootstrap, once per tokenSource: when the endpoint is already
// known it returns at once. X-Bootstrap-Key is sent when Config.BootstrapKey is set. It returns an *Error for a status
// >= 400 (a gateway that asks for a key answers 401), and an error when the answer is not JSON, names no
// tokenEndPoint.uri, or names one that does not end in /oauth2/token (smeBase finds SME's own API by trimming that
// suffix, so any other shape is refused). The first entry of apiEndpoints that has a token endpoint wins. The caller holds t.mu.
func (t *tokenSource) discover(ctx context.Context) error {
	if t.tokenEndpoint != "" {
		return nil
	}
	hdr := http.Header{}
	if t.c.cfg.BootstrapKey != "" {
		hdr.Set(bootstrapKeyHdr, t.c.cfg.BootstrapKey)
	}
	resp, err := t.c.roundTrip(ctx, http.MethodGet, t.c.gateway+bootstrapRoute.path(), hdr, nil, true)
	if err != nil {
		return err
	}
	if resp.status >= 400 {
		return newError(http.MethodGet, bootstrapRoute.path(), resp.status, resp.body)
	}
	var info struct {
		APIEndpoints []struct {
			TokenEndPoint struct {
				URI string `json:"uri"`
			} `json:"tokenEndPoint"`
		} `json:"apiEndpoints"`
	}
	if err := json.Unmarshal(resp.body, &info); err != nil {
		return fmt.Errorf("decoding the bootstrap answer: %w", err)
	}
	for _, ep := range info.APIEndpoints {
		if ep.TokenEndPoint.URI != "" {
			if !strings.HasSuffix(ep.TokenEndPoint.URI, tokenPathSuffix) {
				return fmt.Errorf("the token endpoint %q does not end in %s", ep.TokenEndPoint.URI, tokenPathSuffix)
			}
			t.tokenEndpoint = ep.TokenEndPoint.URI
			return nil
		}
	}
	return errors.New("the bootstrap answer names no token endpoint")
}

// smeBase is the address of SME's own API, derived from the token endpoint as the Python client does.
func (t *tokenSource) smeBase() string { return strings.TrimSuffix(t.tokenEndpoint, tokenPathSuffix) }

// onboard enrolls a new CAPIF API invoker at SME (POST {sme}/invoker-registrations) and keeps the returned apiInvokerId
// and onboardingSecret as this process's identity. No enrollment secret is sent, so SME records an rApp. The request
// carries an opaque label "smo-rapp:<Name>:<random>" in place of a public key. It returns an *Error for a status >= 400
// and an error when the answer lacks either field. The caller holds t.mu.
func (t *tokenSource) onboard(ctx context.Context) error {
	label, err := randomToken(8)
	if err != nil {
		return err
	}
	// An opaque label, not a PEM key: this client authenticates with its onboarding secret.
	body, _ := json.Marshal(map[string]string{"apiInvokerPublicKey": fmt.Sprintf("smo-rapp:%s:%s", t.c.cfg.Name, label)})
	hdr := http.Header{"Content-Type": {"application/json"}}
	// Not safe to repeat after a transport error or 502/504: every registration mints a new invoker.
	resp, err := t.c.roundTrip(ctx, http.MethodPost, t.smeBase()+invokerRegistrationRoute.path(), hdr, body, false)
	if err != nil {
		return err
	}
	if resp.status >= 400 {
		return newError(http.MethodPost, invokerRegistrationRoute.path(), resp.status, resp.body)
	}
	var reg struct {
		APIInvokerID     string `json:"apiInvokerId"`
		OnboardingSecret string `json:"onboardingSecret"`
	}
	if err := json.Unmarshal(resp.body, &reg); err != nil || reg.APIInvokerID == "" || reg.OnboardingSecret == "" {
		return errors.New("the registration answer carries no apiInvokerId and onboardingSecret")
	}
	t.invokerID, t.secret = reg.APIInvokerID, reg.OnboardingSecret
	return nil
}

// tokenAnswer is the part of SME's token answer the SDK uses; ExpiresIn is in seconds and 0 when absent.
type tokenAnswer struct {
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in"`
}

// grant asks the token endpoint for a client_credentials token for scope smo-rapp with the stored invoker id and
// secret. It returns an *Error for a status >= 400 (invalid_client when SME no longer knows the invoker: acquire then
// enrolls afresh) and an error when the answer carries no access_token. The caller holds t.mu.
func (t *tokenSource) grant(ctx context.Context) (tokenAnswer, error) {
	body, _ := json.Marshal(map[string]string{
		"grant_type": "client_credentials", "client_id": t.invokerID, "client_secret": t.secret, "scope": rappScope,
	})
	hdr := http.Header{"Content-Type": {"application/json"}}
	resp, err := t.c.roundTrip(ctx, http.MethodPost, t.tokenEndpoint, hdr, body, true) // a grant has no side effect worth guarding
	if err != nil {
		return tokenAnswer{}, err
	}
	if resp.status >= 400 {
		return tokenAnswer{}, newError(http.MethodPost, tokenRoute.path(), resp.status, resp.body)
	}
	var ans tokenAnswer
	if err := json.Unmarshal(resp.body, &ans); err != nil || ans.AccessToken == "" {
		return tokenAnswer{}, errors.New("the token answer carries no access_token")
	}
	return ans, nil
}

// randomToken is n random bytes, URL-safe base64.
func randomToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
