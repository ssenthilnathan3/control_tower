from collections import defaultdict

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
    for instruction in originators:
        bank_rows = banks[instruction.business_event_id]
        lms_rows = bookings[instruction.partner_loan_reference or ""]
        members = _members([instruction], lms_rows, bank_rows)
        if _is_duplicate(instruction, bank_rows) or _is_duplicate(
            instruction, lms_rows
        ):
            outcome = ReconciliationOutcome.DUPLICATE_EVENT
            reason = "multiple full-value records have the same source relationship"
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
        else:
            outcome = ReconciliationOutcome.UNRESOLVED
            reason = "no deterministic exact or duplicate rule applies"
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
    return decisions
