from __future__ import annotations

import random
import shutil
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

from .config import GeneratorConfig
from .contracts import BANK_FIELDS, LMS_FIELDS, ORIGINATOR_FIELDS
from .io import iso as _iso
from .io import sha256 as _sha256
from .io import source_summary as _source_summary
from .io import token as _token
from .io import write_csv as _write_csv
from .io import write_json as _write_json
from .io import write_jsonl as _write_jsonl
from .models import Dataset, GeneratedData


def _assign_anomalies(config: GeneratorConfig, rng: random.Random) -> dict[int, str]:
    # One primary label per event keeps ground-truth evaluation unambiguous.
    indices = list(range(config.instruction_count))
    rng.shuffle(indices)
    assigned: dict[int, str] = {}
    cursor = 0
    for anomaly in config.anomaly_counts:
        next_cursor = cursor + config.anomaly_counts[anomaly]
        assigned.update({index: anomaly for index in indices[cursor:next_cursor]})
        cursor = next_cursor
    return assigned


def generate(
    config_path: Path, output_dir: Path, seed: int | None = None
) -> GeneratedData:
    config = GeneratorConfig.load(config_path, seed)
    rng = random.Random(config.seed)
    assigned = _assign_anomalies(config, rng)
    dataset = Dataset.empty()

    for index in range(config.instruction_count):
        partner = config.partners[index % len(config.partners)]
        business_date = config.business_dates[index % len(config.business_dates)]
        seconds = rng.randint(9 * 3600, 16 * 3600)
        instructed_at = datetime.combine(
            business_date, time(), config.timezone
        ) + timedelta(seconds=seconds)
        amount = rng.randrange(
            config.amount_min_paise, config.amount_max_paise + 1, 100
        )
        instruction_id = f"I-{_token(rng)}"
        loan_ref = f"PL-{_token(rng)}"
        internal_loan = f"LN-{_token(rng)}"
        booking_id = f"BK-{_token(rng)}"
        bank_ref = f"TX-{_token(rng)}"
        batch = f"{partner}-{business_date.isoformat()}"
        anomaly = assigned.get(index, "none")
        cutoff = datetime.combine(business_date, config.cutoff_time, config.timezone)

        expected_sources = (
            ["originator", "bank"]
            if anomaly == "missing_event"
            else ["originator", "lms", "bank"]
        )
        dataset.truth.append(
            {
                "business_event_id": instruction_id,
                "expected_classification": anomaly,
                "expected_amount_paise": amount,
                "expected_sources": expected_sources,
                "partner_code": partner,
            }
        )

        dataset.originator.append(
            {
                "instruction_id": instruction_id,
                "loan_reference": loan_ref,
                "customer_surrogate_id": f"C-{_token(rng, 10)}",
                "partner_code": partner,
                "instruction_timestamp": _iso(instructed_at),
                "amount_paise": amount,
                "currency": config.currency,
                "status": "APPROVED",
                "batch_id": batch,
                "received_timestamp": _iso(instructed_at + timedelta(minutes=2)),
            }
        )

        lms_row = {
            "booking_id": booking_id,
            "internal_loan_id": internal_loan,
            "partner_loan_reference": loan_ref,
            "partner_code": partner,
            "booking_timestamp": _iso(instructed_at + timedelta(minutes=10)),
            "booked_amount_paise": amount,
            "currency": config.currency,
            "booking_status": "BOOKED",
            "batch_id": batch,
            "received_timestamp": _iso(instructed_at + timedelta(minutes=12)),
        }
        bank_row = {
            "transaction_reference": bank_ref,
            "linked_instruction_reference": instruction_id,
            "partner_code": partner,
            "value_timestamp": _iso(instructed_at + timedelta(minutes=5)),
            "debit_amount_paise": amount,
            "currency": config.currency,
            "settlement_status": "SETTLED",
            "reversal_reference": "",
            "batch_id": batch,
            "received_timestamp": _iso(instructed_at + timedelta(minutes=7)),
        }

        if anomaly != "missing_event":
            if anomaly == "status_mismatch":
                lms_row["booking_status"] = "PENDING"
            dataset.lms.append(lms_row)

        bank_refs = [bank_ref]
        if anomaly == "amount_mismatch":
            bank_row["debit_amount_paise"] = amount + 10000
        if anomaly == "timing_difference":
            late_received = cutoff + config.grace / 2
            bank_row["received_timestamp"] = _iso(late_received)
        if anomaly == "composite_match":
            # Put the odd-paise remainder in the second leg. both legs must balance exactly.
            first = amount // 2
            second = amount - first
            bank_row["debit_amount_paise"] = first
            second_ref = f"TX-{_token(rng)}"
            second_row = dict(bank_row)
            second_row["transaction_reference"] = second_ref
            second_row["debit_amount_paise"] = second
            dataset.bank.extend((bank_row, second_row))
            bank_refs.append(second_ref)
        else:
            dataset.bank.append(bank_row)
        if anomaly == "duplicate_event":
            duplicate = dict(bank_row)
            duplicate["transaction_reference"] = f"TX-{_token(rng)}"
            duplicate["received_timestamp"] = _iso(instructed_at + timedelta(minutes=8))
            dataset.bank.append(duplicate)
            bank_refs.append(duplicate["transaction_reference"])

        dataset.relationships.append(
            {
                "business_event_id": instruction_id,
                "originator_record_ids": [instruction_id],
                "lms_record_ids": [] if anomaly == "missing_event" else [booking_id],
                "bank_record_ids": bank_refs,
            }
        )

    if output_dir.exists():
        shutil.rmtree(output_dir)
    feeds_dir = output_dir / "feeds"
    manifests_dir = output_dir / "manifests"
    truth_dir = output_dir / "truth"
    feeds_dir.mkdir(parents=True)
    manifests_dir.mkdir()
    truth_dir.mkdir()
    feed_paths = {
        "originator": feeds_dir / "originator.csv",
        "lms": feeds_dir / "lms.csv",
        "bank": feeds_dir / "bank.csv",
    }
    _write_csv(feed_paths["originator"], ORIGINATOR_FIELDS, dataset.originator)
    _write_csv(feed_paths["lms"], LMS_FIELDS, dataset.lms)
    _write_csv(feed_paths["bank"], BANK_FIELDS, dataset.bank)
    _write_jsonl(truth_dir / "classifications.jsonl", dataset.truth)
    _write_jsonl(truth_dir / "relationships.jsonl", dataset.relationships)

    summaries = {
        "originator": _source_summary(dataset.originator, "amount_paise"),
        "lms": _source_summary(dataset.lms, "booked_amount_paise"),
        "bank": _source_summary(dataset.bank, "debit_amount_paise"),
    }
    for source, summary in summaries.items():
        _write_json(
            manifests_dir / f"{source}.json",
            {
                "batch_scope": "all-generated-batches",
                "currency": config.currency,
                "declared_row_count": summary["row_count"],
                "declared_total_amount_paise": summary["total_amount_paise"],
                "file_sha256": _sha256(feed_paths[source]),
                "generator_version": config.version,
                "source": source,
            },
        )
    report = {
        "generator_version": config.version,
        "seed": config.seed,
        "business_dates": [value.isoformat() for value in config.business_dates],
        "unique_business_events": config.instruction_count,
        "partner_counts": dict(
            sorted(Counter(row["partner_code"] for row in dataset.originator).items())
        ),
        "total_source_records": sum(item["row_count"] for item in summaries.values()),
        "anomaly_counts": dict(
            sorted(
                Counter(
                    row["expected_classification"]
                    for row in dataset.truth
                    if row["expected_classification"] != "none"
                ).items()
            )
        ),
        "sources": {
            source: {**summary, "sha256": _sha256(feed_paths[source])}
            for source, summary in summaries.items()
        },
    }
    _write_json(output_dir / "quality-report.json", report)
    return GeneratedData(output_dir=output_dir, quality_report=report)
