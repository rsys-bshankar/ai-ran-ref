// config_test.go covers ConfigFromEnv through configFrom (config.go): the defaults, the variables it reads, the NAME or
// NAME_FILE rule for secrets, and the mutual-TLS switch. The environment and the files are maps passed to configFrom, so
// no real environment is read. Run: cd smo/sdk-go && go test -race ./... .

package smosdk

import (
	"errors"
	"os"
	"strings"
	"testing"
)

// env returns a getenv function over a map; a name that is not in the map reads as empty.
func env(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

// files returns a readFile function over a map from path to content; any other path fails with os.ErrNotExist.
func files(m map[string]string) func(string) ([]byte, error) {
	return func(p string) ([]byte, error) {
		if v, ok := m[p]; ok {
			return []byte(v), nil
		}
		return nil, os.ErrNotExist
	}
}

// TestConfigFromEnvDefaults
// pins that an empty environment gives the compose gateway address and no invoker or HTTP client.
func TestConfigFromEnvDefaults(t *testing.T) {
	cfg, err := configFrom(env(nil), files(nil))
	if err != nil || cfg.GatewayURL != DefaultGatewayURL || cfg.InvokerID != "" || cfg.HTTPClient != nil {
		t.Fatalf("%v %+v", err, cfg)
	}
}

// TestConfigFromEnvReadsTheVariablesThePythonSDKReads
// pins the variable names shared with the Python SDK, and that a secret file loses its trailing newline.
func TestConfigFromEnvReadsTheVariablesThePythonSDKReads(t *testing.T) {
	cfg, err := configFrom(env(map[string]string{
		"R1_GATEWAY_URL": "http://gw:9000", "MODULE": "hello-rapp", "SMO_INVOKER_ID": "inv-1",
		"SMO_INVOKER_SECRET_FILE": "/run/secrets/inv", "SMO_BOOTSTRAP_KEY": "bk",
	}), files(map[string]string{"/run/secrets/inv": "s3cret\n"}))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.GatewayURL != "http://gw:9000" || cfg.Name != "hello-rapp" || cfg.InvokerID != "inv-1" || cfg.InvokerSecret != "s3cret" || cfg.BootstrapKey != "bk" {
		t.Fatalf("%+v", cfg)
	}
}

// TestConfigFromEnvRefusesAValueAndAFileForTheSameSecret
// pins that setting both NAME and NAME_FILE is an error rather than a silent choice.
func TestConfigFromEnvRefusesAValueAndAFileForTheSameSecret(t *testing.T) {
	_, err := configFrom(env(map[string]string{"SMO_BOOTSTRAP_KEY": "a", "SMO_BOOTSTRAP_KEY_FILE": "/f"}), files(map[string]string{"/f": "b"}))
	if err == nil || !strings.Contains(err.Error(), "set only one") {
		t.Fatalf("err = %v", err)
	}
}

// TestConfigFromEnvReportsAnUnreadableSecretFile
// pins that an unreadable secret file is an error that wraps the cause, not an empty secret.
func TestConfigFromEnvReportsAnUnreadableSecretFile(t *testing.T) {
	_, err := configFrom(env(map[string]string{"SMO_INVOKER_SECRET_FILE": "/missing"}), files(nil))
	if err == nil || !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("err = %v", err)
	}
}

// TestMutualTLSFromEnvNeedsUsableFiles
// pins that SMO_MTLS=on with a missing CA file, or a CA file without a certificate, fails at configuration time.
func TestMutualTLSFromEnvNeedsUsableFiles(t *testing.T) {
	_, err := configFrom(env(map[string]string{"SMO_MTLS": "on", "R1_GATEWAY_URL": "http://gw:8000", "SMO_MTLS_CA_FILE": "/nope/ca.crt"}), files(nil))
	if err == nil || !strings.Contains(err.Error(), "CA file") {
		t.Fatalf("err = %v", err)
	}
	if _, err := MutualTLSClient("/nope", "/nope", "/nope"); err == nil {
		t.Fatal("expected an error")
	}
	dir := t.TempDir()
	bad := dir + "/ca.crt"
	if err := os.WriteFile(bad, []byte("not a certificate"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := MutualTLSClient(bad, bad, bad); err == nil || !strings.Contains(err.Error(), "no certificate") {
		t.Fatalf("err = %v", err)
	}
}

// TestMutualTLSOffLeavesTheSchemeAlone
// pins that SMO_MTLS=off keeps http:// and builds no HTTP client.
func TestMutualTLSOffLeavesTheSchemeAlone(t *testing.T) {
	cfg, err := configFrom(env(map[string]string{"SMO_MTLS": "off", "R1_GATEWAY_URL": "http://gw:8000"}), files(nil))
	if err != nil || cfg.GatewayURL != "http://gw:8000" || cfg.HTTPClient != nil {
		t.Fatalf("%v %+v", err, cfg)
	}
}
