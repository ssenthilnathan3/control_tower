from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from control_tower.ingestion.registry import Base

from .models import (
    BlockerType,
    CloseBlocker,
    CloseOutcome,
    CloseResult,
    CloseScorecard,
    CloseWriteOutcome,
)


class CloseDecisionRecord(Base):
    __tablename__ = "close_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_hash: Mapped[str] = mapped_column(String(64), unique=True)
    reconciliation_run_key: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16))
    accepted_count: Mapped[int] = mapped_column(Integer)
    accepted_value_paise: Mapped[int] = mapped_column(Integer)
    matched_count: Mapped[int] = mapped_column(Integer)
    matched_value_paise: Mapped[int] = mapped_column(Integer)
    pending_count: Mapped[int] = mapped_column(Integer)
    pending_value_paise: Mapped[int] = mapped_column(Integer)
    unresolved_count: Mapped[int] = mapped_column(Integer)
    unresolved_value_paise: Mapped[int] = mapped_column(Integer)
    quarantined_count: Mapped[int] = mapped_column(Integer)
    control_failure_count: Mapped[int] = mapped_column(Integer)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CloseBlockerRecord(Base):
    __tablename__ = "close_blockers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    close_decision_id: Mapped[int] = mapped_column(ForeignKey("close_decisions.id"))
    blocker_type: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(String(160))
    amount_paise: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    evidence_reference: Mapped[str] = mapped_column(String(512))


class CloseControlRepository:
    def __init__(self, engine):
        self.engine = engine
        Base.metadata.create_all(engine)

    def save(
        self,
        decision_hash: str,
        reconciliation_run_key: str,
        snapshot_hash: str,
        policy_version: str,
        policy_hash: str,
        actor: str,
        outcome: CloseOutcome,
        scorecard: CloseScorecard,
        blockers: tuple[CloseBlocker, ...],
        decided_at: datetime,
    ) -> CloseResult:
        try:
            return self._save(
                decision_hash,
                reconciliation_run_key,
                snapshot_hash,
                policy_version,
                policy_hash,
                actor,
                outcome,
                scorecard,
                blockers,
                decided_at,
            )
        except IntegrityError:
            return self.get(decision_hash)

    def _save(
        self,
        decision_hash: str,
        reconciliation_run_key: str,
        snapshot_hash: str,
        policy_version: str,
        policy_hash: str,
        actor: str,
        outcome: CloseOutcome,
        scorecard: CloseScorecard,
        blockers: tuple[CloseBlocker, ...],
        decided_at: datetime,
    ) -> CloseResult:
        with Session(self.engine) as session, session.begin():
            existing = session.scalar(
                select(CloseDecisionRecord).where(
                    CloseDecisionRecord.decision_hash == decision_hash
                )
            )
            if existing is not None:
                return self._result(session, existing, CloseWriteOutcome.REPLAY)
            record = CloseDecisionRecord(
                decision_hash=decision_hash,
                reconciliation_run_key=reconciliation_run_key,
                snapshot_hash=snapshot_hash,
                policy_version=policy_version,
                policy_hash=policy_hash,
                actor=actor,
                outcome=outcome.value,
                accepted_count=scorecard.accepted_count,
                accepted_value_paise=scorecard.accepted_value_paise,
                matched_count=scorecard.matched_count,
                matched_value_paise=scorecard.matched_value_paise,
                pending_count=scorecard.pending_count,
                pending_value_paise=scorecard.pending_value_paise,
                unresolved_count=scorecard.unresolved_count,
                unresolved_value_paise=scorecard.unresolved_value_paise,
                quarantined_count=scorecard.quarantined_count,
                control_failure_count=scorecard.control_failure_count,
                decided_at=decided_at,
            )
            session.add(record)
            session.flush()
            session.add_all(
                CloseBlockerRecord(
                    close_decision_id=record.id,
                    blocker_type=blocker.blocker_type.value,
                    reference=blocker.reference,
                    amount_paise=blocker.amount_paise,
                    reason=blocker.reason,
                    evidence_reference=blocker.evidence_reference,
                )
                for blocker in blockers
            )
            session.flush()
            return CloseResult(
                decision_hash,
                outcome,
                CloseWriteOutcome.CREATED,
                reconciliation_run_key,
                snapshot_hash,
                policy_version,
                actor,
                scorecard,
                blockers,
            )

    def get(self, decision_hash: str) -> CloseResult:
        with Session(self.engine) as session:
            record = session.scalar(
                select(CloseDecisionRecord).where(
                    CloseDecisionRecord.decision_hash == decision_hash
                )
            )
            if record is None:
                raise ValueError(f"close decision does not exist: {decision_hash}")
            return self._result(session, record, CloseWriteOutcome.REPLAY)

    def list(self, limit: int = 50, offset: int = 0) -> list[CloseResult]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1..200 and offset cannot be negative")
        with Session(self.engine) as session:
            records = session.scalars(
                select(CloseDecisionRecord)
                .order_by(CloseDecisionRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                self._result(session, record, CloseWriteOutcome.REPLAY)
                for record in records
            ]

    def count(self) -> int:
        with Session(self.engine) as session:
            return len(session.scalars(select(CloseDecisionRecord.id)).all())

    @staticmethod
    def _result(
        session: Session,
        record: CloseDecisionRecord,
        write_outcome: CloseWriteOutcome,
    ) -> CloseResult:
        blocker_records = session.scalars(
            select(CloseBlockerRecord)
            .where(CloseBlockerRecord.close_decision_id == record.id)
            .order_by(CloseBlockerRecord.id)
        ).all()
        scorecard = CloseScorecard(
            record.accepted_count,
            record.accepted_value_paise,
            record.matched_count,
            record.matched_value_paise,
            record.pending_count,
            record.pending_value_paise,
            record.unresolved_count,
            record.unresolved_value_paise,
            record.quarantined_count,
            record.control_failure_count,
        )
        blockers = tuple(
            CloseBlocker(
                BlockerType(blocker.blocker_type),
                blocker.reference,
                blocker.amount_paise,
                blocker.reason,
                blocker.evidence_reference,
            )
            for blocker in blocker_records
        )
        return CloseResult(
            record.decision_hash,
            CloseOutcome(record.outcome),
            write_outcome,
            record.reconciliation_run_key,
            record.snapshot_hash,
            record.policy_version,
            record.actor,
            scorecard,
            blockers,
        )
