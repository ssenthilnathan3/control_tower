import csv
import hashlib
import json
from pathlib import Path

from control_tower.generator import ANOMALIES, generate

CONFIG = Path("config/generator.json")


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(CONFIG, first)
    generate(CONFIG, second)
    assert _tree_hash(first) == _tree_hash(second)


def test_fresh_seed_changes_data_but_keeps_contract(tmp_path: Path) -> None:
    first = generate(CONFIG, tmp_path / "first", seed=101)
    second = generate(CONFIG, tmp_path / "second", seed=202)
    assert _tree_hash(first.output_dir) != _tree_hash(second.output_dir)
    assert first.quality_report["total_source_records"] >= 5000
    assert second.quality_report["total_source_records"] >= 5000


def test_volume_distribution_and_report_hashes(tmp_path: Path) -> None:
    result = generate(CONFIG, tmp_path / "run")
    report = result.quality_report
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert report["unique_business_events"] >= 1000
    assert report["total_source_records"] >= 5000
    assert len(report["business_dates"]) >= 3
    assert len(report["partner_counts"]) >= 3
    assert set(report["anomaly_counts"]) == set(ANOMALIES)
    assert sum(report["anomaly_counts"].values()) >= config["instruction_count"] * 0.05
    for source, summary in report["sources"].items():
        path = result.output_dir / "feeds" / f"{source}.csv"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == summary["sha256"]
        rows = _rows(path)
        assert len(rows) == summary["row_count"]
        manifest = json.loads(
            (result.output_dir / "manifests" / f"{source}.json").read_text()
        )
        assert manifest["declared_row_count"] == len(rows)
        assert manifest["declared_total_amount_paise"] == summary["total_amount_paise"]
        assert manifest["file_sha256"] == summary["sha256"]


def test_composites_balance_and_timing_is_inside_grace(tmp_path: Path) -> None:
    result = generate(CONFIG, tmp_path / "run")
    truth = [
        json.loads(line)
        for line in (result.output_dir / "truth/classifications.jsonl")
        .read_text()
        .splitlines()
    ]
    bank = _rows(result.output_dir / "feeds/bank.csv")
    by_instruction: dict[str, list[dict[str, str]]] = {}
    for row in bank:
        by_instruction.setdefault(row["linked_instruction_reference"], []).append(row)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    grace_seconds = config["grace_minutes"] * 60
    for item in truth:
        rows = by_instruction[item["business_event_id"]]
        if item["expected_classification"] == "composite_match":
            assert len(rows) == 2
            assert (
                sum(int(row["debit_amount_paise"]) for row in rows)
                == item["expected_amount_paise"]
            )
        if item["expected_classification"] == "timing_difference":
            received = __import__("datetime").datetime.fromisoformat(
                rows[0]["received_timestamp"]
            )
            cutoff = received.replace(hour=18, minute=0, second=0)
            assert 0 < (received - cutoff).total_seconds() <= grace_seconds
