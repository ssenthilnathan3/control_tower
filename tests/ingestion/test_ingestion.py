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


def test_rejects_a_source_contract_violation(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    feed = generated.output_dir / "feeds/originator.csv"
    rows = list(csv.DictReader(feed.read_text().splitlines()))
    rows[0]["currency"] = "USD"
    with feed.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(IngestionError, match="currency: must be INR"):
        ingest_generated_feeds(generated.output_dir, tmp_path / "evidence")


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

    with pytest.raises(IngestionError, match="bank manifest mismatch"):
        ingest_generated_feeds(generated.output_dir, tmp_path / "evidence")
