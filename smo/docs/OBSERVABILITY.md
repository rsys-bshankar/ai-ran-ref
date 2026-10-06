# Traces and logs (PR-OBS-3, PR-OBS-6)

How to see one request across the SMO: a distributed trace in Tempo, the structured logs in Loki, and Grafana joining the two on the trace id. Metrics are separate (`/metrics`, `PR-OBS-2`). All of this is off by default; a deployment that does nothing behaves as before.

## The ids a request carries

| Id | Where it comes from | Where to find it |
|---|---|---|
| `X-Correlation-ID` | the caller's, else a UUID made by the first module (R1 Termination for external traffic); `R1Client` and the gateway pass it on | every log line (`correlationId`), the response header, the audit chain |
| `traceparent` (W3C Trace Context) | the caller's; with spans on, a request without one starts a new trace | every log line of a request that belongs to a trace (`traceId`), every span |

The two are independent: the correlation id is this build's own request id and is always present; the trace id exists when a caller sent a `traceparent` or spans are on. A span carries the correlation id as `smo.correlation_id`, so either id finds the other.

## Distributed traces

**Propagation** is always on and needs nothing installed (`smo_shared/tracing.py`). A valid inbound `traceparent` (and `tracestate`) is held for the request and sent on by every `R1Client` call and by the R1 Termination gateway, so the whole fan-out of one request shares the caller's trace id. An invalid header is ignored; a request with none sends none.

**Spans** are opt-in. Set `SMO_OTEL_ENDPOINT` to the OTLP/HTTP base URL of Tempo or an OpenTelemetry collector (`http://tempo:4318`) and have the OpenTelemetry packages in the image. Then each request a module handles is a `SERVER` span (`GET /deployments/{deployment_id}`, with `http.route` and `http.response.status_code`; a 5xx is an error span), and each call through `R1Client`, and each forward by the gateway, is a `CLIENT` span (`POST nfo`, with `smo.target`). The `traceparent` sent downstream names the client span, so the next module's server span is its child.

| Setting | Meaning |
|---|---|
| `SMO_OTEL_ENDPOINT` | OTLP/HTTP base URL (`/v1/traces` is appended); empty or unset: no spans |
| `SMO_OTEL_SAMPLE_RATIO` | share of new traces recorded, 0 to 1, default 1; a trace that arrives with a sampled flag keeps it |
| `MODULE` | the span's `service.name` (already set in every container) |

Not recorded: request or response bodies, headers, query strings, raw paths (the span name is the route template), credentials. Without the packages the service logs one warning at start-up and propagates only.

**The packages are an optional build.** `requirements/tracing.txt` (hashed, `smo-shared[tracing]`) is installed into an image only with `--build-arg WITH_TRACING=1`; the images the release workflow publishes do not have it. Compose: `SMO_WITH_TRACING=1 docker compose build`. For Kubernetes build and publish your own images with the argument and set `image.registry` / `image.prefix`.

**Not done:** database spans (SQLAlchemy), and spans for calls that do not go through `R1Client` (the gateway's token check against SME, caller-registered webhooks). `OPEN_ITEMS.md`, `OBS-3.4`.

### Compose

```bash
SMO_WITH_TRACING=1 SMO_OTEL_ENDPOINT=http://tempo:4318 docker compose --profile tracing up -d --build
```

Grafana is at http://localhost:3001 (anonymous, a lab: bound to this machine). Explore, data source Tempo, search by service or paste a trace id; from a span, "Logs for this span" opens Loki with the trace id when the `logging` profile runs too.

### Kubernetes

```bash
helm upgrade --install smo deploy/helm/smo -n smo --create-namespace \
  --set observability.tempo.enabled=true --set observability.grafana.enabled=true      # modules then send to http://tempo:4318
```

or `--set tracing.endpoint=http://collector.obs:4318` for a Tempo or collector of your own (the bundled one is a lab store on an emptyDir). The configuration is `deploy/helm/smo/files/observability/`, the same files compose mounts.

## Logs

Every service writes one JSON object per line on stdout (`smo_shared/logconfig.py`: `timestamp`, `level`, `logger`, `service`, `message`, `correlationId`, `traceId`, and the extras; the access line adds `method`, `route`, `status`, `durationMs`). Fluent Bit reads the containers' logs, unpacks that JSON and ships it to Loki, with two stream labels: `service` and `level` (plus `job="smo"`, and the namespace on Kubernetes). Everything else stays in the line and is filtered at query time.

```bash
docker compose --profile logging up -d          # Loki, Fluent Bit and Grafana
```

Kubernetes: `--set observability.loki.enabled=true --set observability.fluentBit.enabled=true --set observability.grafana.enabled=true` (Fluent Bit is a DaemonSet that reads `/var/log/containers`, so it runs as root: a lab choice; in production ship the pods' stdout with the platform's own agent, the JSON is the same).

### Queries (Grafana, Explore, data source Loki)

| Question | LogQL |
|---|---|
| Every line of one request, by correlation id | `{job="smo"} \| json \| correlationId="5b0f1c2e-..."` |
| Every line of one trace | `{job="smo"} \| json \| traceId="4bf92f3577b34da6a3ce929d0e0e4736"` |
| Errors of one module | `{job="smo", service="nfo", level="ERROR"}` |
| Failed requests through the gateway | `{job="smo", service="r1-termination"} \| json \| logger="smo.access" \| status >= 500` |
| Slow requests (over a second) | `{job="smo"} \| json \| logger="smo.access" \| durationMs > 1000` |
| One route | `{job="smo"} \| json \| route="/deployments/{deployment_id}"` |
| Requests per module, per minute | `sum by (service) (count_over_time({job="smo"} \| json \| logger="smo.access" [1m]))` |

Use the response header's `X-Correlation-ID` (or the `traceId` of a trace in Tempo) as the value. In Loki, a `traceId` in a line is a link that opens the trace in Tempo (derived field), and the other way round.

### Elasticsearch

The log line is flat JSON, so a mapping needs few decisions: `timestamp` as `date`; `level`, `logger`, `service`, `route`, `method`, `correlationId`, `traceId` as `keyword`; `status` as `integer`; `durationMs` as `float`; `message` and `exception` as `text`; map any other key dynamically as `keyword`. Not shipped or tested here (`OPEN_ITEMS.md`, `OBS-6.3`).

## What is verified

`shared/tests/test_tracing.py`: parsing per the W3C rules; a `traceparent` and `tracestate` surviving an in-process module hop (an `R1Client` call from one app into another) with spans off; with the SDK, the span tree across the hop (caller's span, server span, client span, next server span) and an error span on a 5xx; the trace id in the JSON log line. `r1-termination/tests/test_main.py`: the gateway forwards a valid header and drops an invalid one. `tests_integration/test_helm_chart.py`: the stack renders and is off by default. Not verified here: a live Tempo, Loki, Fluent Bit and Grafana (the images and configuration files were not run in the build environment).
