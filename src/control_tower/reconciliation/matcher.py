from collections import defaultdict
from datetime import timedelta

from control_tower.canonical import (
    CanonicalEvent,
    CanonicalStatus,
    SourceSystem,
)

from .models import ReconciliationDecision, ReconciliationOutcome, ReconciliationPolicy


def _members(*groups: list[CanonicalEvent]) -> tuple[int, ...]:
    return tuple(
        sorted(
            event.provenance.source_version_id for group in groups for event in group
        )
    )


def _is_duplicate(instruction: CanonicalEvent, events: list[CanonicalEvent]) -> bool:
    # Two full-value callbacks for one instruction are duplicate delivery, not a split.
    signatures = [
        (event.amount_paise, event.currency, event.canonical_status)
        for event in events
        if event.amount_paise == instruction.amount_paise
    ]
    return len(signatures) != len(set(signatures))


def _is_composite(
    instruction: CanonicalEvent,
    bank_rows: list[CanonicalEvent],
    lms_rows: list[CanonicalEvent],
) -> bool:
    related = bank_rows + lms_rows
    return (
        bool(bank_rows)
        and bool(lms_rows)
        and (len(bank_rows) > 1 or len(lms_rows) > 1)
        and all(event.currency == "INR" for event in related)
        and all(event.canonical_status is CanonicalStatus.SUCCESS for event in related)
        and all(
            event.received_timestamp <= instruction.reconciliation_cutoff
            for event in related
        )
        and sum(event.amount_paise for event in bank_rows) == instruction.amount_paise
        and sum(event.amount_paise for event in lms_rows) == instruction.amount_paise
    )


def _is_timing_difference(
    instruction: CanonicalEvent,
    bank_rows: list[CanonicalEvent],
    lms_rows: list[CanonicalEvent],
    grace_minutes: int,
) -> bool:
    related = bank_rows + lms_rows
    grace_end = instruction.reconciliation_cutoff + timedelta(minutes=grace_minutes)
    return (
        bool(bank_rows)
        and bool(lms_rows)
        and all(event.canonical_status is CanonicalStatus.SUCCESS for event in related)
        and sum(event.amount_paise for event in bank_rows) == instruction.amount_paise
        and sum(event.amount_paise for event in lms_rows) == instruction.amount_paise
        and any(
            event.received_timestamp > instruction.reconciliation_cutoff
            for event in related
        )
        and all(event.received_timestamp <= grace_end for event in related)
    )


def reconcile(
    events: list[CanonicalEvent], policy: ReconciliationPolicy
) -> list[ReconciliationDecision]:
    originators = sorted(
        (event for event in events if event.source_system is SourceSystem.ORIGINATOR),
        key=lambda event: event.business_event_id,
    )
    banks: dict[str, list[CanonicalEvent]] = defaultdict(list)
    bookings: dict[str, list[CanonicalEvent]] = defaultdict(list)
    for event in events:
        if event.source_system is SourceSystem.BANK:
            banks[event.correlation_id].append(event)
        elif event.source_system is SourceSystem.LMS:
            bookings[event.correlation_id].append(event)

    decisions: list[ReconciliationDecision] = []
    used_versions: set[int] = set()
    for instruction in originators:
        bank_rows = banks[instruction.business_event_id]
        lms_rows = bookings[instruction.partner_loan_reference or ""]
        members = _members([instruction], lms_rows, bank_rows)
        if _is_duplicate(instruction, bank_rows) or _is_duplicate(
            instruction, lms_rows
        ):
            outcome = ReconciliationOutcome.DUPLICATE_EVENT
            reason = "multiple full-value records have the same source relationship"
        elif _is_composite(instruction, bank_rows, lms_rows):
            outcome = ReconciliationOutcome.COMPOSITE_MATCH
            reason = "related source legs sum exactly to the instruction amount"
        elif (
            len(bank_rows) == 1
            and len(lms_rows) == 1
            and instruction.canonical_status is CanonicalStatus.READY
            and bank_rows[0].canonical_status is CanonicalStatus.SUCCESS
            and lms_rows[0].canonical_status is CanonicalStatus.SUCCESS
            and {instruction.currency, bank_rows[0].currency, lms_rows[0].currency}
            == {"INR"}
            and instruction.amount_paise
            == bank_rows[0].amount_paise
            == lms_rows[0].amount_paise
            and bank_rows[0].received_timestamp <= instruction.reconciliation_cutoff
            and lms_rows[0].received_timestamp <= instruction.reconciliation_cutoff
        ):
            outcome = ReconciliationOutcome.EXACT_MATCH
            reason = "stable references, status, currency, amount, and cutoff agree"
        elif not bank_rows or not lms_rows:
            outcome = ReconciliationOutcome.MISSING_EVENT
            missing = "bank" if not bank_rows else "LMS"
            reason = f"{missing} event is absent from the source relationship"
        elif any(
            event.canonical_status is not CanonicalStatus.SUCCESS
            for event in bank_rows + lms_rows
        ):
            outcome = ReconciliationOutcome.STATUS_MISMATCH
            reason = "source statuses do not agree on a successful disbursement"
        elif (
            sum(event.amount_paise for event in bank_rows) != instruction.amount_paise
            or sum(event.amount_paise for event in lms_rows) != instruction.amount_paise
        ):
            outcome = ReconciliationOutcome.AMOUNT_MISMATCH
            reason = "source amounts do not balance to the instruction"
        elif _is_timing_difference(
            instruction, bank_rows, lms_rows, policy.grace_minutes
        ):
            outcome = ReconciliationOutcome.TIMING_DIFFERENCE
            reason = "matching evidence arrived after cutoff but inside grace"
        else:
            outcome = ReconciliationOutcome.UNRESOLVED
            reason = "evidence does not satisfy a deterministic rule"
        decisions.append(
            ReconciliationDecision(
                instruction.business_event_id,
                outcome,
                instruction.amount_paise,
                reason,
                policy.rule_version,
                members,
            )
        )
        used_versions.update(members)
    for event in sorted(events, key=lambda item: item.provenance.source_version_id):
        version_id = event.provenance.source_version_id
        if version_id in used_versions:
            continue
        decisions.append(
            ReconciliationDecision(
                f"orphan:{event.source_system.value}:{event.source_record_id}",
                ReconciliationOutcome.UNRESOLVED,
                event.amount_paise,
                "source record has no related originator instruction",
                policy.rule_version,
                (version_id,),
            )
        )
    return decisions
