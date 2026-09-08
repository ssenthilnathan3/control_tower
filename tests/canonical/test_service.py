import csv
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from control_tower.canonical import (
    CanonicalizationError,
    CanonicalRepository,
    canonicalize,
)
from control_tower.generator import generate
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds
from control_tower.ingestion.registry import Registration, RegistrationOutcome

CONFIG = Path("config/generator.json")


def test_normalizes_eligible_ingestion_versions_with_lineage(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    evidence = tmp_path / "evidence"
    ingested = ingest_generated_feeds(generated.output_dir, evidence)
    registry = IngestionRegistry.local(evidence)
    repository = CanonicalRepository(registry.engine)

    first = canonicalize(registry, repository)
    second = canonicalize(registry, repository)

    assert first.created_count == ingested.accepted_row_count
    assert first.replayed_count == 0
    assert second.created_count == 0
    assert second.replayed_count == ingested.accepted_row_count
    assert repository.count() == ingested.accepted_row_count


def test_rejects_evidence_changed_after_ingestion(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    evidence = tmp_path / "evidence"
    ingested = ingest_generated_feeds(generated.output_dir, evidence)
    artifact = ingested.artifacts[0].evidence_path / "source.csv"
    artifact.write_bytes(artifact.read_bytes().replace(b"APPROVED", b"REJECTED", 1))

    with pytest.raises(CanonicalizationError, match="artifact hash does not match"):
        canonicalize(IngestionRegistry.local(evidence))


def _write_originator(path: Path, status: str) -> tuple[dict[str, str], str, str]:
    row = {
        "instruction_id": "instruction-1",
        "loan_reference": "partner-loan-1",
        "customer_surrogate_id": "customer-1",
        "partner_code": "ARUNA",
        "instruction_timestamp": "2026-09-01T10:00:00+05:30",
        "amount_paise": "125000",
        "currency": "INR",
        "status": status,
        "batch_id": "batch-1",
        "received_timestamp": "2026-09-01T10:02:00+05:30",
    }
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=row, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
    raw = path.read_bytes()
    payload_hash = hashlib.sha256(raw.splitlines(keepends=True)[1]).hexdigest()
    return row, payload_hash, hashlib.sha256(raw).hexdigest()


def test_blocks_existing_canonical_record_until_conflict_is_resolved(
    tmp_path: Path,
) -> None:
    registry = IngestionRegistry.local(tmp_path)
    repository = CanonicalRepository(registry.engine)
    original_path = tmp_path / "original.csv"
    row, original_hash, original_artifact_hash = _write_originator(
        original_path, "APPROVED"
    )
    original = Registration(
        "originator",
        row["batch_id"],
        row["instruction_id"],
        row["partner_code"],
        original_hash,
        original_artifact_hash,
        f"{original_path}#line=2",
        "ACCEPTED",
    )
    assert registry.register(original) is RegistrationOutcome.NEW
    assert canonicalize(registry, repository).created_count == 1

    changed_path = tmp_path / "changed.csv"
    _, changed_hash, changed_artifact_hash = _write_originator(changed_path, "REJECTED")
    changed = replace(
        original,
        payload_hash=changed_hash,
        artifact_hash=changed_artifact_hash,
        source_location=f"{changed_path}#line=2",
    )
    assert registry.register(changed) is RegistrationOutcome.CONFLICT
    canonicalize(registry, repository)
    assert repository.count("ACTIVE") == 0

    registry.resolve_conflict(
        "originator",
        row["batch_id"],
        row["instruction_id"],
        changed_hash,
        actor="approver-1",
        reason="partner confirmed the corrected status",
    )
    result = canonicalize(registry, repository)
    assert result.created_count == 1
    assert repository.count("ACTIVE") == 1
    assert repository.count("BLOCKED") == 1
