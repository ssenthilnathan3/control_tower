from pathlib import Path

from fastapi.testclient import TestClient

from control_tower.exceptions import ExceptionRole
from control_tower.generator import generate
from control_tower.web import Principal, WebSettings, create_app


def _client(tmp_path: Path) -> TestClient:
    input_root = tmp_path / "input"
    input_root.mkdir()
    generate(Path("config/generator.json"), input_root / "case", seed=314159)
    settings = WebSettings(
        f"sqlite:///{tmp_path / 'web.db'}",
        tmp_path / "evidence",
        input_root,
    )
    return TestClient(
        create_app(
            settings,
            {
                "operator-token": Principal("operator-1", ExceptionRole.OPERATOR),
                "approver-token": Principal("approver-1", ExceptionRole.APPROVER),
            },
        )
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_requires_server_configured_identity(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/").status_code == 200
    assert client.get("/api/exceptions").status_code == 401
    assert client.get("/api/exceptions", headers=_headers("unknown")).status_code == 401
    me = client.get("/api/me", headers=_headers("operator-token"))
    assert me.json() == {"actor": "operator-1", "role": "OPERATOR"}
    invalid = client.post(
        "/api/ingestion-runs",
        headers=_headers("operator-token"),
        json={"directory": "."},
    )
    assert invalid.status_code == 400
    assert "feeds/ and manifests/" in invalid.json()["detail"]


def test_operator_journey_and_approver_boundary(tmp_path: Path) -> None:
    client = _client(tmp_path)
    operator = _headers("operator-token")
    approver = _headers("approver-token")

    ingested = client.post(
        "/api/ingestion-runs", headers=operator, json={"directory": "case"}
    )
    assert ingested.status_code == 200
    ingestion_run_key = ingested.json()["run_key"]

    reconciled = client.post("/api/reconciliation-runs", headers=operator)
    assert reconciled.status_code == 200
    reconciliation_run_key = reconciled.json()["reconciliation"]["run_key"]
    queue = client.get("/api/exceptions", headers=operator).json()
    assert queue

    exception_id = queue[0]["exception_id"]
    reason = {"reason": "source evidence checked"}
    assert (
        client.post(
            f"/api/exceptions/{exception_id}/investigate", headers=operator, json=reason
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/exceptions/{exception_id}/request-resolution",
            headers=operator,
            json={"reason": "correction confirmed", "material_override": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/exceptions/{exception_id}/approve", headers=operator, json=reason
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/exceptions/{exception_id}/approve", headers=approver, json=reason
        ).status_code
        == 200
    )

    history = client.get(
        f"/api/exceptions/{exception_id}/actions", headers=operator
    ).json()
    assert history[-1]["actor"] == "approver-1"
    assert history[-1]["actor_role"] == "APPROVER"

    assert (
        client.post(
            "/api/close-decisions",
            headers=operator,
            json={
                "reconciliation_run_key": reconciliation_run_key,
                "ingestion_run_key": ingestion_run_key,
            },
        ).status_code
        == 403
    )
    close = client.post(
        "/api/close-decisions",
        headers=approver,
        json={
            "reconciliation_run_key": reconciliation_run_key,
            "ingestion_run_key": ingestion_run_key,
        },
    )
    assert close.status_code == 200
    assert close.json()["outcome"] == "HOLD"
    assert close.json()["scorecard"]["accepted_count"] == 2000
