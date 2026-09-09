import hashlib
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from control_tower.ingestion.registry import Base
from control_tower.reconciliation import PersistedDecision

from .models import ExceptionStatus


class ExceptionRecord(Base):
    __tablename__ = "exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_id: Mapped[str] = mapped_column(String(36), unique=True)
    business_event_id: Mapped[str] = mapped_column(String(128), unique=True)
    latest_decision_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_decisions.id")
    )
    classification: Mapped[str] = mapped_column(String(32))
    amount_paise: Mapped[int] = mapped_column(Integer)
    partner_code: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(32), default="UNASSESSED")
    owner: Mapped[str] = mapped_column(String(128), default="UNASSIGNED")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExceptionEvidenceRecord(Base):
    __tablename__ = "exception_evidence"
    __table_args__ = (
        UniqueConstraint(
            "exception_record_id",
            "decision_id",
            "source_version_id",
            name="uq_exception_decision_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_record_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_decisions.id"))
    source_version_id: Mapped[int] = mapped_column(ForeignKey("source_versions.id"))


class ExceptionWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"
    UPDATED = "UPDATED"


class ExceptionRepository:
    def __init__(self, engine):
        self.engine = engine
        Base.metadata.create_all(engine)

    def create(
        self, decision: PersistedDecision, detected_at: datetime
    ) -> ExceptionWriteOutcome:
        with Session(self.engine) as session, session.begin():
            existing = session.scalar(
                select(ExceptionRecord).where(
                    ExceptionRecord.business_event_id == decision.business_event_id
                )
            )
            if existing is not None:
                return (
                    ExceptionWriteOutcome.REPLAY
                    if existing.latest_decision_id == decision.decision_id
                    else ExceptionWriteOutcome.UPDATED
                )
            digest = hashlib.sha256(
                f"{decision.partner_code}:{decision.business_event_id}".encode()
            ).hexdigest()[:20]
            exception = ExceptionRecord(
                exception_id=f"EXC-{digest}",
                business_event_id=decision.business_event_id,
                latest_decision_id=decision.decision_id,
                classification=decision.outcome.value,
                amount_paise=decision.amount_paise,
                partner_code=decision.partner_code,
                status=ExceptionStatus.OPEN.value,
                detected_at=detected_at,
            )
            session.add(exception)
            session.flush()
            session.add_all(
                ExceptionEvidenceRecord(
                    exception_record_id=exception.id,
                    decision_id=decision.decision_id,
                    source_version_id=source_version_id,
                )
                for source_version_id in decision.source_version_ids
            )
            return ExceptionWriteOutcome.CREATED

    def count(self) -> int:
        with Session(self.engine) as session:
            return len(session.scalars(select(ExceptionRecord.id)).all())
