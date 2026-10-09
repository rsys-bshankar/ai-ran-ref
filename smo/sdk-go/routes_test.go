package smosdk

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEveryRouteTheSDKCallsIsInTheCommittedOpenAPIDocuments is the drift check that stands in for generated clients
// (README, "Why hand-written"): docs/openapi/<module>.json is generated from each module's live FastAPI schema and
// kept current by the platform's own CI, so a route renamed or removed there fails this test instead of an rApp.
func TestEveryRouteTheSDKCallsIsInTheCommittedOpenAPIDocuments(t *testing.T) {
	docs := map[string]map[string]map[string]json.RawMessage{}
	for _, r := range allRoutes {
		if _, ok := docs[r.spec]; !ok {
			raw, err := os.ReadFile(filepath.Join("..", "docs", "openapi", r.spec+".json"))
			if err != nil {
				t.Fatalf("%s: %v", r.spec, err)
			}
			var doc struct {
				Paths map[string]map[string]json.RawMessage `json:"paths"`
			}
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatalf("%s: %v", r.spec, err)
			}
			docs[r.spec] = doc.Paths
		}
		ops, ok := docs[r.spec][r.tmpl]
		if !ok {
			t.Errorf("%s %s is not a path of docs/openapi/%s.json", r.method, r.tmpl, r.spec)
			continue
		}
		if _, ok := ops[strings.ToLower(r.method)]; !ok {
			t.Errorf("%s %s: the document has the path but not the method", r.method, r.tmpl)
		}
	}
}

func TestRoutePathFillsAndEscapesPlaceholders(t *testing.T) {
	if got := operatorAPIPutRoute.path("a/b c"); got != "/rapp-mgmt/instances/a%2Fb%20c/operator-api" {
		t.Fatalf("path = %q", got)
	}
	if got := tokenRoute.path(); got != "/oauth2/token" {
		t.Fatalf("direct route path = %q", got)
	}
	for name, f := range map[string]func(){
		"too many": func() { dmeTypesRoute.path("x") },
		"too few":  func() { instanceRoute.path() },
	} {
		func() {
			defer func() {
				if recover() == nil {
					t.Errorf("%s: expected a panic (a programming error)", name)
				}
			}()
			f()
		}()
	}
}
