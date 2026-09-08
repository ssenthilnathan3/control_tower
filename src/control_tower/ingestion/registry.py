from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    identity: Mapped[SourceIdentity] = relationship(back_populates="versions")


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
                    identity.versions.append(
                        self._version(record, len(identity.versions) + 1, now)
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
        identity.versions.append(
            self._version(
                record, max(item.version for item in identity.versions) + 1, now
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
                identity.state, tuple(item.payload_hash for item in versions)
            )

    @staticmethod
    def _version(record: Registration, version: int, now: datetime) -> SourceVersion:
        return SourceVersion(
            version=version,
            payload_hash=record.payload_hash,
            artifact_hash=record.artifact_hash,
            source_location=record.source_location,
            validation_state=record.validation_state,
            first_seen_at=now,
        )
