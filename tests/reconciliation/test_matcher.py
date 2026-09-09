from dataclasses import replace
from datetime import date, datetime, timezone

from control_tower.canonical import (
    CanonicalEvent,
    CanonicalStatus,
    EventType,
    SourceProvenance,
    SourceSystem,
)
from control_tower.reconciliation import (
    ReconciliationOutcome,
    ReconciliationPolicy,
    reconcile,
)

POLICY = ReconciliationPolicy("phase1-v1", 120)


def _event(source: SourceSystem, version: int) -> CanonicalEvent:
    fields = {
        SourceSystem.ORIGINATOR: (
            EventType.INSTRUCTION,
            "instruction-1",
            "instruction-1",
            CanonicalStatus.READY,
            "partner-loan-1",
        ),
        SourceSystem.LMS: (
            EventType.BOOKING,
            "booking-1",
            "partner-loan-1",
            CanonicalStatus.SUCCESS,
            "partner-loan-1",
        ),
        SourceSystem.BANK: (
            EventType.SETTLEMENT,
            "transaction-1",
            "instruction-1",
            CanonicalStatus.SUCCESS,
            None,
        ),
    }
    event_type, record_id, correlation_id, status, partner_loan_reference = fields[
        source
    ]
    return CanonicalEvent(
        SourceProvenance(version, "a" * 64, "b" * 64, f"source.csv#line={version + 1}"),
        source,
        event_type,
        record_id,
        record_id,
        correlation_id,
        "ARUNA",
        "batch-1",
        "loan-1" if source is SourceSystem.LMS else None,
        "customer-1" if source is SourceSystem.ORIGINATOR else None,
        partner_loan_reference,
        datetime(2026, 9, 1, 4, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 4, 32, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc),
        date(2026, 9, 1),
        125000,
        "INR",
        "APPROVED" if source is SourceSystem.ORIGINATOR else "SUCCESS",
        status,
        None,
    )


def test_matches_one_record_from_each_source_exactly() -> None:
    decision = reconcile(
        [
            _event(SourceSystem.ORIGINATOR, 1),
            _event(SourceSystem.LMS, 2),
            _event(SourceSystem.BANK, 3),
        ],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.EXACT_MATCH
    assert decision.source_version_ids == (1, 2, 3)


def test_duplicate_full_value_bank_record_is_not_forced_into_a_match() -> None:
    bank = _event(SourceSystem.BANK, 3)
    duplicate = replace(
        bank,
        provenance=replace(bank.provenance, source_version_id=4),
        source_record_id="transaction-2",
    )

    decision = reconcile(
        [
            _event(SourceSystem.ORIGINATOR, 1),
            _event(SourceSystem.LMS, 2),
            bank,
            duplicate,
        ],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.DUPLICATE_EVENT
    assert decision.source_version_ids == (1, 2, 3, 4)


def test_matches_documented_bank_split_when_legs_balance_exactly() -> None:
    bank = replace(_event(SourceSystem.BANK, 3), amount_paise=60000)
    second_leg = replace(
        bank,
        provenance=replace(bank.provenance, source_version_id=4),
        source_record_id="transaction-2",
        amount_paise=65000,
    )

    decision = reconcile(
        [
            _event(SourceSystem.ORIGINATOR, 1),
            _event(SourceSystem.LMS, 2),
            bank,
            second_leg,
        ],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.COMPOSITE_MATCH
    assert decision.source_version_ids == (1, 2, 3, 4)


def test_does_not_match_split_when_one_paise_is_missing() -> None:
    bank = replace(_event(SourceSystem.BANK, 3), amount_paise=60000)
    second_leg = replace(
        bank,
        provenance=replace(bank.provenance, source_version_id=4),
        source_record_id="transaction-2",
        amount_paise=64999,
    )

    decision = reconcile(
        [
            _event(SourceSystem.ORIGINATOR, 1),
            _event(SourceSystem.LMS, 2),
            bank,
            second_leg,
        ],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.AMOUNT_MISMATCH


def test_marks_matching_late_record_pending_inside_grace() -> None:
    bank = replace(
        _event(SourceSystem.BANK, 3),
        received_timestamp=datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc),
    )

    decision = reconcile(
        [_event(SourceSystem.ORIGINATOR, 1), _event(SourceSystem.LMS, 2), bank],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.TIMING_DIFFERENCE


def test_does_not_hide_record_arriving_after_grace() -> None:
    bank = replace(
        _event(SourceSystem.BANK, 3),
        received_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
    )

    decision = reconcile(
        [_event(SourceSystem.ORIGINATOR, 1), _event(SourceSystem.LMS, 2), bank],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.UNRESOLVED


def test_classifies_missing_lms_event() -> None:
    decision = reconcile(
        [_event(SourceSystem.ORIGINATOR, 1), _event(SourceSystem.BANK, 3)], POLICY
    )[0]

    assert decision.outcome is ReconciliationOutcome.MISSING_EVENT


def test_classifies_status_mismatch_before_amount_checks() -> None:
    lms = replace(_event(SourceSystem.LMS, 2), canonical_status=CanonicalStatus.PENDING)
    decision = reconcile(
        [_event(SourceSystem.ORIGINATOR, 1), lms, _event(SourceSystem.BANK, 3)],
        POLICY,
    )[0]

    assert decision.outcome is ReconciliationOutcome.STATUS_MISMATCH


def test_keeps_orphan_source_record_in_unresolved_output() -> None:
    bank = replace(
        _event(SourceSystem.BANK, 3),
        source_record_id="orphan-bank",
        correlation_id="missing-instruction",
    )

    decisions = reconcile([bank], POLICY)

    assert len(decisions) == 1
    assert decisions[0].outcome is ReconciliationOutcome.UNRESOLVED
    assert decisions[0].source_version_ids == (3,)
