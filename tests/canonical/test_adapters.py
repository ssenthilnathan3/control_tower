from control_tower.canonical import (
    CanonicalStatus,
    EventType,
    SourceProvenance,
    SourceSystem,
    adapt,
)

PROVENANCE = SourceProvenance(1, "a" * 64, "b" * 64, "source.csv#line=2")


def test_originator_adapter_maps_instruction() -> None:
    event = adapt(
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
        PROVENANCE,
    )

    assert event.source_system is SourceSystem.ORIGINATOR
    assert event.event_type is EventType.INSTRUCTION
    assert event.canonical_status is CanonicalStatus.READY
    assert event.business_event_id == "instruction-1"
    assert event.partner_loan_reference == "partner-loan-1"
    assert event.amount_paise == 125000


def test_lms_adapter_maps_booking() -> None:
    event = adapt(
        "lms",
        {
            "booking_id": "booking-1",
            "internal_loan_id": "loan-1",
            "partner_loan_reference": "partner-loan-1",
            "partner_code": "ARUNA",
            "booking_timestamp": "2026-09-01T10:10:00+05:30",
            "booked_amount_paise": "125000",
            "currency": "INR",
            "booking_status": "BOOKED",
            "batch_id": "batch-1",
            "received_timestamp": "2026-09-01T10:12:00+05:30",
        },
        PROVENANCE,
    )

    assert event.source_system is SourceSystem.LMS
    assert event.event_type is EventType.BOOKING
    assert event.canonical_status is CanonicalStatus.SUCCESS
    assert event.correlation_id == "partner-loan-1"
    assert event.loan_id == "loan-1"


def test_bank_adapter_maps_settlement_and_reversal_reference() -> None:
    event = adapt(
        "bank",
        {
            "transaction_reference": "transaction-1",
            "linked_instruction_reference": "instruction-1",
            "partner_code": "ARUNA",
            "value_timestamp": "2026-09-01T10:05:00+05:30",
            "debit_amount_paise": "125000",
            "currency": "INR",
            "settlement_status": "REVERSED",
            "reversal_reference": "transaction-0",
            "batch_id": "batch-1",
            "received_timestamp": "2026-09-01T10:07:00+05:30",
        },
        PROVENANCE,
    )

    assert event.source_system is SourceSystem.BANK
    assert event.event_type is EventType.SETTLEMENT
    assert event.canonical_status is CanonicalStatus.REVERSED
    assert event.correlation_id == "instruction-1"
    assert event.related_event_reference == "transaction-0"
