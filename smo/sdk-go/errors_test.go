// errors_test.go covers newError and the status helpers of errors.go: the body shapes the platform answers with, the
// 4 KiB cut of the kept body, and the errors.As lookup through wrapping. Pure functions, no server. Run:
// cd smo/sdk-go && go test -race ./... .

package smosdk

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

// TestErrorMapping is a table of response bodies (ProblemDetails, plain and list details, OAuth errors, the gateway's flat
// problem, non-JSON, empty) and the status, title and detail each must map to; it also checks the shape of Error().
func TestErrorMapping(t *testing.T) {
	for _, tc := range []struct {
		name, body    string
		status        int
		title, detail string
	}{
		{"problem details under detail", `{"detail":{"title":"RAPP_INSTANCE_NOT_FOUND","status":404,"detail":"no such instance"}}`, 404, "RAPP_INSTANCE_NOT_FOUND", "no such instance"},
		{"plain string detail", `{"detail":"boom"}`, 500, "", "boom"},
		{"validation list", `{"detail":[{"loc":["body","x"],"msg":"required"}]}`, 422, "", `[{"loc":["body","x"],"msg":"required"}]`},
		{"oauth error", `{"error":"invalid_client","error_description":"invoker not registered"}`, 400, "invalid_client", "invoker not registered"},
		{"oauth error without description", `{"error":"unsupported_grant_type"}`, 400, "unsupported_grant_type", ""},
		{"gateway flat problem", `{"title":"UNAUTHORIZED","status":401,"detail":"this gateway asks for the bootstrap key"}`, 401, "UNAUTHORIZED", "this gateway asks for the bootstrap key"},
		{"problem with structured detail", `{"detail":{"title":"X","status":409,"detail":{"a":1}}}`, 409, "X", `{"a":1}`},
		{"not json", "Bad Gateway\n", 502, "", "Bad Gateway"},
		{"empty", "", 503, "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newError("GET", "/x", tc.status, []byte(tc.body))
			if e.StatusCode != tc.status || e.Title != tc.title || e.Detail != tc.detail {
				t.Fatalf("got %+v", e)
			}
			if !strings.Contains(e.Error(), fmt.Sprint(tc.status)) || !strings.HasPrefix(e.Error(), "GET /x: ") {
				t.Fatalf("Error() = %q", e.Error())
			}
		})
	}
}

// TestErrorKeepsOnlyTheStartOfALargeBody
// pins the 4 KiB bound on the body an *Error keeps.
func TestErrorKeepsOnlyTheStartOfALargeBody(t *testing.T) {
	e := newError("GET", "/x", 500, []byte(strings.Repeat("a", 10000)))
	if len(e.Body) != maxBodyInError {
		t.Fatalf("body = %d bytes", len(e.Body))
	}
}

// TestStatusHelpersLookThroughWrapping
// pins that StatusOf and the Is* helpers find an *Error inside a wrapped error, and that other errors have status 0.
func TestStatusHelpersLookThroughWrapping(t *testing.T) {
	wrapped := fmt.Errorf("outer: %w", newError("POST", "/x", 404, nil))
	if !IsNotFound(wrapped) || IsConflict(wrapped) || IsForbidden(wrapped) || StatusOf(wrapped) != 404 {
		t.Fatal("IsNotFound")
	}
	if !IsForbidden(newError("GET", "/x", 403, nil)) || !IsConflict(newError("GET", "/x", 409, nil)) {
		t.Fatal("helpers")
	}
	if StatusOf(errors.New("plain")) != 0 || StatusOf(nil) != 0 {
		t.Fatal("a non-platform error has no status")
	}
}
