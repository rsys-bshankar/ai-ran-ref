// Command hello-rapp is the smallest rApp written against the Go SDK. It
//
//   - obtains its access token (the SDK discovers the token endpoint, enrolls an API invoker at SME and asks for the
//     smo-rapp scope, or uses the invoker rApp Management issued for the instance: SMO_INVOKER_ID / SMO_INVOKER_SECRET);
//   - registers its operator API with rApp Management (when HELLO_INSTANCE_ID is set), so the page its manifest
//     declares (package/manifest.yaml, operatorUi) is drawn by the operator console and served by this process;
//   - "heartbeats": every HELLO_HEARTBEAT_SECONDS it reads the DME type catalogue through R1, the data route a real
//     rApp starts with, and keeps the count and time for the page;
//   - on SIGTERM/SIGINT withdraws its operator API registration and exits 0 (the instance itself is terminated by an
//     operator through rApp Management, which an rApp may not do to itself).
//
// Environment (the SDK's own variables are in sdk-go/README.md): HELLO_INSTANCE_ID, HELLO_OPERATOR_API_BASE (default
// http://hello-rapp:8000), HELLO_LISTEN (default :8000), HELLO_HEARTBEAT_SECONDS (default 30).
//
// `hello-rapp -probe` GETs the local /health and exits 0 or 1: the container healthcheck, since the image has no shell.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	smosdk "github.com/rsys-bshankar/ai-ran-ref/smo/sdk-go"
)

// state is what the operator page shows; the status route serves a copy of it.
type state struct {
	mu         sync.Mutex
	Registered bool      `json:"operatorApiRegistered"`
	Beats      int       `json:"beats"`
	LastBeatAt time.Time `json:"lastBeatAt"`
	LastError  string    `json:"lastError"`
	DmeTypes   int       `json:"dmeTypes"`
}

func (s *state) snapshot() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{"state": "RUNNING", "operatorApiRegistered": s.Registered, "beats": s.Beats, "lastBeatAt": s.LastBeatAt,
		"lastError": s.LastError, "dmeTypes": s.DmeTypes}
}

// beat reads the DME type catalogue through R1 (a data route) and records the outcome.
func beat(ctx context.Context, c *smosdk.Client, s *state) {
	page, err := c.Data().DiscoverTypes(ctx, "")
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Beats++
	s.LastBeatAt = time.Now().UTC()
	if err != nil {
		s.LastError = err.Error()
		return
	}
	s.LastError, s.DmeTypes = "", len(page.Items)
}

func newMux(c *smosdk.Client, s *state) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	// the operator API: the routes package/manifest.yaml declares, under the base registered at rApp Management
	mux.HandleFunc("GET /instances/{instanceId}/status", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, s.snapshot())
	})
	mux.HandleFunc("POST /instances/{instanceId}/run", func(w http.ResponseWriter, r *http.Request) {
		beat(r.Context(), c, s)
		writeJSON(w, http.StatusOK, s.snapshot())
	})
	return mux
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func getenv(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "-probe" {
		os.Exit(probe(getenv("HELLO_LISTEN", ":8000")))
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("hello-rapp stopped", "error", err)
		os.Exit(1)
	}
}

func probe(listen string) int {
	addr := listen
	if addr[0] == ':' {
		addr = "127.0.0.1" + addr
	}
	cl := http.Client{Timeout: 3 * time.Second}
	resp, err := cl.Get("http://" + addr + "/health")
	if err != nil {
		return 1
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func run(log *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	cfg, err := smosdk.ConfigFromEnv()
	if err != nil {
		return err
	}
	if cfg.Name == "" {
		cfg.Name = "hello-rapp"
	}
	client, err := smosdk.New(cfg)
	if err != nil {
		return err
	}
	interval := 30
	if v, err := strconv.Atoi(getenv("HELLO_HEARTBEAT_SECONDS", "30")); err == nil && v > 0 {
		interval = v
	}

	// 1. register: the first call enrolls the invoker and obtains the token (retried with backoff while the stack starts)
	if _, err := client.Token(ctx); err != nil {
		return fmt.Errorf("no access token: %w", err)
	}
	log.Info("registered with SME and holding an access token", "gateway", cfg.GatewayURL)

	st := &state{}
	srv := &http.Server{Addr: getenv("HELLO_LISTEN", ":8000"), Handler: newMux(client, st), ReadHeaderTimeout: 10 * time.Second}
	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()

	// 2. declare where the operator page's routes are served
	instance := os.Getenv("HELLO_INSTANCE_ID")
	if instance != "" {
		base := getenv("HELLO_OPERATOR_API_BASE", "http://hello-rapp:8000")
		if err := client.RApp().RegisterOperatorAPI(ctx, instance, base); err != nil {
			return fmt.Errorf("registering the operator API of instance %s: %w", instance, err)
		}
		st.mu.Lock()
		st.Registered = true
		st.mu.Unlock()
		log.Info("operator API registered", "instance", instance, "base", base)
	}

	// 3. heartbeat until told to stop
	beat(ctx, client, st)
	tick := time.NewTicker(time.Duration(interval) * time.Second)
	defer tick.Stop()
loop:
	for {
		select {
		case <-tick.C:
			beat(ctx, client, st)
			log.Info("heartbeat", "status", st.snapshot())
		case err := <-serveErr:
			if !errors.Is(err, http.ErrServerClosed) {
				return err
			}
			break loop
		case <-ctx.Done():
			break loop
		}
	}

	// 4. terminate: leave cleanly (a fresh context: the signal's has expired)
	bye, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if instance != "" {
		if err := client.RApp().ClearOperatorAPI(bye, instance); err != nil {
			log.Warn("could not withdraw the operator API", "error", err)
		}
	}
	if err := srv.Shutdown(bye); err != nil {
		return err
	}
	log.Info("stopped")
	return nil
}
