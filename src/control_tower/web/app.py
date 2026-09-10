import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from control_tower.canonical import (
    CanonicalizationPolicy,
    CanonicalRepository,
    canonicalize,
)
from control_tower.close_control import CloseControlRepository, calculate_close
from control_tower.exceptions import (
    ExceptionRepository,
    ExceptionRole,
    ExceptionStatus,
    create_exceptions,
)
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds
from control_tower.reconciliation import (
    ReconciliationPolicy,
    ReconciliationRepository,
    run_reconciliation,
)

from .auth import Authenticator, Principal, load_identities, require_role


@dataclass(frozen=True)
class WebSettings:
    database_url: str
    evidence_root: Path
    input_root: Path
    config_root: Path = Path("config")

    @classmethod
    def from_environment(cls) -> "WebSettings":
        evidence = Path(os.getenv("CONTROL_TOWER_EVIDENCE_ROOT", "evidence"))
        return cls(
            os.getenv(
                "CONTROL_TOWER_DATABASE_URL",
                f"sqlite:///{(evidence / 'ingestion.db').resolve()}",
            ),
            evidence,
            Path(os.getenv("CONTROL_TOWER_INPUT_ROOT", "generated")).resolve(),
        )


class IngestRequest(BaseModel):
    directory: str


class ExceptionActionRequest(BaseModel):
    reason: str
    assignee: str | None = None
    material_override: bool = False


class CloseRequest(BaseModel):
    reconciliation_run_key: str
    ingestion_run_key: str


def _json(value):
    return asdict(value)


def create_app(
    settings: WebSettings | None = None,
    identities: dict[str, Principal] | None = None,
) -> FastAPI:
    settings = settings or WebSettings.from_environment()
    settings.evidence_root.mkdir(parents=True, exist_ok=True)
    auth = Authenticator(identities if identities is not None else load_identities())
    Authenticated = Annotated[Principal, Depends(auth.authenticate)]
    ingestion = IngestionRegistry(settings.database_url)
    reconciliation = ReconciliationRepository(ingestion.engine)
    exceptions = ExceptionRepository(ingestion.engine)
    closes = CloseControlRepository(ingestion.engine)
    canonical = CanonicalRepository(ingestion.engine)
    app = FastAPI(title="Co-lending Control Tower", version="0.1.0")
    ui_root = Path(__file__).with_name("ui") / "dist"
    app.mount("/assets", StaticFiles(directory=ui_root / "assets"), name="ui-assets")

    @app.exception_handler(ValueError)
    async def value_error_handler(_request, error: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ui_root / "index.html")

    @app.get("/api/me")
    def me(principal: Authenticated):
        return _json(principal)

    @app.post("/api/ingestion-runs")
    def ingest(body: IngestRequest, principal: Authenticated):
        require_role(principal, ExceptionRole.OPERATOR, ExceptionRole.APPROVER)
        requested = (settings.input_root / body.directory).resolve()
        if (
            requested != settings.input_root
            and settings.input_root not in requested.parents
        ):
            raise HTTPException(400, "input directory escapes configured root")
        if not (requested / "feeds").is_dir() or not (requested / "manifests").is_dir():
            raise HTTPException(
                400,
                "input directory must contain feeds/ and manifests/; "
                "generated data is usually under development",
            )
        result = ingest_generated_feeds(
            requested, settings.evidence_root, settings.database_url
        )
        return _json(result)

    @app.get("/api/ingestion-runs")
    def ingestion_runs(
        _principal: Authenticated,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return {
            "items": [_json(item) for item in ingestion.ingestion_runs(limit, offset)],
            "total": ingestion.ingestion_run_count(),
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/reconciliation-runs")
    def reconcile(principal: Authenticated):
        require_role(principal, ExceptionRole.OPERATOR, ExceptionRole.APPROVER)
        canonical_result = canonicalize(
            ingestion,
            CanonicalizationPolicy.load(settings.config_root / "canonicalization.json"),
            canonical,
        )
        result = run_reconciliation(
            canonical,
            ReconciliationPolicy.load(settings.config_root / "reconciliation.json"),
            reconciliation,
        )
        exception_result = create_exceptions(result.run_key, reconciliation, exceptions)
        return {
            "canonicalization": _json(canonical_result),
            "reconciliation": _json(result),
            "exceptions": _json(exception_result),
        }

    @app.get("/api/reconciliation-runs")
    def reconciliation_runs(
        _principal: Authenticated,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return {
            "items": [_json(item) for item in reconciliation.runs(limit, offset)],
            "total": reconciliation.run_count(),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/reconciliation-runs/{run_key}/decisions")
    def decisions(run_key: str, _principal: Authenticated):
        return [_json(item) for item in reconciliation.decisions_for_run(run_key)]

    @app.get("/api/exceptions")
    def exception_queue(
        _principal: Authenticated,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: ExceptionStatus | None = None,
        partner_code: str | None = None,
        priority: str | None = None,
        classification: str | None = None,
        owner: str | None = None,
        min_amount_paise: int | None = Query(None, ge=0),
    ):
        return {
            "items": [
                _json(item)
                for item in exceptions.list(
                    limit,
                    offset,
                    status,
                    partner_code,
                    priority,
                    classification,
                    owner,
                    min_amount_paise,
                )
            ],
            "total": exceptions.count(
                status,
                partner_code,
                priority,
                classification,
                owner,
                min_amount_paise,
            ),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/exceptions/{exception_id}/actions")
    def action_history(exception_id: str, _principal: Authenticated):
        return [_json(item) for item in exceptions.actions(exception_id)]

    @app.get("/api/exception-summary")
    def exception_summary(_principal: Authenticated):
        return [_json(item) for item in exceptions.classification_summary()]

    @app.post("/api/exceptions/{exception_id}/{action}")
    def act_on_exception(
        exception_id: str,
        action: str,
        body: ExceptionActionRequest,
        principal: Authenticated,
    ):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if action == "assign":
            require_role(principal, ExceptionRole.OPERATOR, ExceptionRole.APPROVER)
            exceptions.assign(
                exception_id,
                body.assignee or "",
                principal.actor,
                principal.role,
                body.reason,
                now,
            )
        elif action == "investigate":
            require_role(principal, ExceptionRole.OPERATOR, ExceptionRole.APPROVER)
            exceptions.start_investigation(
                exception_id, principal.actor, principal.role, body.reason, now
            )
        elif action == "request-resolution":
            require_role(principal, ExceptionRole.OPERATOR, ExceptionRole.APPROVER)
            exceptions.request_resolution(
                exception_id,
                principal.actor,
                principal.role,
                body.reason,
                now,
                body.material_override,
            )
        elif action in {"approve", "reject"}:
            require_role(principal, ExceptionRole.APPROVER)
            method = (
                exceptions.approve_resolution
                if action == "approve"
                else exceptions.reject_resolution
            )
            method(exception_id, principal.actor, principal.role, body.reason, now)
        else:
            raise HTTPException(404, "unknown exception action")
        return {"status": "accepted"}

    @app.post("/api/close-decisions")
    def close(body: CloseRequest, principal: Authenticated):
        require_role(principal, ExceptionRole.APPROVER)
        return _json(
            calculate_close(
                body.reconciliation_run_key,
                body.ingestion_run_key,
                principal.actor,
                reconciliation,
                ingestion,
                closes,
            )
        )

    @app.get("/api/close-decisions")
    def close_decisions(
        _principal: Authenticated,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return {
            "items": [_json(item) for item in closes.list(limit, offset)],
            "total": closes.count(),
            "limit": limit,
            "offset": offset,
        }

    return app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
