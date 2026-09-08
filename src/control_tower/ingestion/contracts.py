from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


def _required(value: str) -> None:
    if not value.strip():
        raise ValueError("is required")


def _positive_paise(value: str) -> None:
    if int(value) <= 0:
        raise ValueError("must be a positive integer number of paise")


def _inr(value: str) -> None:
    if value != "INR":
        raise ValueError("must be INR")


def _timestamp(value: str) -> None:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("must include a timezone offset")


def _one_of(*allowed: str) -> Callable[[str], None]:
    def validate(value: str) -> None:
        if value not in allowed:
            raise ValueError(f"must be one of {', '.join(allowed)}")

    return validate


@dataclass(frozen=True)
class SourceContract:
    fields: tuple[str, ...]
    record_id_field: str
    amount_field: str
    validators: dict[str, Callable[[str], None]]

    def validate(self, row: dict[str, str]) -> list[str]:
        errors: list[str] = []
        for field, validator in self.validators.items():
            try:
                validator(row.get(field, ""))
            except (TypeError, ValueError) as error:
                errors.append(f"{field}: {error}")
        return errors


COMMON_REQUIRED = {
    "partner_code": _required,
    "batch_id": _required,
    "currency": _inr,
    "received_timestamp": _timestamp,
}

CONTRACTS = {
    "originator": SourceContract(
        fields=(
            "instruction_id",
            "loan_reference",
            "customer_surrogate_id",
            "partner_code",
            "instruction_timestamp",
            "amount_paise",
            "currency",
            "status",
            "batch_id",
            "received_timestamp",
        ),
        record_id_field="instruction_id",
        amount_field="amount_paise",
        validators={
            **COMMON_REQUIRED,
            "instruction_id": _required,
            "loan_reference": _required,
            "customer_surrogate_id": _required,
            "instruction_timestamp": _timestamp,
            "amount_paise": _positive_paise,
            "status": _one_of("APPROVED", "REJECTED", "CANCELLED"),
        },
    ),
    "lms": SourceContract(
        fields=(
            "booking_id",
            "internal_loan_id",
            "partner_loan_reference",
            "partner_code",
            "booking_timestamp",
            "booked_amount_paise",
            "currency",
            "booking_status",
            "batch_id",
            "received_timestamp",
        ),
        record_id_field="booking_id",
        amount_field="booked_amount_paise",
        validators={
            **COMMON_REQUIRED,
            "booking_id": _required,
            "internal_loan_id": _required,
            "partner_loan_reference": _required,
            "booking_timestamp": _timestamp,
            "booked_amount_paise": _positive_paise,
            "booking_status": _one_of("BOOKED", "PENDING", "FAILED", "REVERSED"),
        },
    ),
    "bank": SourceContract(
        fields=(
            "transaction_reference",
            "linked_instruction_reference",
            "partner_code",
            "value_timestamp",
            "debit_amount_paise",
            "currency",
            "settlement_status",
            "reversal_reference",
            "batch_id",
            "received_timestamp",
        ),
        record_id_field="transaction_reference",
        amount_field="debit_amount_paise",
        validators={
            **COMMON_REQUIRED,
            "transaction_reference": _required,
            "linked_instruction_reference": _required,
            "value_timestamp": _timestamp,
            "debit_amount_paise": _positive_paise,
            "settlement_status": _one_of("SETTLED", "PENDING", "FAILED", "REVERSED"),
        },
    ),
}
