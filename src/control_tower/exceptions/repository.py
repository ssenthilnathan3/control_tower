import hashlib
from datetime import datetime, timedelta
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

from .config import ExceptionPolicy
from .models import (
    ExceptionAction,
    ExceptionRole,
    ExceptionStatus,
    ExceptionWorkflowError,
)


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
    assignee: Mapped[str | None] = mapped_column(String(128))
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    escalation_path: Mapped[str] = mapped_column(String(256), default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_requested_by: Mapped[str | None] = mapped_column(String(128))


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


class ExceptionActionRecord(Base):
    __tablename__ = "exception_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_record_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"))
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    actor_role: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    before_status: Mapped[str | None] = mapped_column(String(32))
    after_status: Mapped[str] = mapped_column(String(32))
    before_assignee: Mapped[str | None] = mapped_column(String(128))
    after_assignee: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExceptionWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"
    UPDATED = "UPDATED"


class ExceptionRepository:
    def __init__(self, engine):
        self.engine = engine
        Base.metadata.create_all(engine)

    def create(
        self,
        decision: PersistedDecision,
        detected_at: datetime,
        policy: ExceptionPolicy,
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
                priority=policy.priority(decision.amount_paise),
                owner=policy.classes[decision.outcome].owner,
                recommended_action=policy.classes[decision.outcome].recommended_action,
                escalation_path=policy.classes[decision.outcome].escalation_path,
                detected_at=detected_at,
                sla_deadline=detected_at
                + timedelta(hours=policy.classes[decision.outcome].sla_hours),
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
            session.add(
                ExceptionActionRecord(
                    exception_record_id=exception.id,
                    action=ExceptionAction.DETECTED.value,
                    actor="system",
                    actor_role=ExceptionRole.SYSTEM.value,
                    reason=decision.reason,
                    before_status=None,
                    after_status=ExceptionStatus.OPEN.value,
                    before_assignee=None,
                    after_assignee=None,
                    created_at=detected_at,
                )
            )
            return ExceptionWriteOutcome.CREATED

    def assign(
        self,
        exception_id: str,
        assignee: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
    ) -> None:
        if role not in {ExceptionRole.OPERATOR, ExceptionRole.APPROVER}:
            raise ExceptionWorkflowError("operator or approver role is required")
        if not assignee.strip():
            raise ExceptionWorkflowError("assignee is required")
        with Session(self.engine) as session, session.begin():
            exception = self._get(session, exception_id)
            before = exception.assignee
            exception.assignee = assignee
            self._action(
                session,
                exception,
                ExceptionAction.ASSIGNED,
                actor,
                role,
                reason,
                at,
                exception.status,
                before,
            )

    def start_investigation(
        self,
        exception_id: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
    ) -> None:
        self._transition(
            exception_id,
            {ExceptionStatus.OPEN, ExceptionStatus.REOPENED},
            ExceptionStatus.INVESTIGATING,
            ExceptionAction.INVESTIGATION_STARTED,
            actor,
            role,
            reason,
            at,
        )

    def request_resolution(
        self,
        exception_id: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
    ) -> None:
        self._transition(
            exception_id,
            {ExceptionStatus.INVESTIGATING},
            ExceptionStatus.PENDING_APPROVAL,
            ExceptionAction.RESOLUTION_REQUESTED,
            actor,
            role,
            reason,
            at,
            requested_by=actor,
        )

    def _transition(
        self,
        exception_id: str,
        allowed: set[ExceptionStatus],
        target: ExceptionStatus,
        action: ExceptionAction,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
        requested_by: str | None = None,
    ) -> None:
        if role not in {ExceptionRole.OPERATOR, ExceptionRole.APPROVER}:
            raise ExceptionWorkflowError("operator or approver role is required")
        with Session(self.engine) as session, session.begin():
            exception = self._get(session, exception_id)
            before_status = exception.status
            if ExceptionStatus(before_status) not in allowed:
                raise ExceptionWorkflowError(
                    f"cannot {action.value} from {before_status}"
                )
            exception.status = target.value
            if requested_by:
                exception.resolution_requested_by = requested_by
            self._action(
                session,
                exception,
                action,
                actor,
                role,
                reason,
                at,
                before_status,
                exception.assignee,
            )

    @staticmethod
    def _get(session: Session, exception_id: str) -> ExceptionRecord:
        exception = session.scalar(
            select(ExceptionRecord).where(ExceptionRecord.exception_id == exception_id)
        )
        if exception is None:
            raise ExceptionWorkflowError(f"exception does not exist: {exception_id}")
        return exception

    @staticmethod
    def _action(
        session: Session,
        exception: ExceptionRecord,
        action: ExceptionAction,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
        before_status: str,
        before_assignee: str | None,
    ) -> None:
        if not actor.strip() or not reason.strip():
            raise ExceptionWorkflowError("actor and reason are required")
        session.add(
            ExceptionActionRecord(
                exception_record_id=exception.id,
                action=action.value,
                actor=actor,
                actor_role=role.value,
                reason=reason,
                before_status=before_status,
                after_status=exception.status,
                before_assignee=before_assignee,
                after_assignee=exception.assignee,
                created_at=at,
            )
        )

    def count(self) -> int:
        with Session(self.engine) as session:
            return len(session.scalars(select(ExceptionRecord.id)).all())
