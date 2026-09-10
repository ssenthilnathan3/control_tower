from __future__ import annotations

from dataclasses import dataclass
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
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, selectinload

from control_tower.ingestion.registry import Base

from .models import ReconciliationDecision, ReconciliationOutcome


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
    partner_code: Mapped[str] = mapped_column(String(64), index=True)
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


@dataclass(frozen=True)
class PersistedDecision:
    decision_id: int
    run_key: str
    business_event_id: str
    partner_code: str
    outcome: ReconciliationOutcome
    amount_paise: int
    reason: str
    rule_version: str
    source_version_ids: tuple[int, ...]


@dataclass(frozen=True)
class PersistedRun:
    run_key: str
    snapshot_hash: str
    config_hash: str
    rule_version: str
    created_at: datetime


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
                    partner_code=decision.partner_code,
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

    def decisions_for_run(self, run_key: str) -> list[PersistedDecision]:
        with Session(self.engine) as session:
            run = session.scalar(
                select(ReconciliationRunRecord)
                .options(
                    selectinload(ReconciliationRunRecord.decisions).selectinload(
                        DecisionRecord.members
                    )
                )
                .where(ReconciliationRunRecord.run_key == run_key)
            )
            if run is None:
                raise ValueError(f"reconciliation run does not exist: {run_key}")
            return [
                PersistedDecision(
                    decision.id,
                    run.run_key,
                    decision.business_event_id,
                    decision.partner_code,
                    ReconciliationOutcome(decision.outcome),
                    decision.amount_paise,
                    decision.reason,
                    decision.rule_version,
                    tuple(
                        sorted(member.source_version_id for member in decision.members)
                    ),
                )
                for decision in sorted(run.decisions, key=lambda item: item.id)
            ]

    def run(self, run_key: str) -> PersistedRun:
        with Session(self.engine) as session:
            run = session.scalar(
                select(ReconciliationRunRecord).where(
                    ReconciliationRunRecord.run_key == run_key
                )
            )
            if run is None:
                raise ValueError(f"reconciliation run does not exist: {run_key}")
            return PersistedRun(
                run.run_key,
                run.snapshot_hash,
                run.config_hash,
                run.rule_version,
                run.created_at,
            )

    def runs(self, limit: int = 50, offset: int = 0) -> list[PersistedRun]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1..200 and offset cannot be negative")
        with Session(self.engine) as session:
            records = session.scalars(
                select(ReconciliationRunRecord)
                .order_by(ReconciliationRunRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                PersistedRun(
                    record.run_key,
                    record.snapshot_hash,
                    record.config_hash,
                    record.rule_version,
                    record.created_at,
                )
                for record in records
            ]
