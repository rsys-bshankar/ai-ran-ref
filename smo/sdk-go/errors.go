// errors.go maps a platform answer with an error status to *Error and offers StatusOf, IsNotFound, IsConflict and
// IsForbidden to test it. Client.Do (client.go) and the token flow (auth.go) return what newError builds here.
//
// The SDK adds no interpretation: Title and Detail carry the platform's own words, whichever of the four body shapes
// newError reads, and Body keeps the start of the raw answer. isConcurrentModification is the one place that gives a title
// meaning (the write race Do repeats once). errors_test.go has the table of body shapes: extend it with any new one.

package smosdk

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
)

// maxBodyInError bounds how much of a response body an *Error keeps.
const maxBodyInError = 4096

// Error is a platform answer with an error status (>= 400). It is the Go
// counterpart of the Python SDK's SdkError: the real status code and the
// platform's own explanation, never a silent success. Transport failures
// (no answer at all) are not *Error: they are returned as the underlying
// net/http error, and context cancellation as the context's error.
//
// The platform answers in one of four shapes, all understood here:
// ProblemDetails under "detail" ({"detail": {"title", "status", "detail"}}),
// a plain string or a list under "detail", an RFC 6749 OAuth error
// ({"error", "error_description"}) and the R1 gateway's flat ProblemDetails
// ({"title", "status", "detail"}).
type Error struct {
	Method     string // HTTP method of the failed call
	Path       string // request path (no query string, no host)
	StatusCode int
	Title      string // ProblemDetails title or OAuth "error" code, "" if the body had neither
	Detail     string // ProblemDetails detail, OAuth error_description or the plain-string detail
	Body       []byte // the response body, cut at 4 KiB
}

// Error formats the failure as "METHOD PATH: STATUS TITLE: DETAIL"; an empty title or detail is left out. The response
// body is not included.
func (e *Error) Error() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s %s: %d", e.Method, e.Path, e.StatusCode)
	if e.Title != "" {
		b.WriteString(" " + e.Title)
	}
	if e.Detail != "" {
		b.WriteString(": " + e.Detail)
	}
	return b.String()
}

// StatusOf returns the HTTP status of an *Error in err's chain, or 0.
func StatusOf(err error) int {
	var e *Error
	if errors.As(err, &e) {
		return e.StatusCode
	}
	return 0
}

// IsNotFound reports whether err is a platform 404.
func IsNotFound(err error) bool { return StatusOf(err) == http.StatusNotFound }

// IsConflict reports whether err is a platform 409 (an illegal transition, a
// name conflict, or a lost write race that survived the SDK's one repeat).
func IsConflict(err error) bool { return StatusOf(err) == http.StatusConflict }

// IsForbidden reports whether err is a platform 403 (for example
// ROLE_NOT_PERMITTED: the route is not open to the rApp role).
func IsForbidden(err error) bool { return StatusOf(err) == http.StatusForbidden }

// newError maps a response with an error status to an *Error.
func newError(method, path string, status int, body []byte) *Error {
	e := &Error{Method: method, Path: path, StatusCode: status, Body: body}
	if len(e.Body) > maxBodyInError {
		e.Body = e.Body[:maxBodyInError]
	}
	var raw struct {
		Detail           json.RawMessage `json:"detail"`
		Title            string          `json:"title"`
		Error            string          `json:"error"`
		ErrorDescription string          `json:"error_description"`
	}
	if json.Unmarshal(body, &raw) != nil {
		e.Detail = strings.TrimSpace(string(e.Body))
		return e
	}
	switch {
	case raw.Error != "": // RFC 6749 section 5.2
		e.Title, e.Detail = raw.Error, raw.ErrorDescription
	case len(raw.Detail) > 0:
		e.Title, e.Detail = parseDetail(raw.Detail)
		if e.Title == "" {
			e.Title = raw.Title // the gateway's flat shape: {"title", "status", "detail": "text"}
		}
	default:
		e.Title = raw.Title
	}
	return e
}

// parseDetail reads the value of a "detail" key: a ProblemDetails object, a string or anything else (kept as JSON).
func parseDetail(raw json.RawMessage) (title, detail string) {
	var problem struct {
		Title  string `json:"title"`
		Detail any    `json:"detail"`
	}
	if json.Unmarshal(raw, &problem) == nil {
		switch d := problem.Detail.(type) {
		case nil:
		case string:
			detail = d
		default:
			b, _ := json.Marshal(d)
			detail = string(b)
		}
		return problem.Title, detail
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return "", text
	}
	return "", string(raw)
}

// isConcurrentModification is the 409 whose ProblemDetails title is
// CONCURRENT_MODIFICATION (smo_shared/versioning.py): a write race the platform
// rolled back, which is safe to send once more. Other 409s are final answers.
func isConcurrentModification(err *Error) bool {
	return err != nil && err.StatusCode == http.StatusConflict && err.Title == "CONCURRENT_MODIFICATION"
}
