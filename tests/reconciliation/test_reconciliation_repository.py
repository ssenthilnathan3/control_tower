from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from control_tower.reconciliation import (
    ReconciliationDecision,
    ReconciliationOutcome,
    ReconciliationRepository,
    ReconciliationWriteOutcome,
)
from control_tower.reconciliation.repository import (
    DecisionRecord,
    ReconciliationRunRecord,
)


def test_persists_a_reconciliation_run_idempotently(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reconciliation.db'}")
    repository = ReconciliationRepository(engine)
    decisions = [
        ReconciliationDecision(
            "instruction-1",
            "ARUNA",
            ReconciliationOutcome.EXACT_MATCH,
            125000,
            "all three sources agree",
            "phase1-v1",
            (1, 2, 3),
        )
    ]

    first = repository.save("a" * 64, "b" * 64, "c" * 64, "phase1-v1", decisions)
    replay = repository.save("a" * 64, "b" * 64, "c" * 64, "phase1-v1", decisions)

    assert first is ReconciliationWriteOutcome.CREATED
    assert replay is ReconciliationWriteOutcome.REPLAY
    assert repository.runs()[0].run_key == "a" * 64
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ReconciliationRunRecord))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(DecisionRecord)) == 1
