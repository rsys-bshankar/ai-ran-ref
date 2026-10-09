package smosdk

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// DefaultGatewayURL is where R1 Termination is in the reference compose stack.
const DefaultGatewayURL = "http://r1-termination:8000"

// Config configures a Client. The zero value is usable except for the gateway,
// which defaults to the compose address.
type Config struct {
	// GatewayURL is R1 Termination's base URL, no trailing path. Default DefaultGatewayURL.
	GatewayURL string
	// Name labels this rApp in its API-invoker registration at SME ("smo-rapp:<Name>:<random>"). Default "go-rapp".
	Name string
	// InvokerID and InvokerSecret pin a pre-provisioned invoker identity (rApp Management issues one per instance:
	// POST /rapp-mgmt/instances/{id}/credentials, handed to the workload as SMO_INVOKER_ID / SMO_INVOKER_SECRET).
	// Both empty: the client enrolls its own invoker at SME on first use.
	InvokerID, InvokerSecret string
	// BootstrapKey is sent as X-Bootstrap-Key to GET /bootstrap; needed only when the gateway runs with R1_BOOTSTRAP_KEY.
	BootstrapKey string
	// BearerToken, when set, is sent as is and the SME flow is not used (the counterpart of R1Client(bearer_token=...)).
	// A 401 is then returned to the caller; there is no way to renew a token the SDK did not obtain.
	BearerToken string
	// HTTPClient carries every request (timeouts, proxy, mutual TLS). Default: a client with a 30 s timeout per attempt.
	HTTPClient *http.Client
	// Retry is the retry policy; the zero value is 4 attempts, 200 ms base, 5 s cap, jitter.
	Retry RetryPolicy
	// UserAgent is sent on every request. Default "smo-sdk-go/<Version>".
	UserAgent string
}

// ConfigFromEnv reads the same variables the Python SDK's R1Client reads, so one deployment environment configures
// either SDK:
//
//	R1_GATEWAY_URL                          the gateway (default http://r1-termination:8000)
//	MODULE                                  the registration label (default "go-rapp")
//	SMO_INVOKER_ID, SMO_INVOKER_SECRET      a pinned invoker identity ( SMO_INVOKER_SECRET_FILE: read the secret from a file )
//	SMO_BOOTSTRAP_KEY[_FILE]                the key for GET /bootstrap
//	SMO_MTLS=on, SMO_MTLS_CERT_FILE, SMO_MTLS_KEY_FILE, SMO_MTLS_CA_FILE
//	                                        mutual TLS to the gateway: http:// becomes https:// and the client presents its
//	                                        certificate (defaults /run/mtls/tls.crt, tls.key, ca.crt)
//
// SMO_IDENTITY_KIND is not read: this SDK is always an rApp (scope smo-rapp, no enrollment secret).
func ConfigFromEnv() (Config, error) { return configFrom(os.Getenv, os.ReadFile) }

func configFrom(getenv func(string) string, readFile func(string) ([]byte, error)) (Config, error) {
	secret := func(name string) (string, error) { // NAME or NAME_FILE, not both
		v, path := getenv(name), getenv(name+"_FILE")
		if v != "" && path != "" {
			return "", fmt.Errorf("both %s and %s_FILE are set: set only one", name, name)
		}
		if path != "" {
			b, err := readFile(path)
			if err != nil {
				return "", fmt.Errorf("%s_FILE=%s cannot be read: %w", name, path, err)
			}
			v = strings.TrimSuffix(string(b), "\n")
		}
		return v, nil
	}
	cfg := Config{GatewayURL: getenv("R1_GATEWAY_URL"), Name: getenv("MODULE"), InvokerID: getenv("SMO_INVOKER_ID")}
	var err error
	if cfg.InvokerSecret, err = secret("SMO_INVOKER_SECRET"); err != nil {
		return Config{}, err
	}
	if cfg.BootstrapKey, err = secret("SMO_BOOTSTRAP_KEY"); err != nil {
		return Config{}, err
	}
	if cfg.GatewayURL == "" {
		cfg.GatewayURL = DefaultGatewayURL
	}
	switch strings.ToLower(strings.TrimSpace(getenv("SMO_MTLS"))) {
	case "on", "1", "true", "yes", "require":
		dir := "/run/mtls"
		pick := func(name, def string) string {
			if v := getenv(name); v != "" {
				return v
			}
			return dir + "/" + def
		}
		hc, err := MutualTLSClient(pick("SMO_MTLS_CA_FILE", "ca.crt"), pick("SMO_MTLS_CERT_FILE", "tls.crt"), pick("SMO_MTLS_KEY_FILE", "tls.key"))
		if err != nil {
			return Config{}, err
		}
		cfg.HTTPClient = hc
		cfg.GatewayURL = strings.Replace(cfg.GatewayURL, "http://", "https://", 1)
	}
	return cfg, nil
}

// MutualTLSClient is an http.Client that verifies servers against the CA file and presents the client certificate
// (PR-SEC-2, SMO_MTLS=on). It also uses TLS 1.2 or later and the default 30 s timeout of a Client.
func MutualTLSClient(caFile, certFile, keyFile string) (*http.Client, error) {
	ca, err := os.ReadFile(caFile)
	if err != nil {
		return nil, fmt.Errorf("mTLS CA file: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, errors.New("mTLS CA file holds no certificate")
	}
	cert, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, fmt.Errorf("mTLS client certificate: %w", err)
	}
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool, Certificates: []tls.Certificate{cert}}
	return &http.Client{Transport: tr, Timeout: 30 * time.Second}, nil
}
