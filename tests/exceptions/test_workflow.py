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
    queue = repository.list(partner_code="ARUNA")
    history = repository.actions(exception_id)

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        actions = session.scalars(
            select(ExceptionActionRecord).order_by(ExceptionActionRecord.id)
        ).all()
        assert exception.status == "PENDING_APPROVAL"
        assert exception.assignee == "operator-2"
        assert exception.resolution_requested_by == "operator-2"
        assert queue[0].exception_id == exception_id
        assert queue[0].amount_paise == 125000
        assert len(history) == 4
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


def test_approver_resolves_pending_exception(tmp_path) -> None:
    engine, repository, exception_id = _queue(tmp_path)
    repository.start_investigation(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "checked evidence", NOW
    )
    repository.request_resolution(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "correction received", NOW
    )

    repository.approve_resolution(
        exception_id,
        "approver-1",
        ExceptionRole.APPROVER,
        "evidence is sufficient",
        NOW,
    )

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        assert exception.status == "RESOLVED"


def test_approver_resolves_selected_exceptions_from_open(tmp_path) -> None:
    engine, repository, exception_id = _queue(tmp_path)

    resolved_count = repository.resolve_many(
        "approver-1",
        ExceptionRole.APPROVER,
        "bulk review completed",
        NOW,
        [exception_id],
    )

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        actions = session.scalars(
            select(ExceptionActionRecord).order_by(ExceptionActionRecord.id)
        ).all()
        assert resolved_count == 1
        assert exception.status == "RESOLVED"
        assert [action.action for action in actions[-3:]] == [
            "INVESTIGATION_STARTED",
            "RESOLUTION_REQUESTED",
            "APPROVED",
        ]


def test_rejection_returns_to_investigation(tmp_path) -> None:
    engine, repository, exception_id = _queue(tmp_path)
    repository.start_investigation(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "checked evidence", NOW
    )
    repository.request_resolution(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "correction received", NOW
    )

    repository.reject_resolution(
        exception_id, "approver-1", ExceptionRole.APPROVER, "missing bank proof", NOW
    )

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        action = session.scalars(
            select(ExceptionActionRecord).order_by(ExceptionActionRecord.id.desc())
        ).first()
        assert exception.status == "INVESTIGATING"
        assert action.action == "REJECTED"


def test_material_override_cannot_be_self_approved(tmp_path) -> None:
    _, repository, exception_id = _queue(tmp_path)
    repository.start_investigation(
        exception_id, "approver-1", ExceptionRole.APPROVER, "checked evidence", NOW
    )
    repository.request_resolution(
        exception_id,
        "approver-1",
        ExceptionRole.APPROVER,
        "manual amount override",
        NOW,
        material_override=True,
    )

    with pytest.raises(ExceptionWorkflowError, match="another approver"):
        repository.approve_resolution(
            exception_id,
            "approver-1",
            ExceptionRole.APPROVER,
            "approving my override",
            NOW,
        )


def test_changed_evidence_reopens_resolved_exception(tmp_path) -> None:
    engine, repository, exception_id = _queue(tmp_path)
    repository.start_investigation(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "checked evidence", NOW
    )
    repository.request_resolution(
        exception_id, "operator-1", ExceptionRole.OPERATOR, "correction received", NOW
    )
    repository.approve_resolution(
        exception_id,
        "approver-1",
        ExceptionRole.APPROVER,
        "evidence is sufficient",
        NOW,
    )
    reconciliation = ReconciliationRepository(engine)
    reconciliation.save(
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "phase1-v1",
        [
            ReconciliationDecision(
                "instruction-1",
                "ARUNA",
                ReconciliationOutcome.STATUS_MISMATCH,
                125000,
                "later status evidence differs",
                "phase1-v1",
                (7, 8, 9),
            )
        ],
    )

    result = create_exceptions("d" * 64, reconciliation, repository, NOW)

    with Session(engine) as session:
        exception = session.scalar(select(ExceptionRecord))
        action = session.scalars(
            select(ExceptionActionRecord).order_by(ExceptionActionRecord.id.desc())
        ).first()
        assert result.created_count == 0
        assert result.updated_count == 1
        assert exception.exception_id == exception_id
        assert exception.status == "REOPENED"
        assert exception.classification == "STATUS_MISMATCH"
        assert action.action == "REOPENED"


def test_action_history_cannot_be_changed(tmp_path) -> None:
    engine, _, _ = _queue(tmp_path)

    with Session(engine) as session:
        action = session.scalar(select(ExceptionActionRecord))
        action.reason = "rewritten history"
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
