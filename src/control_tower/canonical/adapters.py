from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from .models import CanonicalEvent, CanonicalStatus, EventType, SourceSystem


class SourceAdapter(ABC):
    source_system: SourceSystem
    event_type: EventType
    timestamp_field: str
    amount_field: str
    status_field: str
    status_map: ClassVar[dict[str, CanonicalStatus]]

    @abstractmethod
    def adapt(self, row: dict[str, str]) -> CanonicalEvent:
        pass

    def _common(self, row: dict[str, str]) -> dict[str, object]:
        source_timestamp = datetime.fromisoformat(row[self.timestamp_field])
        source_status = row[self.status_field]
        return {
            "source_system": self.source_system,
            "event_type": self.event_type,
            "partner_code": row["partner_code"],
            "batch_id": row["batch_id"],
            "source_timestamp": source_timestamp,
            "received_timestamp": datetime.fromisoformat(row["received_timestamp"]),
            "business_date": source_timestamp.date(),
            "amount_paise": int(row[self.amount_field]),
            "currency": row["currency"],
            "source_status": source_status,
            "canonical_status": self.status_map[source_status],
        }


class OriginatorAdapter(SourceAdapter):
    source_system = SourceSystem.ORIGINATOR
    event_type = EventType.INSTRUCTION
    timestamp_field = "instruction_timestamp"
    amount_field = "amount_paise"
    status_field = "status"
    status_map: ClassVar[dict[str, CanonicalStatus]] = {
        "APPROVED": CanonicalStatus.READY,
        "REJECTED": CanonicalStatus.FAILED,
        "CANCELLED": CanonicalStatus.CANCELLED,
    }

    def adapt(self, row: dict[str, str]) -> CanonicalEvent:
        return CanonicalEvent(
            **self._common(row),
            source_record_id=row["instruction_id"],
            business_event_id=row["instruction_id"],
            correlation_id=row["instruction_id"],
            loan_id=None,
            customer_surrogate_id=row["customer_surrogate_id"],
            partner_loan_reference=row["loan_reference"],
            related_event_reference=None,
        )


class LmsAdapter(SourceAdapter):
    source_system = SourceSystem.LMS
    event_type = EventType.BOOKING
    timestamp_field = "booking_timestamp"
    amount_field = "booked_amount_paise"
    status_field = "booking_status"
    status_map: ClassVar[dict[str, CanonicalStatus]] = {
        "BOOKED": CanonicalStatus.SUCCESS,
        "PENDING": CanonicalStatus.PENDING,
        "FAILED": CanonicalStatus.FAILED,
        "REVERSED": CanonicalStatus.REVERSED,
    }

    def adapt(self, row: dict[str, str]) -> CanonicalEvent:
        return CanonicalEvent(
            **self._common(row),
            source_record_id=row["booking_id"],
            business_event_id=row["booking_id"],
            correlation_id=row["partner_loan_reference"],
            loan_id=row["internal_loan_id"],
            customer_surrogate_id=None,
            partner_loan_reference=row["partner_loan_reference"],
            related_event_reference=None,
        )


class BankAdapter(SourceAdapter):
    source_system = SourceSystem.BANK
    event_type = EventType.SETTLEMENT
    timestamp_field = "value_timestamp"
    amount_field = "debit_amount_paise"
    status_field = "settlement_status"
    status_map: ClassVar[dict[str, CanonicalStatus]] = {
        "SETTLED": CanonicalStatus.SUCCESS,
        "PENDING": CanonicalStatus.PENDING,
        "FAILED": CanonicalStatus.FAILED,
        "REVERSED": CanonicalStatus.REVERSED,
    }

    def adapt(self, row: dict[str, str]) -> CanonicalEvent:
        return CanonicalEvent(
            **self._common(row),
            source_record_id=row["transaction_reference"],
            business_event_id=row["transaction_reference"],
            correlation_id=row["linked_instruction_reference"],
            loan_id=None,
            customer_surrogate_id=None,
            partner_loan_reference=None,
            related_event_reference=row["reversal_reference"] or None,
        )


ADAPTERS: dict[SourceSystem, SourceAdapter] = {
    SourceSystem.ORIGINATOR: OriginatorAdapter(),
    SourceSystem.LMS: LmsAdapter(),
    SourceSystem.BANK: BankAdapter(),
}


def adapt(source: SourceSystem | str, row: dict[str, str]) -> CanonicalEvent:
    return ADAPTERS[SourceSystem(source)].adapt(row)
