from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


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
class CanonicalEvent:
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
