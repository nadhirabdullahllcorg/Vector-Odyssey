"""
Dashboard backend smoke tests -- the REST/WebSocket surface a browser talks
to. Skipped entirely if the optional `dashboard` dependency group isn't
installed, since nothing on the EA/VO side requires it (that separation is
also what tests/unit/test_architecture.py::test_vo_does_not_import_dashboard
checks the other direction).
"""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture
def client() -> fastapi_testclient.TestClient:
    from dashboard.backend.app import app

    with fastapi_testclient.TestClient(app) as test_client:
        yield test_client


def test_health_endpoint_reports_ok(client: fastapi_testclient.TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_runtime_endpoint_starts_disconnected(
    client: fastapi_testclient.TestClient,
) -> None:
    response = client.get("/api/runtime")
    body = response.json()

    assert body["connection"] == "DISCONNECTED"
    assert body["emitted_at"] is None


def test_settings_endpoint_is_honest_about_not_existing_yet(
    client: fastapi_testclient.TestClient,
) -> None:
    response = client.get("/api/settings")
    body = response.json()

    assert body["schema_version"] is None
    assert body["settings"] == {}


def test_settings_write_is_refused_with_a_reason(
    client: fastapi_testclient.TestClient,
) -> None:
    response = client.put("/api/settings", json={"anything": True})
    body = response.json()

    assert "error" in body


def test_websocket_immediately_sends_the_current_state(
    client: fastapi_testclient.TestClient,
) -> None:
    with client.websocket_connect("/ws/runtime") as websocket:
        message = websocket.receive_json()

    assert message["connection"] == "DISCONNECTED"


def test_root_serves_the_frontend_shell(
    client: fastapi_testclient.TestClient,
) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Vector Odyssey Dashboard" in response.text
