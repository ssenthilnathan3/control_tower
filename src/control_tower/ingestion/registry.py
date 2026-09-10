from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)


class Base(DeclarativeBase):
    pass


class SourceIdentity(Base):
    __tablename__ = "source_identities"
    __table_args__ = (
        UniqueConstraint("source", "batch_id", "record_id", name="uq_source_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    batch_id: Mapped[str] = mapped_column(String(128))
    record_id: Mapped[str] = mapped_column(String(128))
    partner_code: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    versions: Mapped[list[SourceVersion]] = relationship(
        back_populates="identity", cascade="all, delete-orphan"
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("identity_id", "payload_hash", name="uq_identity_payload"),
        UniqueConstraint("identity_id", "version", name="uq_identity_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("source_identities.id"))
    version: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    source_location: Mapped[str] = mapped_column(String(256))
    validation_state: Mapped[str] = mapped_column(String(32))
    is_selected: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    identity: Mapped[SourceIdentity] = relationship(back_populates="versions")


class DeliveryControlRecord(Base):
    __tablename__ = "delivery_controls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_key: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(32))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    row_count: Mapped[int] = mapped_column(Integer)
    total_amount_paise: Mapped[int | None] = mapped_column(Integer)
    quarantined_count: Mapped[int] = mapped_column(Integer)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    evidence_path: Mapped[str] = mapped_column(String(512))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegistrationOutcome(str, Enum):
    NEW = "NEW"
    REPLAY = "REPLAY"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Registration:
    source: str
    batch_id: str
    record_id: str
    partner_code: str
    payload_hash: str
    artifact_hash: str
    source_location: str
    validation_state: str


@dataclass(frozen=True)
class IdentitySnapshot:
    state: str
    payload_hashes: tuple[str, ...]
    selected_payload_hash: str | None


class ConflictResolutionError(ValueError):
    pass


class ConflictResolution(Base):
    __tablename__ = "conflict_resolutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("source_identities.id"))
    selected_version_id: Mapped[int] = mapped_column(ForeignKey("source_versions.id"))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(512))
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class CanonicalCandidate:
    source_version_id: int
    source: str
    payload_hash: str
    artifact_hash: str
    source_location: str


@dataclass(frozen=True)
class DeliveryControl:
    control_id: int
    delivery_key: str
    source: str
    artifact_hash: str
    status: str
    row_count: int
    total_amount_paise: int | None
    quarantined_count: int
    failure_reason: str | None
    evidence_path: str


@dataclass(frozen=True)
class QuarantineSnapshot:
    source_version_id: int
    source: str
    batch_id: str
    record_id: str
    partner_code: str
    artifact_hash: str
    source_location: str


class IngestionRegistry:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)

    @classmethod
    def local(cls, evidence_root: Path) -> IngestionRegistry:
        database = (evidence_root / "ingestion.db").resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{database}")

    def register(self, record: Registration) -> RegistrationOutcome:
        # The constraints are the final guard when two workers race on one identity.
        for attempt in range(2):
            try:
                return self._register(record)
            except IntegrityError:
                if attempt:
                    raise
        raise RuntimeError("registration retry exhausted")

    def record_delivery_control(
        self,
        delivery_key: str,
        source: str,
        artifact_hash: str,
        manifest_hash: str,
        status: str,
        row_count: int,
        total_amount_paise: int | None,
        quarantined_count: int,
        failure_reason: str | None,
        evidence_path: Path,
    ) -> int:
        with Session(self.engine) as session, session.begin():
            existing = session.scalar(
                select(DeliveryControlRecord).where(
                    DeliveryControlRecord.delivery_key == delivery_key
                )
            )
            if existing is not None:
                return existing.id
            record = DeliveryControlRecord(
                delivery_key=delivery_key,
                source=source,
                artifact_hash=artifact_hash,
                manifest_hash=manifest_hash,
                status=status,
                row_count=row_count,
                total_amount_paise=total_amount_paise,
                quarantined_count=quarantined_count,
                failure_reason=failure_reason,
                evidence_path=str(evidence_path),
                received_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.flush()
            return record.id

    def delivery_controls(self, control_ids: tuple[int, ...]) -> list[DeliveryControl]:
        if not control_ids:
            return []
        with Session(self.engine) as session:
            records = session.scalars(
                select(DeliveryControlRecord).where(
                    DeliveryControlRecord.id.in_(control_ids)
                )
            ).all()
            if len(records) != len(set(control_ids)):
                raise ValueError("one or more delivery controls do not exist")
            return [
                DeliveryControl(
                    record.id,
                    record.delivery_key,
                    record.source,
                    record.artifact_hash,
                    record.status,
                    record.row_count,
                    record.total_amount_paise,
                    record.quarantined_count,
                    record.failure_reason,
                    record.evidence_path,
                )
                for record in sorted(records, key=lambda item: item.id)
            ]

    def delivery_control_ids(self, status: str | None = None) -> tuple[int, ...]:
        with Session(self.engine) as session:
            statement = select(DeliveryControlRecord.id)
            if status is not None:
                statement = statement.where(DeliveryControlRecord.status == status)
            return tuple(session.scalars(statement.order_by(DeliveryControlRecord.id)))

    def quarantines_for_artifacts(
        self, artifact_hashes: tuple[str, ...]
    ) -> list[QuarantineSnapshot]:
        if not artifact_hashes:
            return []
        with Session(self.engine) as session:
            records = session.execute(
                select(SourceVersion, SourceIdentity)
                .join(SourceIdentity, SourceVersion.identity_id == SourceIdentity.id)
                .where(
                    SourceVersion.artifact_hash.in_(artifact_hashes),
                    SourceVersion.validation_state == "QUARANTINED",
                )
            ).all()
            return [
                QuarantineSnapshot(
                    version.id,
                    identity.source,
                    identity.batch_id,
                    identity.record_id,
                    identity.partner_code,
                    version.artifact_hash,
                    version.source_location,
                )
                for version, identity in sorted(records, key=lambda item: item[0].id)
            ]

    def artifact_hashes_for_versions(
        self, source_version_ids: tuple[int, ...]
    ) -> tuple[str, ...]:
        if not source_version_ids:
            return ()
        with Session(self.engine) as session:
            records = session.execute(
                select(SourceVersion.id, SourceVersion.artifact_hash).where(
                    SourceVersion.id.in_(source_version_ids)
                )
            ).all()
            if len(records) != len(set(source_version_ids)):
                raise ValueError("one or more source versions do not exist")
            return tuple(sorted({artifact_hash for _, artifact_hash in records}))

    def _register(self, record: Registration) -> RegistrationOutcome:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            return self._register_in_session(session, record, now)

    def register_many(self, records: list[Registration]) -> list[RegistrationOutcome]:
        # One artifact is one commit. per-row commits made a normal replay take minutes.
        for attempt in range(2):
            try:
                return self._register_many(records)
            except IntegrityError:
                if attempt:
                    raise
        raise RuntimeError("registration retry exhausted")

    def _register_many(self, records: list[Registration]) -> list[RegistrationOutcome]:
        if not records:
            return []
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            # An artifact contains one source. load its identities and versions once;
            # querying per row turns replay into an N+1 path.
            existing = session.scalars(
                select(SourceIdentity)
                .options(selectinload(SourceIdentity.versions))
                .where(SourceIdentity.source == records[0].source)
            ).all()
            identities = {
                (identity.batch_id, identity.record_id): identity
                for identity in existing
            }
            outcomes: list[RegistrationOutcome] = []
            for record in records:
                key = (record.batch_id, record.record_id)
                identity = identities.get(key)
                if identity is None:
                    identity = SourceIdentity(
                        source=record.source,
                        batch_id=record.batch_id,
                        record_id=record.record_id,
                        partner_code=record.partner_code,
                        state=record.validation_state,
                        first_seen_at=now,
                    )
                    identity.versions.append(self._version(record, 1, now))
                    session.add(identity)
                    identities[key] = identity
                    outcomes.append(RegistrationOutcome.NEW)
                elif any(
                    item.payload_hash == record.payload_hash
                    for item in identity.versions
                ):
                    outcomes.append(
                        RegistrationOutcome.CONFLICT
                        if identity.state == "CONFLICT"
                        else RegistrationOutcome.REPLAY
                    )
                else:
                    identity.state = "CONFLICT"
                    for version in identity.versions:
                        version.is_selected = False
                    identity.versions.append(
                        self._version(
                            record, len(identity.versions) + 1, now, is_selected=False
                        )
                    )
                    outcomes.append(RegistrationOutcome.CONFLICT)
            return outcomes

    def _register_in_session(
        self, session: Session, record: Registration, now: datetime
    ) -> RegistrationOutcome:
        identity = session.scalar(
            select(SourceIdentity).where(
                SourceIdentity.source == record.source,
                SourceIdentity.batch_id == record.batch_id,
                SourceIdentity.record_id == record.record_id,
            )
        )
        if identity is None:
            identity = SourceIdentity(
                source=record.source,
                batch_id=record.batch_id,
                record_id=record.record_id,
                partner_code=record.partner_code,
                state=record.validation_state,
                first_seen_at=now,
            )
            identity.versions.append(self._version(record, 1, now))
            session.add(identity)
            return RegistrationOutcome.NEW

        if any(item.payload_hash == record.payload_hash for item in identity.versions):
            return (
                RegistrationOutcome.CONFLICT
                if identity.state == "CONFLICT"
                else RegistrationOutcome.REPLAY
            )

        # Never choose between two payloads under the same source identity here.
        # Both survive and canonicalization must wait for the conflict to be resolved.
        identity.state = "CONFLICT"
        for version in identity.versions:
            version.is_selected = False
        identity.versions.append(
            self._version(
                record,
                max(item.version for item in identity.versions) + 1,
                now,
                is_selected=False,
            )
        )
        return RegistrationOutcome.CONFLICT

    def get_identity(
        self, source: str, batch_id: str, record_id: str
    ) -> IdentitySnapshot | None:
        with Session(self.engine) as session:
            identity = session.scalar(
                select(SourceIdentity).where(
                    SourceIdentity.source == source,
                    SourceIdentity.batch_id == batch_id,
                    SourceIdentity.record_id == record_id,
                )
            )
            if identity is None:
                return None
            versions = sorted(identity.versions, key=lambda value: value.version)
            return IdentitySnapshot(
                identity.state,
                tuple(item.payload_hash for item in versions),
                next(
                    (item.payload_hash for item in versions if item.is_selected), None
                ),
            )

    def resolve_conflict(
        self,
        source: str,
        batch_id: str,
        record_id: str,
        selected_payload_hash: str,
        actor: str,
        reason: str,
    ) -> None:
        if not actor.strip() or not reason.strip():
            raise ConflictResolutionError("actor and reason are required")
        with Session(self.engine) as session, session.begin():
            identity = session.scalar(
                select(SourceIdentity)
                .options(selectinload(SourceIdentity.versions))
                .where(
                    SourceIdentity.source == source,
                    SourceIdentity.batch_id == batch_id,
                    SourceIdentity.record_id == record_id,
                )
            )
            if identity is None or identity.state != "CONFLICT":
                raise ConflictResolutionError("source identity is not conflicted")
            selected = next(
                (
                    version
                    for version in identity.versions
                    if version.payload_hash == selected_payload_hash
                ),
                None,
            )
            if selected is None:
                raise ConflictResolutionError("selected payload is not a known version")
            if selected.validation_state != "ACCEPTED":
                raise ConflictResolutionError(
                    "a quarantined payload cannot be selected"
                )
            for version in identity.versions:
                version.is_selected = version.id == selected.id
            identity.state = "ACCEPTED"
            session.add(
                ConflictResolution(
                    identity_id=identity.id,
                    selected_version_id=selected.id,
                    actor=actor,
                    reason=reason,
                    resolved_at=datetime.now(timezone.utc),
                )
            )

    def canonical_candidates(self) -> list[CanonicalCandidate]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(SourceVersion, SourceIdentity.source)
                .join(SourceVersion.identity)
                .where(
                    SourceIdentity.state == "ACCEPTED",
                    SourceVersion.validation_state == "ACCEPTED",
                    SourceVersion.is_selected.is_(True),
                )
                .order_by(SourceVersion.id)
            ).all()
            return [
                CanonicalCandidate(
                    version.id,
                    source,
                    version.payload_hash,
                    version.artifact_hash,
                    version.source_location,
                )
                for version, source in rows
            ]

    @staticmethod
    def _version(
        record: Registration,
        version: int,
        now: datetime,
        is_selected: bool = True,
    ) -> SourceVersion:
        return SourceVersion(
            version=version,
            payload_hash=record.payload_hash,
            artifact_hash=record.artifact_hash,
            source_location=record.source_location,
            validation_state=record.validation_state,
            is_selected=is_selected,
            first_seen_at=now,
        )
