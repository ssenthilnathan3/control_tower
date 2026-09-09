from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from control_tower.reconciliation import ReconciliationOutcome, ReconciliationRepository

from .config import ExceptionPolicy
from .repository import ExceptionRepository, ExceptionWriteOutcome

BLOCKING_OUTCOMES = {
    ReconciliationOutcome.DUPLICATE_EVENT,
    ReconciliationOutcome.AMOUNT_MISMATCH,
    ReconciliationOutcome.STATUS_MISMATCH,
    ReconciliationOutcome.MISSING_EVENT,
    ReconciliationOutcome.UNRESOLVED,
}


@dataclass(frozen=True)
class ExceptionSyncResult:
    created_count: int
    replayed_count: int
    blocking_value_paise: int


def create_exceptions(
    run_key: str,
    reconciliation_repository: ReconciliationRepository,
    exception_repository: ExceptionRepository | None = None,
    detected_at: datetime | None = None,
    policy: ExceptionPolicy | None = None,
) -> ExceptionSyncResult:
    exception_repository = exception_repository or ExceptionRepository(
        reconciliation_repository.engine
    )
    detected_at = detected_at or datetime.now(timezone.utc)
    policy = policy or ExceptionPolicy.load(Path("config/exceptions.json"))
    decisions = [
        decision
        for decision in reconciliation_repository.decisions_for_run(run_key)
        if decision.outcome in BLOCKING_OUTCOMES
    ]
    outcomes = [
        exception_repository.create(decision, detected_at, policy)
        for decision in decisions
    ]
    return ExceptionSyncResult(
        outcomes.count(ExceptionWriteOutcome.CREATED),
        outcomes.count(ExceptionWriteOutcome.REPLAY),
        sum(decision.amount_paise for decision in decisions),
    )


from .config import ExceptionPolicy
