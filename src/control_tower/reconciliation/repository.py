from __future__ import annotations

from datetime import datetime, timezone
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
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from control_tower.ingestion.registry import Base

from .models import ReconciliationDecision


class ReconciliationRunRecord(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decisions: Mapped[list[DecisionRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DecisionRecord(Base):
    __tablename__ = "reconciliation_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "business_event_id", name="uq_run_business_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"))
    business_event_id: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32))
    amount_paise: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(String(64))
    run: Mapped[ReconciliationRunRecord] = relationship(back_populates="decisions")
    members: Mapped[list[DecisionMemberRecord]] = relationship(
        cascade="all, delete-orphan"
    )


class DecisionMemberRecord(Base):
    __tablename__ = "reconciliation_decision_members"
    __table_args__ = (
        UniqueConstraint("run_id", "source_version_id", name="uq_run_source_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_decisions.id"))
    source_version_id: Mapped[int] = mapped_column(ForeignKey("source_versions.id"))
    run: Mapped[ReconciliationRunRecord] = relationship()


class ReconciliationWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"


class ReconciliationRepository:
    def __init__(self, engine):
        self.engine = engine
        Base.metadata.create_all(engine)

    def save(
        self,
        run_key: str,
        snapshot_hash: str,
        config_hash: str,
        rule_version: str,
        decisions: list[ReconciliationDecision],
    ) -> ReconciliationWriteOutcome:
        with Session(self.engine) as session, session.begin():
            if session.scalar(
                select(ReconciliationRunRecord.id).where(
                    ReconciliationRunRecord.run_key == run_key
                )
            ):
                return ReconciliationWriteOutcome.REPLAY
            run = ReconciliationRunRecord(
                run_key=run_key,
                snapshot_hash=snapshot_hash,
                config_hash=config_hash,
                rule_version=rule_version,
                created_at=datetime.now(timezone.utc),
            )
            for decision in decisions:
                record = DecisionRecord(
                    business_event_id=decision.business_event_id,
                    outcome=decision.outcome.value,
                    amount_paise=decision.amount_paise,
                    reason=decision.reason,
                    rule_version=decision.rule_version,
                )
                record.members = [
                    DecisionMemberRecord(run=run, source_version_id=source_version_id)
                    for source_version_id in decision.source_version_ids
                ]
                run.decisions.append(record)
            session.add(run)
            return ReconciliationWriteOutcome.CREATED
