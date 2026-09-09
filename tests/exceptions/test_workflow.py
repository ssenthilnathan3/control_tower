from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from control_tower.exceptions import (
    ExceptionRepository,
    ExceptionRole,
    ExceptionWorkflowError,
    create_exceptions,
)
from control_tower.exceptions.repository import ExceptionActionRecord, ExceptionRecord
from control_tower.reconciliation import (
    ReconciliationDecision,
    ReconciliationOutcome,
    ReconciliationRepository,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _queue(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    reconciliation = ReconciliationRepository(engine)
    reconciliation.save(
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "phase1-v1",
        [
            ReconciliationDecision(
                "instruction-1",
                "ARUNA",
                ReconciliationOutcome.AMOUNT_MISMATCH,
                125000,
                "bank amount differs",
                "phase1-v1",
                (1, 2, 3),
            )
        ],
    )
    repository = ExceptionRepository(engine)
    create_exceptions("a" * 64, reconciliation, repository, NOW)
    with Session(engine) as session:
        exception_id = session.scalar(select(ExceptionRecord.exception_id))
    return engine, repository, exception_id


def test_operator_assigns_investigates_and_requests_resolution(tmp_path) -> None:
    engine, repository, exception_id = _queue(tmp_path)

    repository.assign(
        exception_id,
        "operator-2",
        "operator-1",
        ExceptionRole.OPERATOR,
        "taking ownership",
        NOW,
    )
    repository.start_investigation(
        exception_id,
        "operator-2",
        ExceptionRole.OPERATOR,
        "checking source totals",
        NOW,
    )
    repository.request_resolution(
        exception_id,
        "operator-2",
        ExceptionRole.OPERATOR,
        "bank confirmed correction",
        NOW,
    )

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        actions = session.scalars(
            select(ExceptionActionRecord).order_by(ExceptionActionRecord.id)
        ).all()
        assert exception.status == "PENDING_APPROVAL"
        assert exception.assignee == "operator-2"
        assert exception.resolution_requested_by == "operator-2"
        assert [action.action for action in actions] == [
            "DETECTED",
            "ASSIGNED",
            "INVESTIGATION_STARTED",
            "RESOLUTION_REQUESTED",
        ]


def test_rejects_invalid_workflow_transition(tmp_path) -> None:
    _, repository, exception_id = _queue(tmp_path)

    with pytest.raises(ExceptionWorkflowError, match="cannot RESOLUTION_REQUESTED"):
        repository.request_resolution(
            exception_id, "operator-1", ExceptionRole.OPERATOR, "not investigated", NOW
        )
