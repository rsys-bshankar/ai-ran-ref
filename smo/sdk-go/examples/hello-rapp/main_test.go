package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	smosdk "github.com/rsys-bshankar/ai-ran-ref/smo/sdk-go"
)

// platform answers the four things the example needs: /bootstrap, SME's registration and token, and the DME catalogue.
func platform(t *testing.T) *httptest.Server {
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/bootstrap":
			_, _ = w.Write([]byte(`{"apiEndpoints":[{"tokenEndPoint":{"uri":"` + srv.URL + `/sme/oauth2/token"}}]}`))
		case "/sme/invoker-registrations":
			w.WriteHeader(201)
			_, _ = w.Write([]byte(`{"apiInvokerId":"inv","onboardingSecret":"sec"}`))
		case "/sme/oauth2/token":
			_, _ = w.Write([]byte(`{"access_token":"t","expires_in":300}`))
		case "/dme/dme-types":
			if r.Header.Get("Authorization") != "Bearer t" {
				w.WriteHeader(401)
				return
			}
			_, _ = w.Write([]byte(`{"items":[{"typeName":"a"},{"typeName":"b"}],"total":2,"limit":100,"offset":0}`))
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestTheOperatorRoutesServeWhatTheManifestDeclares(t *testing.T) {
	p := platform(t)
	c, err := smosdk.New(smosdk.Config{GatewayURL: p.URL})
	if err != nil {
		t.Fatal(err)
	}
	app := httptest.NewServer(newMux(c, &state{}))
	defer app.Close()

	post, err := http.Post(app.URL+"/instances/i-1/run", "application/json", strings.NewReader("{}"))
	if err != nil || post.StatusCode != 200 {
		t.Fatalf("run: %v %v", err, post)
	}
	get, err := http.Get(app.URL + "/instances/i-1/status")
	if err != nil || get.StatusCode != 200 {
		t.Fatalf("status: %v %v", err, get)
	}
	var st map[string]any
	if err := json.NewDecoder(get.Body).Decode(&st); err != nil {
		t.Fatal(err)
	}
	// the fields package/manifest.yaml's panels read
	if st["state"] != "RUNNING" || st["beats"] != float64(1) || st["dmeTypes"] != float64(2) || st["lastError"] != "" {
		t.Fatalf("status = %v", st)
	}
	if st["lastBeatAt"] == nil {
		t.Fatal("no lastBeatAt")
	}
}

func TestABeatThatFailsIsRecordedNotFatal(t *testing.T) {
	c, _ := smosdk.New(smosdk.Config{GatewayURL: "http://127.0.0.1:1", BearerToken: "t", Retry: smosdk.RetryPolicy{MaxAttempts: 1}})
	s := &state{}
	beat(t.Context(), c, s)
	if s.Beats != 1 || s.LastError == "" || s.DmeTypes != 0 {
		t.Fatalf("%+v", s)
	}
}

func TestProbe(t *testing.T) {
	app := httptest.NewServer(newMux(nil, &state{}))
	defer app.Close()
	if got := probe(strings.TrimPrefix(app.URL, "http://")); got != 0 {
		t.Fatalf("probe = %d", got)
	}
	if got := probe("127.0.0.1:1"); got != 1 {
		t.Fatalf("probe of nothing = %d", got)
	}
}
