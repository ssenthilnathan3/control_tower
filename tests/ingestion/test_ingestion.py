import csv
import hashlib
import json
from pathlib import Path

import pytest

from control_tower.generator import generate
from control_tower.ingestion import IngestionError, ingest_generated_feeds

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
