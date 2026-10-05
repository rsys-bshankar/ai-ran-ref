"""Prints how many requests this pod has counted for GET /health (smo_http_requests_total), from its own /metrics.

    kubectl -n smo exec -i POD -- python3 - < scripts/k8s_route_count.py

Probes (/live, /ready) and /metrics are not counted by the service, so the number only moves when something calls /health: the CI job `helm`
uses it to see that calls to a Service reach every replica behind it.
"""
import re
import urllib.request

text = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=5).read().decode()
print(int(sum(float(m.group(1)) for m in re.finditer(r'^smo_http_requests_total\{[^}]*route="/health"[^}]*\} ([0-9.e+]+)', text, re.M))))
