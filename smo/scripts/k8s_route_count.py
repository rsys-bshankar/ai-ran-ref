"""Prints how many requests this pod has counted (smo_http_requests_total, all routes), from its own /metrics.

    kubectl -n smo exec -i POD -- python3 - < scripts/k8s_route_count.py

Probes (/live, /ready, /health) and /metrics are not counted by the service, so the number moves only with real requests: the CI job `helm`
sends a burst of calls to a path that does not exist (counted as `unmatched`, answered 404) and uses the difference to see that calls to a
Service reach every replica behind it.
"""
import re
import urllib.request

text = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=5).read().decode()
print(int(sum(float(m.group(1)) for m in re.finditer(r'^smo_http_requests_total\{[^}]*\} ([0-9.e+]+)', text, re.M))))
