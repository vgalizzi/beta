"""/metrics tests: what the instrumentation records, and what it leaves out.

No service is needed: the client does not run the lifespan, so no dependency is contacted.
The metrics registry is global to the process and counts across tests, so the assertions
check that a series exists or not, never its exact value.
"""

from fastapi.testclient import TestClient

from beta.api.app import app

# Without the lifespan, /health fails on the missing clients: the test only needs the
# request to go through the middleware, so server errors become 500 responses instead
# of exceptions raised in the test.
client = TestClient(app, raise_server_exceptions=False)


def _requests_total_lines() -> list[str]:
    body = client.get("/metrics").text
    return [line for line in body.splitlines() if line.startswith("http_requests_total{")]


def test_metrics_are_served_in_prometheus_text_format() -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in response.text


def test_a_request_is_counted_under_its_route() -> None:
    client.get("/openapi.json")

    assert any('handler="/openapi.json"' in line for line in _requests_total_lines())


def test_unknown_paths_share_a_single_series() -> None:
    # One series per raw path would let any client create series at will.
    client.get("/no-such-path/1")
    client.get("/no-such-path/2")

    lines = _requests_total_lines()
    assert any('handler="none"' in line for line in lines)
    assert not any("no-such-path" in line for line in lines)


def test_probe_endpoints_are_not_counted() -> None:
    # /health and /metrics are polled by machines every few seconds: counted, they would
    # drown the real traffic.
    client.get("/health")
    client.get("/metrics")

    lines = _requests_total_lines()
    assert not any('handler="/health"' in line for line in lines)
    assert not any('handler="/metrics"' in line for line in lines)
