from datetime import datetime, timezone

import pytest

from control_tower.close_control import (
    BlockerType,
    CloseControlRepository,
    CloseOutcome,
    ClosePolicy,
    CloseWriteOutcome,
    calculate_close,
)
from control_tower.ingestion.registry import IngestionRegistry, Registration
from control_tower.reconciliation import (
    ReconciliationDecision,
    ReconciliationOutcome,
    ReconciliationRepository,
)

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)


def _scope(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'close.db'}"
    ingestion = IngestionRegistry(database_url)
    ingestion.register_many(
        [
            Registration(
                "originator",
                "batch-1",
                "record-1",
                "ARUNA",
                "1" * 64,
                "a" * 64,
                "source.csv#line=2",
                "ACCEPTED",
            ),
            Registration(
                "originator",
                "batch-1",
                "record-2",
                "ARUNA",
                "2" * 64,
                "a" * 64,
                "source.csv#line=3",
                "ACCEPTED",
            ),
            Registration(
                "originator",
                "batch-1",
                "record-3",
                "ARUNA",
                "3" * 64,
                "a" * 64,
                "source.csv#line=4",
                "QUARANTINED",
            ),
        ]
    )
    source_ids = tuple(
        item.source_version_id for item in ingestion.canonical_candidates()
    )
    passed_control = ingestion.record_delivery_control(
        "b" * 64,
        "originator",
        "a" * 64,
        "c" * 64,
        "PASSED",
        3,
        30000,
        1,
        None,
        tmp_path / "passed",
    )
    failed_control = ingestion.record_delivery_control(
        "d" * 64,
        "bank",
        "e" * 64,
        "f" * 64,
        "FAILED",
        2,
        20000,
        0,
        "declared total differs",
        tmp_path / "failed",
    )
    reconciliation = ReconciliationRepository(ingestion.engine)
    reconciliation.save(
        "r" * 64,
        "s" * 64,
        "t" * 64,
        "phase1-v1",
        [
            ReconciliationDecision(
                "instruction-1",
                "ARUNA",
                ReconciliationOutcome.EXACT_MATCH,
                10000,
                "all sources agree",
                "phase1-v1",
                (source_ids[0],),
            ),
            ReconciliationDecision(
                "instruction-2",
                "ARUNA",
                ReconciliationOutcome.AMOUNT_MISMATCH,
                20000,
                "bank amount differs",
                "phase1-v1",
                (source_ids[1],),
            ),
        ],
    )
    return ingestion, reconciliation, passed_control, failed_control


def test_persists_hold_with_scorecard_and_traceable_blockers(tmp_path) -> None:
    ingestion, reconciliation, passed, failed = _scope(tmp_path)
    repository = CloseControlRepository(ingestion.engine)

    result = calculate_close(
        "r" * 64,
        (passed, failed),
        "close-operator",
        reconciliation,
        ingestion,
        repository,
        decided_at=NOW,
    )
    replay = calculate_close(
        "r" * 64,
        (failed, passed),
        "another-operator",
        reconciliation,
        ingestion,
        repository,
        decided_at=NOW,
    )

    assert result.outcome is CloseOutcome.HOLD
    assert result.write_outcome is CloseWriteOutcome.CREATED
    assert replay.write_outcome is CloseWriteOutcome.REPLAY
    assert replay.decision_hash == result.decision_hash
    assert replay.actor == "close-operator"
    assert result.scorecard.accepted_count == 2
    assert result.scorecard.accepted_value_paise == 30000
    assert result.scorecard.matched_value_paise == 10000
    assert result.scorecard.unresolved_value_paise == 20000
    assert result.scorecard.quarantined_count == 1
    assert result.scorecard.control_failure_count == 1
    assert {item.blocker_type for item in result.blockers} == {
        BlockerType.UNRESOLVED,
        BlockerType.QUARANTINE,
        BlockerType.CONTROL_FAILURE,
    }
    assert all(item.reference and item.evidence_reference for item in result.blockers)


def test_policy_threshold_changes_only_expected_blockers(tmp_path) -> None:
    ingestion, reconciliation, passed, _ = _scope(tmp_path)
    strict = ClosePolicy("strict", 0, 0, False, False)
    tolerant = ClosePolicy("tolerant", 0, 20000, False, False)

    held = calculate_close(
        "r" * 64,
        (passed,),
        "close-operator",
        reconciliation,
        ingestion,
        policy=strict,
        decided_at=NOW,
    )
    closed = calculate_close(
        "r" * 64,
        (passed,),
        "close-operator",
        reconciliation,
        ingestion,
        policy=tolerant,
        decided_at=NOW,
    )

    assert held.outcome is CloseOutcome.HOLD
    assert [item.blocker_type for item in held.blockers] == [BlockerType.UNRESOLVED]
    assert closed.outcome is CloseOutcome.CLOSE
    assert closed.blockers == ()
    assert closed.scorecard == held.scorecard
    assert closed.decision_hash != held.decision_hash


def test_rejects_scope_without_controls_for_reconciliation_evidence(tmp_path) -> None:
    ingestion, reconciliation, _, _ = _scope(tmp_path)

    with pytest.raises(ValueError, match="do not cover"):
        calculate_close(
            "r" * 64,
            (),
            "close-operator",
            reconciliation,
            ingestion,
            decided_at=NOW,
        )
