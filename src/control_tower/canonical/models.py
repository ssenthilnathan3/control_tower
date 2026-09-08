import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CanonicalContractError(ValueError):
    pass


class SourceSystem(str, Enum):
    ORIGINATOR = "originator"
    LMS = "lms"
    BANK = "bank"


class EventType(str, Enum):
    INSTRUCTION = "INSTRUCTION"
    BOOKING = "BOOKING"
    SETTLEMENT = "SETTLEMENT"


class CanonicalStatus(str, Enum):
    READY = "READY"
    SUCCESS = "SUCCESS"
    PENDING = "PENDING"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class SourceProvenance:
    source_version_id: int
    payload_hash: str
    artifact_hash: str
    source_location: str

    def __post_init__(self) -> None:
        if self.source_version_id <= 0:
            raise CanonicalContractError("source_version_id must be positive")
        if not HASH_PATTERN.fullmatch(self.payload_hash):
            raise CanonicalContractError("payload_hash must be a lowercase SHA-256")
        if not HASH_PATTERN.fullmatch(self.artifact_hash):
            raise CanonicalContractError("artifact_hash must be a lowercase SHA-256")
        if "#line=" not in self.source_location:
            raise CanonicalContractError("source_location must include a line number")


@dataclass(frozen=True)
class CanonicalEvent:
    provenance: SourceProvenance
    source_system: SourceSystem
    event_type: EventType
    source_record_id: str
    business_event_id: str
    correlation_id: str
    partner_code: str
    batch_id: str
    loan_id: str | None
    customer_surrogate_id: str | None
    partner_loan_reference: str | None
    source_timestamp: datetime
    received_timestamp: datetime
    business_date: date
    amount_paise: int
    currency: str
    source_status: str
    canonical_status: CanonicalStatus
    related_event_reference: str | None

    def __post_init__(self) -> None:
        required = {
            "source_record_id": self.source_record_id,
            "business_event_id": self.business_event_id,
            "correlation_id": self.correlation_id,
            "partner_code": self.partner_code,
            "batch_id": self.batch_id,
            "source_status": self.source_status,
        }
        empty = [name for name, value in required.items() if not value.strip()]
        if empty:
            raise CanonicalContractError(f"{', '.join(empty)} cannot be empty")
        if self.amount_paise <= 0:
            raise CanonicalContractError("amount_paise must be positive")
        if self.currency != "INR":
            raise CanonicalContractError("currency must be INR")
        for field, value in (
            ("source_timestamp", self.source_timestamp),
            ("received_timestamp", self.received_timestamp),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise CanonicalContractError(f"{field} must include a timezone")
