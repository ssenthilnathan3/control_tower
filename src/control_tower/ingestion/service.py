from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from .contracts import CONTRACTS, SourceContract
from .models import (
    IngestedArtifact,
    IngestionError,
    IngestionResult,
    QuarantinedRecord,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_manifest(path: Path, source: str) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IngestionError(f"{source} manifest is unreadable: {error}") from error
    if manifest.get("source") != source:
        raise IngestionError(
            f"{source} manifest declares source {manifest.get('source')!r}"
        )
    return manifest


def _read_feed(
    feed_path: Path, source: str, contract: SourceContract
) -> tuple[bytes, list[dict[str, str]]]:
    try:
        raw = feed_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise IngestionError(f"{source} feed is unreadable: {error}") from error
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != contract.fields:
        raise IngestionError(f"{source} headers do not match the published contract")
    return raw, list(reader)


def _detail_total(rows: list[dict[str, str]], amount_field: str) -> int | None:
    try:
        return sum(int(row[amount_field]) for row in rows)
    except (KeyError, TypeError, ValueError):
        return None


def _verify_controls(
    source: str,
    raw: bytes,
    rows: list[dict[str, str]],
    contract: SourceContract,
    manifest: dict[str, object],
) -> tuple[str, int]:
    artifact_hash = _sha256_bytes(raw)
    total = _detail_total(rows, contract.amount_field)
    expected = {
        "file_sha256": artifact_hash,
        "declared_row_count": len(rows),
        "declared_total_amount_paise": total,
    }
    mismatches = [
        f"{key}: declared {manifest.get(key)!r}, actual {value!r}"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    if mismatches:
        raise IngestionError(f"{source} manifest mismatch: {'; '.join(mismatches)}")
    if total is None:
        raise IngestionError(f"{source} detail amount total is not computable")
    return artifact_hash, total


def _preserve_evidence(
    evidence_root: Path,
    source: str,
    raw: bytes,
    rows: list[dict[str, str]],
    contract: SourceContract,
    artifact_hash: str,
    total: int | None,
    validation_errors: list[tuple[str, ...]],
) -> tuple[Path, tuple[QuarantinedRecord, ...]]:
    destination = evidence_root / "artifacts" / source / artifact_hash

    # Same hash means same bytes. reuse the evidence instead of writing another copy.
    if destination.exists():
        records = [
            json.loads(line)
            for line in (destination / "records.jsonl").read_text().splitlines()
        ]
        quarantined = tuple(
            QuarantinedRecord(
                source=record["source"],
                record_id=record["record_id"],
                line_number=record["line_number"],
                payload_hash=record["payload_hash"],
                source_location=record["source_location"],
                errors=tuple(record["validation_errors"]),
            )
            for record in records
            if record["validation_state"] == "QUARANTINED"
        )
        return destination, quarantined
    temporary = destination.with_name(f".{artifact_hash}.{uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    artifact_path = temporary / "source.csv"
    artifact_path.write_bytes(raw)

    # Generated feeds have no multiline fields. hash the physical line so an operator
    # can reproduce this row's evidence from `source.csv`.
    physical_lines = raw.splitlines(keepends=True)
    quarantined: list[QuarantinedRecord] = []
    with (temporary / "records.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        for line_number, (row, raw_line, errors) in enumerate(
            zip(rows, physical_lines[1:], validation_errors), start=2
        ):
            payload_hash = _sha256_bytes(raw_line)
            source_location = f"{artifact_path.name}#line={line_number}"
            record = {
                "artifact_hash": artifact_hash,
                "line_number": line_number,
                "payload_hash": payload_hash,
                "record_id": row.get(contract.record_id_field, ""),
                "source": source,
                "source_location": source_location,
                "validation_errors": errors,
                "validation_state": "QUARANTINED" if errors else "ACCEPTED",
            }
            output.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
            if errors:
                quarantined.append(
                    QuarantinedRecord(
                        source,
                        record["record_id"],
                        line_number,
                        payload_hash,
                        source_location,
                        errors,
                    )
                )
    receipt = {
        "artifact_hash": artifact_hash,
        "accepted_count": len(rows) - len(quarantined),
        "quarantined_count": len(quarantined),
        "row_count": len(rows),
        "source": source,
        "total_amount_paise": total,
    }
    (temporary / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(temporary, destination)
    except OSError:
        # Another worker may have won the same-hash race. its bytes are equivalent.
        if not destination.exists():
            raise
        shutil.rmtree(temporary)
    return destination, tuple(quarantined)


def ingest_generated_feeds(input_dir: Path, evidence_root: Path) -> IngestionResult:
    artifacts: list[IngestedArtifact] = []
    for source, contract in CONTRACTS.items():
        feed_path = input_dir / "feeds" / f"{source}.csv"
        manifest = _load_manifest(input_dir / "manifests" / f"{source}.json", source)
        raw, rows = _read_feed(feed_path, source, contract)
        artifact_hash = _sha256_bytes(raw)
        validation_errors = [tuple(contract.validate(row)) for row in rows]
        total = _detail_total(rows, contract.amount_field)

        # A failed control is still evidence of what arrived. store it before rejecting.
        evidence_path, quarantined = _preserve_evidence(
            evidence_root,
            source,
            raw,
            rows,
            contract,
            artifact_hash,
            total,
            validation_errors,
        )
        artifact_hash, verified_total = _verify_controls(
            source, raw, rows, contract, manifest
        )
        artifacts.append(
            IngestedArtifact(
                source,
                artifact_hash,
                len(rows),
                verified_total,
                evidence_path,
                len(rows) - len(quarantined),
                quarantined,
            )
        )
    return IngestionResult(tuple(artifacts))
