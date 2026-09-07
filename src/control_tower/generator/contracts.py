ANOMALIES = (
    "missing_event",
    "duplicate_event",
    "amount_mismatch",
    "status_mismatch",
    "timing_difference",
    "composite_match",
)

ORIGINATOR_FIELDS = (
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
)
LMS_FIELDS = (
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
)
BANK_FIELDS = (
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
)
