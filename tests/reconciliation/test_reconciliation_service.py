from pathlib import Path

from sqlalchemy import create_engine

from control_tower.canonical import (
    CanonicalizationPolicy,
    SourceProvenance,
    adapt,
)
from control_tower.reconciliation import (
    ReconciliationPolicy,
    ReconciliationRepository,
    ReconciliationWriteOutcome,
    run_reconciliation,
)


class CanonicalRecords:
    def __init__(self, engine, events):
        self.engine = engine
        self._events = events

    def active_events(self):
        return self._events


def _instruction():
    policy = CanonicalizationPolicy.load(Path("config/canonicalization.json"))
    return adapt(
        "originator",
        {
            "instruction_id": "instruction-1",
            "loan_reference": "partner-loan-1",
            "customer_surrogate_id": "customer-1",
            "partner_code": "ARUNA",
            "instruction_timestamp": "2026-09-01T10:00:00+05:30",
            "amount_paise": "125000",
            "currency": "INR",
            "status": "APPROVED",
            "batch_id": "batch-1",
            "received_timestamp": "2026-09-01T10:02:00+05:30",
        },
        SourceProvenance(1, "a" * 64, "b" * 64, "source.csv#line=2"),
        policy,
    )


def test_same_snapshot_and_policy_replay_the_run(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    canonical = CanonicalRecords(engine, [_instruction()])
    repository = ReconciliationRepository(engine)
    policy = ReconciliationPolicy("phase1-v1", 120)

    first = run_reconciliation(canonical, policy, repository)
    replay = run_reconciliation(canonical, policy, repository)

    assert first.write_outcome is ReconciliationWriteOutcome.CREATED
    assert replay.write_outcome is ReconciliationWriteOutcome.REPLAY
    assert first.run_key == replay.run_key
    assert first.decision_count == 1


def test_policy_change_creates_a_new_run_identity(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    canonical = CanonicalRecords(engine, [_instruction()])
    repository = ReconciliationRepository(engine)

    first = run_reconciliation(
        canonical, ReconciliationPolicy("phase1-v1", 120), repository
    )
    changed = run_reconciliation(
        canonical, ReconciliationPolicy("phase1-v1", 90), repository
    )

    assert first.run_key != changed.run_key
    assert changed.write_outcome is ReconciliationWriteOutcome.CREATED
