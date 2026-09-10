from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from control_tower.exceptions import ExceptionRepository, create_exceptions
from control_tower.exceptions.repository import ExceptionRecord
from control_tower.reconciliation import (
    ReconciliationDecision,
    ReconciliationOutcome,
    ReconciliationRepository,
)

BLOCKING = [
    ReconciliationOutcome.DUPLICATE_EVENT,
    ReconciliationOutcome.AMOUNT_MISMATCH,
    ReconciliationOutcome.STATUS_MISMATCH,
    ReconciliationOutcome.MISSING_EVENT,
    ReconciliationOutcome.UNRESOLVED,
    ReconciliationOutcome.TIMING_DIFFERENCE,
]


@pytest.mark.parametrize("excluded", [ReconciliationOutcome.EXACT_MATCH])
def test_queue_covers_actionable_value_and_excludes_matches(tmp_path, excluded) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'coverage.db'}")
    reconciliation = ReconciliationRepository(engine)
    decisions = [
        ReconciliationDecision(
            f"instruction-{index}",
            "ARUNA",
            outcome,
            index * 10000,
            outcome.value,
            "phase1-v1",
            (index,),
        )
        for index, outcome in enumerate([*BLOCKING, excluded], start=1)
    ]
    reconciliation.save("a" * 64, "b" * 64, "c" * 64, "phase1-v1", decisions)

    result = create_exceptions(
        "a" * 64,
        reconciliation,
        ExceptionRepository(engine),
        datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
    )

    with Session(engine) as session:
        classifications = set(session.scalars(select(ExceptionRecord.classification)))
    assert result.created_count == len(BLOCKING)
    assert result.blocking_value_paise == sum(range(1, 7)) * 10000
    assert result.blocking_value_paise / 100 == 2100
    assert classifications == {outcome.value for outcome in BLOCKING}
    assert excluded.value not in classifications
