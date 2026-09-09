from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from control_tower.ingestion.registry import Base, SourceIdentity, SourceVersion

from .models import (
    CanonicalEvent,
    CanonicalStatus,
    EventType,
    SourceProvenance,
    SourceSystem,
)


class CanonicalRecord(Base):
    __tablename__ = "canonical_records"
    __table_args__ = (
        UniqueConstraint("source_version_id", name="uq_canonical_source_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_version_id: Mapped[int] = mapped_column(
        ForeignKey("source_versions.id", ondelete="RESTRICT")
    )
    source_system: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(32))
    source_record_id: Mapped[str] = mapped_column(String(128))
    business_event_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    partner_code: Mapped[str] = mapped_column(String(64), index=True)
    batch_id: Mapped[str] = mapped_column(String(128), index=True)
    loan_id: Mapped[str | None] = mapped_column(String(128))
    customer_surrogate_id: Mapped[str | None] = mapped_column(String(128))
    partner_loan_reference: Mapped[str | None] = mapped_column(String(128))
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciliation_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    business_date: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    source_status: Mapped[str] = mapped_column(String(32))
    canonical_status: Mapped[str] = mapped_column(String(32))
    related_event_reference: Mapped[str | None] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    source_location: Mapped[str] = mapped_column(String(512))
    record_state: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class CanonicalWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"


@dataclass(frozen=True)
class CanonicalWrite:
    event: CanonicalEvent

    @property
    def source_version_id(self) -> int:
        return self.event.provenance.source_version_id


class CanonicalRepository:
    def __init__(self, engine):
        self.engine = engine
        Base.metadata.create_all(engine)

    def save_many(self, writes: list[CanonicalWrite]) -> list[CanonicalWriteOutcome]:
        if not writes:
            return []
        version_ids = [write.source_version_id for write in writes]
        with Session(self.engine) as session, session.begin():
            existing = {
                record.source_version_id: record
                for record in session.scalars(
                    select(CanonicalRecord).where(
                        CanonicalRecord.source_version_id.in_(version_ids)
                    )
                )
            }
            outcomes: list[CanonicalWriteOutcome] = []
            for write in writes:
                if write.source_version_id in existing:
                    existing[write.source_version_id].record_state = "ACTIVE"
                    outcomes.append(CanonicalWriteOutcome.REPLAY)
                    continue
                record = self._record(write)
                session.add(record)
                existing[write.source_version_id] = record
                outcomes.append(CanonicalWriteOutcome.CREATED)
            return outcomes

    def sync_eligibility(self) -> None:
        with Session(self.engine) as session, session.begin():
            eligible = (
                select(SourceVersion.id)
                .join(SourceVersion.identity)
                .where(
                    SourceIdentity.state == "ACCEPTED",
                    SourceVersion.validation_state == "ACCEPTED",
                    SourceVersion.is_selected.is_(True),
                )
            )
            session.execute(update(CanonicalRecord).values(record_state="BLOCKED"))
            session.execute(
                update(CanonicalRecord)
                .where(CanonicalRecord.source_version_id.in_(eligible))
                .values(record_state="ACTIVE")
            )

    def count(self, record_state: str | None = None) -> int:
        with Session(self.engine) as session:
            statement = select(func.count()).select_from(CanonicalRecord)
            if record_state:
                statement = statement.where(
                    CanonicalRecord.record_state == record_state
                )
            return session.scalar(statement) or 0

    def active_events(self) -> list[CanonicalEvent]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(CanonicalRecord)
                .where(CanonicalRecord.record_state == "ACTIVE")
                .order_by(CanonicalRecord.source_version_id)
            ).all()
            return [self._event(record) for record in records]

    @staticmethod
    def _record(write: CanonicalWrite) -> CanonicalRecord:
        event = write.event
        return CanonicalRecord(
            source_version_id=write.source_version_id,
            source_system=event.source_system.value,
            event_type=event.event_type.value,
            source_record_id=event.source_record_id,
            business_event_id=event.business_event_id,
            correlation_id=event.correlation_id,
            partner_code=event.partner_code,
            batch_id=event.batch_id,
            loan_id=event.loan_id,
            customer_surrogate_id=event.customer_surrogate_id,
            partner_loan_reference=event.partner_loan_reference,
            source_timestamp=event.source_timestamp,
            received_timestamp=event.received_timestamp,
            reconciliation_cutoff=event.reconciliation_cutoff,
            business_date=event.business_date,
            amount_paise=event.amount_paise,
            currency=event.currency,
            source_status=event.source_status,
            canonical_status=event.canonical_status.value,
            related_event_reference=event.related_event_reference,
            payload_hash=event.provenance.payload_hash,
            artifact_hash=event.provenance.artifact_hash,
            source_location=event.provenance.source_location,
            record_state="ACTIVE",
        )

    @staticmethod
    def _event(record: CanonicalRecord) -> CanonicalEvent:
        return CanonicalEvent(
            provenance=SourceProvenance(
                record.source_version_id,
                record.payload_hash,
                record.artifact_hash,
                record.source_location,
            ),
            source_system=SourceSystem(record.source_system),
            event_type=EventType(record.event_type),
            source_record_id=record.source_record_id,
            business_event_id=record.business_event_id,
            correlation_id=record.correlation_id,
            partner_code=record.partner_code,
            batch_id=record.batch_id,
            loan_id=record.loan_id,
            customer_surrogate_id=record.customer_surrogate_id,
            partner_loan_reference=record.partner_loan_reference,
            source_timestamp=record.source_timestamp.replace(tzinfo=timezone.utc),
            received_timestamp=record.received_timestamp.replace(tzinfo=timezone.utc),
            reconciliation_cutoff=record.reconciliation_cutoff.replace(
                tzinfo=timezone.utc
            ),
            business_date=record.business_date,
            amount_paise=record.amount_paise,
            currency=record.currency,
            source_status=record.source_status,
            canonical_status=CanonicalStatus(record.canonical_status),
            related_event_reference=record.related_event_reference,
        )
