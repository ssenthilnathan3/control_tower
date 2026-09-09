from datetime import datetime, timezone

from sqlalchemy import create_engine

from control_tower.exceptions import ExceptionRepository, create_exceptions
from control_tower.reconciliation import (
    ReconciliationDecision,
    ReconciliationOutcome,
    ReconciliationRepository,
)


def test_creates_one_exception_per_blocking_decision_and_replays(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'exceptions.db'}")
    reconciliation = ReconciliationRepository(engine)
    decisions = [
        ReconciliationDecision(
            "instruction-1",
            "ARUNA",
            ReconciliationOutcome.AMOUNT_MISMATCH,
            125000,
            "bank amount differs",
            "phase1-v1",
            (1, 2, 3),
        ),
        ReconciliationDecision(
            "instruction-2",
            "ARUNA",
            ReconciliationOutcome.TIMING_DIFFERENCE,
            50000,
            "inside grace",
            "phase1-v1",
            (4, 5, 6),
        ),
    ]
    reconciliation.save("a" * 64, "b" * 64, "c" * 64, "phase1-v1", decisions)
    exceptions = ExceptionRepository(engine)
    detected_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

    first = create_exceptions("a" * 64, reconciliation, exceptions, detected_at)
    replay = create_exceptions("a" * 64, reconciliation, exceptions, detected_at)

    assert first.created_count == 1
    assert first.blocking_value_paise == 125000
    assert replay.replayed_count == 1
    assert exceptions.count() == 1
