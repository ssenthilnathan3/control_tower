from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
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
    __table_args__ = (
        UniqueConstraint(
            "partner_code", "business_event_id", name="uq_exception_partner_event"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_id: Mapped[str] = mapped_column(String(36), unique=True)
    business_event_id: Mapped[str] = mapped_column(String(128))
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
    material_override: Mapped[bool] = mapped_column(Boolean, default=False)


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


@event.listens_for(ExceptionActionRecord, "before_update")
@event.listens_for(ExceptionActionRecord, "before_delete")
def _reject_action_mutation(*_args) -> None:
    raise RuntimeError("exception actions are append-only")


class ExceptionWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"
    UPDATED = "UPDATED"


@dataclass(frozen=True)
class ExceptionSummary:
    exception_id: str
    business_event_id: str
    partner_code: str
    classification: str
    amount_paise: int
    status: ExceptionStatus
    priority: str
    owner: str
    assignee: str | None
    detected_at: datetime
    sla_deadline: datetime | None


@dataclass(frozen=True)
class ExceptionActionSnapshot:
    action: ExceptionAction
    actor: str
    actor_role: ExceptionRole
    reason: str
    before_status: str | None
    after_status: str
    before_assignee: str | None
    after_assignee: str | None
    created_at: datetime


@dataclass(frozen=True)
class ExceptionClassSummary:
    classification: str
    count: int
    amount_paise: int


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
        try:
            return self._create(decision, detected_at, policy)
        except IntegrityError:
            with Session(self.engine) as session:
                existing = session.scalar(
                    select(ExceptionRecord).where(
                        ExceptionRecord.business_event_id == decision.business_event_id,
                        ExceptionRecord.partner_code == decision.partner_code,
                    )
                )
                if existing and existing.latest_decision_id == decision.decision_id:
                    return ExceptionWriteOutcome.REPLAY
            raise

    def _create(
        self,
        decision: PersistedDecision,
        detected_at: datetime,
        policy: ExceptionPolicy,
    ) -> ExceptionWriteOutcome:
        with Session(self.engine) as session, session.begin():
            existing = session.scalar(
                select(ExceptionRecord).where(
                    ExceptionRecord.business_event_id == decision.business_event_id,
                    ExceptionRecord.partner_code == decision.partner_code,
                )
            )
            if existing is not None:
                if existing.latest_decision_id == decision.decision_id:
                    return ExceptionWriteOutcome.REPLAY
                before_status = existing.status
                existing.latest_decision_id = decision.decision_id
                existing.classification = decision.outcome.value
                existing.amount_paise = decision.amount_paise
                existing.priority = policy.priority(decision.amount_paise)
                class_policy = policy.classes[decision.outcome]
                existing.owner = class_policy.owner
                existing.recommended_action = class_policy.recommended_action
                existing.escalation_path = class_policy.escalation_path
                existing.sla_deadline = detected_at + timedelta(
                    hours=class_policy.sla_hours
                )
                session.add_all(
                    ExceptionEvidenceRecord(
                        exception_record_id=existing.id,
                        decision_id=decision.decision_id,
                        source_version_id=source_version_id,
                    )
                    for source_version_id in decision.source_version_ids
                )
                if before_status == ExceptionStatus.RESOLVED.value:
                    existing.status = ExceptionStatus.REOPENED.value
                    self._action(
                        session,
                        existing,
                        ExceptionAction.REOPENED,
                        "system",
                        ExceptionRole.SYSTEM,
                        "reconciliation decision changed",
                        detected_at,
                        before_status,
                        existing.assignee,
                    )
                return ExceptionWriteOutcome.UPDATED
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
        material_override: bool = False,
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
            material_override=material_override,
        )

    def approve_resolution(
        self,
        exception_id: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
    ) -> None:
        self._decide_resolution(exception_id, actor, role, reason, at, approved=True)

    def reject_resolution(
        self,
        exception_id: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
    ) -> None:
        self._decide_resolution(exception_id, actor, role, reason, at, approved=False)

    def resolve_all(
        self,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
        exception_ids: list[str] | None = None,
    ) -> int:
        if role is not ExceptionRole.APPROVER:
            raise ExceptionWorkflowError("approver role is required")
        with Session(self.engine) as session, session.begin():
            statement = select(ExceptionRecord).where(
                ExceptionRecord.status != ExceptionStatus.RESOLVED.value
            )
            if exception_ids is not None:
                statement = statement.where(
                    ExceptionRecord.exception_id.in_(exception_ids)
                )
            records = session.scalars(statement).all()
            for exception in records:
                status = ExceptionStatus(exception.status)
                if status in {ExceptionStatus.OPEN, ExceptionStatus.REOPENED}:
                    before = exception.status
                    exception.status = ExceptionStatus.INVESTIGATING.value
                    self._action(
                        session,
                        exception,
                        ExceptionAction.INVESTIGATION_STARTED,
                        actor,
                        role,
                        reason,
                        at,
                        before,
                        exception.assignee,
                    )
                    status = ExceptionStatus.INVESTIGATING
                if status is ExceptionStatus.INVESTIGATING:
                    before = exception.status
                    exception.status = ExceptionStatus.PENDING_APPROVAL.value
                    exception.resolution_requested_by = actor
                    exception.material_override = False
                    self._action(
                        session,
                        exception,
                        ExceptionAction.RESOLUTION_REQUESTED,
                        actor,
                        role,
                        reason,
                        at,
                        before,
                        exception.assignee,
                    )
                if (
                    exception.material_override
                    and exception.resolution_requested_by == actor
                ):
                    raise ExceptionWorkflowError(
                        "material override requires another approver"
                    )
                before = exception.status
                exception.status = ExceptionStatus.RESOLVED.value
                self._action(
                    session,
                    exception,
                    ExceptionAction.APPROVED,
                    actor,
                    role,
                    reason,
                    at,
                    before,
                    exception.assignee,
                )
            return len(records)

    def _decide_resolution(
        self,
        exception_id: str,
        actor: str,
        role: ExceptionRole,
        reason: str,
        at: datetime,
        approved: bool,
    ) -> None:
        if role is not ExceptionRole.APPROVER:
            raise ExceptionWorkflowError("approver role is required")
        with Session(self.engine) as session, session.begin():
            exception = self._get(session, exception_id)
            if exception.status != ExceptionStatus.PENDING_APPROVAL.value:
                raise ExceptionWorkflowError(
                    f"cannot decide resolution from {exception.status}"
                )
            if (
                exception.material_override
                and exception.resolution_requested_by == actor
            ):
                raise ExceptionWorkflowError(
                    "material override requires another approver"
                )
            before_status = exception.status
            exception.status = (
                ExceptionStatus.RESOLVED.value
                if approved
                else ExceptionStatus.INVESTIGATING.value
            )
            self._action(
                session,
                exception,
                ExceptionAction.APPROVED if approved else ExceptionAction.REJECTED,
                actor,
                role,
                reason,
                at,
                before_status,
                exception.assignee,
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
        material_override: bool | None = None,
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
            if material_override is not None:
                exception.material_override = material_override
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

    def count(
        self,
        status: ExceptionStatus | None = None,
        partner_code: str | None = None,
        priority: str | None = None,
        classification: str | None = None,
        owner: str | None = None,
        min_amount_paise: int | None = None,
    ) -> int:
        with Session(self.engine) as session:
            statement = select(ExceptionRecord.id)
            if status is not None:
                statement = statement.where(ExceptionRecord.status == status.value)
            if partner_code is not None:
                statement = statement.where(
                    ExceptionRecord.partner_code.ilike(f"%{partner_code}%")
                )
            if priority:
                statement = statement.where(ExceptionRecord.priority == priority)
            if classification:
                statement = statement.where(
                    ExceptionRecord.classification.ilike(f"%{classification}%")
                )
            if owner:
                statement = statement.where(
                    ExceptionRecord.owner.ilike(f"%{owner}%")
                    | ExceptionRecord.assignee.ilike(f"%{owner}%")
                )
            if min_amount_paise is not None:
                statement = statement.where(
                    ExceptionRecord.amount_paise >= min_amount_paise
                )
            return len(session.scalars(statement).all())

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: ExceptionStatus | None = None,
        partner_code: str | None = None,
        priority: str | None = None,
        classification: str | None = None,
        owner: str | None = None,
        min_amount_paise: int | None = None,
    ) -> list[ExceptionSummary]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1..200 and offset cannot be negative")
        statement = select(ExceptionRecord)
        if status is not None:
            statement = statement.where(ExceptionRecord.status == status.value)
        if partner_code is not None:
            statement = statement.where(
                ExceptionRecord.partner_code.ilike(f"%{partner_code}%")
            )
        if priority:
            statement = statement.where(ExceptionRecord.priority == priority)
        if classification:
            statement = statement.where(
                ExceptionRecord.classification.ilike(f"%{classification}%")
            )
        if owner:
            statement = statement.where(
                ExceptionRecord.owner.ilike(f"%{owner}%")
                | ExceptionRecord.assignee.ilike(f"%{owner}%")
            )
        if min_amount_paise is not None:
            statement = statement.where(
                ExceptionRecord.amount_paise >= min_amount_paise
            )
        statement = statement.order_by(ExceptionRecord.id).limit(limit).offset(offset)
        with Session(self.engine) as session:
            return [self._summary(record) for record in session.scalars(statement)]

    def actions(self, exception_id: str) -> list[ExceptionActionSnapshot]:
        with Session(self.engine) as session:
            exception = self._get(session, exception_id)
            records = session.scalars(
                select(ExceptionActionRecord)
                .where(ExceptionActionRecord.exception_record_id == exception.id)
                .order_by(ExceptionActionRecord.id)
            )
            return [
                ExceptionActionSnapshot(
                    ExceptionAction(record.action),
                    record.actor,
                    ExceptionRole(record.actor_role),
                    record.reason,
                    record.before_status,
                    record.after_status,
                    record.before_assignee,
                    record.after_assignee,
                    record.created_at,
                )
                for record in records
            ]

    def classification_summary(self) -> list[ExceptionClassSummary]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    ExceptionRecord.classification,
                    func.count(ExceptionRecord.id),
                    func.sum(ExceptionRecord.amount_paise),
                )
                .where(ExceptionRecord.status != ExceptionStatus.RESOLVED.value)
                .group_by(ExceptionRecord.classification)
                .order_by(func.sum(ExceptionRecord.amount_paise).desc())
            )
            return [
                ExceptionClassSummary(classification, count, amount_paise or 0)
                for classification, count, amount_paise in rows
            ]

    def resolved_decision_ids(self, decision_ids: list[int]) -> set[int]:
        if not decision_ids:
            return set()
        with Session(self.engine) as session:
            return set(
                session.scalars(
                    select(ExceptionRecord.latest_decision_id).where(
                        ExceptionRecord.latest_decision_id.in_(decision_ids),
                        ExceptionRecord.status == ExceptionStatus.RESOLVED.value,
                    )
                ).all()
            )

    def unresolved_ids(self) -> list[str]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(ExceptionRecord.exception_id)
                    .where(ExceptionRecord.status != ExceptionStatus.RESOLVED.value)
                    .order_by(ExceptionRecord.id)
                ).all()
            )

    @staticmethod
    def _summary(record: ExceptionRecord) -> ExceptionSummary:
        return ExceptionSummary(
            record.exception_id,
            record.business_event_id,
            record.partner_code,
            record.classification,
            record.amount_paise,
            ExceptionStatus(record.status),
            record.priority,
            record.owner,
            record.assignee,
            record.detected_at,
            record.sla_deadline,
        )
