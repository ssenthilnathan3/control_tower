import csv
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from control_tower.generator import generate
from control_tower.ingestion import (
    IngestionError,
    IngestionRegistry,
    ingest_generated_feeds,
)
from control_tower.ingestion.registry import Registration, RegistrationOutcome

CONFIG = Path("config/generator.json")


def test_ingests_valid_feeds_and_preserves_immutable_evidence(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    result = ingest_generated_feeds(generated.output_dir, tmp_path / "evidence")

    assert len(result.artifacts) == 3
    assert result.accepted_row_count == generated.quality_report["total_source_records"]
    for artifact in result.artifacts:
        preserved = artifact.evidence_path / "source.csv"
        original = generated.output_dir / "feeds" / f"{artifact.source}.csv"
        assert preserved.read_bytes() == original.read_bytes()
        assert (
            hashlib.sha256(preserved.read_bytes()).hexdigest() == artifact.artifact_hash
        )
        records = (artifact.evidence_path / "records.jsonl").read_text().splitlines()
        assert len(records) == artifact.row_count
        first = json.loads(records[0])
        assert first["source_location"] == "source.csv#line=2"
        assert len(first["payload_hash"]) == 64


def test_quarantines_a_bad_row_without_rejecting_valid_rows(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    feed = generated.output_dir / "feeds/originator.csv"
    rows = list(csv.DictReader(feed.read_text().splitlines()))
    rows[0]["currency"] = "USD"
    with feed.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    manifest_path = generated.output_dir / "manifests/originator.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["file_sha256"] = hashlib.sha256(feed.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    result = ingest_generated_feeds(generated.output_dir, tmp_path / "evidence")
    originator = next(item for item in result.artifacts if item.source == "originator")
    assert originator.accepted_count == originator.row_count - 1
    assert originator.quarantined_count == 1
    assert result.quarantined_row_count == 1
    quarantined = originator.quarantined_records[0]
    assert quarantined.line_number == 2
    assert quarantined.errors == ("currency: must be INR",)

    evidence = [
        json.loads(line)
        for line in (originator.evidence_path / "records.jsonl")
        .read_text()
        .splitlines()
    ]
    assert evidence[0]["validation_state"] == "QUARANTINED"
    assert all(row["validation_state"] == "ACCEPTED" for row in evidence[1:])


@pytest.mark.parametrize(
    "manifest_field",
    ["file_sha256", "declared_row_count", "declared_total_amount_paise"],
)
def test_rejects_manifest_control_mismatch(tmp_path: Path, manifest_field: str) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    manifest_path = generated.output_dir / "manifests/bank.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[manifest_field] = "incorrect"
    manifest_path.write_text(json.dumps(manifest))
    artifact_hash = hashlib.sha256(
        (generated.output_dir / "feeds/bank.csv").read_bytes()
    ).hexdigest()

    with pytest.raises(IngestionError, match="bank manifest mismatch"):
        ingest_generated_feeds(generated.output_dir, tmp_path / "evidence")
    assert (
        tmp_path / "evidence" / "artifacts" / "bank" / artifact_hash / "source.csv"
    ).exists()


def test_replay_is_a_persisted_no_op(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    evidence = tmp_path / "evidence"

    first = ingest_generated_feeds(generated.output_dir, evidence)
    second = ingest_generated_feeds(generated.output_dir, evidence)

    assert first.new_row_count == first.accepted_row_count
    assert first.replayed_row_count == 0
    assert second.new_row_count == 0
    assert second.replayed_row_count == first.accepted_row_count
    assert second.conflict_row_count == 0


def test_changed_payload_is_versioned_and_blocks_identity(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    evidence = tmp_path / "evidence"
    ingest_generated_feeds(generated.output_dir, evidence)

    feed = generated.output_dir / "feeds/originator.csv"
    rows = list(csv.DictReader(feed.read_text().splitlines()))
    changed = rows[0]
    changed["status"] = "REJECTED"
    with feed.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=changed, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = generated.output_dir / "manifests/originator.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["file_sha256"] = hashlib.sha256(feed.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    result = ingest_generated_feeds(generated.output_dir, evidence)
    originator = next(item for item in result.artifacts if item.source == "originator")
    assert originator.conflict_count == 1
    assert originator.eligible_count == originator.accepted_count - 1

    registry = IngestionRegistry.local(evidence)
    identity = registry.get_identity(
        "originator", changed["batch_id"], changed["instruction_id"]
    )
    assert identity is not None
    assert identity.state == "CONFLICT"
    assert len(identity.payload_hashes) == 2
    assert identity.payload_hashes[0] != identity.payload_hashes[1]

    replay = ingest_generated_feeds(generated.output_dir, evidence)
    assert replay.conflict_row_count == 1


def test_concurrent_registration_creates_one_identity(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'registry.db'}"
    first = IngestionRegistry(database_url)
    second = IngestionRegistry(database_url)
    record = Registration(
        source="bank",
        batch_id="batch-1",
        record_id="tx-1",
        partner_code="ARUNA",
        payload_hash="a" * 64,
        artifact_hash="b" * 64,
        source_location="source.csv#line=2",
        validation_state="ACCEPTED",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = set(
            executor.map(lambda registry: registry.register(record), (first, second))
        )

    assert outcomes == {RegistrationOutcome.NEW, RegistrationOutcome.REPLAY}
    identity = first.get_identity("bank", "batch-1", "tx-1")
    assert identity is not None
    assert identity.state == "ACCEPTED"
    assert identity.payload_hashes == ("a" * 64,)
