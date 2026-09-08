from datetime import date, datetime

from sqlalchemy import create_engine

from control_tower.canonical import (
    CanonicalEvent,
    CanonicalRepository,
    CanonicalStatus,
    CanonicalWrite,
    CanonicalWriteOutcome,
    EventType,
    SourceProvenance,
    SourceSystem,
)


def _event(record_id: str) -> CanonicalEvent:
    source_timestamp = datetime.fromisoformat("2026-09-01T10:00:00+05:30")
    return CanonicalEvent(
        provenance=SourceProvenance(
            int(record_id.rsplit("-", 1)[1]),
            "a" * 64,
            "b" * 64,
            f"source.csv#line={record_id.rsplit('-', 1)[1]}",
        ),
        source_system=SourceSystem.ORIGINATOR,
        event_type=EventType.INSTRUCTION,
        source_record_id=record_id,
        business_event_id=record_id,
        correlation_id=record_id,
        partner_code="ARUNA",
        batch_id="batch-1",
        loan_id=None,
        customer_surrogate_id="customer-1",
        partner_loan_reference="partner-loan-1",
        source_timestamp=source_timestamp,
        received_timestamp=datetime.fromisoformat("2026-09-01T10:02:00+05:30"),
        business_date=date(2026, 9, 1),
        amount_paise=125000,
        currency="INR",
        source_status="APPROVED",
        canonical_status=CanonicalStatus.READY,
        related_event_reference=None,
    )


def test_persists_canonical_records_idempotently(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'canonical.db'}")
    repository = CanonicalRepository(engine)
    writes = [CanonicalWrite(_event("instruction-1"))]

    assert repository.save_many(writes) == [CanonicalWriteOutcome.CREATED]
    assert repository.save_many(writes) == [CanonicalWriteOutcome.REPLAY]
    assert repository.count() == 1


def test_persists_multiple_source_versions_in_one_batch(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'canonical.db'}")
    repository = CanonicalRepository(engine)
    writes = [
        CanonicalWrite(_event("instruction-1")),
        CanonicalWrite(_event("instruction-2")),
    ]

    assert repository.save_many(writes) == [
        CanonicalWriteOutcome.CREATED,
        CanonicalWriteOutcome.CREATED,
    ]
    assert repository.count() == 2
