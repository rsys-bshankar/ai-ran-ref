// Package smosdk is the Go AI Runtime SDK: a thin client over the SMO's R1
// interface for an rApp written in Go. It is the counterpart of the Python SDK
// (smo/sdk/smo_sdk) and follows golden rule 6 of docs/ARCHITECTURE.md: it calls
// the same R1 Termination routes every other cross-module call uses, adds no
// business logic and does no client-side re-validation; the platform's own
// error is returned as an *Error.
//
//	cfg, _ := smosdk.ConfigFromEnv()
//	c, _ := smosdk.New(cfg)
//	types, err := c.Data().DiscoverTypes(ctx, "")   // token acquired, cached and renewed behind this call
//
// Standard library only. The pieces: Client.Do (any route, authenticated,
// retried), the namespace helpers Data, Models, Platform and RApp (one method
// per route the example rApp and a typical rApp's start-up need), and the
// token flow (auth.go).
package smosdk

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Version is the SDK's own version, sent in the User-Agent.
const Version = "0.1.0"

// maxResponseBytes bounds a response body read into memory.
const maxResponseBytes = 16 << 20

// CorrelationHeader carries a request-scoped id through every module (smo_shared/correlation.py).
const CorrelationHeader = "X-Correlation-ID"

type correlationKey struct{}

// WithCorrelationID returns a context whose calls carry the id in X-Correlation-ID, so one inbound request's
// whole fan-out across the platform shares it. Nothing is sent when the context has none.
func WithCorrelationID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, correlationKey{}, id)
}

// Client is safe for concurrent use. Build one per process: it holds the invoker identity and the cached token.
type Client struct {
	cfg     Config
	http    *http.Client
	gateway string
	tokens  *tokenSource
	now     func() time.Time // replaced in tests
}

// New validates cfg and returns a Client. It makes no network call: the token is obtained on first use.
func New(cfg Config) (*Client, error) {
	if cfg.GatewayURL == "" {
		cfg.GatewayURL = DefaultGatewayURL
	}
	u, err := url.Parse(cfg.GatewayURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("smosdk: GatewayURL %q is not an http(s) base URL", cfg.GatewayURL)
	}
	if (cfg.InvokerID == "") != (cfg.InvokerSecret == "") {
		return nil, errors.New("smosdk: InvokerID and InvokerSecret must be set together")
	}
	if cfg.Name == "" {
		cfg.Name = "go-rapp"
	}
	if cfg.UserAgent == "" {
		cfg.UserAgent = "smo-sdk-go/" + Version
	}
	c := &Client{cfg: cfg, http: cfg.HTTPClient, gateway: strings.TrimRight(cfg.GatewayURL, "/"), now: time.Now}
	if c.http == nil {
		c.http = &http.Client{Timeout: 30 * time.Second}
	}
	c.tokens = newTokenSource(c, cfg.InvokerID, cfg.InvokerSecret)
	return c, nil
}

// Request is one call to a platform route through the gateway.
type Request struct {
	Method string
	Path   string     // as served at R1, with the module prefix: "/dme/dme-types"
	Query  url.Values // optional
	Body   any        // marshalled as JSON when not nil
	Header http.Header
}

// Token returns a valid access token (obtained or renewed if needed). Mostly for diagnostics.
func (c *Client) Token(ctx context.Context) (string, error) {
	if c.cfg.BearerToken != "" {
		return c.cfg.BearerToken, nil
	}
	return c.tokens.get(ctx)
}

// Do sends the request with a Bearer token and decodes a JSON answer into out (nil: discard the body).
//
// It retries per Config.Retry, renews the token and repeats once on a 401, and repeats once a write that lost a race
// (409 CONCURRENT_MODIFICATION), as the Python SDK does. Every POST carries an Idempotency-Key (generated once per call
// and reused by every repeat, so the platform answers a repeat from the stored first answer if the first completed);
// supply your own in r.Header to choose it. An answer with a status >= 400 is returned as *Error.
func (c *Client) Do(ctx context.Context, r Request, out any) error {
	if !strings.HasPrefix(r.Path, "/") {
		return fmt.Errorf("smosdk: request path %q must start with /", r.Path)
	}
	method := strings.ToUpper(r.Method)
	var body []byte
	if r.Body != nil {
		var err error
		if body, err = json.Marshal(r.Body); err != nil {
			return fmt.Errorf("smosdk: encoding the request body: %w", err)
		}
	}
	hdr := http.Header{}
	for k, v := range r.Header {
		hdr[http.CanonicalHeaderKey(k)] = v
	}
	if body != nil {
		hdr.Set("Content-Type", "application/json")
	}
	if method == http.MethodPost && hdr.Get("Idempotency-Key") == "" {
		key, err := randomHex(16)
		if err != nil {
			return err
		}
		hdr.Set("Idempotency-Key", key)
	}
	if id, _ := ctx.Value(correlationKey{}).(string); id != "" {
		hdr.Set(CorrelationHeader, id)
	}
	target := c.gateway + r.Path
	if len(r.Query) > 0 {
		target += "?" + r.Query.Encode()
	}
	mutating := method != http.MethodGet && method != http.MethodHead

	token, err := c.Token(ctx)
	if err != nil {
		return err
	}
	renewed, repeated := false, false
	for {
		hdr.Set("Authorization", "Bearer "+token)
		resp, err := c.roundTrip(ctx, method, target, hdr, body, true)
		if err != nil {
			return err
		}
		if resp.status == http.StatusUnauthorized && !renewed && c.cfg.BearerToken == "" {
			// expired or revoked at SME since it was cached: one fresh token, one repeat
			renewed = true
			if token, err = c.tokens.refresh(ctx, token); err != nil {
				return err
			}
			continue
		}
		if resp.status >= 400 {
			e := newError(method, r.Path, resp.status, resp.body)
			if mutating && !repeated && isConcurrentModification(e) {
				repeated = true
				continue
			}
			return e
		}
		if out == nil || resp.status == http.StatusNoContent || len(bytes.TrimSpace(resp.body)) == 0 {
			return nil
		}
		if err := json.Unmarshal(resp.body, out); err != nil {
			return fmt.Errorf("smosdk: decoding the answer of %s %s: %w", method, r.Path, err)
		}
		return nil
	}
}

type response struct {
	status int
	header http.Header
	body   []byte
}

// roundTrip sends one request, repeating it per the retry policy. safe says whether the call may be repeated after a
// transport error or a 502/504 (the server may have acted); an unsafe call is repeated only on 429 and 503. The
// returned response may carry any status; only transport and context failures are errors.
func (c *Client) roundTrip(ctx context.Context, method, target string, hdr http.Header, body []byte, safe bool) (*response, error) {
	attempts := c.cfg.Retry.attempts()
	for attempt := 1; ; attempt++ {
		req, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("smosdk: building the request: %w", err)
		}
		for k, v := range hdr {
			req.Header[k] = v
		}
		req.Header.Set("User-Agent", c.cfg.UserAgent)
		req.Header.Set("Accept", "application/json")

		resp, err := c.http.Do(req)
		var retryAfter time.Duration
		if err == nil {
			data, rerr := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
			_ = resp.Body.Close()
			if rerr == nil {
				out := &response{status: resp.StatusCode, header: resp.Header, body: data}
				retry, ambiguous := retryableStatus(out.status)
				if !retry || (ambiguous && !safe) || attempt >= attempts {
					return out, nil
				}
				retryAfter = parseRetryAfter(resp.Header)
			} else {
				err = rerr
			}
		}
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if !safe || attempt >= attempts {
				return nil, fmt.Errorf("smosdk: %s %s: %w", method, req.URL.Path, err)
			}
		}
		if serr := sleep(ctx, c.cfg.Retry.wait(attempt, retryAfter)); serr != nil {
			return nil, serr
		}
	}
}

func randomHex(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("smosdk: random id: %w", err)
	}
	return hex.EncodeToString(b), nil
}
